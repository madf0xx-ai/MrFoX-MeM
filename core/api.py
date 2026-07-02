"""FastAPI application implementing the MrFoX-MeM Core HTTP API.

Bound to 127.0.0.1 only. CORS locked to localhost. Parameterized SQL only
(handled in store.py). Every route validates input via pydantic models /
typed query params. No auth: this is a localhost-only single-user tool.
"""
from __future__ import annotations

import os
import re
import uuid
from typing import Any, Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import backends
from . import embed as embed_mod
from . import ingest as ingest_mod
from . import rerank as rerank_mod
from . import tree as tree_mod
from .store import Store

VERSION = "0.1.0"

_CORE_DIR = os.path.dirname(os.path.abspath(__file__))
_UI_DIR = os.path.abspath(os.path.join(_CORE_DIR, "..", "ui"))

app = FastAPI(title="MrFoX-MeM Core", version=VERSION)

# Strict security headers on every response (defense-in-depth for the served UI
# which renders untrusted ingested content). CSP allows same-origin + the pinned
# Cytoscape CDN script only; no inline scripts.
_CSP = (
    "default-src 'none'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = _CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


# CORS locked to localhost / 127.0.0.1 (any port).
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

_store: Optional[Store] = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def err(message: str, code: int = 400) -> JSONResponse:
    return JSONResponse(status_code=code, content={"error": message})


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
_PROJECT_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
# Trigger source for a run / retrieval (per the feed addendum enum).
_SOURCE_RE = re.compile(r"^(session_start|user_prompt|mcp|ui|manual)$")
# Server-minted ids (run_, ret_, ev_, …). Length-capped to bound query inputs.
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def valid_project(name: str) -> bool:
    return bool(name) and bool(_PROJECT_RE.fullmatch(name))


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class IngestBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    project: Optional[str] = Field(default=None, max_length=128)
    exclude: Optional[list[str]] = Field(default=None)

    @field_validator("exclude")
    @classmethod
    def _check_exclude(cls, v):
        if v is None:
            return v
        if len(v) > 200:
            raise ValueError("too many exclude patterns")
        for item in v:
            if not isinstance(item, str) or len(item) > 256:
                raise ValueError("invalid exclude pattern")
        return v


class ContextBody(BaseModel):
    project: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=32)
    content: str = Field(min_length=1, max_length=100_000)
    refs: Optional[list[str]] = Field(default=None)
    # Optional run to tie this saved event to; if omitted the route attaches it
    # to the project's current active run (if any).
    run_id: Optional[str] = Field(default=None, max_length=64)

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, v):
        if v not in {"decision", "work", "note", "prompt"}:
            raise ValueError("kind must be one of decision|work|note|prompt")
        return v

    @field_validator("refs")
    @classmethod
    def _check_refs(cls, v):
        if v is None:
            return v
        if len(v) > 200:
            raise ValueError("too many refs")
        for r in v:
            if not isinstance(r, str) or len(r) > 128:
                raise ValueError("invalid ref id")
        return v


class RunBody(BaseModel):
    project: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=32)
    label: Optional[str] = Field(default=None, max_length=256)

    @field_validator("source")
    @classmethod
    def _check_source(cls, v):
        if not _SOURCE_RE.fullmatch(v):
            raise ValueError("invalid source")
        return v


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict[str, Any]:
    embedder = embed_mod.get_embedder()
    backend = embedder.backend
    # The stdlib hashing backend has no semantic understanding (keyword-level
    # recall only). Surface that loudly so users know to install a real embedder
    # rather than silently getting poor retrieval.
    degraded = backend == "hashing"
    resp: dict[str, Any] = {
        "status": "ok",
        "version": VERSION,
        "embed_backend": backend,
        "embed_dim": embedder.dim,
        "reranker_backend": rerank_mod.get_reranker().backend,
        "embedders": backends.available("embedder"),
        "rerankers": backends.available("reranker"),
        "degraded": degraded,
    }
    if degraded:
        resp["warning"] = (
            "Running the dependency-free hashing embedder — keyword-level recall "
            "only. For real semantic memory install a local embedder: "
            "pip install fastembed"
        )
    return resp


@app.get("/projects")
def projects_get() -> dict[str, Any]:
    """List all ingested projects so the UI can switch between them."""
    store = get_store()
    return {
        "projects": [
            {"id": r["id"], "name": r["name"], "root": r["root"], "created": r["created"]}
            for r in store.list_projects()
        ]
    }


