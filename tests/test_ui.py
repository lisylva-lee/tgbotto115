import pytest

from telegram import InlineKeyboardMarkup

from core.ui import (
    CALLBACK_PREFIX_CID,
    build_cid_keyboard,
    build_dir_confirm_keyboard,
    build_dir_input_prompt,
    build_link_detected_message,
    build_offline_result_message,
    build_set_default_keyboard,
    build_share_progress_message,
    build_share_result_message,
    build_start_message,
    parse_cid_callback_data,
    parse_dir_confirm_callback_data,
    parse_setdef_callback_data,
)


def test_build_cid_keyboard_contains_directory_buttons_and_default():
    keyboard = build_cid_keyboard([("电影", "123456"), ("电视剧", "789012")], "share")

    assert isinstance(keyboard, InlineKeyboardMarkup)
    assert keyboard.inline_keyboard[0][0].text == "📁 电影"
    assert keyboard.inline_keyboard[0][0].callback_data == f"{CALLBACK_PREFIX_CID}:share:0"
    assert keyboard.inline_keyboard[0][1].text == "📁 电视剧"
    assert keyboard.inline_keyboard[-1][0].text == "✅ 使用默认分享目录"
    assert keyboard.inline_keyboard[-1][0].callback_data == f"{CALLBACK_PREFIX_CID}:share:default"


def test_build_cid_keyboard_rejects_unknown_kind():
    with pytest.raises(ValueError):
        build_cid_keyboard([], "unknown")


def test_parse_cid_callback_data_supports_index_and_default():
    assert parse_cid_callback_data("cid:share:2") == ("share", "2")
    assert parse_cid_callback_data("cid:offline:default") == ("offline", "default")
    assert parse_cid_callback_data("menu:help") is None


def test_start_message_is_card_style_and_mentions_batch_buffer():
    text = build_start_message()

    assert "115 ShareBot" in text
    assert "连续转发多条消息" in text
    assert "/set_share_cid" in text
    assert "/add_cid" in text


def test_link_detected_message_shows_counts_timeout_and_fallback():
    text = build_link_detected_message("share", 2, 1, timeout=10)

    assert "📥 检测到 2 个 115 分享链接" in text
    assert "☁️ 同时检测到 1 个离线链接" in text
    assert "10 秒" in text
    assert "也可以直接回复序号" in text


def test_result_messages_are_structured():
    share_text = build_share_result_message(success=3, failed=1, cid_name="电影")
    offline_text = build_offline_result_message(success=2, failed=0, cid_name="动漫")

    assert "📦 分享转存完成" in share_text
    assert "✅ 成功：3" in share_text
    assert "❌ 失败：1" in share_text
    assert "📁 保存目录：电影" in share_text
    assert "☁️ 离线任务提交完成" in offline_text
    assert "📁 保存目录：动漫" in offline_text


def test_share_progress_message_contains_count_and_directory():
    text = build_share_progress_message(processed=1, total=3, cid_name="电影")

    assert "📦 分享转存中" in text
    assert "⏳ 1/3" in text
    assert "📁 目录：电影" in text
    assert "%" in text  # 进度百分比


def test_share_progress_message_final_is_complete():
    text = build_share_progress_message(processed=5, total=5, cid_name="电影")

    assert "✅ 已完成" in text
    assert "📁 目录：电影" in text


def test_result_message_lists_failed_items_when_provided():
    text = build_share_result_message(
        success=1, failed=2, cid_name="电影",
        failed_items=["swaaaa1234", "swbbbb5678"],
    )

    assert "❌ 失败详情" in text
    assert "swaaaa1234" in text
    assert "swbbbb5678" in text


def test_result_message_omits_failed_items_when_none():
    text = build_share_result_message(success=2, failed=0, cid_name="电影")

    assert "失败详情" not in text


def test_set_default_keyboard_contains_dirs_and_new_option():
    kb = build_set_default_keyboard([("电影", "111"), ("电视剧", "222")], "share")

    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "setdef:share:0" in data
    assert "setdef:share:1" in data
    assert "setdef:share:new" in data


def test_set_default_keyboard_rejects_unknown_kind():
    with pytest.raises(ValueError):
        build_set_default_keyboard([], "bogus")


def test_dir_confirm_keyboard_has_ok_and_cancel():
    kb = build_dir_confirm_keyboard()
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "dirconfirm:ok" in data
    assert "dirconfirm:cancel" in data


def test_dir_input_prompt_mentions_cid_and_path():
    text = build_dir_input_prompt("share")
    assert "CID" in text
    assert "路径" in text
    assert "自动创建" in text


def test_parse_setdef_callback_data():
    assert parse_setdef_callback_data("setdef:share:0") == ("share", "0")
    assert parse_setdef_callback_data("setdef:offline:new") == ("offline", "new")
    assert parse_setdef_callback_data("cid:share:0") is None
    assert parse_setdef_callback_data("setdef:bogus:0") is None
    assert parse_setdef_callback_data("setdef:share:x") is None


def test_parse_dir_confirm_callback_data():
    assert parse_dir_confirm_callback_data("dirconfirm:ok") == "ok"
    assert parse_dir_confirm_callback_data("dirconfirm:cancel") == "cancel"
    assert parse_dir_confirm_callback_data("dirconfirm:other") is None
    assert parse_dir_confirm_callback_data("cid:share:0") is None
