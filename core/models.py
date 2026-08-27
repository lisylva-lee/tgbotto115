#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/models.py - 数据模型定义

使用 dataclass 定义结构化数据类型，提供类型安全和代码可读性。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any


@dataclass
class ShareLink:
    """115 分享链接数据"""
    share_code: str
    receive_code: str
    original_text: str = ""
    
    def __hash__(self) -> int:
        return hash((self.share_code, self.receive_code))
    
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ShareLink):
            return False
        return self.share_code == other.share_code and self.receive_code == other.receive_code
    
    @property
    def key(self) -> str:
        """返回唯一键用于日志记录"""
        return f"{self.share_code}_{self.receive_code}"


@dataclass
class FileInfo:
    """文件信息"""
    name: str
    fid: str
    cid: Optional[str] = None
    size: int = 0
    parent_cid: str = ""
    depth: int = 0
    extension: str = ""
    original_item: dict = field(default_factory=dict)
    
    @classmethod
    def from_api_item(cls, item: dict, parent_cid: str, depth: int) -> "FileInfo":
        """从 API 响应项创建 FileInfo"""
        name = item.get('n', '未知')
        ext = name.split('.')[-1].upper() if '.' in name else 'Unknown'
        return cls(
            name=name,
            fid=item.get('fid', ''),
            cid=item.get('cid'),
            size=item.get('s', 0),
            parent_cid=parent_cid,
            depth=depth,
            extension=ext,
            original_item=item
        )


@dataclass
class DirInfo:
    """目录信息"""
    name: str
    cid: str
    parent_cid: str = ""
    depth: int = 0
    original_item: dict = field(default_factory=dict)
    
    @classmethod
    def from_api_item(cls, item: dict, parent_cid: str, depth: int) -> "DirInfo":
        """从 API 响应项创建 DirInfo"""
        return cls(
            name=item.get('n', '未知'),
            cid=item.get('cid', ''),
            parent_cid=parent_cid,
            depth=depth,
            original_item=item
        )


@dataclass
class TransferResult:
    """转存结果"""
    share_code: str
    receive_code: str
    success: bool
    timestamp: str = field(default_factory=lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    details: dict = field(default_factory=dict)
    error: Optional[str] = None
    
    @property
    def status(self) -> str:
        return 'success' if self.success else 'failed'
    
    def to_dict(self) -> dict:
        """转换为字典用于 JSON 序列化"""
        return {
            'share_code': self.share_code,
            'receive_code': self.receive_code,
            'status': self.status,
            'timestamp': self.timestamp,
            'details': self.details,
            'error': self.error
        }


@dataclass
class OfflineTaskResult:
    """离线任务结果"""
    url: str
    success: bool
    response: Any = None
    error: Optional[str] = None


@dataclass
class UserSession:
    """用户会话数据"""
    user_id: int
    share_links: list = field(default_factory=list)
    offline_links: list = field(default_factory=list)
    target_cid: Optional[str] = None
    cid_name: Optional[str] = None
    action_choice: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
