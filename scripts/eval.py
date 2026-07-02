#!/usr/bin/env python3
"""MrFoX-MeM retrieval-quality eval — hit@k / recall@k / MRR with per-stage ablation.

The token-savings benchmark (scripts/benchmark.py) answers "how lean is the
slice"; this answers the harder, more important question: "did it retrieve the
RIGHT things?" — the metric vLLM-grade credibility actually rests on.

It ingests a repo, runs a labeled query set (eval/queries.json) through the
retrieval pipeline at several ablation levels, and reports hit@k / recall@k /
MRR per level so each stage's contribution (RRF base → +graph PageRank →
+cross-encoder rerank) is measurable — not asserted.

A retrieved node counts as relevant if any of a query's `relevant` substrings
appears (case-insensitively) in the node's label or path.

Usage:
    python scripts/eval.py [--path .] [--k 8] [--queries eval/queries.json]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import embed, rerank  # noqa: E402
from core import ingest as ingest_mod  # noqa: E402
from core import tree as tree_mod  # noqa: E402
from core.store import Store  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Ablation levels: name -> hybrid_search kwargs. Each adds one stage so the
# marginal contribution of graph expansion and reranking is visible.
_ABLATIONS = [
    ("base (vec+FTS+RRF)", {"graph_weight": 0.0, "use_reranker": False}),
    ("+graph (PageRank)", {"graph_weight": 0.5, "use_reranker": False}),
    ("+rerank (cross-enc)", {"graph_weight": 0.5, "use_reranker": True}),
]


def _score_query(results: list[dict], relevant: list[str], k: int) -> tuple[float, float, float]:
    """Return (hit@k, reciprocal_rank, recall@k) for one query's top-k results."""
    subs = [s.lower() for s in relevant]
    matched: set[str] = set()
    first_rank = 0
    for i, r in enumerate(results[:k]):
        hay = (str(r.get("label", "")) + " " + str(r.get("path", ""))).lower()
        hit_here = False
        for s in subs:
            if s in hay:
                matched.add(s)
                hit_here = True
        if hit_here and first_rank == 0:
            first_rank = i + 1
    hit = 1.0 if first_rank else 0.0
    rr = (1.0 / first_rank) if first_rank else 0.0
    recall = (len(matched) / len(subs)) if subs else 0.0
    return hit, rr, recall


def run_eval(path: str, queries: list[dict], k: int = 8, db_path: str | None = None) -> dict:
    tmpdir = None
    if db_path is None:
        tmpdir = tempfile.mkdtemp(prefix="mrfox-eval-")
        db_path = os.path.join(tmpdir, "eval.db")

    store = Store(db_path)
    ingest_mod.ingest(store, path, project="eval")

    levels: dict[str, dict] = {}
    for name, kwargs in _ABLATIONS:
        hits, rrs, recalls, lat = [], [], [], []
        for item in queries:
            q = item["q"]
            rel = item.get("relevant", [])
            t = time.time()
            results = tree_mod.hybrid_search(store, "eval", q, k=k, **kwargs)
            lat.append((time.time() - t) * 1000.0)
            h, rr, rc = _score_query(results, rel, k)
            hits.append(h)
            rrs.append(rr)
            recalls.append(rc)
        levels[name] = {
            "hit_at_k": statistics.mean(hits),
            "mrr": statistics.mean(rrs),
            "recall_at_k": statistics.mean(recalls),
            "latency_ms": statistics.mean(lat),
        }
    store.close()
    return {
        "k": k,
        "n_queries": len(queries),
        "embed_backend": embed.get_embedder().backend,
        "reranker_backend": rerank.get_reranker().backend,
        "levels": levels,
    }


def _print_markdown(res: dict) -> None:
    print(f"# MrFoX-MeM retrieval eval — {res['n_queries']} queries @ k={res['k']}\n")
    print(f"- embed backend: **{res['embed_backend']}**, reranker: **{res['reranker_backend']}**\n")
    print("| pipeline level | hit@k | recall@k | MRR | latency |")
    print("|---|--:|--:|--:|--:|")
    for name, m in res["levels"].items():
        print(f"| {name} | {m['hit_at_k']*100:.0f}% | {m['recall_at_k']*100:.0f}% | "
              f"{m['mrr']:.3f} | {m['latency_ms']:.1f}ms |")
    best = max(res["levels"].values(), key=lambda m: (m["hit_at_k"], m["mrr"]))
    print(f"\n**Best: {best['hit_at_k']*100:.0f}% hit@{res['k']}, MRR {best['mrr']:.3f}.**")


def _load_queries(qpath: str) -> list[dict]:
    with open(qpath, "r", encoding="utf-8") as fh:
        return json.load(fh).get("queries", [])


def main() -> int:
    ap = argparse.ArgumentParser(description="MrFoX-MeM retrieval-quality eval")
    ap.add_argument("--path", default=_REPO, help="repo to ingest (default: this repo)")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--queries", default=os.path.join(_REPO, "eval", "queries.json"))
    ap.add_argument("--out", default=os.path.join(_REPO, "eval", "results.json"))
    args = ap.parse_args()

    queries = _load_queries(args.queries)
    res = run_eval(os.path.abspath(args.path), queries, args.k)
    _print_markdown(res)
    try:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2)
        print(f"\n(results written to {os.path.relpath(args.out, _REPO)})")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
