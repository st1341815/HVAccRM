"""楼盘 / 房号管理：级联选择、快捷创建、模糊搜索。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import log_action
from ..auth import current_user_or_redirect
from ..db import commit_retry, get_db
from ..models import Building, Customer, Project, Room, UNIT_OPTIONS, User
from ..permissions import require_any
from ..regions import validate_region
from ..templating import redirect, render
from ..utils import client_ip, parse_date, parse_float, parse_int

router = APIRouter(prefix="/projects", tags=["projects"])
VIEW_PERMS = ("customer:view", "contract:view", "task:view", "report:view", "admin:setting")


def _ensure_building(db: Session, project_id: int, name: str) -> Building:
    """楼栋去重登记：不存在则创建并返回（规范化房号填写）。"""
    name = (name or "").strip()
    b = db.scalars(
        select(Building).where(Building.project_id == project_id).where(Building.name == name)
    ).first()
    if not b:
        b = Building(project_id=project_id, name=name)
        db.add(b)
        db.flush()
    return b


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
    region_error = validate_region(city, district)
    if region_error:
        return render(request, "projects/form.html", project=None, error=region_error)
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
    buildings = list(
        db.scalars(select(Building).where(Building.project_id == project_id).order_by(Building.id)).all()
    )
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
    unit = unit.strip()
    if unit and unit not in UNIT_OPTIONS:
        # 单元只允许 1单元~9单元（下拉固定值），不接受自由输入
        return redirect(
            f"/projects/{project_id}",
            "单元只能从下拉中选择：1单元 ~ 9单元",
            "err",
        )
    exists = db.scalars(
        select(Room)
        .where(Room.project_id == project_id)
        .where(Room.building == building.strip())
        .where(Room.unit == (unit or None))
        .where(Room.room_no == room_no.strip())
    ).first()
    if exists:
        return redirect(f"/projects/{project_id}", "该房号已存在", "err")
    # 楼栋标准化：未登记则自动写入 buildings 表（去重）
    _ensure_building(db, project_id, building.strip())
    room = Room(
        project_id=project_id,
        building=building.strip(),
        unit=unit or None,
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


@router.post("/{project_id}/delete")
def delete_project(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_any("admin:setting", "customer:delete")),
):
    """删除楼盘（连带其房号）。楼盘下若已挂客户，必须先处理客户，禁止直接删除。"""
    project = db.get(Project, project_id)
    if not project:
        return redirect("/projects", "楼盘不存在", "err")
    room_ids = select(Room.id).where(Room.project_id == project_id)
    used = db.scalar(select(func.count(Customer.id)).where(Customer.room_id.in_(room_ids))) or 0
    if used:
        return redirect(
            f"/projects/{project_id}",
            f"该楼盘下有 {used} 个客户，不能删除；请先改动或删除这些客户的房号",
            "err",
        )
    room_count = db.scalar(select(func.count(Room.id)).where(Room.project_id == project_id)) or 0
    name = project.name
    log_action(db, user, "delete", "projects", project.id, old=project, ip=client_ip(request))
    db.delete(project)  # rooms 关系是 cascade="all, delete-orphan"，房号随之删除
    commit_retry(db)
    suffix = f"及其 {room_count} 个房号" if room_count else ""
    return redirect("/projects", f"楼盘「{name}」{suffix}已删除")


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
    buildings = list(
        db.scalars(select(Building).where(Building.project_id == project_id).order_by(Building.id)).all()
    )
    counts = dict(
        db.execute(
            select(Room.building, func.count(Room.id))
            .where(Room.project_id == project_id)
            .group_by(Room.building)
        ).all()
    )
    response = render(request, "_fragments/buildings.html", project_id=project_id, buildings=buildings, counts=counts)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/{project_id}/buildings")
def add_building(
    request: Request,
    project_id: int,
    name: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_any("customer:edit", "admin:setting")),
):
    """预录楼栋（去重）。房号表单从该表下拉，保证楼栋命名一致。"""
    project = db.get(Project, project_id)
    if not project:
        return redirect("/projects", "楼盘不存在", "err")
    name = name.strip()
    if not name:
        return redirect(f"/projects/{project_id}", "楼栋名称必填", "err")
    exists = db.scalars(
        select(Building).where(Building.project_id == project_id).where(Building.name == name)
    ).first()
    if exists:
        return redirect(f"/projects/{project_id}", f"楼栋「{name}」已存在", "err")
    db.add(Building(project_id=project_id, name=name))
    db.flush()
    log_action(db, user, "create", "buildings", None, new={"project_id": project_id, "name": name}, ip=client_ip(request))
    commit_retry(db)
    return redirect(f"/projects/{project_id}", f"楼栋「{name}」已添加")


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
    response = render(
        request, "_fragments/rooms.html", project_id=project_id, building=building, rooms=rooms
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/rooms/search")
def room_search(
    request: Request,
    project_id: int = 0,
    q: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_any(*VIEW_PERMS)),
):
    """模糊搜索房号：支持按房号（1203）或楼盘名称（华润紫玥台）搜索。

    未指定楼盘时跨楼盘搜；指定楼盘时只搜该楼盘。
    """
    key = q.strip()
    project_ids = [p.id for p in db.scalars(select(Project)).all() if key and key in (p.name or "")]
    conds = [Room.room_no.like(f"%{key}%")]
    if project_ids:
        conds.append(Room.project_id.in_(project_ids))  # 命中的楼盘下的全部房号
    stmt = select(Room).where(or_(*conds))
    if project_id:
        stmt = stmt.where(Room.project_id == project_id)
    rooms = list(db.scalars(stmt.order_by(Room.building, Room.room_no).limit(50)).all())
    projects = {p.id: p.label for p in db.scalars(select(Project)).all()}
    return render(
        request,
        "_fragments/room_search.html",
        rooms=rooms,
        projects=projects,
        q=q,
        project_id=project_id,
    )
