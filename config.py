#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py - 集中配置管理（config.yaml 驱动）

从项目根目录的 config.yaml 加载 Telegram token、115 cookie 与运行时参数。
缺失/未配置时抛出 ConfigError，给出明确的配置指引。

数据文件（目录映射、用户目录、转存日志、链接记录等）统一存到本地 SQLite
（share.db），不再使用散落的 txt/json 文件。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import os
import sys

try:
    import yaml
except ImportError:
    yaml = None


class ConfigError(RuntimeError):
    """配置缺失或非法时抛出，附带如何修复的说明。"""


# ================================
# 路径常量
# ================================
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
CONFIG_FILE = BASE_DIR / "config.yaml"
CONFIG_EXAMPLE_FILE = BASE_DIR / "config.example.yaml"

# 本地 SQLite 数据库（目录映射、用户目录、默认目录、链接、转存日志）
DB_FILE = DATA_DIR / "share.db"

# 确保 data 目录存在
DATA_DIR.mkdir(exist_ok=True)


def _load_yaml() -> dict:
    """读取并解析 config.yaml，缺失时给出引导。"""
    if not CONFIG_FILE.exists():
        raise ConfigError(
            "缺少配置文件 config.yaml。\n"
            f"请复制 {CONFIG_EXAMPLE_FILE.name} 为 config.yaml 并填写必填项：\n"
            f"  Copy-Item {CONFIG_EXAMPLE_FILE.name} {CONFIG_FILE.name}\n"
            "然后编辑 config.yaml，填写 telegram.bot_token 与 p115.cookie。"
        )
    if yaml is None:
        raise ConfigError("缺少 PyYAML 依赖。请执行：pip install pyyaml")
    try:
        data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise ConfigError(f"config.yaml 解析失败：{e}") from e
    if not isinstance(data, dict):
        raise ConfigError("config.yaml 内容格式不正确，应为 YAML 映射（key: value）。")
    return data


def _require_str(mapping: dict, section: str, key: str) -> str:
    """从配置段中读取必填字符串，缺失/为空时报错。"""
    value = (mapping.get(key) or "").strip()
    if not value:
        raise ConfigError(
            f"config.yaml 缺少必填项：{section}.{key}。\n"
            f"请在 config.yaml 的 [{section}] 段填写该值。"
        )
    return value


# ================================
# 读取 config.yaml
# ================================
_raw_config = _load_yaml()

_telegram = _raw_config.get("telegram") or {}
_p115 = _raw_config.get("p115") or {}
_runtime = _raw_config.get("runtime") or {}
_defaults = _raw_config.get("defaults") or {}

BOT_TOKEN = _require_str(_telegram, "telegram", "bot_token")
COOKIE = _require_str(_p115, "p115", "cookie")


# 兼容旧环境变量覆盖（可选）
if os.getenv("TELEGRAM_BOT_TOKEN"):
    BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN").strip()


# ================================
# 运行时配置（dataclass）
# ================================
@dataclass
class AppConfig:
    """应用运行时配置。"""

    # 路径
    base_dir: Path = field(default_factory=lambda: BASE_DIR)
    data_dir: Path = field(default_factory=lambda: DATA_DIR)
    db_file: Path = field(default_factory=lambda: DB_FILE)

    # 运行时（512MB VPS 优化默认值，可被 config.yaml runtime 覆盖）
    max_concurrent_tasks: int = 3
    batch_size: int = 50
    max_recursion_depth: int = 15
    request_delay: float = 0.5
    retry_delay: float = 3.0
    max_retries: int = 3

    # 115 API 全局 QPS 限制（默认 1，防止高频请求触发风控导致 cookie 失效）
    request_qps: float = 1.0

    # Session 配置
    session_ttl: int = 3600
    session_maxsize: int = 100

    # 目录选择超时
    cid_selection_timeout: int = 10

    # 消息缓冲
    message_buffer_timeout: float = 3.0

    @classmethod
    def from_yaml(cls, runtime: dict) -> "AppConfig":
        """从 config.yaml 的 runtime 段构建配置。"""
        config = cls()

        def _int(key, default):
            try:
                return int(runtime.get(key, default))
            except (TypeError, ValueError):
                return default

        def _float(key, default):
            try:
                return float(runtime.get(key, default))
            except (TypeError, ValueError):
                return default

        config.max_concurrent_tasks = _int("max_concurrent_tasks", config.max_concurrent_tasks)
        config.batch_size = _int("batch_size", config.batch_size)
        config.max_recursion_depth = _int("max_recursion_depth", config.max_recursion_depth)
        config.request_delay = _float("request_delay", config.request_delay)
        config.retry_delay = _float("retry_delay", config.retry_delay)
        config.max_retries = _int("max_retries", config.max_retries)
        config.request_qps = _float("request_qps", config.request_qps)
        config.session_ttl = _int("session_ttl", config.session_ttl)
        config.session_maxsize = _int("session_maxsize", config.session_maxsize)
        config.cid_selection_timeout = _int("cid_selection_timeout", config.cid_selection_timeout)
        config.message_buffer_timeout = _float("message_buffer_timeout", config.message_buffer_timeout)

        # 环境变量覆盖
        if os.getenv("SHAREBOT_MAX_CONCURRENT"):
            config.max_concurrent_tasks = int(os.getenv("SHAREBOT_MAX_CONCURRENT"))
        if os.getenv("SHAREBOT_BATCH_SIZE"):
            config.batch_size = int(os.getenv("SHAREBOT_BATCH_SIZE"))
        if os.getenv("SHAREBOT_SESSION_TTL"):
            config.session_ttl = int(os.getenv("SHAREBOT_SESSION_TTL"))
        if os.getenv("SHAREBOT_CID_TIMEOUT"):
            config.cid_selection_timeout = int(os.getenv("SHAREBOT_CID_TIMEOUT"))

        return config


# 全局配置实例
config = AppConfig.from_yaml(_runtime)


def get_default_share_cid() -> str:
    """读取 config.yaml defaults.share_default_cid，空串表示未配置。"""
    return (_defaults.get("share_default_cid") or "").strip()


def get_default_offline_cid() -> str:
    """读取 config.yaml defaults.offline_default_cid，空串表示未配置。"""
    return (_defaults.get("offline_default_cid") or "").strip()
