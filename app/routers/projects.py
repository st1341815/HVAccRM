"""楼盘 / 房号管理：级联选择、快捷创建、模糊搜索。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import log_action
from ..auth import current_user_or_redirect
from ..db import commit_retry, get_db
from ..models import Customer, Project, Room, User
from ..permissions import require_any
from ..templating import redirect, render
from ..utils import client_ip, parse_date, parse_float, parse_int

router = APIRouter(prefix="/projects", tags=["projects"])
VIEW_PERMS = ("customer:view", "contract:view", "task:view", "report:view", "admin:setting")


@router.get("")
def list_projects(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_any(*VIEW_PERMS)),
):
    stmt = select(Project).order_by(Project.id.desc())
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(Project.name.like(like), Project.city.like(like), Project.address.like(like))
        )
    projects = list(db.scalars(stmt).all())
    room_counts = dict(
        db.execute(select(Room.project_id, func.count(Room.id)).group_by(Room.project_id)).all()
    )
    cust_counts = dict(
        db.execute(
            select(Room.project_id, func.count(Customer.id))
            .join(Customer, Customer.room_id == Room.id)
            .group_by(Room.project_id)
        ).all()
    )
    return render(
        request,
        "projects/list.html",
        projects=projects,
        q=q,
        room_counts=room_counts,
        cust_counts=cust_counts,
    )


@router.get("/new")
def new_project_form(request: Request, user: User = Depends(require_any("customer:edit", "admin:setting"))):
    return render(request, "projects/form.html", project=None)


@router.post("/new")
def create_project(
    request: Request,
    name: str = Form(...),
    city: str = Form(""),
    district: str = Form(""),
    address: str = Form(""),
    developer: str = Form(""),
    delivery_date: str = Form(""),
    total_units: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_any("customer:edit", "admin:setting")),
):
    if not name.strip():
        return render(request, "projects/form.html", project=None, error="楼盘名称必填")
    project = Project(
        name=name.strip(),
        city=city.strip() or None,
        district=district.strip() or None,
        address=address.strip() or None,
        developer=developer.strip() or None,
        delivery_date=parse_date(delivery_date),
        total_units=parse_int(total_units),
        notes=notes.strip() or None,
    )
    db.add(project)
    db.flush()
    log_action(db, user, "create", "projects", project.id, new=project, ip=client_ip(request))
    commit_retry(db)
    return redirect(f"/projects/{project.id}", f"楼盘「{project.name}」已创建")


@router.post("/quick")
def quick_project(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_any("customer:edit", "admin:setting")),
):
    """客户录入现场快捷新建楼盘（HTMX / JSON 通用）。"""
    name = name.strip()
    if not name:
        return JSONResponse({"ok": False, "error": "楼盘名称必填"}, status_code=400)
    existing = db.scalars(select(Project).where(Project.name == name)).first()
    if existing:
        return JSONResponse({"ok": True, "id": existing.id, "name": existing.name, "existed": True})
    project = Project(name=name)
    db.add(project)
    db.flush()
    log_action(db, user, "create", "projects", project.id, new=project, ip=client_ip(request))
    commit_retry(db)
    return JSONResponse({"ok": True, "id": project.id, "name": project.name, "existed": False})


@router.get("/{project_id}")
def project_detail(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_any(*VIEW_PERMS)),
):
    project = db.get(Project, project_id)
    if not project:
        return render(request, "404.html", status_code=404)
    rooms = list(
        db.scalars(select(Room).where(Room.project_id == project_id).order_by(Room.building, Room.room_no)).all()
    )
    cust_map = {}
    for room in rooms:
        cnt = db.scalar(select(func.count(Customer.id)).where(Customer.room_id == room.id))
        cust_map[room.id] = cnt or 0
    buildings = sorted({r.building for r in rooms})
    return render(
        request,
        "projects/detail.html",
        project=project,
        rooms=rooms,
        buildings=buildings,
        cust_map=cust_map,
    )


@router.post("/{project_id}/rooms")
def add_room(
    request: Request,
    project_id: int,
    building: str = Form(...),
    unit: str = Form(""),
    room_no: str = Form(...),
    floor: str = Form(""),
    area: str = Form(""),
    layout: str = Form(""),
    delivery_date: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_any("customer:edit", "admin:setting")),
):
    project = db.get(Project, project_id)
    if not project:
        return redirect("/projects", "楼盘不存在", "err")
    if not building.strip() or not room_no.strip():
        return redirect(f"/projects/{project_id}", "楼栋与房号必填", "err")
    exists = db.scalars(
        select(Room)
        .where(Room.project_id == project_id)
        .where(Room.building == building.strip())
        .where(Room.unit == (unit.strip() or None))
        .where(Room.room_no == room_no.strip())
    ).first()
    if exists:
        return redirect(f"/projects/{project_id}", "该房号已存在", "err")
    room = Room(
        project_id=project_id,
        building=building.strip(),
        unit=unit.strip() or None,
        room_no=room_no.strip(),
        floor=int(parse_float(floor)) if floor.strip() else None,
        area=parse_float(area) if area.strip() else None,
        layout=layout.strip() or None,
        delivery_date=parse_date(delivery_date),
    )
    db.add(room)
    db.flush()
    log_action(db, user, "create", "rooms", room.id, new=room, ip=client_ip(request))
    commit_retry(db)
    return redirect(f"/projects/{project_id}", f"房号 {room.label} 已添加")


@router.post("/rooms/{room_id}/delete")
def delete_room(
    request: Request,
    room_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_any("admin:setting", "customer:delete")),
):
    room = db.get(Room, room_id)
    if not room:
        return redirect("/projects", "房号不存在", "err")
    used = db.scalar(select(func.count(Customer.id)).where(Customer.room_id == room_id))
    if used:
        return redirect(f"/projects/{room.project_id}", f"该房号已被 {used} 个客户使用，不能删除", "err")
    pid = room.project_id
    log_action(db, user, "delete", "rooms", room.id, old=room, ip=client_ip(request))
    db.delete(room)
    commit_retry(db)
    return redirect(f"/projects/{pid}", "房号已删除")


# ------------------------------------------------------------ HTMX 级联与搜索
@router.get("/{project_id}/buildings")
def building_options(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_any(*VIEW_PERMS)),
):
    rows = db.execute(
        select(Room.building, func.count(Room.id))
        .where(Room.project_id == project_id)
        .group_by(Room.building)
        .order_by(Room.building)
    ).all()
    return render(request, "_fragments/buildings.html", project_id=project_id, buildings=rows)


@router.get("/{project_id}/rooms")
def room_options(
    request: Request,
    project_id: int,
    building: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_any(*VIEW_PERMS)),
):
    stmt = select(Room).where(Room.project_id == project_id)
    if building.strip():
        stmt = stmt.where(Room.building == building.strip())
    rooms = list(db.scalars(stmt.order_by(Room.unit, Room.room_no)).all())
    return render(request, "_fragments/rooms.html", project_id=project_id, building=building, rooms=rooms)


@router.get("/rooms/search")
def room_search(
    request: Request,
    project_id: int = 0,
    q: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_any(*VIEW_PERMS)),
):
    """同一楼盘下模糊搜索房号：输入 1203 列出所有楼栋的 1203。"""
    stmt = select(Room).where(Room.room_no.like(f"%{q.strip()}%"))
    if project_id:
        stmt = stmt.where(Room.project_id == project_id)
    rooms = list(db.scalars(stmt.order_by(Room.building, Room.room_no).limit(50)).all())
    projects = {p.id: p.name for p in db.scalars(select(Project)).all()}
    return render(
        request,
        "_fragments/room_search.html",
        rooms=rooms,
        projects=projects,
        q=q,
        project_id=project_id,
    )
