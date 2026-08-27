#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot.py (重构版：config.yaml + SQLite share.db + 目录 UI 交互)

功能：
- Telegram Bot，处理 115 分享链接转存 / magnet / ed2k 离线下载
- 分享与离线使用独立默认目录，支持目录 UI 交互
- 配置统一在 config.yaml（telegram.bot_token / p115.cookie / runtime）
- 数据（目录映射、用户目录、默认目录、链接、转存日志）统一存本地 share.db

目录 UI 交互：
- /add_cid : 录入 CID（自动查网盘文件夹名）或路径（不存在自动创建），确认后入库
- /set_share_cid / /set_offline_cid : 按钮选择已有目录，或输入新目录/路径设为默认
- /del_cid : 按钮选择删除目录
"""

import os
import sys
import logging
import asyncio
from datetime import datetime

# Telegram 代理支持（读取环境变量 HTTP_PROXY/HTTPS_PROXY，仅用于 Telegram Bot API）
try:
    import httpx
    _TELEGRAM_PROXY = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or ""
    if _TELEGRAM_PROXY:
        _TELEGRAM_PROXY = _TELEGRAM_PROXY.strip()
except ImportError:
    _TELEGRAM_PROXY = ""

from telegram import Update, MessageEntity, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# TTL 缓存用于 session 管理
try:
    from cachetools import TTLCache
except ImportError:
    TTLCache = None

# 配置（config.yaml 驱动）
from config import (
    BOT_TOKEN,
    COOKIE,
    config,
    get_default_share_cid,
    get_default_offline_cid,
)
from core import (
    RE_115_URL,
    RE_MAGNET,
    RE_ED2K,
    RE_TELEGRAPH,
    RE_TELEGRAPH_LOOSE,
    run_blocking_io,
    get_p115_client,
    get_dir_name_by_cid,
    resolve_cid_by_path,
    ensure_dir_by_path,
    process_share_content,
    add_offline_tasks,
    fetch_links_from_page,
    normalize_page_url,
    extract_links_from_reply_markup,
    ShareDB,
)
from core.ui import (
    build_cid_keyboard,
    build_delete_keyboard,
    build_dir_confirm_keyboard,
    build_dir_input_prompt,
    build_link_detected_message,
    build_offline_result_message,
    build_set_default_keyboard,
    build_share_progress_message,
    build_share_result_message,
    build_start_message,
    parse_cid_callback_data,
    parse_del_callback_data,
    parse_dir_confirm_callback_data,
    parse_setdef_callback_data,
)

# ================================
# 日志配置
# ================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ================================
# 数据层（SQLite share.db）
# ================================
db = ShareDB(config.db_file)


# ================================
# 状态机常量
# ================================
DIR_INPUT = 6  # 录入目录：CID 或路径


# ================================
# 全局状态（带 TTL 缓存）
# ================================
if TTLCache is not None:
    user_sessions = TTLCache(maxsize=config.session_maxsize, ttl=config.session_ttl)
    logger.info(f"使用 TTLCache 管理用户会话 (maxsize={config.session_maxsize}, ttl={config.session_ttl}s)")
else:
    user_sessions = {}
    logger.warning("cachetools 未安装，使用普通字典管理会话")

# 消息缓冲队列 {user_id: {...}}
message_buffers: dict[int, dict] = {}


# ================================
# 数据层工具方法
# ================================

async def run_db(func, *args, **kwargs):
    """运行本地 SQLite 阻塞操作（不经 115 全局限速）。"""
    return await asyncio.to_thread(func, *args, **kwargs)


async def load_cid_mapping() -> dict:
    """读取目录映射（名称 -> CID）"""
    return await run_db(db.list_dirs)


async def add_dir(name: str, cid: str) -> None:
    """添加目录映射"""
    await run_db(db.add_dir, name, cid)


async def remove_dir(name: str) -> None:
    """删除目录映射"""
    await run_db(db.remove_dir, name)


async def load_user_cid(user_id: int) -> dict:
    """加载用户自定义 CID（share/offline）"""
    return await run_db(db.get_user_all, user_id)


async def save_user_cid(user_id: int, cid: str, name: str, cid_type: str = 'share') -> None:
    """保存用户自定义 CID"""
    await run_db(db.set_user_cid, user_id, cid_type, cid, name)


async def reset_user_cid(user_id: int) -> None:
    """清除用户所有自定义目录设置"""
    await run_db(db.remove_user_all, user_id)


async def _record_share_links(user_id: int, share_items: list[dict]) -> None:
    """记录接收的分享链接到 share.db"""
    await run_db(db.append_links, user_id, share_items)


async def load_default_cids() -> dict:
    """读取系统默认目录（share/offline）"""
    return await run_db(db.get_default_cids)


async def _resolve_default_cid(value: str) -> tuple[str, str]:
    """把 config.yaml defaults 里的值解析为 (cid, name)。

    支持两种写法：
      1. 纯数字 CID（如 "2951032182821552757"）→ 直接使用
      2. 路径（如 "/云下载"）→ 用当前 cookie 的 115 账号动态解析；
         不存在则自动创建。这样每个账号的云下载目录 CID 不同也能正确工作。

    解析失败返回 ("", "")。
    """
    value = (value or "").strip()
    if not value:
        return "", ""
    # 纯数字 → 直接当 CID
    if value.isdigit():
        return value, ""
    # 路径 → 动态解析（每个账号 CID 不同，不能硬编码）
    client = await get_client()
    if client is None:
        logger.warning("[init] 115 客户端不可用，无法解析默认目录路径")
        return "", ""
    cid = await resolve_cid_by_path(client, value)
    if cid:
        name = await get_dir_name_by_cid(client, cid) or value.lstrip("/>")
        return cid, name
    # 不存在则创建
    cid, name = await ensure_dir_by_path(client, value)
    if cid:
        logger.info(f"[init] 自动创建默认目录 {value} -> CID={cid}")
        return cid, name
    logger.warning(f"[init] 无法解析/创建默认目录路径: {value}")
    return "", ""


async def init_system_defaults() -> None:
    """启动时从 config.yaml defaults 段写入系统默认目录（幂等，不覆盖已有数据）。

    每个 cookie 用户的云下载 CID 不同：配置里可填路径（如 /云下载），
    启动时用当前账号动态解析出实际 CID；也可直接填 CID 数字。
    如果 default_cids 表已有记录则跳过。
    """
    existing = await load_default_cids()

    for kind, raw in (("share", get_default_share_cid()),
                      ("offline", get_default_offline_cid())):
        if not raw:
            continue
        if kind in existing:
            logger.info(f"[init] {kind} 默认目录已存在，跳过")
            continue
        cid, name = await _resolve_default_cid(raw)
        if cid:
            await run_db(db.set_default_cid, kind, cid, name or "云下载")
            logger.info(f"[init] 设置 {kind} 默认目录: {name or raw} (CID={cid})")
        else:
            logger.warning(f"[init] {kind} 默认目录配置 '{raw}' 解析失败，未写入")


async def get_default_share() -> tuple[str, str]:
    """返回 (分享默认CID, 名称)。未配置时返回空。"""
    d = await load_default_cids()
    entry = d.get("share") or {}
    return entry.get("cid", ""), entry.get("name") or ""


async def get_default_offline() -> tuple[str, str]:
    """返回 (离线默认CID, 名称)。未配置时返回空。"""
    d = await load_default_cids()
    entry = d.get("offline") or {}
    return entry.get("cid", ""), entry.get("name") or ""


async def _log_share_transfer(sc: str, rc: str, ok: bool, target_cid: str) -> None:
    """写分享转存日志（key 唯一，upsert）"""
    await run_db(db.upsert_transfer_log, {
        "key": f"{sc}_{rc}",
        "kind": "share",
        "share_code": sc,
        "receive_code": rc,
        "status": "success" if ok else "failed",
        "target_cid": target_cid,
    })


async def get_client():
    """获取 P115Client 实例（cookie 来自 config.yaml）"""
    return await get_p115_client(COOKIE)


async def load_cookie_from_file() -> str:
    """返回 115 cookie（config.yaml）"""
    return COOKIE


# ================================
# 链接解析
# ================================

def extract_all_links_from_message(message) -> dict:
    """提取链接（支持转发消息）。返回 {'share': [...], 'offline': [...], 'pages': [...]}
    pages 为 telegra.ph 等资源聚合页面 URL，需另行抓取解析。"""
    result = {"share": [], "offline": [], "pages": []}
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []

    def _add_share(sc, rc, original):
        if sc:
            result["share"].append({"share_code": sc, "receive_code": rc or "", "original_text": original})

    def _add_page(url):
        cleaned = normalize_page_url(url)
        if cleaned:
            result["pages"].append(cleaned)

    for ent in entities:
        try:
            if ent.type == MessageEntity.URL and text:
                url = text[ent.offset: ent.offset + ent.length]
            elif ent.type == MessageEntity.TEXT_LINK and getattr(ent, 'url', None):
                url = ent.url
            else:
                continue
            if not url:
                continue
            m = RE_115_URL.search(url)
            if m:
                _add_share(m.group(1), m.group(2), url)
                continue
            if RE_TELEGRAPH.match(url):
                _add_page(url)
                continue
            if RE_MAGNET.match(url) or RE_ED2K.match(url):
                result["offline"].append(url)
        except Exception:
            continue

    # 内联键盘按钮中的链接（转发消息：telegra.ph/分享/ed2k 常以内联按钮呈现，
    # 链接在按钮的 url/text/callback_data 里，纯文本扫描读不到）
    _btn_links = extract_links_from_reply_markup(getattr(message, "reply_markup", None))
    result["share"].extend(_btn_links["share"])
    result["offline"].extend(_btn_links["offline"])
    for _u in _btn_links["pages"]:
        _add_page(_u)

    for m in RE_115_URL.finditer(text):
        _add_share(m.group(1), m.group(2), m.group(0))

    for m in RE_TELEGRAPH_LOOSE.finditer(text):
        _add_page(m.group(0))

    result["offline"].extend(RE_MAGNET.findall(text))
    result["offline"].extend(RE_ED2K.findall(text))

    # 去重
    seen_share = set()
    result["share"] = [it for it in result["share"]
                       if (it['share_code'], it['receive_code']) not in seen_share
                       and not seen_share.add((it['share_code'], it['receive_code']))]
    result["offline"] = list(dict.fromkeys(result["offline"]))
    result["pages"] = list(dict.fromkeys(result["pages"]))

    logger.info(
        f"提取到分享 {len(result['share'])} 个，离线 {len(result['offline'])} 个，页面 {len(result['pages'])} 个"
    )
    return result


async def expand_page_links(parsed: dict) -> dict:
    """抓取 telegra.ph 等页面并解析其中的 115 分享 / 磁力 / ed2k 链接，
    合并回 parsed 并去重。"""
    pages = parsed.pop("pages", [])
    if pages:
        logger.info(f"检测到 {len(pages)} 个页面链接，开始抓取解析...")
        for page_url in pages:
            try:
                page_links = await asyncio.to_thread(fetch_links_from_page, page_url)
            except Exception as e:
                logger.exception(f"解析页面失败 {page_url}:")
                continue
            parsed["share"].extend(page_links.get("share", []))
            parsed["offline"].extend(page_links.get("offline", []))

    # 合并后去重
    seen_share = set()
    parsed["share"] = [it for it in parsed["share"]
                       if (it['share_code'], it['receive_code']) not in seen_share
                       and not seen_share.add((it['share_code'], it['receive_code']))]
    parsed["offline"] = list(dict.fromkeys(parsed["offline"]))
    return parsed


# ================================
# Telegram 命令处理
# ================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(build_start_message())


# ---- 目录录入（/add_cid 与 设置默认「输入新目录」共用） ----

async def add_cid_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """添加新目录：进入录入状态（CID 或路径）"""
    context.user_data['dir_flow'] = 'add'
    await update.effective_message.reply_text(build_dir_input_prompt('dir'))
    return DIR_INPUT


async def dir_input_process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理用户输入的 CID 或路径，解析后确认入库。"""
    user_input = (update.message.text or "").strip()
    flow = context.user_data.get('dir_flow', 'add')

    if not user_input:
        await update.effective_message.reply_text("❌ 请输入有效的 CID 或网盘路径。")
        return DIR_INPUT

    # 记录输入（可能同时要设为默认）
    default_kind = None
    if flow == 'set_share':
        default_kind = 'share'
    elif flow == 'set_offline':
        default_kind = 'offline'

    client = await get_client()
    if not client:
        await update.effective_message.reply_text("❌ 115 客户端不可用，请检查 config.yaml 中的 p115.cookie。")
        return DIR_INPUT

    # 判断是 CID 还是路径
    if user_input.lstrip('/').lstrip('>').isdigit():
        # 纯 CID：自动查询文件夹名称
        cid = user_input.strip()
        name = await get_dir_name_by_cid(client, cid)
        if not name:
            await update.effective_message.reply_text(
                f"❌ 未能在网盘上找到 CID {cid} 对应的文件夹。\n请确认该 CID 是否为 115 网盘目录，或改用路径方式输入。"
            )
            return DIR_INPUT
        created = False
    else:
        # 路径方式：解析，若不存在则自动创建
        cid = await resolve_cid_by_path(client, user_input)
        if cid:
            name = user_input.rstrip('/').split('/')[-1] or user_input
            created = False
        else:
            cid, name = await ensure_dir_by_path(client, user_input)
            created = True
            if not cid:
                await update.effective_message.reply_text(f"❌ 无法创建路径：{user_input}")
                return DIR_INPUT

    # 暂存待确认
    context.user_data['pending_dir'] = {
        'name': name, 'cid': cid, 'created': created, 'default_kind': default_kind,
    }

    created_note = "（已在网盘自动创建）" if created else ""
    msg = (
        f"📁 已识别目录：{name} {created_note}\n"
        f"🆔 CID：{cid}\n\n"
        "确认执行？"
    )
    await update.effective_message.reply_text(msg, reply_markup=build_dir_confirm_keyboard())
    return ConversationHandler.END


