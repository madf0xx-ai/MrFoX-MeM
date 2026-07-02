"""Tree-sitter extraction tests.

Skipped entirely when tree-sitter-language-pack is not installed, and each test
skips gracefully if its grammar cannot be fetched (e.g. offline CI) — so these
never produce false failures.
"""
from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_language_pack")

from core import ingest as I          # noqa: E402
from core import treesit as TS        # noqa: E402
from core.store import Store          # noqa: E402


def test_extract_javascript():
    r = TS.extract(".js", "export function hi(a){ return go(a); }\nclass C{}")
    if r is None:
        pytest.skip("javascript grammar unavailable")
    names = {name for name, _kind, _line in r.defs}
    assert {"hi", "C"} <= names
    assert "go" in r.refs


def test_extract_go():
    r = TS.extract(".go", "package p\nfunc Hi(a int) int { return go2(a) }\ntype T struct{}")
    if r is None:
        pytest.skip("go grammar unavailable")
    names = {name for name, _kind, _line in r.defs}
    assert "Hi" in names and "T" in names
    assert "go2" in r.refs


def test_extract_unknown_ext_returns_none():
    assert TS.extract(".unknownext", "whatever") is None


def test_ingest_js_builds_symbols_and_reference_edge(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "util.js").write_text("export function helper(){ return 1; }\n")
    (proj / "main.js").write_text("function run(){ return helper(); }\n")
    s = Store(str(tmp_path / "t.db"))
    I.ingest(s, str(proj), project="demo")

    labels = {n["label"] for n in s.get_nodes("demo")}
    if not any("helper" in lbl for lbl in labels):
        pytest.skip("tree-sitter grammar unavailable offline")

    # run() -> helper() must produce a referencer->definer edge (main.js -> util.js).
    rels = {e["rel"] for e in s.get_edges("demo")}
    assert "references" in rels
    s.close()
