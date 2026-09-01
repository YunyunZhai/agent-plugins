#!/usr/bin/env python3
"""
第 4 步：深度模式可选增强 —— 批量拉取并截断 README。

仅当用户开启【深度语义匹配】开关时执行。对第 3 步的小集合（20-60 条）
批量拉取 README.md，做文本截断（不拿全文），降低 token 与网络开销。

用法:
    python3 fetch_readme.py --input step3.json
    python3 fetch_readme.py --repos "owner/name,owner/name2"
    python3 fetch_readme.py --input step3.json --max-chars 2000

输出: 在每条候选记录上附加 readme_snippet 字段。
"""

import argparse
import json
import re
import sys
from typing import Any, Dict, Optional
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _common.github_client import GitHubClient

DEFAULT_MAX_CHARS = 2000
DEFAULT_KEEP_HEAD = 1200   # 保留 README 开头多少字符
DEFAULT_KEEP_TAIL = 300    # 末尾再保留多少字符
BATCH_SIZE = 10            # README 内容较长，10 条一批避免响应体过大

# 常见 README 文件名，按优先级
README_CANDIDATES = ["README.md", "README", "readme.md", "Readme.md",
                     "README.MD", "README.rst", "README.txt"]


def _strip_markdown(text: str) -> str:
    """轻量清理：去掉图片/链接占位、代码块围栏，压空白。"""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)        # 图片
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)     # 链接→文字
    text = re.sub(r"```[\s\S]*?```", "", text)               # 代码块
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _truncate(text: str, max_chars: int, head: int, tail: int) -> str:
    """截断：保留开头 head 字符 + 末尾 tail 字符，中间省略。"""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    if head + tail >= len(text):
        return text[:max_chars]
    return text[:head] + f"\n\n…[已截断，省略 {len(text) - head - tail} 字符]…\n\n" + text[-tail:]


def _fetch_readme(client: GitHubClient, full_name: str, branch: Optional[str]) -> Optional[str]:
    """拉取单个仓库 README 并截断；失败返回 None。"""
    owner, _, name = full_name.partition("/")
    if not branch:
        branch = "HEAD"
    for fname in README_CANDIDATES:
        expr = f"{branch}:{fname}"
        try:
            data = client.graphql(
                "query($o: String!, $n: String!, $e: String!) "
                "{ r: repository(owner: $o, name: $n) "
                "{ object(expression: $e) { ... on Blob { text } } } }",
                {"o": owner, "n": name, "e": expr},
            )
        except Exception:  # noqa: BLE001
            continue
        blob = data.get("r", {}).get("object")
        if blob and blob.get("text"):
            return blob["text"]
    return None


def _build_readme_query(alias: str, owner: str, name: str, branch: str) -> str:
    """为单个仓库构造 README 查询片段（GraphQL 别名）。"""
    expr = f"{branch}:README.md"
    return f"""
{alias}: repository(owner: "{owner}", name: "{name}") {{
  object(expression: "{expr}") {{ ... on Blob {{ text }} }}
}}"""


def _batch_fetch_readme(
    client: GitHubClient,
    batch: list[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    """批量拉取 README（10 条一批）；失败时逐个重试并跳过不存在的仓库。"""
    fragments = []
    aliases = []
    for j, r in enumerate(batch):
        alias = f"r{j}"
        owner, _, name = r["full_name"].partition("/")
        branch = r.get("default_branch") or "HEAD"
        aliases.append(alias)
        fragments.append(_build_readme_query(alias, owner, name, branch))
    gql = "query { " + " ".join(fragments) + " }"
    try:
        data = client.graphql(gql)
    except Exception:  # noqa: BLE001
        # 批量失败 → 逐个重试，跳过不存在/无权限的仓库
        for r in batch:
            snippet = _fetch_readme(client, r["full_name"], r.get("default_branch"))
            if snippet:
                r["readme_snippet"] = _truncate(
                    _strip_markdown(snippet),
                    DEFAULT_MAX_CHARS, DEFAULT_KEEP_HEAD, DEFAULT_KEEP_TAIL,
                )
        return batch
    # 批量成功 → 提取结果；README.md 为 null 的仓库逐个重试其他文件名
    for alias, r in zip(aliases, batch):
        blob = (data.get(alias) or {}).get("object")
        if blob and blob.get("text"):
            r["readme_snippet"] = _truncate(
                _strip_markdown(blob["text"]),
                DEFAULT_MAX_CHARS, DEFAULT_KEEP_HEAD, DEFAULT_KEEP_TAIL,
            )
        else:
            # README.md 不存在 → 逐个尝试其他文件名
            snippet = _fetch_readme(client, r["full_name"], r.get("default_branch"))
            if snippet:
                r["readme_snippet"] = _truncate(
                    _strip_markdown(snippet),
                    DEFAULT_MAX_CHARS, DEFAULT_KEEP_HEAD, DEFAULT_KEEP_TAIL,
                )
    return batch


def enrich(client: GitHubClient, repos: list[Dict[str, Any]],
           max_chars: int, head: int, tail: int) -> list[Dict[str, Any]]:
    """为每条候选附加截断后的 README 片段（10 条一批 GraphQL 调用）。"""
    global DEFAULT_MAX_CHARS, DEFAULT_KEEP_HEAD, DEFAULT_KEEP_TAIL
    DEFAULT_MAX_CHARS, DEFAULT_KEEP_HEAD, DEFAULT_KEEP_TAIL = max_chars, head, tail
    for i in range(0, len(repos), BATCH_SIZE):
        batch = repos[i:i + BATCH_SIZE]
        _batch_fetch_readme(client, batch)
    return repos


def _load_repos(input_path: Optional[str], repos_arg: Optional[str]) -> list[Dict[str, Any]]:
    if repos_arg:
        return [{"full_name": n.strip()} for n in repos_arg.split(",") if n.strip()]
    if input_path:
        with open(input_path) as f:
            return json.load(f).get("results", [])
    raise SystemExit("需要 --input 或 --repos 参数")


def main() -> None:
    parser = argparse.ArgumentParser(description="第4步：深度模式 README 片段")
    parser.add_argument("--input", default=None, help="第3步输出的 JSON")
    parser.add_argument("--repos", default=None, help="逗号分隔仓库名，便于调试")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS,
                        help=f"README 片段最大字符数（默认 {DEFAULT_MAX_CHARS}）")
    parser.add_argument("--head", type=int, default=DEFAULT_KEEP_HEAD,
                        help=f"保留开头字符数（默认 {DEFAULT_KEEP_HEAD}）")
    parser.add_argument("--tail", type=int, default=DEFAULT_KEEP_TAIL,
                        help=f"保留末尾字符数（默认 {DEFAULT_KEEP_TAIL}）")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = parser.parse_args()

    repos = _load_repos(args.input, args.repos)
    if not repos:
        print("⚠️ 输入为空，无 README 可拉取。", file=sys.stderr)
        sys.exit(0)

    client = GitHubClient()
    enriched = enrich(client, repos, args.max_chars, args.head, args.tail)
    fetched = sum(1 for r in enriched if r.get("readme_snippet"))

    result = {"input": len(repos), "readme_fetched": fetched, "results": enriched}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Step4: 输入 {len(repos)} 条 → 拉取到 README {fetched} 条")
        for r in enriched:
            snip = r.get("readme_snippet", "")
            print(f"  {r['full_name']} README片段 {len(snip)} 字符"
                  f"{' | ' + snip[:60].replace(chr(10),' ') if snip else ''}")


if __name__ == "__main__":
    main()