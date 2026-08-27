#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/__init__.py - Core 模块包初始化

导出公共 API 供 bot.py 等使用。
"""

from .models import (
    ShareLink,
    FileInfo,
    DirInfo,
    TransferResult,
    OfflineTaskResult,
    UserSession,
)

from .utils import (
    format_file_size,
    load_text_file,
    load_json_file,
    save_json_file,
    append_to_file,
    parse_115_links_from_text,
    extract_offline_links,
    fetch_links_from_page,
    normalize_page_url,
    extract_links_from_reply_markup,
    extract_cid_from_text,
    is_directory_item,
    RE_115_URL,
    RE_MAGNET,
    RE_ED2K,
    RE_TELEGRAPH,
    RE_TELEGRAPH_LOOSE,
)

from .client import (
    P115ClientWrapper,
    get_p115_client,
    run_blocking_io,
    get_dir_name_by_cid,
    resolve_cid_by_path,
    ensure_dir_by_path,
)

from .ratelimit import RateLimiter, get_rate_limiter, reset_rate_limiter

from .db import ShareDB

from .transfer import (
    get_all_files_in_directory,
    find_existing_directory,
    create_directory_structure,
    save_files_to_cid,
    try_direct_directory_save,
    process_share_content,
    add_offline_tasks,
)


__all__ = [
    # Models
    'ShareLink',
    'FileInfo',
    'DirInfo',
    'TransferResult',
    'OfflineTaskResult',
    'UserSession',
    # Utils
    'format_file_size',
    'load_text_file',
    'load_json_file',
    'save_json_file',
    'append_to_file',
    'parse_115_links_from_text',
    'extract_offline_links',
    'fetch_links_from_page',
    'normalize_page_url',
    'extract_links_from_reply_markup',
    'extract_cid_from_text',
    'is_directory_item',
    'RE_115_URL',
    'RE_MAGNET',
    'RE_ED2K',
    'RE_TELEGRAPH',
    'RE_TELEGRAPH_LOOSE',
    # Client
    'P115ClientWrapper',
    'get_p115_client',
    'run_blocking_io',
    'get_dir_name_by_cid',
    'resolve_cid_by_path',
    'ensure_dir_by_path',
    # RateLimit
    'RateLimiter',
    'get_rate_limiter',
    'reset_rate_limiter',
    # DB
    'ShareDB',
    # Transfer
    'get_all_files_in_directory',
    'find_existing_directory',
    'create_directory_structure',
    'save_files_to_cid',
    'try_direct_directory_save',
    'process_share_content',
    'add_offline_tasks',
]