async def dir_input_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """取消目录录入"""
    context.user_data.pop('pending_dir', None)
    context.user_data.pop('dir_flow', None)
    await update.effective_message.reply_text("❌ 已取消。")
    return ConversationHandler.END


# ---- 设置默认目录（按钮 + 文本录入双通道） ----

async def set_share_cid_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """设置分享默认目录：进入录入会话；已有目录时同时展示按钮。"""
    context.user_data['dir_flow'] = 'set_share'
    cid_mapping = await load_cid_mapping()
    if cid_mapping:
        await update.effective_message.reply_text(
            "📁 选择【分享】默认目录（或直接发送 CID / 网盘路径）：",
            reply_markup=build_set_default_keyboard(list(cid_mapping.items()), 'share'),
        )
    else:
        await update.effective_message.reply_text(build_dir_input_prompt('share'))
    return DIR_INPUT


async def set_offline_cid_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """设置离线默认目录：进入录入会话；已有目录时同时展示按钮。"""
    context.user_data['dir_flow'] = 'set_offline'
    cid_mapping = await load_cid_mapping()
    if cid_mapping:
        await update.effective_message.reply_text(
            "☁️ 选择【离线】默认目录（或直接发送 CID / 网盘路径）：",
            reply_markup=build_set_default_keyboard(list(cid_mapping.items()), 'offline'),
        )
    else:
        await update.effective_message.reply_text(build_dir_input_prompt('offline'))
    return DIR_INPUT


