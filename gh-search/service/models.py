"""Pydantic 请求/响应模型。"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Channel(str, Enum):
    keyword = "keyword"
    semantic = "semantic"
    hybrid = "hybrid"


class SearchRequest(BaseModel):
    query: str = Field(..., description="用户检索意图")
    channel: Channel = Field(Channel.keyword, description="搜索通道")
    language: Optional[str] = Field(None, description="限定编程语言")
    min_stars: int = Field(200, description="最小 star 阈值")
    top_k: int = Field(50, description="返回候选数")
    star_weight: float = Field(0.0, description="语义排序 star 先验权重，默认纯语义排序")
    enrich: bool = Field(False, description="执行成熟度指标过滤")
    readme: bool = Field(False, description="执行 README 片段增强")
    rerank: bool = Field(True, description="执行百炼 rerank 精排")


class Candidate(BaseModel):
    full_name: str
    description: Optional[str] = None
    topics: List[str] = []
    stars: int = 0
    forks: int = 0
    pushed_at: Optional[str] = None
    created_at: Optional[str] = None
    license: Optional[str] = None
    primary_language: Optional[str] = None
    is_fork: bool = False
    is_archived: bool = False
    _semantic_distance: Optional[float] = None
    _rerank_score: Optional[float] = None
    commits_30d: Optional[int] = None
    merged_prs: Optional[int] = None
    readme_snippet: Optional[str] = None


class SearchResponse(BaseModel):
    query: str
    channel: str
    candidates: int
    candidates_list: List[Dict[str, Any]]
    pipeline_steps: List[str]
    elapsed: Dict[str, float]
    note: Optional[str] = None


class HealthResponse(BaseModel):
    status: str = "ok"
    db_connected: bool = False
    repo_count: int = 0
    vector_count: int = 0


class BillingSummary(BaseModel):
    user_id: str
    period: str
    total_calls: int
    total_tokens: int
    by_channel: Dict[str, int]
