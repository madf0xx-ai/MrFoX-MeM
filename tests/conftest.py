"""Shared test fixtures.

Force deterministic, offline, dependency-free backends for the whole suite,
regardless of whether the optional accelerators (fastembed embedder / reranker)
happen to be installed on the machine running the tests. This keeps tests fast,
hermetic, and identical between local and CI. The accelerated paths are
validated separately (see the reranker/embedder smoke checks).
"""
from __future__ import annotations

import pytest

from core import embed, rerank, tokens


def _force_import_error(*_args, **_kwargs):
    raise ImportError("optional backend disabled during tests")


@pytest.fixture(autouse=True)
def hermetic_backends(monkeypatch):
    # Reset singletons, then make the fastembed backends unavailable so
    # get_embedder() -> HashingEmbedder and get_reranker() -> _NoopReranker.
    embed._EMBEDDER = None
    rerank._RERANKER = None
    monkeypatch.setattr(embed, "FastEmbedEmbedder", _force_import_error)
    monkeypatch.setattr(rerank, "FastEmbedReranker", _force_import_error)
    # Short-circuit tiktoken so token counting is the deterministic chars/4
    # fallback regardless of whether tiktoken is installed locally.
    tokens._ENC = None
    tokens._TRIED = True
    yield
    embed._EMBEDDER = None
    rerank._RERANKER = None
    tokens._TRIED = False
    tokens._ENC = None
