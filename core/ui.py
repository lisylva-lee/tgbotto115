#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/ui.py - Telegram 交互 UI 辅助函数

集中管理按钮键盘、卡片式文案和 callback_data 解析，避免 bot.py 文案继续膨胀。
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

CALLBACK_PREFIX_CID = "cid"
CALLBACK_PREFIX_SETDEF = "setdef"
CALLBACK_PREFIX_DIR_CONFIRM = "dirconfirm"
CALLBACK_PREFIX_DEL = "del"


def build_start_message() -> str:
    """构建卡片式欢迎/帮助消息。"""
    return (
        "🎬 115 ShareBot\n\n"
        "把 115 分享链接、magnet、ed2k 发给我，我会帮你自动处理：\n"
        "📥 转存 115 分享到指定目录\n"
        "☁️ 提交 magnet / ed2k 离线下载任务\n"
        "📁 分享目录和离线目录可以分开设置\n\n"
        "💡 小技巧\n"
        "• 可以连续转发多条消息，我会等待几秒后自动合并处理\n"
        "• 目录选择支持按钮，也保留数字回复作为备用\n"
        "• 超时未选择时，会自动使用你的默认目录\n\n"
        "📋 常用命令\n"
        "/set_share_cid — 设置分享默认目录\n"
        "/set_offline_cid — 设置离线默认目录\n"
        "/add_cid — 添加新目录\n"
        "/del_cid — 删除目录\n"
        "/current_cid — 查看当前设置\n"
        "/list_cid — 列出可用目录\n"
        "/reset_cid — 重置为默认目录"
    )


def _kind_label(kind: str) -> str:
    if kind == "share":
        return "分享"
    if kind == "offline":
        return "离线"
    if kind == "dir":
        return "目录"
    raise ValueError(f"unknown cid keyboard kind: {kind}")


def build_cid_keyboard(cid_list: list[tuple[str, str]], kind: str) -> InlineKeyboardMarkup:
    """构建目录选择按钮。

    callback_data 使用索引而不是 CID，避免 callback_data 过长，并复用 session 中的 cid_list。
    """
    label = _kind_label(kind)
    rows = []
    row = []
    for index, (name, _cid) in enumerate(cid_list):
        row.append(InlineKeyboardButton(f"📁 {name}", callback_data=f"{CALLBACK_PREFIX_CID}:{kind}:{index}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton(f"✅ 使用默认{label}目录", callback_data=f"{CALLBACK_PREFIX_CID}:{kind}:default")
    ])
    return InlineKeyboardMarkup(rows)


def build_set_default_keyboard(cid_list: list[tuple[str, str]], kind: str) -> InlineKeyboardMarkup:
    """构建「设置默认目录」按钮：已有目录 + 输入新目录/路径。"""
    _kind_label(kind)
    rows = []
    row = []
    for index, (name, _cid) in enumerate(cid_list):
        row.append(InlineKeyboardButton(f"📁 {name}", callback_data=f"{CALLBACK_PREFIX_SETDEF}:{kind}:{index}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("✏️ 输入新目录或网盘路径", callback_data=f"{CALLBACK_PREFIX_SETDEF}:{kind}:new")
    ])
    return InlineKeyboardMarkup(rows)


