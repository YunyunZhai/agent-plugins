#!/usr/bin/env python3
"""
火山方舟 Agent Plan chat 封装（OpenAI 兼容接口，stdlib 实现）。

供查询改写（中英翻译）、候选重排等轻量 LLM 任务使用。
套餐内 doubao-seed-2.0-mini 抵扣系数最低，适合高频小请求。

用法:
    from ark_client import ArkChat
    ark = ArkChat()
    text = ark.chat("把下面意图翻译成英文关键词: 低延迟编程智能体")

环境变量:
    ARK_API_KEY   - 方舟 API Key（必需）
    ARK_BASE_URL  - 默认 https://ark.cn-beijing.volces.com/api/plan/v3
    ARK_CHAT_MODEL - 默认 doubao-seed-2.0-mini
"""

import json
import os
import socket
import sys
import time
import urllib.request
from typing import List, Optional


def _force_ipv4() -> None:
    """过滤 getaddrinfo 结果只留 IPv4。

    本机代理/VPN 的 fake-IP DNS 可能返回不可达地址族（Errno 97），导致长跑进程
    间歇性全部请求超时/失败而新进程碰巧正常。国内云 API 均有 IPv4，强制之。
    """
    _orig = socket.getaddrinfo

    def patched(*args, **kwargs):
        return [r for r in _orig(*args, **kwargs) if r[0] == socket.AF_INET]

    socket.getaddrinfo = patched


class ArkError(RuntimeError):
    """方舟调用失败"""


class ArkChat:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 model: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ARK_API_KEY", "")
        self.base_url = (base_url or os.environ.get("ARK_BASE_URL", "")
                         or "https://ark.cn-beijing.volces.com/api/plan/v3").rstrip("/")
        self.model = model or os.environ.get("ARK_CHAT_MODEL", "doubao-seed-2.0-mini")
        if not self.api_key:
            raise ArkError("未设置 ARK_API_KEY 环境变量")

    def chat(self, prompt: str, system: str = "", max_tokens: int = 512,
             json_mode: bool = False, model: Optional[str] = None) -> str:
        """单轮对话。json_mode=True 时强制输出 JSON 对象。"""
        messages: List[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return self._post("/chat/completions", body)["choices"][0]["message"]["content"]

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            return json.load(urllib.request.urlopen(req, timeout=120))
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:200]
            raise ArkError(f"方舟 HTTP {e.code}: {detail}")
        except Exception as e:
            raise ArkError(f"方舟请求失败: {e}")


class ArkEmbed:
    """向量化客户端。doubao-embedding-vision, 单请求上限 10 条。

    AFP 计费: 输入 token × 0.335 / 10000（≤32k 输入档）。
    内置 429 退避与瞬断重试，批量间限速。
    """

    BATCH = 10               # Embeddings API input limit: max 10

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 model: Optional[str] = None):
        if os.environ.get("ARK_FORCE_IPV4", "1") != "0":
            _force_ipv4()
        self.api_key = api_key or os.environ.get("ARK_API_KEY", "").split(",")[0].strip()
        self.base_url = (base_url or os.environ.get("ARK_BASE_URL", "")
                         or "https://ark.cn-beijing.volces.com/api/plan/v3").rstrip("/")
        self.model = model or os.environ.get("ARK_EMBED_MODEL", "doubao-embedding-vision")
        self._key_idx = 0  # 用于多 key 轮换追踪
        if not self.api_key:
            raise ArkError("未设置 ARK_API_KEY 环境变量")

    def embed(self, texts: List[str]) -> List[List[float]]:
        """批量向量化；自动按 10 条分批，返回与输入等长的向量列表。"""
        trace = os.environ.get("ARK_TRACE") == "1"
        vecs: List[List[float]] = []
        # 方舟API单条文本最大100000字节，超长截断
        MAX_BYTES = 95000
        for i in range(0, len(texts), self.BATCH):
            chunk = texts[i:i + self.BATCH]
            chunk = [t[:MAX_BYTES] if len(t.encode()) > MAX_BYTES else t for t in chunk]
            req_body = {"model": self.model, "input": chunk}
            t0 = time.time()
            for attempt in range(3):
                try:
                    r = self._post_raw("/embeddings", req_body)
                    break
                except ArkError as e:
                    err = str(e)
                    if "429" in err:
                        if "AccountQuotaExceeded" in err:
                            # 月配额用完，不可恢复，直接抛出由外层跳过此 key
                            raise
                        # AccountRateLimitExceeded，限速，等待后重试
                        if attempt < 2:
                            if trace:
                                print(f"[ark] chunk{i}: attempt{attempt} err={str(e)[:60]} wait{attempt*3}s",
                                      file=sys.stderr)
                            time.sleep(3 * (attempt + 1))
                            continue
                    raise
            dt = time.time() - t0
            if trace:
                print(f"[ark] chunk{i}: {len(chunk)}条 {r['usage']['total_tokens']}tok {dt:.2f}s",
                      file=sys.stderr)
            vecs += [d["embedding"] for d in r["data"]]
            time.sleep(0.05)
        return vecs

    def _post_raw(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            return json.load(urllib.request.urlopen(req, timeout=120))
        except urllib.error.HTTPError as e:
            raise ArkError(f"HTTP {e.code}: {e.read().decode()[:200]}")
        except Exception as e:
            raise ArkError(f"请求失败: {e}")


if __name__ == "__main__":
    ark = ArkChat()
    print(ark.chat("回复'OK'两个字母即可"))