async def handle_dir_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理目录确认按钮（dirconfirm:ok / dirconfirm:cancel）。"""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    action = parse_dir_confirm_callback_data(query.data)
    if action is None:
        return

    pending = context.user_data.pop('pending_dir', None)
    flow = context.user_data.pop('dir_flow', None)

    if action == 'cancel' or not pending:
        await query.edit_message_text("❌ 已取消。")
        return

    name, cid = pending.get('name'), pending.get('cid')
    await add_dir(name, cid)

    lines = [f"✅ 已添加目录：{name}", f"🆔 CID：{cid}"]
    default_kind = pending.get('default_kind')
    if default_kind:
        user_id = update.effective_user.id
        # 仅写入用户级默认；系统默认是全局配置，不由用户命令修改
        await save_user_cid(user_id, cid, name, cid_type=default_kind)
        label = "分享" if default_kind == "share" else "离线"
        lines.append(f"📌 已设为【{label}】默认目录")

    # 末尾附加当前默认目录汇总：优先用户级，其次系统默认
    user_data = await load_user_cid(update.effective_user.id)
    d = await load_default_cids()
    sys_share = d.get('share') or {}
    sys_offline = d.get('offline') or {}

    us = user_data.get('share') or {}
    uo = user_data.get('offline') or {}
    share_label = us.get('name') or us.get('cid') or sys_share.get('name') or sys_share.get('cid') or '未配置'
    offline_label = uo.get('name') or uo.get('cid') or sys_offline.get('name') or sys_offline.get('cid') or '未配置'

    lines.append("")
    lines.append("📤 分享目录：" + share_label)
    lines.append("☁️ 离线目录：" + offline_label)
    await query.edit_message_text("\n".join(lines))


async def handle_setdef_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理「设置默认目录」按钮回调（仅数字索引，选择已有目录）。"""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    parsed = parse_setdef_callback_data(query.data)
    if not parsed:
        return
    kind, value = parsed
    if value == 'new':
        return  # new 由 setdef_new_start 处理（进入录入会话）
    user_id = update.effective_user.id

    cid_mapping = await load_cid_mapping()
    cid_list = list(cid_mapping.items())
    idx = int(value)
    if not (0 <= idx < len(cid_list)):
        await query.edit_message_text("❌ 目录不存在，请刷新后重试。")
        return
    name, cid = cid_list[idx]

    # 仅写入用户级默认；系统默认是全局配置，不由用户命令修改
    await save_user_cid(user_id, cid, name, cid_type=kind)
    label = "分享" if kind == "share" else "离线"
    await query.edit_message_text(f"✅ 已设置【{label}】默认目录：{name}\nCID: {cid}")


