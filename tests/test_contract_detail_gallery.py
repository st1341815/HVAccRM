"""合同详情页「纸质合同图片」区块的展示约定。

**v1.15.7 起**：图片下方不再显示原始文件名，只保留上传时间与删除入口。
理由：手机拍/扫描的原始文件名多是 `IMG_20261006_143012.jpg` 这种长串，
一图一行的 caption 被文件名撑开，图与图之间对不齐，反而看不清有哪几张。

这里用真实渲染钉住：区块里不能出现 `orig_name`，但时间必须还在
（时间是用来判断「这张是不是最新补拍的那张」的唯一线索，不能一起砍掉）。
"""
from __future__ import annotations

from starlette.requests import Request

from app.models import Contract, Customer, Photo
from app.routers import contracts as contracts_router


def _req(path: str = "/contracts/1") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "scheme": "http",
            "server": ("127.0.0.1", 8090),
            "client": ("10.0.0.9", 52341),
            "headers": [(b"user-agent", b"pytest")],
            "query_string": b"",
        }
    )


def _seed(db, admin):
    cust = Customer(name="纸质图客户", phone="13900000000", type="家装",
                    owner_id=admin.id, status="active")
    db.add(cust)
    db.flush()
    ctr = Contract(customer_id=cust.id, no="PAPER-001", product_type="中央空调",
                   total_amount=20000, sign_date="2026-10-06", status="active",
                   created_by=admin.id)
    db.add(ctr)
    db.flush()
    db.add(Photo(customer_id=cust.id, contract_id=ctr.id, kind="contract",
                 path="paper/1.jpg", orig_name="IMG_20261006_143012_扫描件最终版.jpg",
                 created_at="2026-10-06T14:30:12"))
    db.flush()
    return ctr


def _gallery_segment(html: str) -> str:
    """切出「纸质合同图片」区块（到「收款流水」为止）。"""
    assert "纸质合同图片" in html, "详情页应包含纸质合同图片区块"
    return html.split("纸质合同图片", 1)[1].split("收款流水", 1)[0]


def test_contract_gallery_hides_original_file_name(db, admin):
    ctr = _seed(db, admin)
    resp = contracts_router.contract_detail(
        request=_req(f"/contracts/{ctr.id}"), contract_id=ctr.id, db=db, user=admin
    )
    seg = _gallery_segment(bytes(resp.body).decode())

    assert "IMG_20261006_143012_扫描件最终版.jpg" not in seg, "图片下方不应再显示原始文件名"
    assert ".jpg" not in seg, "区块里不应残留任何文件名"


def test_contract_gallery_keeps_upload_time(db, admin):
    ctr = _seed(db, admin)
    resp = contracts_router.contract_detail(
        request=_req(f"/contracts/{ctr.id}"), contract_id=ctr.id, db=db, user=admin
    )
    seg = _gallery_segment(bytes(resp.body).decode())

    assert "2026-10-06T14:30" in seg, "上传时间要保留，用来分辨哪张是后补的"
