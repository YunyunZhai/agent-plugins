#!/usr/bin/env python3
"""
GitHub API 封装：通过 `gh` CLI 执行 GraphQL 查询与 REST 调用。

本模块不直接发 HTTP 请求，而是调用已认证的 `gh` CLI（`gh api`），
复用用户现有的 GitHub 凭据（~/.config/gh/hosts.yml）。无第三方依赖。

用法（供其他脚本 import）:
    from github_client import GitHubClient, check_gh_available
    client = GitHubClient()
    data = client.graphql(query_str, variables)
    data = client.rest("/repos/owner/name")

环境变量:
    GH_TOKEN / GITHUB_TOKEN  - 若设置了会传给 gh，否则用 gh 的默认凭据
    GH_SEARCH_TIMEOUT        - 单次调用超时（秒，默认 60）
"""

import json
import subprocess
import sys
from typing import Any, Dict, List, Optional


class GitHubError(Exception):
    """GitHub API 调用失败（含 gh 未安装 / 未认证）"""


class GitHubClient:
    def __init__(self, timeout: Optional[int] = None):
        self.timeout = timeout or int(__import__("os").environ.get("GH_SEARCH_TIMEOUT", "60"))

    def _run_gh(self, args: List[str], stdin: Optional[str] = None) -> Dict[str, Any]:
        """执行 gh api 子命令并返回解析后的 JSON；失败抛出 GitHubError。

        瞬时网络错误（fake-ip 链路抖动导致的 timeout/reset）自动重试 3 次。
        """
        last_err: Optional[str] = None
        for attempt in range(3):
            cmd = ["gh", "api"] + args
            try:
                proc = subprocess.run(
                    cmd,
                    input=stdin,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
            except FileNotFoundError:
                raise GitHubError(
                    "未找到 `gh` 命令。请先安装 GitHub CLI：https://cli.github.com/"
                )
            except subprocess.TimeoutExpired:
                raise GitHubError(f"GitHub API 调用超时（{self.timeout}s）：{' '.join(cmd)}")

            if proc.returncode != 0:
                err = (proc.stderr or "").strip()
                # fake-ip 链路瞬断可重试；其余（401/404/422 等）直接抛出
                if ("i/o timeout" in err or "connection reset" in err.lower()
                        or "EOF" in err) and attempt < 2:
                    last_err = err
                    import time as _t
                    _t.sleep(2 ** attempt)
                    continue
                raise GitHubError(f"GitHub API 失败（exit {proc.returncode}）：{err}")

            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError:
                raise GitHubError(f"GitHub API 返回非 JSON：{proc.stdout[:200]}")
        raise GitHubError(f"GitHub API 重试耗尽：{last_err}")

    def graphql(self, query: str, variables: Optional[Dict] = None) -> Dict[str, Any]:
        """执行 GraphQL 查询。返回 data 部分；有 errors 时抛 GitHubError。"""
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        result = self._run_gh(["graphql", "--input", "-"], stdin=json.dumps(payload))
        if result.get("errors"):
            msgs = "; ".join(e.get("message", "") for e in result["errors"])
            raise GitHubError(f"GraphQL 错误：{msgs}")
        return result.get("data", {})

    def rest(self, path: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """执行 REST 调用。path 以 / 开头；params 为查询参数（显式 GET，
        否则 gh api 会把 -f 参数当 POST body 导致搜索类端点 404）。"""
        args = ["--method", "GET", path]
        if params:
            for k, v in params.items():
                args += ["-f", f"{k}={v}"]
        return self._run_gh(args)


def check_gh_available() -> Optional[str]:
    """检查 gh 是否可用并已认证。返回报错信息；正常返回 None。"""
    try:
        proc = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return "未找到 `gh` 命令。请安装 GitHub CLI：https://cli.github.com/"
    except subprocess.TimeoutExpired:
        return "检查 gh 认证超时。"
    if proc.returncode != 0:
        return "GitHub CLI 未认证。请运行 `gh auth login` 完成登录。"
    return None


if __name__ == "__main__":
    # 命令行冒烟测试：python3 github_client.py whoami
    err = check_gh_available()
    if err:
        print(f"❌ {err}", file=sys.stderr)
        sys.exit(1)
    client = GitHubClient()
    data = client.rest("/user")
    print(f"已认证用户: {data.get('login', '?')}")