"""Personalized PageRank tests, validated against hand-computed fixpoints."""
from __future__ import annotations

from core import graph as G
from core.store import Store


def test_empty_graph():
    assert G.personalized_pagerank([], []) == {}


def test_scores_sum_to_one():
    r = G.personalized_pagerank(["a", "b", "c"], [("a", "b", "imports")])
    assert abs(sum(r.values()) - 1.0) < 1e-6


def test_two_node_chain_uniform():
    # A -> B, uniform restart, damping 0.85. Solved analytically:
    #   a = 0.075 + 0.425*b,  a + b = 1  ->  a ~= 0.3509, b ~= 0.6491.
    r = G.personalized_pagerank(["A", "B"], [("A", "B", "imports")])
    assert r["B"] > r["A"]                       # importance flows to the referenced node
    assert abs(r["A"] - 0.3509) < 0.01
    assert abs(r["B"] - 0.6491) < 0.01


def test_personalization_biases_toward_seed():
    # Same A -> B but restart concentrated on A:
    #   a = 0.15 + 0.85*b,  b = 0.85*a,  a + b = 1  ->  a ~= 0.5405, b ~= 0.4595.
    r = G.personalized_pagerank(
        ["A", "B"], [("A", "B", "imports")], personalization={"A": 1.0}
    )
    assert r["A"] > r["B"]
    assert abs(r["A"] - 0.5405) < 0.01


def test_hub_ranks_highest():
    # A, B, C all import H -> H is the structural hub.
    edges = [("A", "H", "imports"), ("B", "H", "imports"), ("C", "H", "imports")]
    r = G.personalized_pagerank(["A", "B", "C", "H"], edges)
    assert max(r, key=r.get) == "H"


def test_deterministic():
    edges = [("a", "b", "imports"), ("b", "c", "references"), ("c", "a", "contains")]
    r1 = G.personalized_pagerank(["a", "b", "c"], edges, personalization={"a": 1.0})
    r2 = G.personalized_pagerank(["a", "b", "c"], edges, personalization={"a": 1.0})
    assert r1 == r2


def test_unknown_and_self_edges_ignored():
    # self-loop and an edge to a non-existent node must not crash or leak mass.
    r = G.personalized_pagerank(
        ["a", "b"], [("a", "a", "imports"), ("a", "zzz", "imports"), ("a", "b", "imports")]
    )
    assert set(r) == {"a", "b"}
    assert abs(sum(r.values()) - 1.0) < 1e-6


def test_graph_scores_over_store(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    for nid in ("a", "b", "h"):
        s.insert_node(nid, "demo", "module", nid, None, None, "", None, "")
    s.insert_edge("e1", "demo", "a", "h", "imports")
    s.insert_edge("e2", "demo", "b", "h", "imports")
    scores = G.graph_scores(s, "demo", seed_ids=["a"])
    assert set(scores) == {"a", "b", "h"}
    assert scores["h"] > scores["b"]            # h is imported; b only imports
    s.close()
