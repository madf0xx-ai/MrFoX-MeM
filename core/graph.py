"""Graph-structural ranking over the knowledge tree.

A dependency-free, deterministic **Personalized PageRank** (power iteration) so
retrieval can exploit the code graph's structure — containment plus
imports/references edges — the way Aider's repo-map does, at zero extra
dependency and zero LLM cost.

Used by ``tree.hybrid_search`` in a HippoRAG-style expansion: vector+FTS finds
the entry-point nodes, PPR seeded on those entry points propagates importance
over the graph, and the resulting ranking is fused back in. That turns
"top-k individually-similar nodes" into "top-k structurally-connected nodes" in
a single cheap pass (no iterative LLM calls).
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

# Importance flows along edges toward the *referenced/contained* node. imports &
# references are the load-bearing semantic links (weighted up); containment is
# quiet scaffolding (weight 1). Tune here, in one place.
_DEFAULT_REL_WEIGHTS = {
    "imports": 3.0,
    "references": 3.0,
    "decided_for": 2.0,
    "contains": 1.0,
}


def personalized_pagerank(
    nodes: Iterable[str],
    edges: Iterable[tuple],
    personalization: Optional[dict[str, float]] = None,
    *,
    damping: float = 0.85,
    max_iter: int = 50,
    tol: float = 1.0e-6,
    rel_weights: Optional[dict[str, float]] = None,
) -> dict[str, float]:
    """Weighted Personalized PageRank via power iteration.

    ``nodes``: iterable of node ids. ``edges``: iterable of ``(src, dst, rel)``
    (or ``(src, dst)``, treated as ``contains``). ``personalization``: optional
    ``{node_id: weight}`` restart distribution (the "seeds"); ``None`` => uniform,
    which yields ordinary PageRank. Dangling nodes (no out-edges) redistribute
    their mass according to the personalization vector, matching networkx's
    ``dangling=personalization`` convention. Returns ``{node_id: score}`` summing
    to ~1. Deterministic: same input always yields the same ranking.
    """
    node_list = list(dict.fromkeys(nodes))            # dedup, stable order
    idx = {n: i for i, n in enumerate(node_list)}
    n = len(node_list)
    if n == 0:
        return {}
    weights = _DEFAULT_REL_WEIGHTS if rel_weights is None else rel_weights

    # Weighted out-adjacency: out[i] = [(j, w), ...]; out_w[i] = sum of weights.
    out: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    out_w = [0.0] * n
    for e in edges:
        if len(e) >= 3:
            u, v, rel = e[0], e[1], e[2]
        else:
            u, v, rel = e[0], e[1], "contains"
        if u not in idx or v not in idx or u == v:
            continue
        w = float(weights.get(rel, 1.0))
        if w <= 0.0:
            continue
        i, j = idx[u], idx[v]
        out[i].append((j, w))
        out_w[i] += w

    # Restart (personalization) distribution p, normalized to sum 1.
    p = [0.0] * n
    if personalization:
        total = 0.0
        for key, val in personalization.items():
            if key in idx and val and val > 0:
                p[idx[key]] += float(val)
                total += float(val)
        if total <= 0.0:
            p = [1.0 / n] * n
        else:
            p = [x / total for x in p]
    else:
        p = [1.0 / n] * n

    r = list(p)
    d = damping
    for _ in range(max_iter):
        # Teleport + dangling mass, both distributed per personalization.
        dangling = sum(r[i] for i in range(n) if out_w[i] == 0.0)
        nxt = [(1.0 - d) * p[i] + d * dangling * p[i] for i in range(n)]
        for i in range(n):
            ri = r[i]
            if ri == 0.0 or out_w[i] == 0.0:
                continue
            share = d * ri / out_w[i]
            for j, w in out[i]:
                nxt[j] += share * w
        diff = 0.0
        for i in range(n):
            diff += abs(nxt[i] - r[i])
        r = nxt
        if diff < tol:
            break
    return {node_list[i]: r[i] for i in range(n)}


# Cache the node/edge lists per (store, project), invalidated by the project
# version — so PPR stops re-scanning the whole node+edge tables on every query.
_GRAPH_CACHE_MAX = 4
_GRAPH_CACHE: dict[tuple[int, str], tuple[int, list, list]] = {}


def _graph_data(store: Any, project: str) -> tuple[list, list]:
    gen = store.project_version(project) if hasattr(store, "project_version") else -1
    key = (getattr(store, "uid", -1), project)
    ent = _GRAPH_CACHE.get(key)
    if ent is not None and ent[0] == gen and gen != -1:
        return ent[1], ent[2]
    nodes = [row["id"] for row in store.get_nodes(project)]
    edges = [(e["src"], e["dst"], e["rel"]) for e in store.get_edges(project)]
    if gen != -1:
        _GRAPH_CACHE[key] = (gen, nodes, edges)
        if len(_GRAPH_CACHE) > _GRAPH_CACHE_MAX:
            for k in list(_GRAPH_CACHE):
                if k != key:
                    _GRAPH_CACHE.pop(k, None)  # pop-not-del: eviction race can't KeyError
                    break
    return nodes, edges


def graph_scores(
    store: Any,
    project: str,
    seed_ids: Optional[Iterable[str]] = None,
    **kwargs: Any,
) -> dict[str, float]:
    """Run Personalized PageRank over a project's stored nodes/edges.

    ``seed_ids`` become the restart distribution (the retrieval entry points);
    with none, this is plain PageRank (global structural importance). Returns
    ``{node_id: score}``.
    """
    nodes, edges = _graph_data(store, project)
    if not nodes:
        return {}
    personalization = None
    if seed_ids:
        personalization = {nid: 1.0 for nid in seed_ids if nid}
    return personalized_pagerank(nodes, edges, personalization, **kwargs)
