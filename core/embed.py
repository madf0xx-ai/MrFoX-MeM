"""Pluggable local embeddings for MrFoX-MeM.

Backend A: ``fastembed`` with ``BAAI/bge-small-en-v1.5`` (384d) when importable.
Backend B: a dependency-free deterministic hashing bag-of-tokens (256d),
L2-normalized. The fallback guarantees the system runs fully offline with zero
external dependencies and zero API tokens.

Single interface::

    embedder = get_embedder()
    vecs = embedder.embed(["hello", "world"])  # list[list[float]]
    embedder.dim       # int
    embedder.backend   # "fastembed" | "hashing"
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import List

from . import backends

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


class HashingEmbedder:
    """Deterministic hashing bag-of-tokens embedder.

    Each token is hashed to a bucket (and a sign) via SHA-256; counts are
    accumulated and the resulting vector is L2-normalized. No model, no network,
    fully deterministic across runs and machines.
    """

    backend = "hashing"

    def __init__(self, dim: int = 256):
        self.dim = dim

    def _embed_one(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        for tok in _tokenize(text):
            h = hashlib.sha256(tok.encode("utf-8")).digest()
            bucket = int.from_bytes(h[:4], "little") % self.dim
            sign = 1.0 if (h[4] & 1) else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [self._embed_one(t) for t in texts]


class FastEmbedEmbedder:
    """Wrapper over fastembed's BAAI/bge-small-en-v1.5 (384d)."""

    backend = "fastembed"

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        from fastembed import TextEmbedding  # type: ignore

        self._model = TextEmbedding(model_name=model_name)
        self.dim = 384
        # Confirm dimensionality from a probe so we never lie in /health.
        try:
            probe = list(self._model.embed(["probe"]))
            if probe:
                self.dim = len(probe[0])
        except Exception:
            pass

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        out = []
        for v in self._model.embed(list(texts)):
            out.append([float(x) for x in v])
        return out


# Register both backends so they are selectable by name (env MRFOX_EMBEDDER) and
# discoverable via backends.available("embedder"). Factories reference the module
# names at call time so tests can monkeypatch FastEmbedEmbedder to force fallback.
@backends.register("embedder", "fastembed")
def _make_fastembed_embedder():
    return FastEmbedEmbedder()


@backends.register("embedder", "hashing")
def _make_hashing_embedder():
    return HashingEmbedder()


_EMBEDDER = None


def get_embedder(prefer_fastembed: bool = True):
    """Return a singleton embedder, resolved via the pluggable registry.

    Order: env ``MRFOX_EMBEDDER`` (comma-separated) first, then the default
    preference (fastembed → hashing). Any backend that fails to construct is
    skipped; the dependency-free hashing embedder is the guaranteed fallback.
    """
    global _EMBEDDER
    if _EMBEDDER is not None:
        return _EMBEDDER
    order = ["fastembed", "hashing"] if prefer_fastembed else ["hashing"]
    _EMBEDDER = backends.resolve("embedder", order, env_var="MRFOX_EMBEDDER") or HashingEmbedder()
    return _EMBEDDER


def embed(texts: List[str]) -> List[List[float]]:
    """Module-level convenience matching the contract interface."""
    return get_embedder().embed(texts)
