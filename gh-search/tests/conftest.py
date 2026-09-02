"""端到端测试共享 fixture。

通过真实 uvicorn 子进程启动 service.main:app，测试通过 httpx 驱动 HTTP 断言。
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

GH_SEARCH_ROOT = Path(__file__).resolve().parent.parent
SERVICE_DIR = GH_SEARCH_ROOT / "service"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _dashscope_ready() -> bool:
    return bool(
        os.environ.get("DASHSCOPE_API_KEY")
        and os.environ.get("DASHSCOPE_BASE_URL")
    )


@pytest.fixture(scope="session")
def server_url():
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env.setdefault("GH_SEARCH_BACKEND", "dashscope")
    env.setdefault("GH_SEARCH_DB", str(GH_SEARCH_ROOT / "data" / "gh_search_qwen.db"))

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "service.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(GH_SEARCH_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        _wait_ready(base_url, proc)
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _wait_ready(base_url: str, proc: subprocess.Popen) -> None:
    deadline = time.monotonic() + 60
    last_err = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            _, stderr = proc.communicate()
            raise RuntimeError(
                f"uvicorn 启动失败（exit={proc.returncode}）: "
                f"{stderr.decode(errors='replace')[-2000:]}"
            )
        try:
            r = httpx.get(f"{base_url}/api/v1/health", timeout=2.0)
            if r.status_code == 200:
                return
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(0.5)
    raise RuntimeError(f"等待服务就绪超时: {last_err}")
