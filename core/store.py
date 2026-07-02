"""SQLite store for MrFoX-MeM.

WAL-mode SQLite with a content-addressed blob table, FTS5 mirrors, and fully
parameterized CRUD. No string-built SQL anywhere in this module.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import struct
import threading
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_CORE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.abspath(os.path.join(_CORE_DIR, "..", "data", "mrfox.db"))


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pack_floats(vec: Iterable[float]) -> bytes:
    floats = list(vec)
    return struct.pack("<%df" % len(floats), *floats)


def unpack_floats(blob: bytes) -> list[float]:
    n = len(blob) // 4
    if n == 0:
        return []
    return list(struct.unpack("<%df" % n, blob))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS project (
    id      TEXT PRIMARY KEY,
    name    TEXT,
    root    TEXT,
    created TEXT
);

CREATE TABLE IF NOT EXISTS node (
    id           TEXT PRIMARY KEY,
    project      TEXT,
    kind         TEXT,
    label        TEXT,
    path         TEXT,
    parent       TEXT,
    summary      TEXT,
    content_hash TEXT,
    created      TEXT
);
CREATE INDEX IF NOT EXISTS idx_node_project ON node(project);
CREATE INDEX IF NOT EXISTS idx_node_parent  ON node(parent);

CREATE TABLE IF NOT EXISTS edge (
    id      TEXT PRIMARY KEY,
    project TEXT,
    src     TEXT,
    dst     TEXT,
    rel     TEXT
);
CREATE INDEX IF NOT EXISTS idx_edge_project ON edge(project);
CREATE INDEX IF NOT EXISTS idx_edge_src     ON edge(src);

CREATE TABLE IF NOT EXISTS blob (
    hash  TEXT PRIMARY KEY,
    bytes BLOB
);

CREATE TABLE IF NOT EXISTS event (
    id           TEXT PRIMARY KEY,
    project      TEXT,
    kind         TEXT,
    content_hash TEXT,
    ts           TEXT,
    meta_json    TEXT,
    run_id       TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_project ON event(project);
-- NB: the idx_event_run index is created in _migrate(), AFTER the run_id column
-- is ensured — a legacy DB reaches _SCHEMA before the column is added.

CREATE TABLE IF NOT EXISTS embedding (
    node_id TEXT PRIMARY KEY,
    dim     INTEGER,
    vec     BLOB
);

-- Content-addressed embedding cache (incremental re-index). Keyed by a hash of
-- (backend, dim, embed_text) so a re-ingest reuses vectors for unchanged content
-- and only re-embeds what actually changed — the expensive part. Cross-project
-- and NOT wiped by clear_project(); a model/backend change yields new keys.
CREATE TABLE IF NOT EXISTS embed_cache (
    text_hash TEXT PRIMARY KEY,
    dim       INTEGER,
    vec       BLOB
);

-- A run = one conversation/session. Invariant: at most ONE 'active' run per
-- project (enforced in create_run, which closes prior active runs first).
CREATE TABLE IF NOT EXISTS run (
    id        TEXT PRIMARY KEY,
    project   TEXT,
    source    TEXT,
    label     TEXT,
    status    TEXT,
    started   TEXT,
    updated   TEXT,
    meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_project ON run(project);

-- A retrieval = one /relevant fetch. node_ids/event_ids are JSON arrays of the
-- ids that fetch injected, so each row is self-describing for the live feed.
CREATE TABLE IF NOT EXISTS retrieval (
    id             TEXT PRIMARY KEY,
    project        TEXT,
    run_id         TEXT,
    source         TEXT,
    prompt         TEXT,
    node_ids       TEXT,
    event_ids      TEXT,
    token_estimate INTEGER,
    ts             TEXT
);
CREATE INDEX IF NOT EXISTS idx_retrieval_project ON retrieval(project);
CREATE INDEX IF NOT EXISTS idx_retrieval_run     ON retrieval(run_id);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS fts_node USING fts5(
    node_id UNINDEXED, project UNINDEXED, label, summary, content
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts_event USING fts5(
    event_id UNINDEXED, project UNINDEXED, content
);
"""


