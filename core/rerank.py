"""Optional local cross-encoder reranker.

When ``fastembed>=0.4`` is installed, the top fused candidates are re-scored with
``Xenova/ms-marco-MiniLM-L-6-v2`` (ONNX/CPU, no torch) — the single biggest
retrieval-quality lever (Anthropic's contextual-retrieval writeup attributes the
49%→67% failure-rate reduction to reranking). When it is absent, ``rerank``
returns ``None`` and the caller keeps the fusion order — so retrieval always
works fully offline with zero extra dependencies.

Mirrors ``core/embed.py``: a lazily-built singleton with a graceful fallback.
"""
from __future__ import annotations

from typing import List, Optional

from . import backends

# Compact, permissive (Apache-2.0), CPU-friendly cross-encoder in fastembed's
# supported set. int8/onnx; ~tens of ms for a few dozen (query, doc) pairs.
_DEFAULT_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"


class _NoopReranker:
    """Fallback used when no reranker backend is available."""

    backend = "none"
    model_name = None

    def rerank(self, query: str, docs: List[str]) -> Optional[List[float]]:
        return None  # signal "no reranking" so the caller keeps fusion order


class FastEmbedReranker:
    """Wrapper over fastembed's TextCrossEncoder."""

    backend = "fastembed"

    def __init__(self, model_name: str = _DEFAULT_MODEL):
        from fastembed.rerank.cross_encoder import TextCrossEncoder  # type: ignore

        self._model = TextCrossEncoder(model_name=model_name)
        self.model_name = model_name

    def rerank(self, query: str, docs: List[str]) -> Optional[List[float]]:
        if not docs:
            return []
        # TextCrossEncoder.rerank yields one relevance score per doc, in order.
        return [float(s) for s in self._model.rerank(query, list(docs))]


@backends.register("reranker", "fastembed")
def _make_fastembed_reranker():
    return FastEmbedReranker()


@backends.register("reranker", "none")
def _make_noop_reranker():
    return _NoopReranker()


_RERANKER = None


def get_reranker(prefer_fastembed: bool = True):
    """Return a singleton reranker, resolved via the pluggable registry.

    Order: env ``MRFOX_RERANKER`` first, then the default (fastembed → none).
    The no-op reranker is the guaranteed fallback, so retrieval never breaks.
    """
    global _RERANKER
    if _RERANKER is not None:
        return _RERANKER
    order = ["fastembed", "none"] if prefer_fastembed else ["none"]
    _RERANKER = backends.resolve("reranker", order, env_var="MRFOX_RERANKER") or _NoopReranker()
    return _RERANKER
