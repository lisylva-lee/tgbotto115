#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/transfer.py - 转存逻辑

包含目录遍历、目录创建、文件转存、离线下载等核心功能。
"""

import asyncio
import logging
from typing import Optional, Any

import requests

from .models import FileInfo, DirInfo, OfflineTaskResult
from .utils import format_file_size, is_directory_item
from .client import run_blocking_io

logger = logging.getLogger(__name__)


# ================================
# 目录遍历
# ================================

async def get_all_files_in_directory(
    client: Any,
    cid: str,
    collected_files: Optional[list[FileInfo]] = None,
    collected_dirs: Optional[list[DirInfo]] = None,
    depth: int = 0,
    share_snap_data: Optional[list[dict]] = None,
    max_depth: int = 15
) -> tuple[list[FileInfo], list[DirInfo]]:
    """
    递归获取目录下所有文件和子目录。
    
    Args:
        client: P115Client 实例
        cid: 目录 CID
        collected_files: 收集的文件列表
        collected_dirs: 收集的目录列表
        depth: 当前递归深度
        share_snap_data: 分享快照数据用于回退
        max_depth: 最大递归深度
        
    Returns:
        (文件列表, 目录列表) 元组
    """
    if collected_files is None:
        collected_files = []
    if collected_dirs is None:
        collected_dirs = []
    
    if depth > max_depth:
        logger.warning(f"{'  ' * depth}[警告] 达到最大递归深度 {max_depth}，停止遍历 CID: {cid}")
        return collected_files, collected_dirs
    
    logger.debug(f"{'  ' * depth}[调试] 遍历目录 CID: {cid} (深度: {depth})")
    
    try:
        files_info = None
        
        # 方法1：使用 fs_files API
        try:
            files_info = await run_blocking_io(client.fs_files, cid, limit=10000)
            if not (files_info and files_info.get('data')):
                files_info = None
        except Exception as e:
            logger.debug(f"{'  ' * depth}[调试] fs_files 失败: {e}")
        
        # 方法2：回退到分享快照数据
        if not files_info and share_snap_data:
            try:
                for item in share_snap_data:
                    if str(item.get('pid')) == str(cid):
                        if not files_info:
                            files_info = {'data': []}
                        files_info['data'].append(item)
            except Exception as e:
                logger.debug(f"{'  ' * depth}[调试] 分享快照回退失败: {e}")
        
        if not files_info or not files_info.get('data'):
            logger.warning(f"{'  ' * depth}[警告] 目录为空或无法获取内容: {cid}")
            return collected_files, collected_dirs
        
        files_list = files_info['data']
        
        for item in files_list:
            if item.get('pid') == '0':
                continue
            
            name = item.get('n', '未知')
            item_cid = item.get('cid')
            fid = item.get('fid')
            
            if is_directory_item(item):
                logger.debug(f"{'  ' * depth}[调试] 子目录: {name} (CID: {item_cid})")
                dir_info = DirInfo.from_api_item(item, cid, depth)
                collected_dirs.append(dir_info)
                
                # 递归遍历子目录
                await get_all_files_in_directory(
                    client, item_cid, collected_files, collected_dirs,
                    depth + 1, share_snap_data, max_depth
                )
            else:
                size = format_file_size(item.get('s', 0))
                logger.debug(f"{'  ' * depth}[调试] 文件: {name} ({size}) fid={fid}")
                file_info = FileInfo.from_api_item(item, cid, depth)
                collected_files.append(file_info)
                
    except Exception as e:
        logger.exception(f"[错误] 遍历目录失败 {cid}:")
    
    return collected_files, collected_dirs


# ================================
# 目录创建
# ================================

async def find_existing_directory(
    client: Any,
    parent_cid: str,
    dir_name: str
) -> Optional[str]:
    """
    查找父目录下已存在的同名目录。
    
    Args:
        client: P115Client 实例
        parent_cid: 父目录 CID
        dir_name: 目录名称
        
    Returns:
        已存在目录的 CID 或 None
    """
    try:
        files_info = await run_blocking_io(client.fs_files, parent_cid)
        for item in files_info.get('data', []) if files_info else []:
            if item.get('n') == dir_name and is_directory_item(item):
                return item.get('cid')
    except Exception as e:
        logger.debug(f"[调试] 查找现有目录失败 {dir_name} 在 {parent_cid}: {e}")
    return None


async def create_directory_structure(
    client: Any,
    target_cid: str,
    dirs_to_create: list[DirInfo]
) -> dict[str, str]:
    """
    在目标位置创建目录结构。
    
    Args:
        client: P115Client 实例
        target_cid: 目标父目录 CID
        dirs_to_create: 要创建的目录列表
        
    Returns:
        原始 CID -> 新 CID 的映射字典
    """
    created = {'root': target_cid}
    
    # 按深度排序，确保父目录先创建
    dirs_sorted = sorted(dirs_to_create, key=lambda x: x.depth)
    
    for d in dirs_sorted:
        dir_name = d.name
        parent_cid = d.parent_cid
        original_cid = d.cid
        new_parent = created.get(parent_cid, target_cid)
        
        try:
            logger.debug(f"[调试] 创建目录: {dir_name} -> 父 {new_parent}")
            result = await run_blocking_io(client.fs_mkdir, dir_name, new_parent)
            
            if result and result.get('cid'):
                new_cid = result['cid']
                created[original_cid] = new_cid
                logger.info(f"[√] 目录创建成功: {dir_name} (CID: {new_cid})")
            else:
                # 尝试查找已存在的目录
                ex = await find_existing_directory(client, new_parent, dir_name)
                if ex:
                    created[original_cid] = ex
                    logger.info(f"[i] 使用已存在目录: {dir_name} (CID: {ex})")
                else:
                    created[original_cid] = new_parent
                    logger.warning(f"[x] 目录创建失败 {dir_name}，使用父CID {new_parent}")
                    
        except Exception as e:
            logger.exception(f"[错误] 创建目录失败 {dir_name}:")
            created[original_cid] = new_parent
        
        await asyncio.sleep(0.1)
    
    return created


# ================================
# 文件转存
# ================================

async def save_files_to_cid(
    cookie_str: str,
    user_id: str,
    share_code: str,
    receive_code: str,
    file_ids: list[str],
    target_cid: str,
    batch_size: int = 50
) -> bool:
    """
    批量转存文件到指定目录。
    
    Args:
        cookie_str: 115 Cookie
        user_id: 用户 ID
        share_code: 分享码
        receive_code: 提取码
        file_ids: 文件 ID 列表
        target_cid: 目标目录 CID
        batch_size: 每批处理数量
        
    Returns:
        是否有任何文件转存成功
    """
    if not file_ids:
        logger.debug("[调试] 无文件可转存")
        return True
    
    url = "https://webapi.115.com/share/receive"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie_str,
    }
    
    ok = 0
    total_batches = (len(file_ids) + batch_size - 1) // batch_size
    
    for i in range(0, len(file_ids), batch_size):
        batch = file_ids[i:i+batch_size]
        payload = {
            "user_id": user_id,
            "share_code": share_code,
            "receive_code": receive_code,
            "file_id": ",".join(batch),
            "cid": target_cid,
        }
        
        batch_num = i // batch_size + 1
        logger.info(f"[调试] 批量转存 {batch_num}/{total_batches} — {len(batch)} 个文件 到 {target_cid}")
        
        try:
            resp = await run_blocking_io(
                requests.post, url, data=payload, headers=headers, timeout=30
            )
            data = resp.json()
            
            if data.get("state"):
                ok += len(batch)
                logger.debug("[√] 本批次成功")
            else:
                logger.warning(f"[x] 本批次失败: {data}")
                
        except Exception as e:
            logger.exception("[x] 请求异常:")
        
        await asyncio.sleep(0.5)
    
    logger.info(f"[总结] 转存成功 {ok}/{len(file_ids)}")
    return ok > 0


async def try_direct_directory_save(
    cookie_str: str,
    user_id: str,
    share_code: str,
    receive_code: str,
    dir_cid: str,
    target_cid: str
) -> bool:
    """
    尝试直接转存整个目录。
    
    Args:
        cookie_str: 115 Cookie
        user_id: 用户 ID
        share_code: 分享码
        receive_code: 提取码
        dir_cid: 源目录 CID
        target_cid: 目标目录 CID
        
    Returns:
        是否成功
    """
    logger.info(f"[尝试] 直接目录转存 CID={dir_cid}")
    
    url = "https://webapi.115.com/share/receive"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie_str,
    }
    payload = {
        "user_id": user_id,
        "share_code": share_code,
        "receive_code": receive_code,
        "file_id": dir_cid,
        "cid": target_cid,
    }
    
    try:
        resp = await run_blocking_io(
            requests.post, url, data=payload, headers=headers, timeout=30
        )
        result = resp.json()
        
        if result.get("state") or (result.get("errno") == 4200045 and "文件已接收" in result.get("error", "")):
            logger.info("[√] 直接目录转存成功或已存在")
            return True
            
        logger.warning(f"[x] 直接目录转存失败: {result}")
        return False
        
    except Exception as e:
        logger.exception("[x] 直接目录转存异常:")
        return False


# ================================
# 分享内容处理
# ================================

async def process_share_content(
    client: Any,
    cookie_str: str,
    share_code: str,
    receive_code: str,
    target_cid: str,
    max_retries: int = 3
) -> bool:
    """
    处理分享内容（文件和目录）。
    
    Args:
        client: P115Client 实例
        cookie_str: 115 Cookie
        share_code: 分享码
        receive_code: 提取码
        target_cid: 目标目录 CID
        max_retries: 最大重试次数
        
    Returns:
        是否成功
    """
    logger.info(f"[调试] 处理分享: {share_code} / {receive_code}")
    
    for attempt in range(max_retries):
        try:
            payload = {"share_code": share_code, "receive_code": receive_code}
            resp = await run_blocking_io(client.share_snap, payload)
            
            if not resp.get('state'):
                logger.warning(f"[调试] 分享快照失败: {resp.get('error')}. 尝试 {attempt + 1}/{max_retries}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(3)
                continue
            
            data_list = resp['data']['list']
            logger.info(f"[调试] 分享包含 {len(data_list)} 项")
            
            all_files: list[FileInfo] = []
            all_dirs: list[DirInfo] = []
            direct_files: list[str] = []
            direct_success = False
            
            for item in data_list:
                name = item.get('n', '未知')
                item_cid = item.get('cid')
                item_fid = item.get('fid')
                
                if is_directory_item(item):
                    logger.info(f"[发现] 目录: {name} (CID: {item_cid}) — 尝试直接转存")
                    
                    if await try_direct_directory_save(
                        cookie_str, client.user_id, share_code, receive_code,
                        item_cid, target_cid
                    ):
                        direct_success = True
                        continue
                    
                    logger.info("[备选] 递归获取目录内容...")
                    dir_info = DirInfo(name=name, cid=item_cid, parent_cid='root', depth=0, original_item=item)
                    all_dirs.append(dir_info)
                    
                    df, dd = await get_all_files_in_directory(
                        client, item_cid, depth=1, share_snap_data=data_list
                    )
                    all_files.extend(df)
                    all_dirs.extend(dd)
                else:
                    logger.info(f"[发现] 文件: {name} ({format_file_size(item.get('s', 0))})")
                    direct_files.append(item_fid)
            
            if direct_success:
                logger.info("[√] 目录直接转存已完成")
                return True
            
            total_files = len(all_files) + len(direct_files)
            logger.info(f"[统计] 文件: {total_files}，目录: {len(all_dirs)}")
            
            if total_files == 0:
                logger.warning("[提示] 未发现可转存文件")
                return False
            
            # 创建目录结构
            created_dirs: dict[str, str] = {}
            if all_dirs:
                logger.info("[阶段1] 创建目录结构 ...")
                created_dirs = await create_directory_structure(client, target_cid, all_dirs)
                logger.info("[√] 目录结构就绪")
            
            # 转存文件
            logger.info("[阶段2] 转存文件 ...")
            
            if direct_files:
                await save_files_to_cid(
                    cookie_str, client.user_id, share_code, receive_code,
                    direct_files, target_cid
                )
            
            if all_files:
                files_by_parent: dict[str, list[str]] = {}
                for fi in all_files:
                    files_by_parent.setdefault(fi.parent_cid, []).append(fi.fid)
                
                for parent_cid, fids in files_by_parent.items():
                    dst_cid = created_dirs.get(parent_cid, target_cid)
                    await save_files_to_cid(
                        cookie_str, client.user_id, share_code, receive_code,
                        fids, dst_cid
                    )
            
            logger.info("[√] 分享处理完成")
            return True
            
        except Exception as e:
            logger.exception(f"[异常] 处理失败，分享 {share_code} / {receive_code}:")
            if attempt < max_retries - 1:
                logger.warning("[调试] 等待3秒重试...")
                await asyncio.sleep(3)
    
    logger.error("[错误] 分享处理失败")
    return False


# ================================
# 离线下载
# ================================

async def add_offline_tasks(
    cookie_str: str,
    urls: list[str],
    target_cid: str
) -> dict:
    """
    向 115 发起离线下载任务。
    
    Args:
        cookie_str: 115 Cookie
        urls: 离线链接列表
        target_cid: 目标目录 CID
        
    Returns:
        {'ok': bool, 'results': [OfflineTaskResult, ...]}
    """
    results: list[dict] = []
    
    if not urls:
        return {"ok": True, "results": results}
    
    common_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://115.com",
        "Referer": "https://115.com/",
        "Cookie": cookie_str,
    }
    
    succ = 0
    
    for url in urls:
        try:
            # wp_path_id 指定离线下载目标目录
            data = {"url": url, "wp_path_id": target_cid}
            
            resp = await run_blocking_io(
                requests.post,
                "https://115.com/web/lixian/?ct=lixian&ac=add_task_url",
                data=data,
                headers=common_headers,
                timeout=30
            )
            
            try:
                j = resp.json()
            except Exception:
                j = {"text": resp.text[:500], "status_code": resp.status_code}
            
            success = _check_offline_success(j, resp.status_code)
            
            results.append({"url": url, "success": success, "resp": j})
            
            if success:
                succ += 1
                logger.info(f"离线任务提交成功: {url}")
            else:
                logger.warning(f"离线任务提交失败: {url}, 响应: {j}")
                
        except Exception as e:
            results.append({"url": url, "success": False, "resp": str(e)})
            logger.exception(f"离线任务提交异常: {url}")
        
        await asyncio.sleep(0.5)
    
    return {"ok": succ > 0, "results": results}


def _check_offline_success(response: dict, status_code: int) -> bool:
    """检查离线任务是否成功"""
    j = response
    resp_str = str(j)
    
    if j.get('state') is True:
        return True
    if j.get('errcode') == 0:
        return True
    if j.get('errno') == 0:
        return True
    if '已添加' in resp_str or '添加成功' in resp_str or '任务已存在' in resp_str:
        return True
    if '成功' in resp_str and ('添加' in resp_str or '任务' in resp_str):
        return True
    if status_code == 200 and ('任务' in resp_str or '添加' in resp_str or '成功' in resp_str):
        return True
    
    return False