@app.delete("/project/{name}")
def project_delete(name: str) -> Any:
    """Purge a project (privacy / right-to-delete): removes its nodes, edges,
    embeddings, events, runs, and retrievals — including any stored prompts."""
    if not valid_project(name):
        return err("invalid project name")
    store = get_store()
    if store.get_project(name) is None:
        return err("project not found", 404)
    store.delete_project(name)
    return {"deleted": name}


@app.post("/ingest")
def ingest_route(body: IngestBody):
    if body.project is not None and not valid_project(ingest_mod.slugify(body.project)):
        return err("invalid project name")
    try:
        res = ingest_mod.ingest(
            get_store(), body.path, project=body.project, exclude=body.exclude
        )
    except ValueError as e:
        return err(str(e), 400)
    except Exception:  # pragma: no cover - defensive
        # Do not echo internal exception text (may leak paths/internals).
        import logging
        logging.getLogger("mrfox").exception("ingest failed")
        return err("ingest failed", 500)
    return {
        "project": res.project,
        "nodes": res.nodes,
        "edges": res.edges,
        "files": res.files,
        "skipped": res.skipped,
        "reused": res.reused,
    }


@app.get("/tree")
def tree_route(project: str = Query(..., max_length=128)):
    if not valid_project(project):
        return err("invalid project name")
    store = get_store()
    if store.get_project(project) is None:
        return err("project not found", 404)
    nodes = [
        {
            "id": n["id"],
            "label": n["label"],
            "kind": n["kind"],
            "path": n["path"],
            "parent": n["parent"],
            "summary": n["summary"] or "",
        }
        for n in store.get_nodes(project)
    ]
    edges = [
        {"src": e["src"], "dst": e["dst"], "rel": e["rel"]}
        for e in store.get_edges(project)
    ]
    return {"project": project, "nodes": nodes, "edges": edges}


@app.get("/search")
def search_route(
    project: str = Query(..., max_length=128),
    q: str = Query(..., min_length=1, max_length=2000),
    k: int = Query(8, ge=1, le=50),
):
    if not valid_project(project):
        return err("invalid project name")
    store = get_store()
    if store.get_project(project) is None:
        return err("project not found", 404)
    results = tree_mod.hybrid_search(store, project, q, k=k)
    return {"results": results}


@app.get("/relevant")
def relevant_route(
    project: str = Query(..., max_length=128),
    prompt: str = Query(..., min_length=1, max_length=8000),
    k: int = Query(8, ge=1, le=50),
    budget_tokens: int = Query(1500, ge=128, le=32000),
    source: str = Query("ui", max_length=32),
    run_id: Optional[str] = Query(None, max_length=64),
):
    if not valid_project(project):
        return err("invalid project name")
    if not _SOURCE_RE.fullmatch(source):
        return err("invalid source")
    if run_id is not None and not _ID_RE.fullmatch(run_id):
        return err("invalid run id")
    store = get_store()
    if store.get_project(project) is None:
        return err("project not found", 404)

    result = tree_mod.relevant(
        store, project, prompt, k=k, budget_tokens=budget_tokens
    )

    # Resolve which run this fetch belongs to: an explicit run_id wins, else the
    # project's current active run (if one is open). We never auto-create a run
    # here — runs are opened explicitly via POST /run.
    target_run = run_id
    if target_run is None:
        active = store.get_current_active_run(project)
        target_run = active["id"] if active is not None else None

    # Log a retrieval row capturing exactly what we injected, so the UI feed can
    # show — and re-highlight in the graph — this fetch.
    retrieval_id = f"ret_{uuid.uuid4().hex[:12]}"
    store.insert_retrieval(
        retrieval_id,
        project,
        target_run,
        source,
        prompt,
        result["node_ids"],
        result["event_ids"],
        result["token_estimate"],
    )
    result["retrieval_id"] = retrieval_id
    return result


