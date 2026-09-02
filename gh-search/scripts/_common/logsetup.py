"""共享日志配置：从 config 的 logging 段读取级别与 file/console 开关。

三个通道脚本（search_repos / semantic_search / hybrid_search）与步骤脚本共用。
日志文件与 gh_search_index_v3.db 同级：gh-search/data/<logger名>.log，
按 5MB×3 轮转，避免无限膨胀；.gitignore 已忽略 *.log。

配置字段（来自 config.yaml 的 logging 段，或环境变量覆盖后的结果）:
    level    - debug / info / warning / error
    file     - 是否落盘 data/*.log
    console  - 是否输出 stderr
"""
import contextvars
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import yaml

# ── request_id: 通过 contextvars 自动传递到所有下游 logger ──
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def set_request_id(rid: str) -> None:
    _request_id_var.set(rid)


def get_request_id() -> str:
    return _request_id_var.get()


class _RequestIdFilter(logging.Filter):
    """自动为每条 log record 注入 request_id 字段。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True

# <scripts>/_common/../.. → gh-search/，data 目录即索引库所在目录
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
GH_SEARCH_ROOT = DATA_DIR.parent

_VALID_LEVELS = ("debug", "info", "warning", "error")


def load_logging_config() -> dict:
    """读取 config.yaml 的 logging 段并应用环境变量覆盖，返回 level/file/console。"""
    config_path = GH_SEARCH_ROOT / "config.yaml"
    logging_cfg: dict = {}
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        logging_cfg = data.get("logging") or {}

    level = str(os.environ.get("GH_SEARCH_LOG_LEVEL", logging_cfg.get("level", "info"))).lower()
    if level not in _VALID_LEVELS:
        level = "info"

    def _as_bool(key: str, env_name: str, default: bool) -> bool:
        raw = os.environ.get(env_name)
        if raw is not None:
            return str(raw).strip().lower() in ("1", "true", "yes", "on")
        return bool(logging_cfg.get(key, default))

    return {
        "level": level,
        "file": _as_bool("file", "GH_SEARCH_LOG_FILE", True),
        "console": _as_bool("console", "GH_SEARCH_LOG_CONSOLE", False),
    }


def _resolve_level(level: str) -> int:
    """把字符串级别映射为 logging 常量；非法值回退 INFO。"""
    return {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "warn": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }.get(str(level).lower(), logging.INFO)


def setup(
    log: logging.Logger,
    *,
    level: str = "info",
    file: bool = True,
    console: bool = False,
) -> str:
    """按配置给 logger 挂 handler 并设置级别，返回日志文件绝对路径。

    文件 handler 常驻 DEBUG 落盘；console handler 仅当 console=True 时挂载。
    logger 自身级别由 level 参数控制（文件 handler 恒为 DEBUG，便于落盘排查）。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{log.name}.log"
    _fmt = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s %(message)s"
    _req_filter = _RequestIdFilter()
    if file:
        fh = RotatingFileHandler(
            path, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(_fmt, datefmt="%m-%d %H:%M:%S"))
        fh.addFilter(_req_filter)
        log.addHandler(fh)
    if console:
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.DEBUG)
        sh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(request_id)s] %(message)s", datefmt="%H:%M:%S"))
        sh.addFilter(_req_filter)
        log.addHandler(sh)
    log.setLevel(_resolve_level(level))
    log.propagate = False
    return str(path)


def configure_root_logging(config: Optional[dict] = None) -> None:
    """配置根 logger，供 REST 服务或脚本统一初始化。

    config 为 logging 段 dict，含 level/file/console 三个键。
    根 logger 使用文件 handler（若 file=True）与 console handler（若 console=True）。
    """
    config = config or {}
    level = config.get("level", "info")
    file_enabled = bool(config.get("file", True))
    console_enabled = bool(config.get("console", False))

    root = logging.getLogger()
    root.setLevel(_resolve_level(level))

    # 避免重复初始化
    if getattr(root, "_gh_search_configured", False):
        return
    root._gh_search_configured = True

    _fmt = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s %(message)s"
    _req_filter = _RequestIdFilter()
    if file_enabled:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / "gh-search.log"
        fh = RotatingFileHandler(
            path, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(_fmt, datefmt="%m-%d %H:%M:%S"))
        fh.addFilter(_req_filter)
        root.addHandler(fh)
    if console_enabled:
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.DEBUG)
        sh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(request_id)s] %(message)s", datefmt="%H:%M:%S"))
        sh.addFilter(_req_filter)
        root.addHandler(sh)