class Store:
    """Lock-serialized wrapper around a single shared SQLite connection."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = os.path.abspath(db_path or _DEFAULT_DB)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.RLock()
        # When True, per-write commits are suppressed so a bulk operation (e.g. a
        # whole ingest) commits ONCE at the end instead of thousands of times.
        self._defer_commit = False
        self.fts_enabled = True
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    # -- setup -----------------------------------------------------------
    def _init_db(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute("PRAGMA foreign_keys=ON;")
            cur.executescript(_SCHEMA)
            self._migrate(cur)
            try:
                cur.executescript(_FTS_SCHEMA)
            except sqlite3.OperationalError:
                # FTS5 not compiled in; degrade gracefully.
                self.fts_enabled = False
            self._commit()

    def _migrate(self, cur: sqlite3.Cursor) -> None:
        """Apply additive schema migrations to pre-existing databases.

        SQLite has no "ADD COLUMN IF NOT EXISTS", and re-running an ALTER that
        adds an existing column raises. So we probe PRAGMA table_info and add the
        column only when it is missing — keeping startup idempotent whether the
        DB is brand new (column already present via _SCHEMA) or predates it.
        """
        event_cols = {r["name"] for r in cur.execute("PRAGMA table_info(event)").fetchall()}
        if "run_id" not in event_cols:
            # Nullable so historical events (saved before runs existed) stay valid.
            cur.execute("ALTER TABLE event ADD COLUMN run_id TEXT")
        # Index lives here (not in _SCHEMA) so it is only built once the column
        # is guaranteed — _SCHEMA executes before this on a legacy upgrade.
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_run ON event(run_id)")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _commit(self) -> None:
        """Commit unless a bulk() block is deferring commits to its end."""
        if not self._defer_commit:
            self._conn.commit()

    @contextlib.contextmanager
    def bulk(self):
        """Group many writes into a single transaction/commit.

        Every write method funnels through ``_commit()``; inside this block those
        per-write commits are suppressed and a single commit runs on exit (or a
        rollback on error). Turns an ingest's thousands of fsync-ing commits into
        one — the dominant ingest speed win. The connection lock is held for the
        duration so the batch is atomic for the (single-user) caller.
        """
        with self._lock:
            prev = self._defer_commit
            self._defer_commit = True
            try:
                yield self
                self._defer_commit = prev
                self._conn.commit()
            except Exception:
                self._defer_commit = prev
                self._conn.rollback()
                raise

    # -- blob ------------------------------------------------------------
    def put_blob(self, data: bytes) -> str:
        """Content-addressed store: write once, dedup by hash."""
        h = sha256_hex(data)
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO blob(hash, bytes) VALUES (?, ?)", (h, data)
            )
            self._commit()
        return h

    def get_blob(self, h: str) -> Optional[bytes]:
        with self._lock:
            row = self._conn.execute(
                "SELECT bytes FROM blob WHERE hash = ?", (h,)
            ).fetchone()
        return bytes(row["bytes"]) if row else None

    # -- project ---------------------------------------------------------
    def upsert_project(self, pid: str, name: str, root: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO project(id, name, root, created) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, root=excluded.root",
                (pid, name, root, utcnow()),
            )
            self._commit()

    def get_project(self, pid: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM project WHERE id = ?", (pid,)
            ).fetchone()

    def list_projects(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM project ORDER BY created DESC"
            ).fetchall()

    def clear_project(self, pid: str) -> None:
        """Make /ingest idempotent: drop all rows for a project."""
        with self._lock:
            node_ids = [
                r["id"]
                for r in self._conn.execute(
                    "SELECT id FROM node WHERE project = ?", (pid,)
                ).fetchall()
            ]
            self._conn.execute("DELETE FROM node WHERE project = ?", (pid,))
            self._conn.execute("DELETE FROM edge WHERE project = ?", (pid,))
            for nid in node_ids:
                self._conn.execute(
                    "DELETE FROM embedding WHERE node_id = ?", (nid,)
                )
            if self.fts_enabled:
                self._conn.execute(
                    "DELETE FROM fts_node WHERE project = ?", (pid,)
                )
            self._commit()

    def delete_project(self, pid: str) -> None:
        """Fully remove a project: nodes, edges, embeddings, FTS, events, runs,
        retrievals, and the project row (the privacy/right-to-delete path).

        Content-addressed blobs (possibly shared across projects) are left in the
        blob store; the embed_cache is content-keyed and also retained.
        """
        self.clear_project(pid)  # nodes / edges / embeddings / fts_node
        with self._lock:
            self._conn.execute("DELETE FROM event WHERE project = ?", (pid,))
            if self.fts_enabled:
                self._conn.execute("DELETE FROM fts_event WHERE project = ?", (pid,))
            self._conn.execute("DELETE FROM retrieval WHERE project = ?", (pid,))
            self._conn.execute("DELETE FROM run WHERE project = ?", (pid,))
            self._conn.execute("DELETE FROM project WHERE id = ?", (pid,))
            self._commit()

    # -- node ------------------------------------------------------------
    def insert_node(
        self,
        node_id: str,
        project: str,
        kind: str,
        label: str,
        path: Optional[str],
        parent: Optional[str],
        summary: str,
        content_hash: Optional[str],
        content: str = "",
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO node"
                "(id, project, kind, label, path, parent, summary, content_hash, created)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    node_id,
                    project,
                    kind,
                    label,
                    path,
                    parent,
                    summary,
                    content_hash,
                    utcnow(),
                ),
            )
            if self.fts_enabled:
                self._conn.execute(
                    "INSERT INTO fts_node(node_id, project, label, summary, content)"
                    " VALUES (?,?,?,?,?)",
                    (node_id, project, label or "", summary or "", content or ""),
                )
            self._commit()

    def get_nodes(self, project: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM node WHERE project = ?", (project,)
            ).fetchall()

    def get_node(self, node_id: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM node WHERE id = ?", (node_id,)
            ).fetchone()

    # -- edge ------------------------------------------------------------
    def insert_edge(
        self, edge_id: str, project: str, src: str, dst: str, rel: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO edge(id, project, src, dst, rel)"
                " VALUES (?,?,?,?,?)",
                (edge_id, project, src, dst, rel),
            )
            self._commit()

    def get_edges(self, project: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM edge WHERE project = ?", (project,)
            ).fetchall()

    # -- embedding -------------------------------------------------------
    def put_embedding(self, node_id: str, dim: int, vec: list[float]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO embedding(node_id, dim, vec) VALUES (?,?,?)",
                (node_id, dim, pack_floats(vec)),
            )
            self._commit()

    def get_embeddings(self, project: str) -> list[tuple[str, list[float]]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT e.node_id AS node_id, e.vec AS vec FROM embedding e "
                "JOIN node n ON n.id = e.node_id WHERE n.project = ?",
                (project,),
            ).fetchall()
        return [(r["node_id"], unpack_floats(bytes(r["vec"]))) for r in rows]

    def get_cached_embeddings(self, keys: list[str]) -> dict[str, list[float]]:
        """Look up cached vectors by content hash (for incremental re-index)."""
        out: dict[str, list[float]] = {}
        if not keys:
            return out
        with self._lock:
            for i in range(0, len(keys), 900):  # bound SQLite variable count
                chunk = keys[i : i + 900]
                placeholders = ",".join("?" * len(chunk))
                rows = self._conn.execute(
                    f"SELECT text_hash, vec FROM embed_cache WHERE text_hash IN ({placeholders})",
                    chunk,
                ).fetchall()
                for r in rows:
                    out[r["text_hash"]] = unpack_floats(bytes(r["vec"]))
        return out

    def put_cached_embedding(self, text_hash: str, dim: int, vec: list[float]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO embed_cache(text_hash, dim, vec) VALUES (?,?,?)",
                (text_hash, dim, pack_floats(vec)),
            )
            self._commit()

    # -- event -----------------------------------------------------------
    def insert_event(
        self,
        event_id: str,
        project: str,
        kind: str,
        content: str,
        meta: Optional[dict[str, Any]] = None,
        run_id: Optional[str] = None,
    ) -> tuple[str, bool]:
        """Content-addressed, dedup. Returns (id, created_new).

        ``run_id`` ties the saved decision/note to a conversation run; it may be
        None when no run is active. On a content dedup hit we return the prior
        event unchanged (it keeps whatever run first recorded it).
        """
        content_hash = self.put_blob(content.encode("utf-8"))
        meta_json = json.dumps(meta or {})
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM event WHERE project = ? AND kind = ? AND content_hash = ?",
                (project, kind, content_hash),
            ).fetchone()
            if existing:
                return existing["id"], False
            self._conn.execute(
                "INSERT INTO event(id, project, kind, content_hash, ts, meta_json, run_id)"
                " VALUES (?,?,?,?,?,?,?)",
                (event_id, project, kind, content_hash, utcnow(), meta_json, run_id),
            )
            if self.fts_enabled:
                self._conn.execute(
                    "INSERT INTO fts_event(event_id, project, content) VALUES (?,?,?)",
                    (event_id, project, content),
                )
            self._commit()
        return event_id, True

    def _event_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        blob = self.get_blob(row["content_hash"]) if row["content_hash"] else None
        content = blob.decode("utf-8", "replace") if blob else ""
        try:
            meta = json.loads(row["meta_json"] or "{}")
        except (ValueError, TypeError):
            meta = {}
        return {
            "id": row["id"],
            "kind": row["kind"],
            "content": content,
            "ts": row["ts"],
            "refs": meta.get("refs", []),
        }

    def get_events(self, project: str, k: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM event WHERE project = ? ORDER BY ts DESC LIMIT ?",
                (project, int(k)),
            ).fetchall()
        return [self._event_to_dict(r) for r in rows]

    def get_events_referencing(
        self, project: str, node_ids: list[str], kinds: Optional[list[str]] = None, k: int = 10
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM event WHERE project = ? ORDER BY ts DESC", (project,)
            ).fetchall()
        target = set(node_ids)
        out: list[dict[str, Any]] = []
        for r in rows:
            ev = self._event_to_dict(r)
            if kinds and ev["kind"] not in kinds:
                continue
            if target and not (set(ev["refs"]) & target):
                continue
            out.append(ev)
            if len(out) >= k:
                break
        return out

    # -- run -------------------------------------------------------------
    def mark_active_runs_done(self, project: str) -> None:
        """Close any 'active' runs for a project (status -> 'done').

        The building block for the "at most one active run per project"
        invariant: callers flip the prior run closed before opening a new one.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE run SET status = 'done', updated = ? "
                "WHERE project = ? AND status = 'active'",
                (utcnow(), project),
            )
            self._commit()

    def create_run(
        self,
        run_id: str,
        project: str,
        source: str,
        label: Optional[str] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> str:
        """Open a new active run, closing any prior active run for the project.

        Closing the old run and inserting the new one happen under one lock so
        the single-active-run invariant cannot be straddled by a concurrent
        start. Returns the ``started`` timestamp.
        """
        now = utcnow()
        meta_json = json.dumps(meta or {})
        with self._lock:
            self._conn.execute(
                "UPDATE run SET status = 'done', updated = ? "
                "WHERE project = ? AND status = 'active'",
                (now, project),
            )
            self._conn.execute(
                "INSERT INTO run"
                "(id, project, source, label, status, started, updated, meta_json)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (run_id, project, source, label, "active", now, now, meta_json),
            )
            self._commit()
        return now

    def get_runs(self, project: str, k: int = 20) -> list[sqlite3.Row]:
        """Runs for a project, newest first, with retrieval/event counts.

        Counts come from correlated subqueries so the feed/timeline renders in
        one round-trip instead of N follow-up queries.
        """
        with self._lock:
            return self._conn.execute(
                "SELECT r.*, "
                "(SELECT COUNT(*) FROM retrieval x WHERE x.run_id = r.id) AS retrieval_count, "
                "(SELECT COUNT(*) FROM event e WHERE e.run_id = r.id) AS event_count "
                "FROM run r WHERE r.project = ? ORDER BY r.started DESC LIMIT ?",
                (project, int(k)),
            ).fetchall()

    def get_run(self, run_id: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM run WHERE id = ?", (run_id,)
            ).fetchone()

    def get_current_active_run(self, project: str) -> Optional[sqlite3.Row]:
        """Resolve the project's single active run, if any.

        The invariant guarantees at most one, but we ORDER BY started DESC so the
        result stays deterministic (newest wins) even if that were ever violated.
        """
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM run WHERE project = ? AND status = 'active' "
                "ORDER BY started DESC LIMIT 1",
                (project,),
            ).fetchone()

    # -- retrieval -------------------------------------------------------
    def insert_retrieval(
        self,
        retrieval_id: str,
        project: str,
        run_id: Optional[str],
        source: str,
        prompt: str,
        node_ids: list[str],
        event_ids: list[str],
        token_estimate: int,
    ) -> str:
        """Log one /relevant fetch and exactly what it injected.

        node_ids/event_ids are JSON-encoded into single TEXT columns. ``run_id``
        may be None: a fetch with no active run is still recorded for the feed —
        we never auto-create a run here. Returns the row's ``ts``.
        """
        ts = utcnow()
        with self._lock:
            self._conn.execute(
                "INSERT INTO retrieval"
                "(id, project, run_id, source, prompt, node_ids, event_ids, token_estimate, ts)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    retrieval_id,
                    project,
                    run_id,
                    source,
                    prompt,
                    json.dumps(node_ids or []),
                    json.dumps(event_ids or []),
                    int(token_estimate),
                    ts,
                ),
            )
            self._commit()
        return ts

    def _retrieval_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            node_ids = json.loads(row["node_ids"] or "[]")
        except (ValueError, TypeError):
            node_ids = []
        try:
            event_ids = json.loads(row["event_ids"] or "[]")
        except (ValueError, TypeError):
            event_ids = []
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "source": row["source"],
            "prompt": row["prompt"],
            "token_estimate": row["token_estimate"],
            "ts": row["ts"],
            "node_ids": node_ids,
            "event_ids": event_ids,
        }

    def get_retrievals(self, project: str, k: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM retrieval WHERE project = ? ORDER BY ts DESC LIMIT ?",
                (project, int(k)),
            ).fetchall()
        return [self._retrieval_to_dict(r) for r in rows]

    def get_run_retrievals(self, run_id: str) -> list[dict[str, Any]]:
        """Retrievals belonging to a run, oldest first (for the step timeline)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM retrieval WHERE run_id = ? ORDER BY ts ASC", (run_id,)
            ).fetchall()
        return [self._retrieval_to_dict(r) for r in rows]

    def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        """Events belonging to a run, oldest first (for the step timeline)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM event WHERE run_id = ? ORDER BY ts ASC", (run_id,)
            ).fetchall()
        return [self._event_to_dict(r) for r in rows]

    # -- FTS search ------------------------------------------------------
    def fts_search_nodes(
        self, project: str, query: str, limit: int = 20
    ) -> list[tuple[str, float]]:
        if not self.fts_enabled or not query.strip():
            return []
        match = _fts_query(query)
        if not match:
            return []
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT node_id, rank FROM fts_node "
                    "WHERE project = ? AND fts_node MATCH ? ORDER BY rank LIMIT ?",
                    (project, match, int(limit)),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        # bm25 rank: lower = better. Convert to a positive score.
        return [(r["node_id"], -float(r["rank"])) for r in rows]

    def search_events(
        self, project: str, query: str, kinds: Optional[list[str]] = None, k: int = 6
    ) -> list[dict[str, Any]]:
        """Lexically search event content (fts_event) so free-text notes/decisions
        surface by MEANING — not only when manually ref-tagged to a hit node.
        Returns event dicts in bm25-rank order."""
        if not self.fts_enabled or not query.strip():
            return []
        match = _fts_query(query)
        if not match:
            return []
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT event_id FROM fts_event "
                    "WHERE project = ? AND fts_event MATCH ? ORDER BY rank LIMIT ?",
                    (project, match, int(max(k * 4, 12))),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
            ids = [r["event_id"] for r in rows]
            if not ids:
                return []
            placeholders = ",".join("?" * len(ids))
            erows = self._conn.execute(
                f"SELECT * FROM event WHERE id IN ({placeholders})", ids
            ).fetchall()
        by_id = {r["id"]: r for r in erows}
        out: list[dict[str, Any]] = []
        for eid in ids:  # preserve bm25 rank order
            r = by_id.get(eid)
            if r is None:
                continue
            ev = self._event_to_dict(r)
            if kinds and ev["kind"] not in kinds:
                continue
            out.append(ev)
            if len(out) >= k:
                break
        return out


def _fts_query(q: str) -> str:
    """Sanitize a user query into a safe FTS5 MATCH expression.

    We tokenize to alphanumerics and OR the terms, each quoted, to avoid FTS5
    syntax injection / errors from special characters.
    """
    import re

    tokens = re.findall(r"[A-Za-z0-9_]+", q)
    tokens = [t for t in tokens if t][:32]
    if not tokens:
        return ""
    return " OR ".join('"%s"' % t for t in tokens)