async def setdef_new_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """点击「输入新目录/路径」按钮：进入录入会话，并标记为设为默认。"""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    await query.answer()
    parsed = parse_setdef_callback_data(query.data)
    if not parsed:
        return ConversationHandler.END
    kind, value = parsed
    if value != 'new':
        return ConversationHandler.END
    context.user_data['dir_flow'] = f'set_{kind}'
    await query.edit_message_text(build_dir_input_prompt(kind))
    return DIR_INPUT


# ---- 删除目录（按钮化） ----

async def del_cid_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """删除目录：按钮选择。"""
    cid_mapping = await load_cid_mapping()
    if not cid_mapping:
        await update.effective_message.reply_text("暂无可删除的目录。")
        return
    await update.effective_message.reply_text(
        "🗑️ 请选择要删除的目录：",
        reply_markup=build_delete_keyboard(list(cid_mapping.items())),
    )


async def handle_del_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理删除目录按钮回调。"""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    value = parse_del_callback_data(query.data)
    if value is None:
        return
    if value == 'cancel':
        await query.edit_message_text("❌ 已取消。")
        return

    cid_mapping = await load_cid_mapping()
    cid_list = list(cid_mapping.items())
    idx = int(value)
    if not (0 <= idx < len(cid_list)):
        await query.edit_message_text("❌ 目录不存在，请刷新后重试。")
        return
    name, cid = cid_list[idx]
    await remove_dir(name)
    await query.edit_message_text(f"✅ 已删除目录：{name}")


# ---- 目录管理查询 ----

async def current_cid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """查看当前目录设置。"""
    user_id = update.effective_user.id
    user_data = await load_user_cid(user_id)
    user_share = user_data.get('share', {})
    user_offline = user_data.get('offline', {})

    d = await load_default_cids()
    sys_share = d.get('share') or {}
    sys_offline = d.get('offline') or {}

    if user_share.get('cid'):
        share_display = f"{user_share.get('name')} ({user_share.get('cid')})"
    else:
        share_display = f"(系统默认) {sys_share.get('name') or sys_share.get('cid') or '未配置'}"
    if user_offline.get('cid'):
        offline_display = f"{user_offline.get('name')} ({user_offline.get('cid')})"
    else:
        offline_display = f"(系统默认) {sys_offline.get('name') or sys_offline.get('cid') or '未配置'}"

    msg = [
        "📁 当前目录设置：\n",
        f"📤 分享目录: {share_display}",
        f"☁️ 离线目录: {offline_display}",
    ]
    await update.effective_message.reply_text("\n".join(msg))


async def list_cid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """列出可用目录。"""
    cid_mapping = await load_cid_mapping()
    if not cid_mapping:
        await update.effective_message.reply_text("暂无目录映射。请使用 /add_cid 添加。")
        return
    lines = ["可用目录列表：\n"]
    for i, (name, cid) in enumerate(cid_mapping.items(), 1):
        lines.append(f"{i}. {name} (CID: {cid})")
    await update.effective_message.reply_text("\n".join(lines))


async def reset_cid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """重置为系统默认目录（清除用户自定义）。"""
    user_id = update.effective_user.id
    await reset_user_cid(user_id)
    await update.effective_message.reply_text("✅ 已重置为系统默认目录设置。")


# ================================
# 核心消息处理
# ================================

async def add_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理用户发送的链接消息（支持转发消息）- 使用缓冲机制"""
    message = update.effective_message
    user_id = update.effective_user.id
    text = (message.text or message.caption or "").strip()

    if not text:
        await message.reply_text("请发送有效的链接！")
        return

    parsed = extract_all_links_from_message(message)

    # telegra.ph 等页面：先抓取解析其中的下载链接
    if parsed.get("pages"):
        await message.reply_text(
            f"🌐 检测到 {len(parsed['pages'])} 个页面链接，正在解析其中的下载链接..."
        )
        await expand_page_links(parsed)

    share_cnt, off_cnt = len(parsed['share']), len(parsed['offline'])

    if share_cnt == 0 and off_cnt == 0:
        await message.reply_text("未检测到有效链接。")
        return

    await buffer_message_to_queue(update, context, parsed)


