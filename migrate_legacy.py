#!/usr/bin/env python3
"""把旧版散落的 txt/json 数据迁移进 SQLite(data/share.db)。

README 一直让用户执行 python migrate_legacy.py，但仓库里此前并没有这个文件，
照做只会得到 "can't open file"。本脚本按 README 记载的对应关系迁移：

    cid_mapping.txt    -> dirs          （名称 -> CID）
    cid_default.txt    -> default_cids  （分享/离线默认目录）
    user_cid.json      -> user_cid      （每个用户的自定义目录）
    links.txt          -> links         （接收过的分享链接）
    transfer_log.json  -> transfer_log  （转存/离线记录）

用法：
    python migrate_legacy.py --dry-run     # 只看解析结果，不写库、不改名（建议先跑）
    python migrate_legacy.py               # 正式迁移，成功后把旧文件改名为 *.migrated
    python migrate_legacy.py --no-rename   # 迁移但不改旧文件名

设计要点：
- 幂等：写库成功后旧文件被改名，重复执行不会再匹配到它们；
- 解析是容错的（多分隔符、两种字段顺序都能认），但旧格式没有权威定义，
  无法覆盖全部历史变体 —— 所以请先用 --dry-run 核对一遍；
- 缺文件不算错误，只提示跳过；
- 只依赖 core.db，不读 config.yaml（迁移旧数据不该要求先填 token/cookie）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from core.db import ShareDB

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "data" / "share.db"

CID_RE = re.compile(r"^\d{6,25}$")
SPLIT_RE = re.compile(r"[=:,，;；|\t ]+")

LEGACY_FILES = (
    "cid_mapping.txt",
    "cid_default.txt",
    "user_cid.json",
    "links.txt",
    "transfer_log.json",
)


def _rows(text: str):
    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        if line and not line.startswith("#"):
            yield line


def parse_dir_mapping(text: str) -> list[tuple[str, str]]:
    """解析 cid_mapping.txt：每行一条 名称/CID，顺序不限、分隔符不限。"""
    out: list[tuple[str, str]] = []
    for line in _rows(text):
        parts = [p for p in SPLIT_RE.split(line) if p]
        cids = [p for p in parts if CID_RE.match(p)]
        names = [p for p in parts if p not in cids]
        if cids and names:
            out.append((" ".join(names), cids[0]))
        elif len(cids) >= 2:
            out.append((cids[0], cids[1]))
    return out


def parse_default_cids(text: str) -> dict[str, str]:
    """解析 cid_default.txt：识别 share/offline 关键字；无关键字时按行序兜底。"""
    found: dict[str, str] = {}
    loose: list[str] = []
    for line in _rows(text):
        cids = [p for p in SPLIT_RE.split(line) if p and CID_RE.match(p)]
        if not cids:
            continue
        lowered = line.lower()
        if "offline" in lowered or "离线" in line:
            found.setdefault("offline", cids[0])
        elif "share" in lowered or "分享" in line:
            found.setdefault("share", cids[0])
        else:
            loose.append(cids[0])
    for kind in ("share", "offline"):
        if kind not in found and loose:
            found[kind] = loose.pop(0)
    return found


def parse_user_cids(obj) -> list[tuple[int, str, str, str]]:
    """解析 user_cid.json：user_id -> cid 字符串 / {share, offline} / {cid, type, name}。"""
    out: list[tuple[int, str, str, str]] = []
    if not isinstance(obj, dict):
        return out
    for raw_uid, value in obj.items():
        try:
            uid = int(str(raw_uid).strip())
        except ValueError:
            continue
        if isinstance(value, str) and CID_RE.match(value.strip()):
            out.append((uid, "share", value.strip(), ""))
            continue
        if not isinstance(value, dict):
            continue
        for kind in ("share", "offline"):
            entry = value.get(kind)
            if isinstance(entry, str) and CID_RE.match(entry.strip()):
                out.append((uid, kind, entry.strip(), ""))
            elif isinstance(entry, dict) and entry.get("cid"):
                out.append((uid, kind, str(entry["cid"]).strip(), str(entry.get("name") or "")))
        if value.get("cid"):
            kind = str(value.get("type") or value.get("kind") or "share").lower()
            if kind not in ("share", "offline"):
                kind = "share"
            out.append((uid, kind, str(value["cid"]).strip(), str(value.get("name") or "")))
    return out


def parse_transfer_log(obj) -> list[dict]:
    if not isinstance(obj, dict):
        return []
    out = []
    for key, entry in obj.items():
        if isinstance(entry, dict):
            item = dict(entry)
            item.setdefault("key", key)
            out.append(item)
    return out


def _read(path: Path):
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="把旧版 txt/json 数据迁移进 SQLite")
    parser.add_argument("--dir", default=str(BASE_DIR), help="旧数据文件所在目录（默认项目根目录）")
    parser.add_argument("--db", default=str(DB_FILE), help="SQLite 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只解析并打印，不写库、不改名")
    parser.add_argument("--no-rename", action="store_true", help="迁移后不改名旧文件")
    args = parser.parse_args(argv)

    src = Path(args.dir)
    dirs_text = _read(src / "cid_mapping.txt")
    defaults_text = _read(src / "cid_default.txt")
    user_text = _read(src / "user_cid.json")
    links_text = _read(src / "links.txt")
    log_text = _read(src / "transfer_log.json")

    dirs = parse_dir_mapping(dirs_text) if dirs_text else []
    default_cids = parse_default_cids(defaults_text) if defaults_text else {}
    links = []
    if links_text:
        from core.utils import parse_115_links_from_text

        links = parse_115_links_from_text(links_text)

    user_cids: list[tuple[int, str, str, str]] = []
    if user_text:
        try:
            user_cids = parse_user_cids(json.loads(user_text))
        except json.JSONDecodeError as exc:
            print("[warn] user_cid.json 不是合法 JSON，已跳过：" + str(exc))

    logs: list[dict] = []
    if log_text:
        try:
            logs = parse_transfer_log(json.loads(log_text))
        except json.JSONDecodeError as exc:
            print("[warn] transfer_log.json 不是合法 JSON，已跳过：" + str(exc))

    print("=== 解析统计 ===")
    print("  dirs         : " + str(len(dirs)) + "  " + str(dirs[:5]))
    print("  default_cids : " + str(default_cids))
    print("  user_cid     : " + str(len(user_cids)) + "  " + str(user_cids[:5]))
    print("  links        : " + str(len(links)))
    print("  transfer_log : " + str(len(logs)))

    if args.dry_run:
        print()
        print("[dry-run] 未写库、未改名。核对无误后去掉 --dry-run 再执行。")
        return 0

    db = ShareDB(args.db)
    existing = db.list_dirs()
    added_dirs = 0
    for name, cid in dirs:
        if name in existing:
            continue
        db.add_dir(name, cid)
        added_dirs += 1

    for kind, cid in default_cids.items():
        db.set_default_cid(kind, cid, "")

    for uid, kind, cid, name in user_cids:
        db.set_user_cid(uid, kind, cid, name)

    if links:
        db.append_links(0, links)

    for entry in logs:
        if entry.get("key"):
            db.upsert_transfer_log(entry)

    print()
    print("[ok] dirs +" + str(added_dirs) + " / default_cids " + str(len(default_cids)) +
          " / user_cid " + str(len(user_cids)) + " / links " + str(len(links)) +
          " / transfer_log " + str(len(logs)))

    if not args.no_rename:
        for name in LEGACY_FILES:
            path = src / name
            if path.exists():
                backup = path.with_name(path.name + ".migrated")
                path.rename(backup)
                print("[ok] " + path.name + " -> " + backup.name)

    print("旧文件已备份为 *.migrated；确认无误后可自行删除。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
