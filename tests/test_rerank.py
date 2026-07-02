"""Reranker tests: graceful no-op fallback + real effect on ordering."""
from __future__ import annotations

from core import ingest as I
from core import rerank as R
from core import tree as T
from core.store import Store


def test_noop_reranker_when_backend_unavailable(monkeypatch):
    R._RERANKER = None

    def boom(*a, **k):
        raise ImportError("fastembed not installed")

    monkeypatch.setattr(R, "FastEmbedReranker", boom)
    rr = R.get_reranker()
    assert rr.backend == "none"
    assert rr.rerank("q", ["a", "b"]) is None      # None => caller keeps fusion order
    R._RERANKER = None


def test_reranker_controls_top_result(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "alpha.py").write_text('def alpha():\n    "alpha thing"\n    return 1\n')
    (proj / "beta.py").write_text('def beta():\n    "beta thing"\n    return 2\n')
    s = Store(str(tmp_path / "t.db"))
    I.ingest(s, str(proj), project="demo")

    class _FakeReranker:
        backend = "fake"

        def rerank(self, query, docs):
            # Strongly prefer any candidate mentioning "beta".
            return [1.0 if "beta" in d.lower() else 0.0 for d in docs]

    monkeypatch.setattr(T.rerank_mod, "get_reranker", lambda: _FakeReranker())

    # Query leans "alpha", but the reranker prefers "beta" -> beta must lead.
    res = T.hybrid_search(s, "demo", "alpha function", k=8, use_reranker=True)
    assert res
    assert "beta" in res[0]["label"].lower()

    # With reranking off, the reranker must not influence anything (no crash).
    res_off = T.hybrid_search(s, "demo", "alpha function", k=8, use_reranker=False)
    assert res_off
    s.close()