async def buffer_message_to_queue(update: Update, context: ContextTypes.DEFAULT_TYPE, parsed: dict) -> None:
    """将解析到的链接加入缓冲队列，等待合并处理"""
    user_id = update.effective_user.id

    if user_id not in message_buffers:
        message_buffers[user_id] = {
            "share_links": list(parsed["share"]),
            "offline_links": list(parsed["offline"]),
            "update": update,
            "context": context,
            "buffer_task": None,
        }
        logger.info(f"用户 {user_id}: 开始缓冲消息，等待 {config.message_buffer_timeout} 秒...")
        task = asyncio.create_task(process_buffered_messages(user_id, config.message_buffer_timeout))
        message_buffers[user_id]["buffer_task"] = task
    else:
        buffer = message_buffers[user_id]
        buffer["share_links"].extend(parsed["share"])
        buffer["offline_links"].extend(parsed["offline"])
        buffer["update"] = update
        buffer["context"] = context
        logger.info(f"用户 {user_id}: 合并链接，累计 {len(buffer['share_links'])} 分享, {len(buffer['offline_links'])} 离线")


async def process_buffered_messages(user_id: int, timeout: float) -> None:
    """等待缓冲超时后处理所有消息"""
    await asyncio.sleep(timeout)

    if user_id not in message_buffers:
        return

    buffer = message_buffers.pop(user_id)
    update = buffer["update"]
    context = buffer["context"]

    # 去重
    seen_share = set()
    unique_share = []
    for it in buffer["share_links"]:
        key = (it['share_code'], it['receive_code'])
        if key not in seen_share:
            seen_share.add(key)
            unique_share.append(it)
    unique_offline = list(dict.fromkeys(buffer["offline_links"]))

    share_cnt = len(unique_share)
    off_cnt = len(unique_offline)

    logger.info(f"用户 {user_id}: 缓冲结束，共 {share_cnt} 个分享链接, {off_cnt} 个离线链接")

    if share_cnt == 0 and off_cnt == 0:
        await update.effective_message.reply_text("未检测到有效链接。")
        return

    user_sessions[user_id] = {"share_links": unique_share, "offline_links": unique_offline}

    cid_mapping = await load_cid_mapping()
    sys_share_cid, _ = await get_default_share()
    sys_offline_cid, _ = await get_default_offline()

    # 只有离线链接
    if off_cnt > 0 and share_cnt == 0:
        if not cid_mapping and not sys_offline_cid:
            await update.effective_message.reply_text("❌ 未配置任何目录。请使用 /add_cid 添加目录。")
            return
        cid_list = list(cid_mapping.items())
        await update.effective_message.reply_text(
            build_link_detected_message("offline", share_cnt, off_cnt, config.cid_selection_timeout),
            reply_markup=build_cid_keyboard(cid_list, "offline"),
        )
        user_sessions[user_id]["cid_list"] = cid_list
        user_sessions[user_id]["awaiting_offline_cid_selection"] = True
        user_sessions[user_id]["selection_start"] = asyncio.get_event_loop().time()
        asyncio.create_task(handle_offline_selection_timeout(update, context, user_id, config.cid_selection_timeout))
        return

    # 有分享链接
    if not cid_mapping and not sys_share_cid:
        await update.effective_message.reply_text("❌ 未配置任何目录。请使用 /add_cid 添加目录。")
        return

    cid_list = list(cid_mapping.items())
    await update.effective_message.reply_text(
        build_link_detected_message("share", share_cnt, off_cnt, config.cid_selection_timeout),
        reply_markup=build_cid_keyboard(cid_list, "share"),
    )
    user_sessions[user_id]["cid_list"] = cid_list
    user_sessions[user_id]["awaiting_cid_selection"] = True
    user_sessions[user_id]["selection_start"] = asyncio.get_event_loop().time()
    asyncio.create_task(handle_selection_timeout(update, context, user_id, config.cid_selection_timeout))