@app.post("/context")
def context_post(body: ContextBody):
    if not valid_project(body.project):
        return err("invalid project name")
    if body.run_id is not None and not _ID_RE.fullmatch(body.run_id):
        return err("invalid run id")
    store = get_store()

    # Tie this saved decision/note to a run: explicit run_id wins, else the
    # project's current active run (if any). Events with no run stay unattached.
    run_id = body.run_id
    if run_id is None:
        active = store.get_current_active_run(body.project)
        run_id = active["id"] if active is not None else None

    meta = {"refs": body.refs or []}
    event_id = f"ev_{uuid.uuid4().hex[:12]}"
    eid, _new = store.insert_event(
        event_id, body.project, body.kind, body.content, meta=meta, run_id=run_id
    )
    return {"id": eid}


@app.get("/context")
def context_get(
    project: str = Query(..., max_length=128),
    k: int = Query(20, ge=1, le=200),
):
    if not valid_project(project):
        return err("invalid project name")
    store = get_store()
    return {"events": store.get_events(project, k=k)}


# ---------------------------------------------------------------------------
# Runs + retrievals (session feed / workflow view)
# ---------------------------------------------------------------------------
@app.post("/run")
def run_post(body: RunBody):
    """Open a new run for a conversation, closing any prior active run."""
    if not valid_project(body.project):
        return err("invalid project name")
    store = get_store()
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    started = store.create_run(run_id, body.project, body.source, label=body.label)
    return {"run_id": run_id, "status": "active", "started": started}


@app.get("/runs")
def runs_get(
    project: str = Query(..., max_length=128),
    k: int = Query(20, ge=1, le=200),
):
    if not valid_project(project):
        return err("invalid project name")
    store = get_store()
    runs = [
        {
            "id": r["id"],
            "source": r["source"],
            "label": r["label"],
            "status": r["status"],
            "started": r["started"],
            "updated": r["updated"],
            "retrieval_count": r["retrieval_count"],
            "event_count": r["event_count"],
        }
        for r in store.get_runs(project, k=k)
    ]
    return {"runs": runs}


@app.get("/run/{run_id}")
def run_get(run_id: str):
    if not _ID_RE.fullmatch(run_id):
        return err("invalid run id")
    store = get_store()
    row = store.get_run(run_id)
    if row is None:
        return err("run not found", 404)
    run = {
        "id": row["id"],
        "project": row["project"],
        "source": row["source"],
        "label": row["label"],
        "status": row["status"],
        "started": row["started"],
        "updated": row["updated"],
    }
    # Merge the run's retrievals and events into one chronological timeline.
    steps: list[dict[str, Any]] = []
    for r in store.get_run_retrievals(run_id):
        steps.append({
            "type": "retrieval",
            "ts": r["ts"],
            "source": r["source"],
            "prompt": r["prompt"],
            "token_estimate": r["token_estimate"],
            "node_ids": r["node_ids"],
            "event_ids": r["event_ids"],
        })
    for ev in store.get_run_events(run_id):
        steps.append({
            "type": "event",
            "ts": ev["ts"],
            "kind": ev["kind"],
            "content": ev["content"],
            "id": ev["id"],
        })
    steps.sort(key=lambda s: s["ts"] or "")
    return {"run": run, "steps": steps}


@app.get("/retrievals")
def retrievals_get(
    project: str = Query(..., max_length=128),
    k: int = Query(30, ge=1, le=200),
):
    if not valid_project(project):
        return err("invalid project name")
    store = get_store()
    out: list[dict[str, Any]] = []
    for r in store.get_retrievals(project, k=k):
        # Resolve node labels here so the feed renders without extra calls.
        # Some ids may no longer resolve (re-ingest dropped them) — skip those.
        nodes = []
        for nid in r["node_ids"]:
            n = store.get_node(nid)
            if n is not None:
                nodes.append({"id": n["id"], "label": n["label"], "kind": n["kind"]})
        out.append({
            "id": r["id"],
            "run_id": r["run_id"],
            "source": r["source"],
            "prompt": r["prompt"],
            "token_estimate": r["token_estimate"],
            "ts": r["ts"],
            "node_ids": r["node_ids"],
            "event_ids": r["event_ids"],
            "nodes": nodes,
        })
    return {"retrievals": out}


# ---------------------------------------------------------------------------
# Static UI (served at / and /ui/*). Mounted last so API routes win.
# ---------------------------------------------------------------------------
if os.path.isdir(_UI_DIR):
    app.mount("/ui", StaticFiles(directory=_UI_DIR, html=True), name="ui")
    app.mount("/", StaticFiles(directory=_UI_DIR, html=True), name="root")
