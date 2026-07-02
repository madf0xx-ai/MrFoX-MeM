"""The benchmark harness must run and produce sane, budget-respecting numbers."""
from __future__ import annotations

from scripts.benchmark import run_benchmark


def test_run_benchmark_small(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text(
        'def alpha():\n    "does alpha work"\n    return beta()\n'
        "def beta():\n    return 1\n"
    )
    (proj / "b.md").write_text("# Notes\nsome notes about alpha and beta\n")

    s = run_benchmark(
        str(proj),
        project="demo",
        budget=400,
        queries=["how does alpha work"],
        db_path=str(tmp_path / "bench.db"),
    )
    assert s["nodes"] > 0
    assert s["rows"], "expected at least one query row"
    assert s["rows"][0]["mem_tokens"] <= 400          # slice honors the budget
    assert s["avg_latency_ms"] >= 0.0
    # Savings can be negative on a trivially small corpus (the fixed injection
    # wrapper exceeds two tiny files) — it's meaningful only at real scale, where
    # it lands ~90%. Here we just assert it's a computed fraction bounded above.
    assert s["avg_savings"] <= 1.0
