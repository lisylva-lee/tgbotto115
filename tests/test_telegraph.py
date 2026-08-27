# -*- coding: utf-8 -*-
"""
tests/test_telegraph.py - telegra.ph 页面链接解析测试

覆盖：
- RE_TELEGRAPH 正则
- fetch_links_from_page 从 HTML（a href / og:description / article 正文）提取
  115 分享 / magnet / ed2k 链接
- 异常与 HTTP 错误时的空结果兜底
- 真实页面抓取验证（网络不可达时自动跳过）
"""

import pytest

from core import (
    RE_TELEGRAPH,
    RE_TELEGRAPH_LOOSE,
    fetch_links_from_page,
    normalize_page_url,
    extract_links_from_reply_markup,
)

# 构造一个 Telegraph 风格页面：ed2k 同时出现在 og:description 与 article 正文
SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head>
<meta property="og:description" content="📀 蜘蛛侠（系列） 115网盘资源分享频道
ed2k://|file|Spider-Man.2002.BluRay.mkv|31985082849|AD792911475CB72433D1BBF3E699B0FB|/
ed2k://|file|The.Amazing.Spider-Man.2.mkv|41086487622|E7D4EED4675D76E68CAC88518C39B8C1|/">
</head>
<body>
<article>
<p>蜘蛛侠（系列）</p>
<p><a href="ed2k://|file|Spider-Man.No.Way.Home.mkv|20385319666|8816FA5E102DEBE9BCE1A667F8275823|/">ed2k</a></p>
<p>115 分享：<a href="https://115.com/s/abc123xyz456?password=8888">115.com/s/abc123xyz456</a></p>
<p>磁力：<a href="magnet:?xt=urn:btih:ABCDEF1234567890ABCDEF1234567890">magnet</a></p>
<p>正文纯文本磁力 magnet:?xt=urn:btih:ZZZZZ9999999999</p>
</article>
</body>
</html>
"""


# ================================
# RE_TELEGRAPH
# ================================

def test_re_telegraph_matches_page_url():
    assert RE_TELEGRAPH.match("https://telegra.ph/蜘蛛侠系列-05-26")
    assert RE_TELEGRAPH.match("https://telegra.ph/Example-Page-01-01")
    assert not RE_TELEGRAPH.match("https://example.com/page")


def test_re_telegraph_findall_in_text():
    text = "看看这个 https://telegra.ph/资源页-08-27 里面有东西"
    m = RE_TELEGRAPH.findall(text)
    assert m == ["https://telegra.ph/资源页-08-27"]


def test_re_telegraph_loose_matches_no_protocol():
    """转发/纯文本常见：不带 https:// 前缀也能识别。"""
    assert RE_TELEGRAPH_LOOSE.match("telegra.ph/蜘蛛侠系列-05-26")
    assert RE_TELEGRAPH_LOOSE.match("www.telegra.ph/Page-01")
    assert RE_TELEGRAPH_LOOSE.match("https://telegra.ph/Page-01")
    # 严格版不匹配无协议
    assert not RE_TELEGRAPH.match("telegra.ph/蜘蛛侠系列-05-26")


def test_normalize_page_url_adds_protocol_and_cleans():
    assert normalize_page_url("telegra.ph/蜘蛛侠系列-05-26") == "https://telegra.ph/蜘蛛侠系列-05-26"
    # 尾部标点清理
    assert normalize_page_url("https://telegra.ph/Page-01，") == "https://telegra.ph/Page-01"
    # 已是完整协议则不变
    assert normalize_page_url("https://telegra.ph/Page-01") == "https://telegra.ph/Page-01"
    # 空 / 空白 / 全标点 → 空串
    assert normalize_page_url("") == ""
    assert normalize_page_url("   ") == ""
    assert normalize_page_url("，。") == ""


# ================================
# extract_links_from_reply_markup（内联按钮中的链接）
# ================================

