"""The retrieval-quality eval harness must run and produce sane metrics."""
from __future__ import annotations

from scripts.eval import run_eval


def test_run_eval_small(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "auth.py").write_text('def login(user):\n    "authenticate a user"\n    return True\n')
    (proj / "search.py").write_text('def query(text):\n    "run a search query"\n    return []\n')
    queries = [
        {"q": "how does a user authenticate and log in", "relevant": ["auth.py", "login"]},
        {"q": "run a search query over text", "relevant": ["search.py", "query"]},
    ]
    res = run_eval(str(proj), queries, k=8, db_path=str(tmp_path / "eval.db"))

    assert res["n_queries"] == 2
    assert set(res["levels"]) == {"base (vec+FTS+RRF)", "+graph (PageRank)", "+rerank (cross-enc)"}
    for level in res["levels"].values():
        assert 0.0 <= level["hit_at_k"] <= 1.0
        assert 0.0 <= level["recall_at_k"] <= 1.0
        assert 0.0 <= level["mrr"] <= 1.0
        assert level["latency_ms"] >= 0.0
