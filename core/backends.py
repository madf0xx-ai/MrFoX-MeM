"""Pluggable backend registry.

vLLM's cleanest exportable pattern, scaled down: register an implementation by
name behind a stable Protocol, then ``resolve()`` picks the first one that
actually constructs — honoring an env override, else a default preference order,
else nothing (caller supplies a hard fallback). New embedders, rerankers, vector
indexes, or language parsers become drop-ins that need **zero core edits**;
third parties register the same way.

Design mirrors vLLM's "entry-point plugins + capability selection + graceful
degradation": selection is "does it build?" (a capability predicate), not a
hard-coded import.

    @register("embedder", "fastembed")
    def _mk():
        return FastEmbedEmbedder()

    emb = resolve("embedder", ["fastembed", "hashing"], env_var="MRFOX_EMBEDDER")
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional, Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Turns text into fixed-dim vectors. ``backend``/``dim`` are introspectable."""

    backend: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


@runtime_checkable
class Reranker(Protocol):
    """Re-scores (query, docs). Returns per-doc scores, or None to keep order."""

    backend: str

    def rerank(self, query: str, docs: list[str]) -> Optional[list[float]]:
        ...


# kind ("embedder"/"reranker"/...) -> {name -> zero-arg factory}
_REGISTRIES: dict[str, dict[str, Callable[[], Any]]] = {}


def register(kind: str, name: str) -> Callable[[Callable[[], Any]], Callable[[], Any]]:
    """Decorator: register a zero-arg factory for ``name`` under ``kind``."""
    reg = _REGISTRIES.setdefault(kind, {})

    def deco(factory: Callable[[], Any]) -> Callable[[], Any]:
        reg[name] = factory
        return factory

    return deco


def available(kind: str) -> list[str]:
    """Names registered under a kind (for /health, docs, diagnostics)."""
    return list(_REGISTRIES.get(kind, {}))


def resolve(
    kind: str,
    default_order: list[str],
    env_var: Optional[str] = None,
) -> Optional[Any]:
    """Build the first backend that constructs without error.

    Order = env override (comma-separated names in ``env_var``) first, then
    ``default_order``. A factory that raises is skipped (graceful degradation).
    Returns the instance, or None if every candidate failed / none registered.
    """
    reg = _REGISTRIES.get(kind, {})
    order: list[str] = []
    if env_var:
        raw = os.environ.get(env_var, "").strip()
        if raw:
            order = [n.strip() for n in raw.split(",") if n.strip()]
    for name in default_order:
        if name not in order:
            order.append(name)
    for name in order:
        factory = reg.get(name)
        if factory is None:
            continue
        try:
            return factory()
        except Exception:
            continue
    return None
