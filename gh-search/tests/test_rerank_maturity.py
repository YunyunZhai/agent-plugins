import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from search.rerank_results import apply_maturity_rerank, compute_repo_maturity


def test_compute_repo_maturity_handles_official_fields():
    repo = {
        "full_name": "example/healthy",
        "stargazers_count": 1500,
        "watchers_count": 120,
        "forks_count": 90,
        "subscribers_count": 30,
        "open_issues_count": 8,
        "pushed_at": "2026-08-20T00:00:00Z",
        "archived": False,
    }

    score = compute_repo_maturity(repo)
    assert 0.0 <= score <= 1.0
    assert score > 0.0


def test_apply_maturity_rerank_prefers_more_mature_project_when_rerank_is_tied():
    candidates = [
        {
            "full_name": "example/old",
            "_rerank_score": 0.90,
            "stargazers_count": 10,
            "watchers_count": 2,
            "forks_count": 1,
            "subscribers_count": 1,
            "open_issues_count": 3,
            "pushed_at": "2020-01-01T00:00:00Z",
            "archived": False,
        },
        {
            "full_name": "example/active",
            "_rerank_score": 0.90,
            "stargazers_count": 1500,
            "watchers_count": 120,
            "forks_count": 90,
            "subscribers_count": 30,
            "open_issues_count": 8,
            "pushed_at": "2026-08-20T00:00:00Z",
            "archived": False,
        },
    ]

    ranked = apply_maturity_rerank(candidates)
    assert ranked[0]["full_name"] == "example/active"


def test_apply_maturity_rerank_penalizes_archived_projects():
    candidates = [
        {
            "full_name": "example/archived",
            "_rerank_score": 0.95,
            "stargazers_count": 5000,
            "watchers_count": 30,
            "forks_count": 25,
            "subscribers_count": 15,
            "open_issues_count": 2,
            "pushed_at": "2022-01-01T00:00:00Z",
            "archived": True,
        },
        {
            "full_name": "example/fresh",
            "_rerank_score": 0.90,
            "stargazers_count": 800,
            "watchers_count": 25,
            "forks_count": 12,
            "subscribers_count": 5,
            "open_issues_count": 4,
            "pushed_at": "2026-08-20T00:00:00Z",
            "archived": False,
        },
    ]

    ranked = apply_maturity_rerank(candidates)
    assert ranked[0]["full_name"] == "example/fresh"
