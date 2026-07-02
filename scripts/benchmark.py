#!/usr/bin/env python3
"""MrFoX-MeM benchmark — honest, local, deterministic.

Ingests a project, runs representative queries through the token-bounded
``/relevant`` assembly, and reports how many tokens that slice injects versus
naively reading the *whole* files the retrieval touched (a realistic "the agent
would otherwise open these files" baseline), plus retrieval latency and graph
size. No network, no API tokens — the numbers are reproducible on any machine.

Usage:
    python scripts/benchmark.py --path /abs/project [--budget 1200]
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import tempfile
import time

# Import the package whether run from the repo root or elsewhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import embed, rerank  # noqa: E402
from core import ingest as ingest_mod  # noqa: E402
from core import tree as tree_mod  # noqa: E402
from core.store import Store  # noqa: E402

DEFAULT_QUERIES = [
    "how is hybrid retrieval fusion computed",
    "where are secrets redacted during ingestion",
    "how does the graph pagerank ranking work",
    "what tools does the MCP server expose",
    "how is the token budget enforced on injected context",
    "how are references edges built between files",
]


def _file_hash_for(store: Store, node: dict) -> str | None:
    """Content hash of the file a retrieved node belongs to (self or parent)."""
    row = store.get_node(node["id"])
    if row is None:
        return None
    if row["content_hash"]:
        return row["content_hash"]
    parent = row["parent"]
    if parent:
        prow = store.get_node(parent)
        if prow is not None and prow["content_hash"]:
            return prow["content_hash"]
    return None


def _full_file_tokens(store: Store, nodes: list[dict]) -> int:
    """Tokens if the agent instead read every distinct touched file in full."""
    seen: set[str] = set()
    total = 0
    for n in nodes:
        h = _file_hash_for(store, n)
        if not h or h in seen:
            continue
        seen.add(h)
        blob = store.get_blob(h)
        if blob:
            total += tree_mod.est_tokens(blob.decode("utf-8", "replace"))
    return total


def run_benchmark(path: str, project: str = "benchmark", budget: int = 1200,
                  queries: list[str] | None = None, db_path: str | None = None) -> dict:
    queries = queries or DEFAULT_QUERIES
    tmpdir = None
    if db_path is None:
        tmpdir = tempfile.mkdtemp(prefix="mrfox-bench-")
        db_path = os.path.join(tmpdir, "bench.db")

    store = Store(db_path)
    t0 = time.time()
    ing = ingest_mod.ingest(store, path, project=project)
    ingest_secs = time.time() - t0

    rows: list[dict] = []
    for q in queries:
        t = time.time()
        res = tree_mod.relevant(store, project, q, budget_tokens=budget)
        latency_ms = (time.time() - t) * 1000.0
        mem_tokens = res["token_estimate"]
        full_tokens = _full_file_tokens(store, res["nodes"])
        savings = (1.0 - mem_tokens / full_tokens) if full_tokens else 0.0
        rows.append({
            "query": q,
            "mem_tokens": mem_tokens,
            "full_tokens": full_tokens,
            "savings": savings,
            "latency_ms": latency_ms,
            "nodes": len(res["nodes"]),
        })

    store.close()
    savings_vals = [r["savings"] for r in rows if r["full_tokens"]]
    summary = {
        "project": ing.project,
        "embed_backend": embed.get_embedder().backend,
        "reranker_backend": rerank.get_reranker().backend,
        "nodes": ing.nodes,
        "edges": ing.edges,
        "files": ing.files,
        "ingest_secs": ingest_secs,
        "avg_savings": statistics.mean(savings_vals) if savings_vals else 0.0,
        "avg_latency_ms": statistics.mean(r["latency_ms"] for r in rows),
        "avg_mem_tokens": statistics.mean(r["mem_tokens"] for r in rows),
        "budget": budget,
        "rows": rows,
    }
    return summary


def _print_markdown(s: dict) -> None:
    print(f"# MrFoX-MeM benchmark — {s['project']}\n")
    print(f"- embed backend: **{s['embed_backend']}**, reranker: **{s['reranker_backend']}**")
    print(f"- graph: **{s['nodes']} nodes / {s['edges']} edges** from {s['files']} files "
          f"(ingest {s['ingest_secs']:.2f}s)")
    print(f"- budget: {s['budget']} tokens\n")
    print("| query | injected tok | full-files tok | savings | latency |")
    print("|---|--:|--:|--:|--:|")
    for r in s["rows"]:
        print(f"| {r['query'][:44]} | {r['mem_tokens']} | {r['full_tokens']} | "
              f"{r['savings']*100:.0f}% | {r['latency_ms']:.1f}ms |")
    print(f"\n**Average: {s['avg_savings']*100:.0f}% fewer tokens injected "
          f"({s['avg_mem_tokens']:.0f} vs full files), {s['avg_latency_ms']:.1f}ms/query.**")


def main() -> int:
    ap = argparse.ArgumentParser(description="MrFoX-MeM local benchmark")
    ap.add_argument("--path", default=os.getcwd(), help="project dir to ingest")
    ap.add_argument("--project", default="benchmark")
    ap.add_argument("--budget", type=int, default=1200)
    args = ap.parse_args()
    if not os.path.isdir(args.path):
        print(f"error: not a directory: {args.path}", file=sys.stderr)
        return 2
    _print_markdown(run_benchmark(os.path.abspath(args.path), args.project, args.budget))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