def test_extract_links_from_reply_markup_extracts_button_links():
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton

    rm = InlineKeyboardMarkup([
        [InlineKeyboardButton(text="📄 详情", url="https://telegra.ph/蜘蛛侠系列-05-26")],
        [InlineKeyboardButton(text="115网盘", url="https://115.com/s/abc123xyz456?password=8888")],
        [InlineKeyboardButton(text="ed2k 下载", url="ed2k://|file|A.mkv|1|ABCDEF|/")],
        [InlineKeyboardButton(text="telegra.ph/无协议页-01", callback_data="x")],
    ])
    r = extract_links_from_reply_markup(rm)

    # url 字段完整协议 + text 字段无协议归一化，共 2 个页面
    assert "https://telegra.ph/蜘蛛侠系列-05-26" in r["pages"]
    assert "https://telegra.ph/无协议页-01" in r["pages"]
    assert len(r["pages"]) == 2
    assert r["share"][0]["share_code"] == "abc123xyz456"
    assert r["share"][0]["receive_code"] == "8888"
    assert any("A.mkv" in u for u in r["offline"])


def test_extract_links_from_reply_markup_none_returns_empty():
    assert extract_links_from_reply_markup(None) == {"share": [], "offline": [], "pages": []}


# ================================
# fetch_links_from_page
# ================================

def test_fetch_links_from_page_extracts_all_kinds(monkeypatch):
    class FakeResp:
        status_code = 200
        text = SAMPLE_HTML

    monkeypatch.setattr("core.utils.requests.get", lambda *a, **k: FakeResp())

    result = fetch_links_from_page("https://telegra.ph/蜘蛛侠系列-05-26")

    # 共 3 个 ed2k + 2 个 magnet（去重后）
    assert len(result["offline"]) == 5
    ed2ks = [u for u in result["offline"] if u.startswith("ed2k://")]
    assert len(ed2ks) == 3
    assert any(u.startswith("ed2k://|file|Spider-Man.2002") for u in ed2ks)
    assert any(u.startswith("ed2k://|file|The.Amazing.Spider-Man.2") for u in ed2ks)
    assert any(u.startswith("ed2k://|file|Spider-Man.No.Way.Home") for u in ed2ks)
    # 磁力：a href 1 个 + 正文纯文本 1 个
    assert any("ABCDEF1234567890ABCDEF1234567890" in u for u in result["offline"])
    assert any("ZZZZZ9999999999" in u for u in result["offline"])
    # 115 分享链接
    assert result["share"] == [
        {"share_code": "abc123xyz456", "receive_code": "8888",
         "original_text": "https://115.com/s/abc123xyz456?password=8888"}
    ]


def test_fetch_links_from_page_dedup_on_duplicate(monkeypatch):
    html = """
    <html><head><meta property="og:description" content="ed2k://|file|A.mkv|1|ABCDEF|/"></head>
    <body><article><a href="ed2k://|file|A.mkv|1|ABCDEF|/">A</a>
    <a href="ed2k://|file|A.mkv|1|ABCDEF|/">A again</a></article></body></html>
    """

    class FakeResp:
        status_code = 200
        text = html

    monkeypatch.setattr("core.utils.requests.get", lambda *a, **k: FakeResp())
    result = fetch_links_from_page("https://telegra.ph/dup")
    assert len(result["offline"]) == 1


def test_fetch_links_from_page_http_error_returns_empty(monkeypatch):
    class FakeResp:
        status_code = 404
        text = "not found"

    monkeypatch.setattr("core.utils.requests.get", lambda *a, **k: FakeResp())
    result = fetch_links_from_page("https://telegra.ph/missing")
    assert result == {"share": [], "offline": []}


def test_fetch_links_from_page_network_error_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise Exception("connection refused")

    monkeypatch.setattr("core.utils.requests.get", boom)
    result = fetch_links_from_page("https://telegra.ph/boom")
    assert result == {"share": [], "offline": []}


# ================================
# 真实页面验证（网络不可达时跳过）
# ================================

def test_real_telegraph_page_spiderman():
    """实际抓取蜘蛛侠页面，应解析出多个 ed2k 链接。"""
    try:
        result = fetch_links_from_page("https://telegra.ph/蜘蛛侠系列-05-26", timeout=15)
    except Exception:
        pytest.skip("网络不可达，跳过真实页面测试")

    assert len(result["offline"]) >= 3, f"预期至少 3 个 ed2k，实际 {len(result['offline'])}"
    assert all(u.startswith("ed2k://") for u in result["offline"])