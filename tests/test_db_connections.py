#!/usr/bin/env python3
"""回归测试：SQLite 连接必须在每次操作后关闭。

历史问题：core/db.py 里写的是 with sqlite3.connect(...)（或 with self._connect() as conn），
而 sqlite3.Connection 的 with 只是事务上下文 —— 退出时 commit/rollback，但**不关闭**
连接。于是每一次 DB 操作都会泄漏一个连接，长跑必然累积文件描述符与内存。

这里用 sqlite3.connect 的 factory 参数跟踪所有连接，逐个断言它们真的被关掉了
（已关闭的连接再 execute 会抛 ProgrammingError）。
"""

import sqlite3

import pytest

from core import db as db_module
from core.db import ShareDB


class TrackingConnection(sqlite3.Connection):
    closed = 0

    def close(self):
        type(self).closed += 1
        return super().close()


@pytest.fixture
def tracked(monkeypatch):
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def fake_connect(*args, **kwargs):
        kwargs.setdefault("factory", TrackingConnection)
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    TrackingConnection.closed = 0
    monkeypatch.setattr(db_module.sqlite3, "connect", fake_connect)
    return opened


def test_every_operation_closes_its_connection(tmp_path, tracked):
    db = ShareDB(tmp_path / "share.db")
    tracked.clear()
    TrackingConnection.closed = 0

    db.add_dir("电影", "123")
    db.list_dirs()
    db.set_default_cid("share", "123", "电影")
    db.get_default_cids()
    db.set_user_cid(1, "share", "123", "电影")
    db.get_user_all(1)
    db.append_links(1, [{"share_code": "abc", "receive_code": "1234"}])
    db.upsert_transfer_log({"key": "abc:1234", "kind": "share", "status": "ok"})
    db.load_transfer_log()
    db._table_count("dirs")

    assert len(tracked) == 10
    assert TrackingConnection.closed == 10
    for conn in tracked:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


def test_data_is_committed_despite_closing(tmp_path):
    path = tmp_path / "share.db"
    ShareDB(path).add_dir("动画", "456")
    # 新实例（新连接）必须能看到已提交的数据
    assert ShareDB(path).list_dirs() == {"动画": "456"}
