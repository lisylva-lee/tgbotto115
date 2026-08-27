#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/utils.py - 共享工具函数

包含格式化、文件操作、正则表达式等通用工具。
"""

import json
import re
import logging
from html import unescape as html_unescape
from pathlib import Path
from typing import Optional, Any

import requests

logger = logging.getLogger(__name__)


# ================================
# 预编译正则表达式
# ================================

# 115 分享链接：严格提取 share_code 与 password
RE_115_URL = re.compile(
    r"https?://(?:115cdn\.com|115\.com)/s/([a-zA-Z0-9]+).*?(?:[?&]password=([a-zA-Z0-9]+))?",
    re.IGNORECASE
)

# 磁力链接
RE_MAGNET = re.compile(r"magnet:\?xt=[^\s]+", re.IGNORECASE)

# 电驴链接
RE_ED2K = re.compile(r"ed2k://[^\s]+", re.IGNORECASE)

# Telegraph 等资源聚合页面链接
RE_TELEGRAPH = re.compile(r"https?://telegra\.ph/[^\s<>'\"]+", re.IGNORECASE)

# Telegraph 宽松版（允许无 https:// 前缀，用于转发/纯文本扫描；www 可选）
RE_TELEGRAPH_LOOSE = re.compile(
    r"(?:https?://)?(?:www\.)?telegra\.ph/[^\s<>'\"]+", re.IGNORECASE
)

# 简化格式链接 (code password)
RE_SIMPLE_LINK = re.compile(r'([a-zA-Z0-9]{10,15})\s+([a-zA-Z0-9]{3,10})')

# CID 格式
RE_CID = re.compile(r'(\d{15,25})')


# ================================
# 文件大小格式化
# ================================

def format_file_size(size_bytes: int | float | None) -> str:
    """
    将字节数格式化为人类可读的文件大小。
    
    Args:
        size_bytes: 文件大小（字节）
        
    Returns:
        格式化的文件大小字符串，如 "1.5GB"
    """
    if not size_bytes:
        return "0B"
    
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    
    while size >= 1024 and i < len(size_names) - 1:
        size /= 1024.0
        i += 1
    
    return f"{size:.1f}{size_names[i]}"


# ================================
# 文件 I/O 操作
# ================================

def load_text_file(path: Path) -> str:
    """
    同步读取文本文件内容。
    
    Args:
        path: 文件路径
        
    Returns:
        文件内容字符串，失败返回空字符串
    """
    try:
        if path.exists():
            return path.read_text(encoding='utf-8').strip()
    except Exception as e:
        logger.error(f"读取文件失败 {path}: {e}")
    return ""


def load_json_file(path: Path) -> dict:
    """
    同步读取 JSON 文件。
    
    Args:
        path: JSON 文件路径
        
    Returns:
        解析后的字典，失败返回空字典
    """
    try:
        if path.exists():
            content = path.read_text(encoding='utf-8')
            if content:
                return json.loads(content)
    except json.JSONDecodeError as e:
        logger.error(f"JSON 解析错误 {path}: {e}")
    except Exception as e:
        logger.error(f"读取 JSON 文件失败 {path}: {e}")
    return {}


def save_json_file(path: Path, data: dict, indent: int = 2) -> bool:
    """
    同步保存 JSON 文件。
    
    Args:
        path: 目标文件路径
        data: 要保存的数据
        indent: JSON 缩进
        
    Returns:
        是否保存成功
    """
    try:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=indent),
            encoding='utf-8'
        )
        return True
    except Exception as e:
        logger.error(f"保存 JSON 文件失败 {path}: {e}")
        return False


def append_to_file(path: Path, content: str) -> bool:
    """
    追加内容到文件。
    
    Args:
        path: 文件路径
        content: 要追加的内容
        
    Returns:
        是否成功
    """
    try:
        with path.open('a', encoding='utf-8') as f:
            f.write(content)
        return True
    except Exception as e:
        logger.error(f"追加文件失败 {path}: {e}")
        return False


# ================================
# 链接解析
# ================================

def parse_115_links_from_text(content: str) -> list[dict]:
    """
    从文本内容解析 115 分享链接。
    
    Args:
        content: 包含链接的文本
        
    Returns:
        解析到的链接列表 [{'share_code', 'receive_code', 'original_text'}, ...]
    """
    links = []
    seen = set()
    
    def add_link(share_code: str, receive_code: str, original_text: str):
        key = (share_code, receive_code)
        if key not in seen and share_code:
            seen.add(key)
            links.append({
                'share_code': share_code,
                'receive_code': receive_code or '',
                'original_text': original_text
            })
    
    # 方法1：匹配完整 URL 格式
    for match in RE_115_URL.finditer(content):
        add_link(match.group(1), match.group(2), match.group(0))
    
    # 方法2：匹配简化格式 (code password)
    for match in RE_SIMPLE_LINK.finditer(content):
        add_link(match.group(1), match.group(2), f'{match.group(1)} {match.group(2)}')
    
    logger.debug(f"从文本中解析到 {len(links)} 个链接")
    return links


def extract_offline_links(content: str) -> list[str]:
    """
    从文本提取离线下载链接（magnet/ed2k）。
    
    Args:
        content: 包含链接的文本
        
    Returns:
        去重后的离线链接列表
    """
    links = []
    seen = set()
    
    for link in RE_MAGNET.findall(content):
        if link not in seen:
            seen.add(link)
            links.append(link)
    
    for link in RE_ED2K.findall(content):
        if link not in seen:
            seen.add(link)
            links.append(link)

    return links


def fetch_links_from_page(url: str, timeout: int = 20) -> dict:
    """
    抓取网页内容并提取其中的 115 分享 / magnet / ed2k 链接。

    适用于 telegra.ph 等资源聚合页面：正文里通常包含多条 ed2k、
    磁力或 115 分享链接（可能以 <a href>、纯文本或 og:description 形式存在）。

    提取来源（三路兜底，任意命中即可）：
      1. 所有 <a href="..."> 的 href 值（Telegraph 会把 URL 自动转成超链接）
      2. <meta property="og:description"> 摘要（频道常把全部 ed2k 写在摘要里）
      3. <article> 正文纯文本（og:description 被截断时的兜底）

    Args:
        url: 页面 URL
        timeout: 请求超时（秒）

    Returns:
        {'share': [{'share_code','receive_code','original_text'}, ...], 'offline': [url, ...]}
    """
    result = {"share": [], "offline": []}
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            logger.warning(f"抓取页面返回 {resp.status_code}: {url}")
            return result
        html = resp.text

        texts: list[str] = []

        # 1) 所有 <a href="...">
        for m in re.finditer(r'<a\s+[^>]*href=["\']([^"\']*)["\']', html, re.IGNORECASE):
            href = html_unescape(m.group(1)).strip()
            if href and not href.startswith(('#', 'javascript:', 'mailto:')):
                texts.append(href)

        # 2) og:description
        m = re.search(
            r'<meta[^>]+property=["\']og:description["\'][^>]*content=["\']([^"\']*)["\']',
            html, re.IGNORECASE
        )
        if m:
            texts.append(html_unescape(m.group(1)))

        # 3) article 正文纯文本
        m = re.search(r'<article[^>]*>(.*?)</article>', html, re.IGNORECASE | re.DOTALL)
        if m:
            body = re.sub(r'<[^>]+>', ' ', m.group(1))
            texts.append(html_unescape(body))

        content = "\n".join(texts)

        # 仅匹配完整 URL 格式（避免简化格式 code password 误报）
        seen = set()
        for m in RE_115_URL.finditer(content):
            key = (m.group(1), m.group(2) or '')
            if key not in seen:
                seen.add(key)
                result["share"].append({
                    "share_code": m.group(1),
                    "receive_code": m.group(2) or '',
                    "original_text": m.group(0),
                })

        result["offline"] = extract_offline_links(content)

        logger.info(
            f"页面 {url} 解析到分享 {len(result['share'])} 个，离线 {len(result['offline'])} 个"
        )
    except Exception as e:
        logger.error(f"抓取页面失败 {url}: {e}")
    return result


def normalize_page_url(url: str) -> str:
    """
    清理并补全页面 URL：去除首尾空白/尾部标点，缺失协议前缀时补 https://。

    转发/纯文本里的 telegra.ph 链接常不带协议前缀，Telegram 也不一定生成
    URL 实体；归一化后统一为 https://telegra.ph/... 便于后续抓取。

    Args:
        url: 原始页面 URL 文本

    Returns:
        归一化后的 URL；为空或清洗后为空返回 ""。
    """
    cleaned = (url or "").strip().rstrip('.,;:!?)]}\'"，。；：！？）》】')
    if not cleaned:
        return ""
    if not cleaned.startswith(("http://", "https://")):
        cleaned = "https://" + cleaned
    return cleaned


def extract_links_from_reply_markup(reply_markup) -> dict:
    """
    从 Telegram 内联键盘按钮中提取 115 分享 / magnet / ed2k / telegra.ph 链接。

    频道/资源 bot 常把 telegra.ph 链接做成内联按钮（url 或 text 字段），
    转发后纯文本扫描读不到，必须遍历 reply_markup.inline_keyboard 的每个
    按钮的 url / text / callback_data。

    Args:
        reply_markup: Telegram 的 InlineKeyboardMarkup 对象（可为 None）

    Returns:
        {'share': [...], 'offline': [...], 'pages': [归一化后的 telegra.ph URL, ...]}
    """
    result = {"share": [], "offline": [], "pages": []}
    if reply_markup is None:
        return result

    seen_share = set()
    for row in getattr(reply_markup, "inline_keyboard", None) or []:
        for btn in row:
            for val in (getattr(btn, "url", None), getattr(btn, "text", None),
                        getattr(btn, "callback_data", None)):
                if not val:
                    continue
                m = RE_115_URL.search(val)
                if m:
                    key = (m.group(1), m.group(2) or '')
                    if key not in seen_share:
                        seen_share.add(key)
                        result["share"].append({
                            "share_code": m.group(1),
                            "receive_code": m.group(2) or '',
                            "original_text": val,
                        })
                for pm in RE_TELEGRAPH_LOOSE.finditer(val):
                    norm = normalize_page_url(pm.group(0))
                    if norm:
                        result["pages"].append(norm)
                for om in RE_ED2K.findall(val):
                    result["offline"].append(om)
                for mm in RE_MAGNET.findall(val):
                    result["offline"].append(mm)

    result["offline"] = list(dict.fromkeys(result["offline"]))
    result["pages"] = list(dict.fromkeys(result["pages"]))
    return result


def extract_cid_from_text(content: str) -> Optional[str]:
    """
    从文本提取 CID。
    
    Args:
        content: 包含 CID 的文本
        
    Returns:
        提取到的 CID 或 None
    """
    match = RE_CID.search(content)
    return match.group(1) if match else None


# ================================
# 判断辅助函数
# ================================

def is_directory_item(item: dict) -> bool:
    """
    判断 API 返回的 item 是否为目录。
    
    Args:
        item: API 返回的文件/目录项
        
    Returns:
        是否为目录
    """
    ico = item.get('ico', '')
    fid = item.get('fid')
    cid = item.get('cid')
    
    # ico 为 folder，或者没有 fid 但有 cid
    return ico == 'folder' or (not bool(fid) and bool(cid))