async def handle_selection_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, timeout: int) -> None:
    """处理目录选择超时"""
    await asyncio.sleep(timeout)

    if user_id in user_sessions and user_sessions[user_id].get("awaiting_cid_selection"):
        session = user_sessions[user_id]
        session["awaiting_cid_selection"] = False

        user_data = await load_user_cid(user_id)
        user_share = user_data.get('share', {})
        sys_share_cid, sys_share_name = await get_default_share()
        effective_cid = user_share.get('cid') or sys_share_cid
        effective_name = user_share.get('name') or sys_share_name or "默认分享目录"

        await update.effective_message.reply_text(f"⏱️ 选择超时，使用 {effective_name}...")
        await process_with_cid(update, context, session, effective_cid, effective_name)
        user_sessions.pop(user_id, None)


async def handle_offline_selection_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, timeout: int) -> None:
    """处理离线目录选择超时"""
    await asyncio.sleep(timeout)

    if user_id in user_sessions and user_sessions[user_id].get("awaiting_offline_cid_selection"):
        session = user_sessions[user_id]
        session["awaiting_offline_cid_selection"] = False

        user_data = await load_user_cid(user_id)
        user_offline = user_data.get('offline', {})
        sys_offline_cid, sys_offline_name = await get_default_offline()
        effective_cid = user_offline.get('cid') or sys_offline_cid
        effective_name = user_offline.get('name') or sys_offline_name or "默认离线目录"

        await update.effective_message.reply_text(f"⏱️ 选择超时，使用 {effective_name}...")
        await process_offline_only(update, context, session.get("offline_links", []), effective_cid, effective_name)
        user_sessions.pop(user_id, None)


async def handle_cid_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理目录选择按钮回调（链接处理时）。"""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    parsed = parse_cid_callback_data(query.data)
    if not parsed:
        return

    kind, value = parsed
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        await query.edit_message_text("⚠️ 当前选择已过期，请重新发送链接。")
        return

    session = user_sessions[user_id]
    cid_list = session.get("cid_list", [])
    selected_cid = None
    selected_name = None

    if value == "default":
        if kind == "share":
            selected_cid, selected_name = await get_default_share()
            selected_name = selected_name or "默认分享目录"
        else:
            selected_cid, selected_name = await get_default_offline()
            selected_name = selected_name or "默认离线目录"
    else:
        idx = int(value)
        if 0 <= idx < len(cid_list):
            selected_name, selected_cid = cid_list[idx]

    if not selected_cid:
        await query.edit_message_text("❌ 目录不可用或默认目录未配置，请重新发送链接后再试。")
        user_sessions.pop(user_id, None)
        return

    if kind == "offline":
        session["awaiting_offline_cid_selection"] = False
        await query.edit_message_text(f"✅ 已选择离线目录：{selected_name}\n\n☁️ 正在提交离线任务...")
        await process_offline_only(update, context, session.get("offline_links", []), selected_cid, selected_name)
        user_sessions.pop(user_id, None)
        return

    session["awaiting_cid_selection"] = False
    await query.edit_message_text(f"✅ 已选择分享目录：{selected_name}\n\n📦 正在开始转存...")
    has_pending_offline = await process_with_cid(update, context, session, selected_cid, selected_name)
    if not has_pending_offline:
        user_sessions.pop(user_id, None)


async def handle_user_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理用户的选择"""
    user_id = update.effective_user.id
    user_input = (update.message.text or '').strip()

    # 缓冲阶段：合并新链接
    if user_id in message_buffers:
        parsed = extract_all_links_from_message(update.message)
        if parsed.get("pages"):
            await expand_page_links(parsed)
        if parsed["share"] or parsed["offline"]:
            buffer = message_buffers[user_id]
            buffer["share_links"].extend(parsed["share"])
            buffer["offline_links"].extend(parsed["offline"])
            buffer["update"] = update
            buffer["context"] = context
            logger.info(f"用户 {user_id}: 合并链接，累计 {len(buffer['share_links'])} 分享, {len(buffer['offline_links'])} 离线")
            return
        logger.debug(f"用户 {user_id}: 缓冲期间收到非链接消息，忽略")
        return

    if user_id not in user_sessions:
        await add_link(update, context)
        return

    session = user_sessions[user_id]

    # 处理离线目录选择（数字备用）
    if session.get("awaiting_offline_cid_selection"):
        session["awaiting_offline_cid_selection"] = False
        cid_list = session.get("cid_list", [])
        selected_cid = None
        selected_name = None
        try:
            choice = int(user_input)
            if 1 <= choice <= len(cid_list):
                selected_name, selected_cid = cid_list[choice - 1]
            elif choice == len(cid_list) + 1:
                selected_cid, selected_name = await get_default_offline()
                selected_name = selected_name or "默认离线目录"
            else:
                await update.effective_message.reply_text("❌ 无效选择，使用默认离线目录。")
                selected_cid, selected_name = await get_default_offline()
                selected_name = selected_name or "默认离线目录"
        except ValueError:
            await update.effective_message.reply_text("❌ 请输入数字，使用默认离线目录。")
            selected_cid, selected_name = await get_default_offline()
            selected_name = selected_name or "默认离线目录"

        if not selected_cid:
            await update.effective_message.reply_text("❌ 未配置默认离线目录。")
            user_sessions.pop(user_id, None)
            return

        await update.effective_message.reply_text(f"✅ 已选择：{selected_name}")
        await process_offline_only(update, context, session.get("offline_links", []), selected_cid, selected_name)
        user_sessions.pop(user_id, None)
        return

    # 处理分享目录选择（数字备用）
    if session.get("awaiting_cid_selection"):
        session["awaiting_cid_selection"] = False
        cid_list = session.get("cid_list", [])
        selected_cid = None
        selected_name = None
        try:
            choice = int(user_input)
            if 1 <= choice <= len(cid_list):
                selected_name, selected_cid = cid_list[choice - 1]
            elif choice == len(cid_list) + 1:
                selected_cid, selected_name = await get_default_share()
                selected_name = selected_name or "默认分享目录"
            else:
                await update.effective_message.reply_text("❌ 无效选择，使用默认目录。")
                selected_cid, selected_name = await get_default_share()
                selected_name = selected_name or "默认分享目录"
        except ValueError:
            await update.effective_message.reply_text("❌ 请输入数字，使用默认目录。")
            selected_cid, selected_name = await get_default_share()
            selected_name = selected_name or "默认分享目录"

        if not selected_cid:
            await update.effective_message.reply_text("❌ 未配置默认目录。")
            user_sessions.pop(user_id, None)
            return

        await update.effective_message.reply_text(f"✅ 已选择：{selected_name}")
        has_pending_offline = await process_with_cid(update, context, session, selected_cid, selected_name)
        if not has_pending_offline:
            user_sessions.pop(user_id, None)
        return

    await add_link(update, context)


