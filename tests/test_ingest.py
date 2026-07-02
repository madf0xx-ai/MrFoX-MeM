"""Ingest tests: path safety, secret redaction, static parsing (never exec)."""
from __future__ import annotations

import os

import pytest

from core import ingest as I
from core.store import Store


def test_slugify():
    assert I.slugify("My Project!") == "my-project"
    assert I.slugify("   ") == "project"


def test_embed_text_prepends_structural_context():
    assert I._embed_text("core/tree.py", "module: tree.py", "") == "core/tree.py\nmodule: tree.py"
    assert I._embed_text("", "   ", "x") == "x"
    assert I._embed_text() == ""


def test_validate_path_rejects_bad_inputs(tmp_path):
    with pytest.raises(ValueError):
        I.validate_ingest_path("relative/path")            # not absolute
    with pytest.raises(ValueError):
        I.validate_ingest_path(str(tmp_path / "missing"))  # does not exist
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(ValueError):
        I.validate_ingest_path(str(f))                     # not a directory
    d = tmp_path / "proj"
    d.mkdir()
    assert I.validate_ingest_path(str(d)) == os.path.realpath(str(d))


def test_allowed_roots_confinement(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("MRFOX_ALLOWED_ROOTS", str(other))
    with pytest.raises(ValueError):
        I.validate_ingest_path(str(proj))                  # outside allow-list
    monkeypatch.setenv("MRFOX_ALLOWED_ROOTS", str(tmp_path))
    assert I.validate_ingest_path(str(proj))               # inside allow-list


def test_secret_content_scan():
    assert I._scan_secret_content("AKIA" + "A" * 16)
    assert I._scan_secret_content("-----BEGIN RSA PRIVATE KEY-----")
    assert not I._scan_secret_content("def add(a, b): return a + b")


def test_parse_python_extracts_symbols_and_imports():
    src = (
        "import os\n"
        "from sys import path\n"
        "def foo():\n"
        '    "docstring"\n'
        "    return 1\n"
        "class Bar:\n"
        "    pass\n"
    )
    _summary, syms, imps = I.parse_python(src)
    names = {s.name for s in syms}
    assert {"foo", "Bar"} <= names
    assert "os" in imps and "sys" in imps


def test_secret_scan_bounded_on_long_line():
    # ~600KB single line of dotted tokens: the line-length guard must skip it so
    # the URL-credential regex cannot spin (ReDoS/DoS). If unbounded, this hangs.
    assert I._scan_secret_content("a" + ".b" * 300_000) is False


def test_parse_python_tolerates_pathological_nesting():
    # Deeply nested source raises RecursionError inside ast.parse; it must be
    # caught so one crafted file can't abort the whole ingest transaction.
    src = "x = " + "1+" * 80_000 + "1\n"
    summary, syms, imps = I.parse_python(src)
    assert isinstance(syms, list) and isinstance(imps, list)
    assert isinstance(I.python_call_refs(src), list)


def test_looks_binary_on_null_bytes(tmp_path):
    p = tmp_path / "blob.bin"
    p.write_bytes(b"\x00\x01\x02text")
    assert I._looks_binary(str(p))


def test_ingest_redacts_secret_files(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "main.py").write_text('def hello():\n    return "hi"\n')
    (proj / ".env").write_text("SECRET_KEY=supersecretvalue1234567890\n")
    s = Store(str(tmp_path / "t.db"))
    res = I.ingest(s, str(proj), project="demo")
    assert res.files >= 2
    env_nodes = [n for n in s.get_nodes("demo") if n["label"] == ".env"]
    assert env_nodes, "the .env file should still be represented as a node"
    assert env_nodes[0]["summary"] == I.REDACTION           # raw secret never stored
    s.close()


def test_reingest_reuses_cached_embeddings(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("def foo():\n    return 1\n")
    s = Store(str(tmp_path / "t.db"))
    r1 = I.ingest(s, str(proj), project="demo")
    assert r1.reused == 0                       # cold cache: nothing to reuse
    r2 = I.ingest(s, str(proj), project="demo")
    assert r2.reused > 0                          # unchanged content -> vectors reused
    s.close()


def test_reference_edges_skip_common_identifiers(tmp_path):
    # 'run' is a ubiquitous name -> must NOT be resolved into a references edge.
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("def run():\n    return 1\n")
    (proj / "b.py").write_text("def go():\n    return run()\n")
    s = Store(str(tmp_path / "t.db"))
    I.ingest(s, str(proj), project="demo")
    rels = [e["rel"] for e in s.get_edges("demo")]
    assert "references" not in rels
    s.close()


def test_reference_edges_link_distinctive_names(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "util.py").write_text("def compute_hmac_signature():\n    return 1\n")
    (proj / "main.py").write_text("def handler():\n    return compute_hmac_signature()\n")
    s = Store(str(tmp_path / "t.db"))
    I.ingest(s, str(proj), project="demo")
    ref_edges = [e for e in s.get_edges("demo") if e["rel"] == "references"]
    assert ref_edges, "a distinctive cross-file call should create a references edge"
    s.close()


def test_ingest_excludes_are_honored(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "keep.py").write_text("x = 1\n")
    skip = proj / "skipme"
    skip.mkdir()
    (skip / "hidden.py").write_text("y = 2\n")
    s = Store(str(tmp_path / "t.db"))
    I.ingest(s, str(proj), project="demo", exclude=["skipme"])
    labels = {n["label"] for n in s.get_nodes("demo")}
    assert "keep.py" in labels
    assert "hidden.py" not in labels
    s.close()
