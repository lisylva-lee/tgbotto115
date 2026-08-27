# -*- coding: utf-8 -*-
"""
tests/test_link_extraction.py - 消息链接提取 + 页面按钮优先规则测试

覆盖：
- extract_all_links_from_message 提取 share / offline / pages
- 「按钮优先」规则：内联按钮中的页面链接为正式入口（如"查看资源"），
  存在按钮页面时忽略正文中散落的补充页面（修复 15 链接多检测问题）
- 无按钮时回退到正文 telegra.ph 链接
- 按钮中的分享/离线链接不受页面取舍影响

说明：该函数位于 bot.py，而 bot.py 模块级会初始化 SQLite（可能有锁），
因此本测试用 importlib 提取函数源码到独立命名空间执行，避免副作用。
"""

import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MessageEntity,
)

import core

PROJ = Path(__file__).resolve().parent.parent


def _load_extract_fn():
    """从 bot.py 源码中提取 extract_all_links_from_message 函数并注入依赖执行。"""
    source = (PROJ / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "extract_all_links_from_message":
            func_node = node
            break
    assert func_node is not None, "bot.py 中未找到 extract_all_links_from_message"

    ns = {
        "normalize_page_url": core.normalize_page_url,
        "extract_links_from_reply_markup": core.extract_links_from_reply_markup,
        "RE_115_URL": core.RE_115_URL,
        "RE_TELEGRAPH": core.RE_TELEGRAPH,
        "RE_TELEGRAPH_LOOSE": core.RE_TELEGRAPH_LOOSE,
        "RE_MAGNET": core.RE_MAGNET,
        "RE_ED2K": core.RE_ED2K,
        "MessageEntity": MessageEntity,
        "logger": logging.getLogger("test_link_extraction"),
    }
    exec(compile(ast.Module([func_node], type_ignores=[]), "bot.py", "exec"), ns)
    return ns["extract_all_links_from_message"]


extract_all_links_from_message = _load_extract_fn()


def make_msg(text="", entities=None, reply_markup=None):
    return SimpleNamespace(
        text=text,
        caption=None,
        entities=entities or [],
        caption_entities=[],
        reply_markup=reply_markup,
    )


def test_button_pages_priority_over_text_pages():
    """有按钮页面时，忽略正文中散落的补充页面（修复多检测）。"""
    rm = InlineKeyboardMarkup([
        [InlineKeyboardButton(text="📎 查看资源", url="https://telegra.ph/蜘蛛侠系列-05-26")],
    ])
    msg = make_msg(
        text="蜘蛛侠 https://telegra.ph/蜘蛛侠2-2004-05-05 https://telegra.ph/蜘蛛侠3-2007-05-03",
        reply_markup=rm,
    )
    r = extract_all_links_from_message(msg)
    assert r["pages"] == ["https://telegra.ph/蜘蛛侠系列-05-26"]


def test_no_button_falls_back_to_text_pages():
    """无按钮时，正文中的 telegra.ph 链接仍被采集。"""
    msg = make_msg(text="看看 https://telegra.ph/蜘蛛侠系列-05-26 还有 https://telegra.ph/蜘蛛侠2-2004-05-05")
    r = extract_all_links_from_message(msg)
    assert len(r["pages"]) == 2
    assert "https://telegra.ph/蜘蛛侠系列-05-26" in r["pages"]
    assert "https://telegra.ph/蜘蛛侠2-2004-05-05" in r["pages"]


def test_button_and_text_same_page_dedup():
    """按钮与正文指向同一页面时不重复。"""
    rm = InlineKeyboardMarkup([
        [InlineKeyboardButton(text="查看资源", url="https://telegra.ph/蜘蛛侠系列-05-26")],
    ])
    msg = make_msg(text="资源：https://telegra.ph/蜘蛛侠系列-05-26", reply_markup=rm)
    r = extract_all_links_from_message(msg)
    assert r["pages"] == ["https://telegra.ph/蜘蛛侠系列-05-26"]


def test_button_share_offline_unaffected_by_page_priority():
    """按钮中的 115 分享 / ed2k 链接不受页面取舍影响。"""
    rm = InlineKeyboardMarkup([
        [InlineKeyboardButton(text="115", url="https://115.com/s/abc123?password=8888")],
        [InlineKeyboardButton(text="ed2k", url="ed2k://|file|test.mkv|1|ABCD|/")],
        [InlineKeyboardButton(text="页面", url="https://telegra.ph/Page-01")],
    ])
    msg = make_msg(text="测试 https://telegra.ph/其他-01", reply_markup=rm)
    r = extract_all_links_from_message(msg)
    assert len(r["share"]) == 1 and r["share"][0]["share_code"] == "abc123"
    assert len(r["offline"]) == 1
    # 页面只取按钮中的
    assert r["pages"] == ["https://telegra.ph/Page-01"]


def test_no_links_returns_empty():
    msg = make_msg(text="你好，今天天气不错")
    r = extract_all_links_from_message(msg)
    assert r == {"share": [], "offline": [], "pages": []}


def test_share_from_plain_text_still_extracted():
    """纯文本里的 115 分享链接不受影响。"""
    msg = make_msg(text="分享：https://115.com/s/abc123?password=8888")
    r = extract_all_links_from_message(msg)
    assert len(r["share"]) == 1
    assert r["share"][0]["share_code"] == "abc123"