#!/usr/bin/env python3
"""分页回归测试：115 /files 的 limit 默认只有 32，必须显式分页才能取全。

旧实现的两处问题（都会静默丢数据，不留任何报错）：
- core/client.py 首屏不传 limit（只拿到 32 条），随后用 len(data) < 1000 判断"取完了"
  -> 循环恒在第一次 break，翻页分支是死代码；
- core/transfer.py 用 limit=10000（超过接口上限 1150）且完全不分页 -> 大目录只转一部分。

这里的用例把"必须翻页"钉死：数据量刻意超过一页（1150）。
"""

import pytest

from core.client import FS_FILES_PAGE_LIMIT, list_all
from core.transfer import find_existing_directory, get_all_files_in_directory

PAGE = FS_FILES_PAGE_LIMIT


class FakeClient:
    """模拟 115 /files：不传 limit 时只给 32 条（和真实接口一致），传了就按 offset 切片。"""

    def __init__(self, pages: dict, default_page: int = 32):
        self.pages = pages
        self.default_page = default_page
        self.calls: list[dict] = []

    def fs_files(self, cid, **kwargs):
        limit = kwargs.get("limit")
        offset = kwargs.get("offset") or 0
        self.calls.append({"cid": cid, "limit": limit, "offset": offset})
        page = limit or self.default_page
        return {"data": self.pages.get(cid, [])[offset:offset + page]}


def make_files(count, pid="ROOT", prefix="f"):
    return [
        {"n": f"{prefix}{i}.mkv", "fid": str(i), "cid": "", "s": 1024, "pid": pid}
        for i in range(count)
    ]


def make_dirs(count, pid="ROOT"):
    return [{"n": f"dir{i}", "cid": str(i), "ico": "folder", "pid": pid} for i in range(count)]


@pytest.mark.asyncio
async def test_list_all_fetches_every_page():
    client = FakeClient({"0": make_files(PAGE + 50, pid="0")})
    items = await list_all(client, "0")
    assert len(items) == PAGE + 50
    assert [c["offset"] for c in client.calls] == [0, PAGE]
    assert all(c["limit"] == PAGE for c in client.calls)


@pytest.mark.asyncio
async def test_list_all_stops_on_short_page():
    client = FakeClient({"0": make_files(10, pid="0")})
    items = await list_all(client, "0")
    assert len(items) == 10
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_find_existing_directory_sees_beyond_first_page():
    # 目标目录排在最后一页：旧实现只读首屏 32 条会漏检，于是重复创建同名目录
    client = FakeClient({"0": make_dirs(PAGE + 5)})
    assert await find_existing_directory(client, "0", f"dir{PAGE + 4}") == str(PAGE + 4)


@pytest.mark.asyncio
async def test_get_all_files_collects_beyond_first_page():
    items = make_files(PAGE + 10)
    items.append({"n": "sub", "cid": "SUB", "ico": "folder", "pid": "ROOT"})
    client = FakeClient({
        "ROOT": items,
        "SUB": [{"n": "inner.mkv", "fid": "999", "cid": "", "s": 1, "pid": "SUB"}],
    })
    files, dirs = await get_all_files_in_directory(client, "ROOT")
    assert len(files) == PAGE + 11
    assert [d.name for d in dirs] == ["sub"]
