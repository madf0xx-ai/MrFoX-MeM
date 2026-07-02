"""Pluggable backend registry: register / available / resolve / fallback / env."""
from __future__ import annotations

from core import backends, embed, rerank


def _boom():
    raise RuntimeError("backend failed to construct")


def test_register_and_resolve():
    @backends.register("t-emb", "fake")
    def _mk():
        class _F:
            backend = "fake"
            dim = 3

            def embed(self, texts):
                return [[0.0, 0.0, 0.0] for _ in texts]

        return _F()

    assert "fake" in backends.available("t-emb")
    inst = backends.resolve("t-emb", ["fake"])
    assert inst is not None and inst.backend == "fake"


def test_resolve_skips_failing_then_falls_back():
    backends.register("t2", "broken")(_boom)
    backends.register("t2", "ok")(lambda: "OK")
    assert backends.resolve("t2", ["broken", "ok"]) == "OK"


def test_resolve_none_when_all_fail():
    backends.register("t4", "x")(_boom)
    assert backends.resolve("t4", ["x", "missing"]) is None


def test_resolve_env_override(monkeypatch):
    backends.register("t3", "a")(lambda: "A")
    backends.register("t3", "b")(lambda: "B")
    monkeypatch.setenv("MRFOX_T3", "b")
    assert backends.resolve("t3", ["a"], env_var="MRFOX_T3") == "B"   # env wins
    monkeypatch.delenv("MRFOX_T3")
    assert backends.resolve("t3", ["a"], env_var="MRFOX_T3") == "A"   # default order


def test_core_backends_resolve_through_registry():
    embed._EMBEDDER = None
    rerank._RERANKER = None
    e = embed.get_embedder()
    r = rerank.get_reranker()
    assert e.backend in ("fastembed", "hashing") and e.dim > 0
    assert r.backend in ("fastembed", "none")
    assert "hashing" in backends.available("embedder")
    assert "none" in backends.available("reranker")


def test_protocols_are_runtime_checkable():
    assert isinstance(embed.HashingEmbedder(dim=8), backends.Embedder)
    assert isinstance(rerank._NoopReranker(), backends.Reranker)
