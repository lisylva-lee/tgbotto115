#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/db.py - 本地 SQLite 持久化层（share.db）

取代原先散落的 txt/json 数据文件：
  - cid_mapping.txt  -> dirs          （名称 -> CID 的目录映射）
  - cid_default.txt  -> default_cids  （分享/离线默认目录）
  - user_cid.json    -> user_cid      （每个用户自定义目录）
  - links.txt        -> links         （接收过的分享链接）
  - transfer_log.json-> transfer_log  （转存/离线记录）

使用线程安全的连接管理（check_same_thread=False + 每次操作独立连接），
适配 asyncio 环境（调用方用 run_blocking_io 包住）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional


class ShareDB:
    """SQLite 数据访问对象。"""

    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._init_lock:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS dirs (
                        name TEXT PRIMARY KEY,
                        cid TEXT NOT NULL,
                        created_at TEXT DEFAULT (datetime('now','localtime'))
                    );
                    CREATE TABLE IF NOT EXISTS default_cids (
                        kind TEXT PRIMARY KEY,          -- 'share' | 'offline'
                        cid TEXT NOT NULL,
                        name TEXT,
                        updated_at TEXT DEFAULT (datetime('now','localtime'))
                    );
                    CREATE TABLE IF NOT EXISTS user_cid (
                        user_id INTEGER NOT NULL,
                        kind TEXT NOT NULL,             -- 'share' | 'offline'
                        cid TEXT NOT NULL,
                        name TEXT,
                        updated_at TEXT DEFAULT (datetime('now','localtime')),
                        PRIMARY KEY (user_id, kind)
                    );
                    CREATE TABLE IF NOT EXISTS links (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        share_code TEXT,
                        receive_code TEXT,
                        created_at TEXT DEFAULT (datetime('now','localtime'))
                    );
                    CREATE TABLE IF NOT EXISTS transfer_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        key TEXT UNIQUE,
                        kind TEXT,                       -- 'share' | 'offline'
                        share_code TEXT,
                        receive_code TEXT,
                        url TEXT,
                        status TEXT,
                        target_cid TEXT,
                        detail TEXT,
                        created_at TEXT DEFAULT (datetime('now','localtime'))
                    );
                    """
                )

    # ---------------- dirs（目录映射） ----------------

    def list_dirs(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name, cid FROM dirs ORDER BY name").fetchall()
        return {r["name"]: r["cid"] for r in rows}

    def add_dir(self, name: str, cid: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO dirs (name, cid) VALUES (?, ?)",
                (name, cid),
            )

    def remove_dir(self, name: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM dirs WHERE name = ?", (name,))

    # ---------------- default_cids（系统默认目录） ----------------

    def get_default_cids(self) -> dict[str, dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT kind, cid, name FROM default_cids").fetchall()
        return {r["kind"]: {"cid": r["cid"], "name": r["name"]} for r in rows}

    def set_default_cid(self, kind: str, cid: str, name: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO default_cids (kind, cid, name, updated_at) "
                "VALUES (?, ?, ?, datetime('now','localtime'))",
                (kind, cid, name),
            )

    def remove_default_cid(self, kind: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM default_cids WHERE kind = ?", (kind,))

    # ---------------- user_cid（用户自定义目录） ----------------

    def get_user_cid(self, user_id: int, kind: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cid, name FROM user_cid WHERE user_id = ? AND kind = ?",
                (user_id, kind),
            ).fetchone()
        if not row:
            return None
        return {"cid": row["cid"], "name": row["name"]}

    def get_user_all(self, user_id: int) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT kind, cid, name FROM user_cid WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return {r["kind"]: {"cid": r["cid"], "name": r["name"]} for r in rows}

    def set_user_cid(self, user_id: int, kind: str, cid: str, name: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO user_cid (user_id, kind, cid, name, updated_at) "
                "VALUES (?, ?, ?, ?, datetime('now','localtime'))",
                (user_id, kind, cid, name),
            )

    def remove_user_cid(self, user_id: int, kind: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM user_cid WHERE user_id = ? AND kind = ?", (user_id, kind))

    def remove_user_all(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM user_cid WHERE user_id = ?", (user_id,))

    # ---------------- links（接收的分享链接） ----------------

    def append_links(self, user_id: int, items: list[dict]) -> None:
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO links (user_id, share_code, receive_code) VALUES (?, ?, ?)",
                [(user_id, it.get("share_code"), it.get("receive_code")) for it in items],
            )

    # ---------------- transfer_log（转存/离线记录） ----------------

    def upsert_transfer_log(self, entry: dict) -> None:
        """key 唯一；写入 share/offline 记录。"""
        key = entry["key"]
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO transfer_log "
                "(key, kind, share_code, receive_code, url, status, target_cid, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))",
                (
                    key,
                    entry.get("kind"),
                    entry.get("share_code"),
                    entry.get("receive_code"),
                    entry.get("url"),
                    entry.get("status"),
                    entry.get("target_cid"),
                    entry.get("detail"),
                ),
            )

    def load_transfer_log(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM transfer_log").fetchall()
        out = {}
        for r in rows:
            out[r["key"]] = {
                "kind": r["kind"],
                "share_code": r["share_code"],
                "receive_code": r["receive_code"],
                "url": r["url"],
                "status": r["status"],
                "target_cid": r["target_cid"],
                "detail": r["detail"],
                "created_at": r["created_at"],
            }
        return out

    # ---------------- 工具 ----------------

    def _table_count(self, table: str) -> int:
        """返回指定业务表的行数（迁移时判空用）。"""
        with self._connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
        return int(row["c"]) if row else 0

    def reset(self) -> None:
        """清空所有业务表（保留表结构）。测试用。"""
        with self._connect() as conn:
            for t in ("transfer_log", "links", "user_cid", "default_cids", "dirs"):
                conn.execute(f"DELETE FROM {t}")
