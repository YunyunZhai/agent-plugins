"""配置管理：config.yaml 加载 + 环境变量覆盖。"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """加载 config.yaml，环境变量覆盖关键字段。"""
    config_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    config: Dict[str, Any] = {}

    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    # 环境变量覆盖
    config.setdefault("github", {})
    config.setdefault("embedding", {})
    config.setdefault("server", {})
    config.setdefault("billing", {})
    config.setdefault("logging", {})

    if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        config["github"]["token"] = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if os.environ.get("GH_SEARCH_TIMEOUT"):
        config["github"]["timeout"] = int(os.environ["GH_SEARCH_TIMEOUT"])
    if os.environ.get("GH_SEARCH_BACKEND"):
        config["embedding"]["backend"] = os.environ["GH_SEARCH_BACKEND"]
    if os.environ.get("GH_SEARCH_DB"):
        config["embedding"]["db_path"] = os.environ["GH_SEARCH_DB"]
    if os.environ.get("GH_SEARCH_EMBED_DIM"):
        config["embedding"]["dim"] = int(os.environ["GH_SEARCH_EMBED_DIM"])

    # 日志配置：级别 + 文件/console 开关，环境变量可覆盖
    _DEFAULT_LOG_LEVEL = "info"
    level = os.environ.get("GH_SEARCH_LOG_LEVEL", config["logging"].get("level", _DEFAULT_LOG_LEVEL))
    level = str(level).lower()
    if level not in ("debug", "info", "warning", "error"):
        level = _DEFAULT_LOG_LEVEL
    config["logging"]["level"] = level

    def _as_bool(key: str, env_name: str, default: bool) -> bool:
        raw = os.environ.get(env_name)
        if raw is None:
            return bool(config["logging"].get(key, default))
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    config["logging"]["file"] = _as_bool("file", "GH_SEARCH_LOG_FILE", True)
    config["logging"]["console"] = _as_bool("console", "GH_SEARCH_LOG_CONSOLE", False)

    return config


def get_github_token(config: Dict[str, Any]) -> Optional[str]:
    """获取 GitHub token：config > 环境变量 > gh CLI。"""
    token = config.get("github", {}).get("token", "")
    if token:
        return token
    return None  # 让 GitHubClient 自动走 gh CLI


# 全局配置单例
_config: Optional[Dict[str, Any]] = None


def get_config() -> Dict[str, Any]:
    global _config
    if _config is None:
        _config = load_config()
    return _config
