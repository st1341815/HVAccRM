"""一次性修正：楼盘名称中的阿拉伯数字期数转汉字大写数字（1期→一期 … 10期→十期）。

用法（在容器内执行）：
    python -m scripts.normalize_project_names
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Project  # noqa: E402

CN_PHASE = {"1": "一", "2": "二", "3": "三", "4": "四", "5": "五", "6": "六", "7": "七", "8": "八", "9": "九", "10": "十"}


def normalize_phase(name: str) -> str:
    name = (name or "").strip()
    return re.sub(r"(\d+)期", lambda m: CN_PHASE.get(m.group(1), m.group(1)) + "期", name)


def main() -> None:
    db = SessionLocal()
    try:
        projects = list(db.scalars(select(Project)).all())
        changed = 0
        for p in projects:
            new_name = normalize_phase(p.name)
            if new_name != p.name:
                print(f"修正：{p.name!r} -> {new_name!r}")
                p.name = new_name
                changed += 1
        db.commit()
        print(f"完成：共 {len(projects)} 个楼盘，修正 {changed} 个。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
