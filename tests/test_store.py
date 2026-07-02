"""Store tests: content-addressing, dedup, the single-active-run invariant."""
from __future__ import annotations

import pytest

from core.store import Store, pack_floats, sha256_hex, unpack_floats


def _store(tmp_path):
    return Store(str(tmp_path / "t.db"))


def test_blob_is_content_addressed_and_deduped(tmp_path):
    s = _store(tmp_path)
    h1 = s.put_blob(b"hello")
    h2 = s.put_blob(b"hello")
    assert h1 == h2 == sha256_hex(b"hello")
    assert s.get_blob(h1) == b"hello"
    s.close()


def test_float_pack_roundtrip():
    v = [0.5, -1.0, 2.25]
    assert unpack_floats(pack_floats(v)) == v
    assert unpack_floats(b"") == []


def test_event_dedup_by_content(tmp_path):
    s = _store(tmp_path)
    s.upsert_project("p", "p", "/tmp")
    id1, new1 = s.insert_event("ev_1", "p", "note", "same text")
    id2, new2 = s.insert_event("ev_2", "p", "note", "same text")
    assert new1 is True and new2 is False
    assert id1 == id2
    events = s.get_events("p", k=10)
    assert len(events) == 1
    assert events[0]["content"] == "same text"
    s.close()


def test_single_active_run_invariant(tmp_path):
    s = _store(tmp_path)
    s.create_run("run_a", "p", "session_start", label="a")
    s.create_run("run_b", "p", "user_prompt", label="b")
    active = s.get_current_active_run("p")
    assert active is not None and active["id"] == "run_b"   # newest wins
    assert s.get_run("run_a")["status"] == "done"           # prior closed
    s.close()


def test_retrieval_roundtrip_preserves_id_arrays(tmp_path):
    s = _store(tmp_path)
    s.insert_retrieval("ret_1", "p", "run_x", "mcp", "hi", ["n1", "n2"], ["e1"], 42)
    rows = s.get_retrievals("p", k=5)
    assert len(rows) == 1
    r = rows[0]
    assert r["node_ids"] == ["n1", "n2"]
    assert r["event_ids"] == ["e1"]
    assert r["token_estimate"] == 42
    s.close()


def test_embed_cache_roundtrip(tmp_path):
    s = _store(tmp_path)
    s.put_cached_embedding("h1", 3, [0.5, -0.25, 0.75])
    got = s.get_cached_embeddings(["h1", "missing"])
    assert got == {"h1": [0.5, -0.25, 0.75]}
    assert s.get_cached_embeddings([]) == {}
    s.close()


def test_delete_project_purges_everything(tmp_path):
    s = _store(tmp_path)
    s.upsert_project("p", "p", "/tmp")
    s.insert_node("n1", "p", "file", "a.py", None, None, "sum", None, "content")
    s.insert_event("ev1", "p", "note", "a secret decision")
    s.insert_retrieval("r1", "p", None, "ui", "a stored prompt", ["n1"], [], 5)
    s.create_run("run1", "p", "ui")

    s.delete_project("p")

    assert s.get_project("p") is None
    assert s.get_nodes("p") == []
    assert s.get_events("p", k=10) == []
    assert s.get_retrievals("p", k=10) == []
    assert s.get_runs("p", k=10) == []
    s.close()


def test_search_events_by_content(tmp_path):
    s = _store(tmp_path)
    s.upsert_project("p", "p", "/tmp")
    s.insert_event("ev1", "p", "decision", "We chose JWT bearer tokens for authentication")
    s.insert_event("ev2", "p", "note", "The deepfake pipeline lives in the scanner module")
    hits = s.search_events("p", "authentication tokens", kinds=["decision", "note"], k=5)
    assert any("JWT" in e["content"] for e in hits)          # found by meaning, no refs
    assert all(isinstance(e["content"], str) for e in hits)
    s.close()


def test_bulk_is_atomic_and_rolls_back_on_error(tmp_path):
    s = _store(tmp_path)
    with s.bulk():
        s.put_blob(b"kept")
    assert s.get_blob(sha256_hex(b"kept")) == b"kept"     # committed on exit

    with pytest.raises(RuntimeError):
        with s.bulk():
            s.put_blob(b"discarded")
            raise RuntimeError("boom")
    assert s.get_blob(sha256_hex(b"discarded")) is None    # rolled back
    s.close()


def test_nodes_and_embeddings_persist(tmp_path):
    s = _store(tmp_path)
    s.insert_node("n1", "p", "file", "main.py", "/p/main.py", None, "entry", None, "content")
    # Values chosen to be exactly representable in float32 so the round-trip is ==.
    s.put_embedding("n1", 3, [0.5, -0.25, 0.75])
    got = s.get_embeddings("p")
    assert got == [("n1", [0.5, -0.25, 0.75])]
    assert s.get_node("n1")["label"] == "main.py"
    s.close()
