"""Embedder unit tests: determinism, normalization, offline fallback."""
from __future__ import annotations

import math

from core import embed as E


def test_hashing_deterministic_and_l2_normalized():
    e = E.HashingEmbedder(dim=64)
    a = e.embed(["hello world"])[0]
    b = e.embed(["hello world"])[0]
    assert a == b                     # deterministic across calls
    assert len(a) == 64
    norm = math.sqrt(sum(x * x for x in a))
    assert abs(norm - 1.0) < 1e-9     # unit vector


def test_hashing_empty_text_is_zero_vector():
    e = E.HashingEmbedder(dim=32)
    v = e.embed([""])[0]
    assert v == [0.0] * 32            # no tokens -> norm 0 -> left as zeros


def test_hashing_distinguishes_texts():
    e = E.HashingEmbedder(dim=128)
    a, b = e.embed(["authentication token refresh", "quicksort pivot partition"])
    assert a != b


def test_get_embedder_is_singleton_and_offline_safe():
    emb = E.get_embedder()
    assert emb is E.get_embedder()               # cached singleton
    assert emb.backend in ("fastembed", "hashing")
    assert emb.dim > 0
    assert E.embed([]) == []                      # module convenience, empty in/out