async def process_with_cid(update: Update, context: ContextTypes.DEFAULT_TYPE, session: dict, share_cid: str, share_cid_name: str) -> bool:
    """使用指定 CID 处理分享和离线链接。返回 True 如果有离线链接待处理。"""
    share_items = session.get("share_links", [])
    offline_urls = session.get("offline_links", [])
    user_id = update.effective_user.id

    # 记录分享链接到 share.db
    if share_items:
        await _record_share_links(user_id, share_items)

    cookie = await load_cookie_from_file()
    client = await get_client()

    # 处理分享链接
    if share_items:
        if not client:
            await update.effective_message.reply_text("⚠️ 115 客户端未初始化，跳过分享转存。")
        else:
            total = len(share_items)
            status_message = await update.effective_message.reply_text(
                build_share_progress_message(0, total, share_cid_name)
            )

            success = failed = 0
            failed_items: list[str] = []
            for i, it in enumerate(share_items, 1):
                sc, rc = it['share_code'], it['receive_code']
                ok = await process_share_content(client, cookie, sc, rc, share_cid)
                await _log_share_transfer(sc, rc, ok, share_cid)

                if ok:
                    success += 1
                else:
                    failed += 1
                    failed_items.append(f"{sc} / {rc or '无提取码'}")

                try:
                    await status_message.edit_text(build_share_progress_message(i, total, share_cid_name))
                except Exception as e:
                    logger.debug(f"更新进度卡片失败: {e}")

                await asyncio.sleep(1)

            try:
                await status_message.edit_text(build_share_result_message(success, failed, share_cid_name, failed_items))
            except Exception as e:
                logger.debug(f"更新结果卡片失败: {e}")
                await update.effective_message.reply_text(build_share_result_message(success, failed, share_cid_name, failed_items))

    # 处理离线链接 - 显示目录选择菜单
    if offline_urls:
        cid_mapping = await load_cid_mapping()
        sys_offline_cid, _ = await get_default_offline()

        if not cid_mapping and not sys_offline_cid:
            await update.effective_message.reply_text("❌ 未配置离线目录，跳过离线下载。")
            return False

        cid_list = list(cid_mapping.items())
        await update.effective_message.reply_text(
            build_link_detected_message("offline", 0, len(offline_urls), config.cid_selection_timeout),
            reply_markup=build_cid_keyboard(cid_list, "offline"),
        )

        user_sessions[user_id] = {
            "share_links": [],
            "offline_links": offline_urls,
            "cid_list": cid_list,
            "awaiting_offline_cid_selection": True,
            "selection_start": asyncio.get_event_loop().time(),
        }
        asyncio.create_task(handle_offline_selection_timeout(update, context, user_id, config.cid_selection_timeout))
        return True

    return False


