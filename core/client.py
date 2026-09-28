#!/usr/bin/env python3
"""
core/client.py - P115Client 封装

提供线程安全的客户端初始化与缓存，以及 115 网盘目录操作工具：
  - get_dir_name_by_cid  : 由 CID 查询文件夹名称
  - resolve_cid_by_path  : 由路径（/a/b/c）解析目录 CID
  - ensure_dir_by_path   : 由路径逐级创建目录（已存在则复用），返回最终 CID

Cookie 来自 config.yaml（p115.cookie），不再读 cookie.txt。
"""

import asyncio
import logging
from typing import Any, Optional

from .ratelimit import get_rate_limiter

logger = logging.getLogger(__name__)

# 尝试导入 P115Client
try:
    from p115client import P115Client
except ImportError:
    P115Client = None
    logger.warning("p115client 未安装，部分功能将不可用")


class P115ClientWrapper:
    """P115Client 的异步包装器，提供线程安全的单例模式。"""

    _instance: Optional["P115ClientWrapper"] = None
    # 不要在导入时创建 asyncio.Lock：它会绑定到第一个使用它的事件循环，之后在另一个
    # 事件循环里使用会抛 "is bound to a different event loop"（测试、循环重启都会踩）。
    # 改为按当前事件循环惰性创建。
    _lock: asyncio.Lock | None = None
    _lock_loop: asyncio.AbstractEventLoop | None = None

    def __init__(self, qps: float | None = None):
        self._client: Any | None = None
        self._cookie: str | None = None
        self._init_lock = asyncio.Lock()
        # 并发上限读取 config.yaml 的 runtime.max_concurrent_tasks（此前硬编码 3，
        # 导致配置项调了没反应）
        try:
            from config import config as app_config
        except Exception:
            app_config = None
        if qps is None:
            qps = getattr(app_config, "request_qps", 1.0) if app_config else 1.0
        max_concurrent = getattr(app_config, "max_concurrent_tasks", 3) if app_config else 3
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrent)))
        # 全局限速器：所有 115 请求按 QPS 节流，防止触发风控导致 cookie 失效
        self._qps = qps
        self._rate_limiter = get_rate_limiter(self._qps)

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """返回绑定到当前事件循环的锁（惰性创建，跨循环自动重建）。"""
        loop = asyncio.get_running_loop()
        if cls._lock is None or cls._lock_loop is not loop:
            cls._lock = asyncio.Lock()
            cls._lock_loop = loop
        return cls._lock

    @classmethod
    async def get_instance(cls) -> "P115ClientWrapper":
        async with cls._get_lock():
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    async def run_blocking_io(self, func, *args, **kwargs):
        async with self._semaphore:
            # 全局限速：任意两次 115 请求间隔 >= 1/QPS
            await self._rate_limiter.acquire()
            return await asyncio.to_thread(func, *args, **kwargs)

    async def get_client(self, cookie: str) -> Any | None:
        """获取 P115Client 实例（线程安全）。cookie 为 config.yaml 中的字符串。"""
        async with self._init_lock:
            if self._client is not None:
                return self._client
            if P115Client is None:
                logger.warning("p115client 未安装，无法创建客户端")
                return None
            try:
                # 注意：不要传 check_for_relogin —— PyPI 0.0.9.x 已移除该参数，
                # 传了会 TypeError 导致初始化失败（镜像/新装环境必现）；
                # 且该参数会触发自动 relogin 可能作废 config.yaml 里的 cookie。
                self._client = await self.run_blocking_io(P115Client, cookie)
                if self._client and getattr(self._client, 'user_id', None):
                    logger.info(f"115 客户端初始化成功，user_id={self._client.user_id}")
                else:
                    logger.error("115 客户端初始化失败：无法获取 user_id")
                    self._client = None
                return self._client
            except Exception:
                logger.exception("初始化 115 客户端失败:")
                self._client = None
                return None

    @property
    def client(self) -> Any | None:
        return self._client

    def invalidate(self):
        self._client = None


async def get_p115_client(cookie: str) -> Any | None:
    """获取 P115Client 的便捷函数（全局限速由 config.request_qps 控制）。"""
    wrapper = await P115ClientWrapper.get_instance()
    return await wrapper.get_client(cookie)


async def run_blocking_io(func, *args, **kwargs):
    """运行阻塞 I/O 的便捷函数。"""
    wrapper = await P115ClientWrapper.get_instance()
    return await wrapper.run_blocking_io(func, *args, **kwargs)


# ================================
# 115 网盘目录工具
# ================================

async def get_dir_name_by_cid(client: Any, cid: str) -> str | None:
    """由 CID 查询目录名称。

    fs_info(proapi) 常因 authorization 不可用，因此改用 fs_files 从根目录递归搜索
    匹配该 CID 的目录项，取其名称。返回 None 表示未找到。
    """
    if not cid:
        return None
    try:
        result = await _search_dir_name_by_cid(client, str(cid), "0", depth=0, max_depth=8)
        return result
    except Exception:
        logger.debug(f"搜索目录名失败: {cid}")
        return None


async def _search_dir_name_by_cid(client: Any, cid: str, parent_cid: str, depth: int, max_depth: int) -> str | None:
    """在 parent_cid 下递归查找 cid 对应的目录名（分页遍历）。"""
    if depth > max_depth:
        return None
    try:
        items = await _list_files_all(client, parent_cid)
    except Exception:
        return None
    for item in items:
        if str(item.get("cid")) == cid:
            return item.get("n")
    for item in items:
        if is_folder_item(item) and item.get("cid"):
            found = await _search_dir_name_by_cid(client, cid, str(item.get("cid")), depth + 1, max_depth)
            if found:
                return found
    return None


