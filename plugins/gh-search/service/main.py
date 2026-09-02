"""FastAPI 入口：REST 搜索服务。"""

import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

# 确保 service 目录在 path 中
_SERVICE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SERVICE_DIR))

from config import get_config
from models import (
    BillingSummary,
    Channel,
    HealthResponse,
    SearchRequest,
    SearchResponse,
)
from billing import record_call, get_summary
from pipeline import run_pipeline

app = FastAPI(
    title="gh-search API",
    description="GitHub 智能开源项目搜索 REST 服务",
    version="1.0.0",
)

config = get_config()


@app.get("/api/v1/health", response_model=HealthResponse)
def health_check():
    """健康检查：验证数据库连接和索引状态。"""
    try:
        _SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "gh-search" / "scripts"
        sys.path.insert(0, str(_SCRIPTS_DIR))
        from _common.sqlite_store import connect, count_repos, count_vectors

        db_path = config.get("embedding", {}).get("db_path")
        conn = connect(db_path)
        repo_count = count_repos(conn)
        vector_count = count_vectors(conn)
        conn.close()
        return HealthResponse(
            status="ok",
            db_connected=True,
            repo_count=repo_count,
            vector_count=vector_count,
        )
    except Exception as e:
        return HealthResponse(status=f"error: {e}", db_connected=False)


@app.post("/api/v1/search", response_model=SearchResponse)
def search(
    req: SearchRequest,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    """搜索端点：按 channel 执行搜索管线。"""
    user_id = x_user_id or "anonymous"
    t0 = __import__("time").time()

    try:
        result = run_pipeline(
            query=req.query,
            channel=req.channel.value,
            language=req.language,
            min_stars=req.min_stars,
            top_k=req.top_k,
            star_weight=req.star_weight,
            do_enrich=req.enrich,
            do_readme=req.readme,
            do_rerank=req.rerank,
            backend=config.get("embedding", {}).get("backend", "local"),
            db_path=config.get("embedding", {}).get("db_path"),
        )
        elapsed = __import__("time").time() - t0

        # 记费
        record_call(
            user_id=user_id,
            channel=req.channel.value,
            candidates=result["candidates"],
            elapsed_s=elapsed,
            db_path=config.get("billing", {}).get("db_path"),
        )

        return SearchResponse(**result)
    except Exception as e:
        elapsed = __import__("time").time() - t0
        record_call(
            user_id=user_id,
            channel=req.channel.value,
            error=True,
            elapsed_s=elapsed,
            db_path=config.get("billing", {}).get("db_path"),
        )
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/billing/summary", response_model=BillingSummary)
def billing_summary(
    user_id: str,
    period: str,
):
    """查询用户某月的用量汇总。"""
    result = get_summary(
        user_id=user_id,
        period=period,
        db_path=config.get("billing", {}).get("db_path"),
    )
    return BillingSummary(**result)
