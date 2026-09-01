"""共享日志配置：DEBUG 级别常驻落盘到插件 data 目录，stderr 按 --debug 开启。

三个通道脚本（search_repos / semantic_search / hybrid_search）共用。
日志文件与 gh_search_index_v3.db 同级：gh-search/data/<logger名>.log，
按 5MB×3 轮转，避免无限膨胀；.gitignore 已忽略 *.log。
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# <scripts>/_common/../.. → gh-search/，data 目录即索引库所在目录
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def setup(log: logging.Logger, stderr_debug: bool = False) -> str:
    """给 logger 挂文件 handler（DEBUG 常驻落盘）+ 可选 stderr DEBUG handler。

    返回日志文件绝对路径。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{log.name}.log"
    fh = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024,
                             backupCount=2, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", datefmt="%m-%d %H:%M:%S"))
    log.addHandler(fh)
    if stderr_debug:
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.DEBUG)
        sh.setFormatter(logging.Formatter(
            "%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S"))
        log.addHandler(sh)
    log.setLevel(logging.DEBUG)
    return str(path)