async def process_offline_only(update: Update, context: ContextTypes.DEFAULT_TYPE, offline_urls: list, offline_cid: str, offline_cid_name: str = "离线目录") -> None:
    """处理离线下载"""
    if not offline_cid:
        await update.effective_message.reply_text("❌ 未配置离线默认目录。")
        return

    cookie = await load_cookie_from_file()
    if not cookie:
        await update.effective_message.reply_text("❌ Cookie 未配置（config.yaml 的 p115.cookie）。")
        return

    await update.effective_message.reply_text(f"☁️ 提交 {len(offline_urls)} 个离线任务...")

    try:
        res = await add_offline_tasks(cookie, offline_urls, offline_cid)
        success = 0
        failed_items: list[str] = []
        for r in res.get('results', []):
            if r.get('success'):
                success += 1
            else:
                failed_items.append(r.get('url') or '未知')
        failed = len(offline_urls) - success
        await update.effective_message.reply_text(
            build_offline_result_message(success, failed, offline_cid_name, failed_items)
        )
    except Exception as e:
        await update.effective_message.reply_text(f"❌ 离线提交异常: {e}")
        logger.exception("离线任务异常:")


# ================================
# 错误处理
# ================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception(f"错误: {context.error}")
    if update and hasattr(update, 'effective_message') and update.effective_message:
        await update.effective_message.reply_text(f"❌ 发生错误: {context.error}")


# ================================
# 启动入口
# ================================

def get_bot_token() -> str:
    """返回 Bot token（config.yaml）。"""
    if not BOT_TOKEN:
        logger.error("Bot Token 未配置（config.yaml 的 telegram.bot_token）")
        return ""
    return BOT_TOKEN


async def post_init(application: Application) -> None:
    """设置 Bot 菜单命令 + 启动时初始化系统默认目录"""
    # 初始化系统默认目录（幂等，不覆盖已有数据）
    try:
        await init_system_defaults()
    except Exception:
        logger.exception("[init] 系统默认目录初始化失败（不影响启动）")

    commands = [
        BotCommand("start", "查看帮助"),
        BotCommand("set_share_cid", "设置分享默认目录"),
        BotCommand("set_offline_cid", "设置离线默认目录"),
        BotCommand("add_cid", "添加新目录"),
        BotCommand("del_cid", "删除目录"),
        BotCommand("current_cid", "查看当前设置"),
        BotCommand("list_cid", "列出可用目录"),
        BotCommand("reset_cid", "重置为默认目录"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("已设置 Bot 菜单命令")


def _build_application(token: str) -> Application:
    """构建 Application。若设置了 HTTP_PROXY/HTTPS_PROXY 环境变量，则 Telegram Bot API
    走该代理（仅影响 Telegram，不影响 115 API）。"""
    builder = Application.builder().token(token).post_init(post_init)
    if _TELEGRAM_PROXY:
        logger.info(f"[proxy] Telegram Bot API 将通过代理连接: {_TELEGRAM_PROXY}")
        client = httpx.AsyncClient(proxy=_TELEGRAM_PROXY, trust_env=False)
        builder = builder.http_client(client)
    return builder.build()


def main() -> None:
    token = get_bot_token()
    if not token:
        logger.error("Bot Token 未设置")
        sys.exit(1)

    application = _build_application(token)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("current_cid", current_cid))
    application.add_handler(CommandHandler("list_cid", list_cid))
    application.add_handler(CommandHandler("reset_cid", reset_cid))

    # 目录按钮回调（需放在通用 MessageHandler 之前）
    # setdef:new 由会话入口处理；数字索引由 handle_setdef_button 处理；dirconfirm 由 handle_dir_confirm 处理
    application.add_handler(CallbackQueryHandler(handle_setdef_button, pattern=r"^setdef:(share|offline):\d+$"))
    application.add_handler(CallbackQueryHandler(handle_dir_confirm, pattern=r"^dirconfirm:"))
    application.add_handler(CallbackQueryHandler(handle_del_button, pattern=r"^del:"))
    application.add_handler(CallbackQueryHandler(handle_cid_button, pattern=r"^cid:"))

    # 删除目录（按钮）
    application.add_handler(CommandHandler("del_cid", del_cid_start))

    # 目录录入会话（/add_cid、/set_share_cid、/set_offline_cid、或按钮「输入新目录」）
    dir_conv = ConversationHandler(
        entry_points=[
            CommandHandler("add_cid", add_cid_start),
            CommandHandler("set_share_cid", set_share_cid_start),
            CommandHandler("set_offline_cid", set_offline_cid_start),
            CallbackQueryHandler(setdef_new_start, pattern=r"^setdef:(share|offline):new$"),
        ],
        states={
            DIR_INPUT: [MessageHandler(filters.TEXT & (~filters.COMMAND), dir_input_process)],
        },
        fallbacks=[
            CommandHandler("cancel", dir_input_cancel),
            # 会话残留时这些命令仍需能命中（否则静默失效）
            CommandHandler("add_cid", add_cid_start),
            CommandHandler("set_share_cid", set_share_cid_start),
            CommandHandler("set_offline_cid", set_offline_cid_start),
            # 会话激活后 entry_points 不再检查，「输入新目录」按钮必须走 fallback 才能命中
            CallbackQueryHandler(setdef_new_start, pattern=r"^setdef:(share|offline):new$"),
        ],
        conversation_timeout=120,  # 会话 120s 无操作自动结束，避免残留导致后续命令失效
    )
    application.add_handler(dir_conv)

    # 支持普通消息和转发消息
    application.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
        handle_user_choice,
    ))
    application.add_error_handler(error_handler)

    logger.info("机器人启动...")
    application.run_polling()


if __name__ == "__main__":
    main()
