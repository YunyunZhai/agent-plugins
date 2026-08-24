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
import urllib.request
from typing import List, Optional


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
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            r = json.load(urllib.request.urlopen(req, timeout=60))
        except Exception as e:
            raise ArkError(f"方舟 chat 失败: {e}")
        try:
            return r["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise ArkError(f"方舟响应格式异常: {str(r)[:200]}")


if __name__ == "__main__":
    ark = ArkChat()
    print(ark.chat("回复'OK'两个字母即可"))
