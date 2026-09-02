"""gh-search REST 服务端到端测试。

真实启动 service.main:app（uvicorn 子进程），通过 httpx 驱动 HTTP 断言。
"""

import os
import time

import httpx
import pytest


def _dashscope_ready() -> bool:
    return bool(
        os.environ.get("DASHSCOPE_API_KEY")
        and os.environ.get("DASHSCOPE_BASE_URL")
    )


def _current_period() -> str:
    return time.strftime("%Y-%m")


def test_health(server_url):
    r = httpx.get(f"{server_url}/api/v1/health", timeout=30.0)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db_connected"] is True


def test_keyword_search(server_url):
    r = httpx.post(
        f"{server_url}/api/v1/search",
        json={"query": "python", "channel": "keyword", "top_k": 5},
        headers={"X-User-Id": "e2e-keyword"},
        timeout=60.0,
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["candidates_list"], list)
    assert body["candidates"] == len(body["candidates_list"])


def test_semantic_search(server_url):
    if not _dashscope_ready():
        pytest.skip("缺少 DASHSCOPE_API_KEY / DASHSCOPE_BASE_URL，跳过语义通道真路径")

    r = httpx.post(
        f"{server_url}/api/v1/search",
        json={"query": "启动快的编码智能体", "channel": "semantic", "top_k": 5},
        headers={"X-User-Id": "e2e-semantic"},
        timeout=120.0,
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["candidates_list"], list)
    assert body["channel"] == "semantic"


def test_hybrid_search(server_url):
    r = httpx.post(
        f"{server_url}/api/v1/search",
        json={"query": "python http framework", "channel": "hybrid", "top_k": 5},
        headers={"X-User-Id": "e2e-hybrid"},
        timeout=120.0,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["channel"] == "hybrid"
    assert isinstance(body["candidates_list"], list)
    assert body["candidates"] == len(body["candidates_list"])


def test_full_pipeline(server_url):
    # 用高 star + 小 top_k 限定候选集，控制 enrich/readme 的网络开销
    r = httpx.post(
        f"{server_url}/api/v1/search",
        json={
            "query": "tensorflow",
            "channel": "keyword",
            "min_stars": 10000,
            "top_k": 3,
            "enrich": True,
            "readme": True,
            "rerank": True,
        },
        headers={"X-User-Id": "e2e-pipeline"},
        timeout=180.0,
    )
    assert r.status_code == 200
    steps = r.json()["pipeline_steps"]
    assert "recall(keyword)" in steps
    assert "enrich" in steps
    assert "readme" in steps
    assert "rerank" in steps


def test_default_pipeline(server_url):
    r = httpx.post(
        f"{server_url}/api/v1/search",
        json={"query": "python", "channel": "keyword", "top_k": 5},
        headers={"X-User-Id": "e2e-default"},
        timeout=60.0,
    )
    assert r.status_code == 200
    assert r.json()["pipeline_steps"] == ["recall(keyword)"]


def test_billing_summary(server_url):
    user_id = f"e2e-billing-{int(time.time())}"
    httpx.post(
        f"{server_url}/api/v1/search",
        json={"query": "python", "channel": "keyword", "top_k": 3},
        headers={"X-User-Id": user_id},
        timeout=60.0,
    )
    r = httpx.get(
        f"{server_url}/api/v1/billing/summary",
        params={"user_id": user_id, "period": _current_period()},
        timeout=30.0,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_calls"] > 0


def test_invalid_channel(server_url):
    r = httpx.post(
        f"{server_url}/api/v1/search",
        json={"query": "python", "channel": "invalid"},
        headers={"X-User-Id": "e2e-invalid"},
        timeout=30.0,
    )
    assert r.status_code == 422