async def resolve_cid_by_path(client: Any, path: str) -> str | None:
    """由路径（如 /电影/动作）解析目录 CID（fs_dir_getid）。返回 None 表示不存在。

    注意：fs_dir_getid 成功时把 id 放在响应顶层（{"state":true,...,"id":"..."}），
    而非 data 内，需要同时兼容两种结构。
    """
    path = path.strip()
    if not path:
        return None
    if not path.startswith(("/", ">")):
        path = "/" + path
    try:
        info = await run_blocking_io(client.fs_dir_getid, path)
        if not info.get("state"):
            return None
        # 顶层 id（fs_dir_getid 实际返回）
        cid = info.get("id")
        if not cid:
            # 兼容 data 内结构
            data = info.get("data")
            if isinstance(data, dict):
                cid = data.get("id") or data.get("cid")
            else:
                cid = data
        return str(cid) if cid else None
    except Exception:
        logger.debug(f"fs_dir_getid 解析路径失败: {path}")
        return None


async def ensure_dir_by_path(client: Any, path: str) -> tuple[str | None, str]:
    """
    确保路径存在，返回 (cid, name)。
    - 若路径已存在，直接返回其 CID 与末级目录名。
    - 若不存在，逐级 fs_mkdir 创建。
    """
    path = path.strip()
    if not path:
        return None, ""
    parts = [p for p in path.replace(">", "/").split("/") if p]
    if not parts:
        return None, ""

    # 从根逐级解析/创建
    current_cid = "0"  # 根目录
    for seg in parts:
        child_cid = await _find_child_dir_cid(client, current_cid, seg)
        if child_cid is None:
            # 需要创建
            try:
                mkdir = await run_blocking_io(client.fs_mkdir, seg, current_cid)
            except Exception as e:
                logger.warning(f"fs_mkdir 请求异常: {seg} in {current_cid}: {e}")
                return None, seg
            if not mkdir or mkdir.get("state") is False:
                logger.warning(f"fs_mkdir 创建失败: {seg} in {current_cid}: {mkdir}")
                return None, seg
            # 新目录 id 位于响应顶层(cid/file_id)，部分接口包在 data 内，全部兼容
            new_cid = (
                mkdir.get("cid")
                or mkdir.get("file_id")
                or ((mkdir.get("data") or {}).get("cid") if isinstance(mkdir.get("data"), dict) else None)
                or ((mkdir.get("data") or {}).get("file_id") if isinstance(mkdir.get("data"), dict) else None)
            )
            if not new_cid:
                logger.warning(f"fs_mkdir 响应未包含新目录ID: {mkdir}")
                return None, seg
            current_cid = str(new_cid)
        else:
            current_cid = child_cid

    return current_cid, parts[-1]


async def _find_child_dir_cid(client: Any, parent_cid: str, name: str) -> str | None:
    """在 parent_cid 目录下查找名为 name 的子目录 CID（分页遍历）。"""
    try:
        items = await _list_files_all(client, parent_cid)
        for item in items:
            if str(item.get("n")) == name and is_folder_item(item):
                return str(item.get("cid") or item.get("fid"))
    except Exception:
        logger.debug(f"fs_files 查找子目录失败: {name} in {parent_cid}")
    return None


# 115 /files 的 limit 默认只有 32（API 文档：最大值 1,150）。首屏不传 limit 只会拿到
# 第一页 32 条，此后任何"len(data) < limit 就认为取完"的判断都会立刻成立，翻页逻辑
# 永远不会执行 —— 表现为目录名解析失败、同名目录漏检（进而重复建目录）、大目录静默截断。
FS_FILES_PAGE_LIMIT = 1150
FS_FILES_MAX_ITEMS = 200000


async def list_all(
    client: Any,
    parent_cid: str,
    limit: int = FS_FILES_PAGE_LIMIT,
    max_items: int = FS_FILES_MAX_ITEMS,
) -> list[dict]:
    """分页拉取目录下的全部条目。

    - 每页都显式传 limit（默认 1150，115 接口允许的最大值）与 offset；
    - 以"本页返回条数 < limit"判断结束，而不是拿首屏的 32 条去比 1000；
    - 单次调用最多收集 max_items 条，防御异常响应导致的死循环。
    分页失败会向上抛出，由调用方决定降级还是报错 —— 不要静默当成"目录就这些"。
    """
    all_items: list[dict] = []
    offset = 0
    while True:
        files_info = await run_blocking_io(
            client.fs_files, parent_cid, offset=offset, limit=limit
        )
        data = (files_info or {}).get("data") or []
        all_items.extend(data)
        if len(data) < limit or len(all_items) >= max_items:
            break
        offset += limit
    return all_items


async def _list_files_all(client: Any, parent_cid: str, limit: int = FS_FILES_PAGE_LIMIT) -> list[dict]:
    """兼容旧调用点：等价于 list_all（失败向上抛出，由调用方处理）。"""
    return await list_all(client, parent_cid, limit=limit)


def is_folder_item(item: dict) -> bool:
    """判断 API item 是否为目录。"""
    return str(item.get("ico")) == "folder" or (
        not bool(item.get("fid")) and bool(item.get("cid"))
    )