def build_dir_confirm_keyboard() -> InlineKeyboardMarkup:
    """添加目录 / 设为默认前的确认按钮。"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 确认", callback_data=f"{CALLBACK_PREFIX_DIR_CONFIRM}:ok"),
            InlineKeyboardButton("❌ 取消", callback_data=f"{CALLBACK_PREFIX_DIR_CONFIRM}:cancel"),
        ]
    ])


def build_delete_keyboard(cid_list: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """构建删除目录按钮（每行一个 + 取消）。"""
    rows = []
    row = []
    for index, (name, _cid) in enumerate(cid_list):
        row.append(InlineKeyboardButton(f"🗑️ {name}", callback_data=f"{CALLBACK_PREFIX_DEL}:{index}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ 取消", callback_data=f"{CALLBACK_PREFIX_DEL}:cancel")])
    return InlineKeyboardMarkup(rows)


def parse_del_callback_data(data: str | None) -> str | None:
    """解析删除目录 callback_data -> 索引字符串 | 'cancel' | None。"""
    if not data:
        return None
    parts = data.split(":", 1)
    if len(parts) != 2 or parts[0] != CALLBACK_PREFIX_DEL:
        return None
    if parts[1] != "cancel" and not parts[1].isdigit():
        return None
    return parts[1]


def build_dir_input_prompt(kind: str) -> str:
    """构建目录录入提示（CID 或路径）。"""
    label = _kind_label(kind)
    return (
        f"📁 正在添加【{label}】目录\n\n"
        "请发送以下任一内容：\n"
        "1️⃣ 网盘目录 CID（我会自动查询对应的文件夹名称）\n"
        "2️⃣ 网盘路径，如 /电影/动作（不存在会自动创建）\n\n"
        "也可以发送 /cancel 取消。"
    )


def parse_cid_callback_data(data: str | None) -> tuple[str, str] | None:
    """解析目录选择 callback_data。"""
    if not data:
        return None
    parts = data.split(":", 2)
    if len(parts) != 3:
        return None
    prefix, kind, value = parts
    if prefix != CALLBACK_PREFIX_CID or kind not in {"share", "offline"}:
        return None
    if value != "default" and not value.isdigit():
        return None
    return kind, value


def parse_setdef_callback_data(data: str | None) -> tuple[str, str] | None:
    """解析「设置默认目录」callback_data -> (kind, value)。value 为索引或 'new'。"""
    if not data:
        return None
    parts = data.split(":", 2)
    if len(parts) != 3:
        return None
    prefix, kind, value = parts
    if prefix != CALLBACK_PREFIX_SETDEF or kind not in {"share", "offline"}:
        return None
    if value != "new" and not value.isdigit():
        return None
    return kind, value


def parse_dir_confirm_callback_data(data: str | None) -> str | None:
    """解析目录确认 callback_data -> 'ok' | 'cancel' | None。"""
    if not data:
        return None
    parts = data.split(":", 1)
    if len(parts) != 2 or parts[0] != CALLBACK_PREFIX_DIR_CONFIRM:
        return None
    if parts[1] not in {"ok", "cancel"}:
        return None
    return parts[1]


def build_link_detected_message(kind: str, share_count: int, offline_count: int, timeout: int) -> str:
    """构建检测到链接后的目录选择提示。"""
    _kind_label(kind)  # validate kind
    lines = []
    if kind == "share":
        lines.append(f"📥 检测到 {share_count} 个 115 分享链接")
        if offline_count:
            lines.append(f"☁️ 同时检测到 {offline_count} 个离线链接")
        lines.append("\n请选择分享链接保存目录：")
    else:
        lines.append(f"☁️ 检测到 {offline_count} 个离线链接")
        lines.append("\n请选择离线下载保存目录：")
    lines.append(f"\n⏱️ {timeout} 秒内未选择，将自动使用默认目录")
    lines.append("💡 也可以直接回复序号作为备用")
    return "\n".join(lines)


def build_share_progress_message(processed: int, total: int, cid_name: str) -> str:
    """构建分享转存进度卡片。"""
    if total <= 0:
        percent = 100
    else:
        percent = round(processed / total * 100)
    done = processed >= total and total > 0
    status = "✅ 已完成" if done else "📦 分享转存中"
    return (
        f"{status}\n\n"
        f"⏳ {processed}/{total}\n"
        f"📈 进度：{percent}%\n"
        f"📁 目录：{cid_name}"
    )


def _render_failed_items(failed_items: list[str] | None) -> str:
    """渲染失败详情列表（最多 10 条，避免刷屏）。"""
    if not failed_items:
        return ""
    lines = ["", "❌ 失败详情："]
    shown = failed_items[:10]
    for item in shown:
        lines.append(f"• {item}")
    if len(failed_items) > 10:
        lines.append(f"… 等共 {len(failed_items)} 项")
    return "\n".join(lines)


def build_share_result_message(success: int, failed: int, cid_name: str, failed_items: list[str] | None = None) -> str:
    """构建分享转存结果摘要。"""
    return (
        "📦 分享转存完成\n\n"
        f"✅ 成功：{success}\n"
        f"❌ 失败：{failed}\n"
        f"📁 保存目录：{cid_name}"
        f"{_render_failed_items(failed_items)}"
    )


def build_offline_result_message(success: int, failed: int, cid_name: str, failed_items: list[str] | None = None) -> str:
    """构建离线任务提交结果摘要。"""
    return (
        "☁️ 离线任务提交完成\n\n"
        f"✅ 成功：{success}\n"
        f"❌ 失败：{failed}\n"
        f"📁 保存目录：{cid_name}"
        f"{_render_failed_items(failed_items)}"
    )
