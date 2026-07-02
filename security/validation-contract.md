# MrFoX-MeM — Pre-ship Consistency Audit (spec ↔ code ↔ docs)

Date: 2026-06-27 · Scope: static review only (no servers started).
Method: cross-checked `CONTRACT.md`, `README.md`, `Makefile`, `run.sh`,
`core/*.py`, `mcp/server.ts`, `hooks/*`, and the config files.

## Severity counts
- **Blocker: 1**
- **Major: 2**
- **Minor: 4**

The HTTP API surface, SQLite schema, FTS tables, embedding backends, the 5 MCP
tools, and the hooks all **match the contract** with no functional drift (see
"Verified consistent" at the bottom). The issues below are build/doc-wiring
problems, one of which breaks a fresh install outright.

---

## BLOCKER

### B1 — `make setup` installs a requirements file that does not exist
- **Where:** `Makefile:41` → `uv pip install --python $(VENV) -r core/requirements.txt`; `README.md:76` ("installs `core/requirements.txt`").
- **Actual:** the deps file is `requirements.txt` at the **repo root** (`/requirements.txt`). `core/requirements.txt` is **MISSING** (`ls core/` confirms only `__init__.py, api.py, embed.py, ingest.py, run.py, store.py, tree.py`).
- **Impact:** `make setup` fails immediately → no `.venv`, no installed deps. This cascades: `run.sh:41` calls `make setup` on a fresh checkout (so `./run.sh` fails too), and `make serve` / `make mcp` then run against a missing venv. **Every fresh user is dead on step 1 of the Quickstart.**
- **Fix:** either move `requirements.txt` → `core/requirements.txt`, or change `Makefile:41` and `README.md:76` to reference `requirements.txt`. (Pick one; the root location is the one that actually ships.)

---

## MAJOR

### M1 — MCP server registered under two different names across the docs
- **Where:** `README.md:122` `claude mcp add mrfox …` and Cursor key `"mrfox"` (`README.md:137`); vs `mcp/README.md:43` `claude mcp add mrfox-mem …` and Cursor key `"mrfox-mem"` (`mcp/README.md:60`).
- **Impact:** A user reading the top-level README registers `mrfox`; one reading `mcp/README.md` registers `mrfox-mem`. Following both (or switching docs) yields duplicate/!mismatched server entries and confusion about which name to invoke. Both work in isolation, but the docs contradict each other.
- **Fix:** pick one canonical server name and use it in both READMEs (and ideally in `claude mcp list` examples).

### M2 — Default project name mismatch silently yields empty results
- **Where:** `Makefile:15` `MRFOX_PROJECT ?= default` (the fallback used by `make ingest` when none is passed) vs `.env.example:9`, `README.md` / `hooks/settings.snippet.json:5` which all use `my-project`.
- **Impact:** A user who runs `make ingest PATH=…` **without** `MRFOX_PROJECT` ingests into project `default`, but the hooks/MCP env default is `my-project`. `/tree`, `/relevant`, `/search` for `my-project` then hit `store.get_project()` → `null` → **HTTP 404 "project not found"** (`core/api.py:173,203,219`), and the fail-open hooks print nothing. Looks like "memory does nothing" with no error. The README's explicit `MRFOX_PROJECT=my-project` example avoids it, but the default is a real footgun.
- **Fix:** make the Makefile default match the docs (`MRFOX_PROJECT ?= my-project`), or have `make ingest` require an explicit project, or document the `default` fallback.

---

## MINOR

### m1 — Contradictory Node.js version requirement
- **Where:** `README.md:63` "Node.js 18+" vs `mcp/README.md:37` "Requires Node 25 (Node 18+ for the global `fetch`/`AbortController`…)".
- **Impact:** "Node 25" is wrong/confusing (no such LTS); contradicts the 18+ stated everywhere else. `package.json` declares no `engines` field to settle it.
- **Fix:** state one floor (Node 18+, since global `fetch`/`AbortController` are the only modern needs) in both files; optionally add `"engines": { "node": ">=18" }` to `mcp/package.json`.

### m2 — `MRFOX_ALLOWED_ROOTS` is implemented but undocumented
- **Where:** used in `core/ingest.py:293,298,336` (optional ingest allow-list confinement) but absent from `.env.example` and `README.md` / Security notes.
- **Impact:** A genuine, security-relevant hardening knob is undiscoverable; users can't opt into path confinement they don't know exists.
- **Fix:** document `MRFOX_ALLOWED_ROOTS` (os.pathsep-separated roots) in `.env.example` and the README Security section.

### m3 — `MRFOX_PORT` is honored by `core.run` but ignored by the launchers
- **Where:** `core/run.py:14` reads `MRFOX_PORT`; but `Makefile:13` (`PORT := 8077`), `Makefile:49` (`make serve` → `uvicorn … --port 8077`) and `run.sh:16` (`PORT="8077"`) hardcode 8077 and launch `core.api:app` directly (not `core.run`).
- **Impact:** Setting `MRFOX_PORT` has no effect via any documented run path; the only consumer (`python -m core.run`) isn't referenced by any Makefile target, README command, or `run.sh`. Undocumented + inert in practice.
- **Fix:** either thread `MRFOX_PORT` into `Makefile`/`run.sh` (e.g. `PORT ?= $(MRFOX_PORT)`), or drop the env read from `run.py` and note the fixed 8077.

### m4 — `.env.example` omits half the env surface
- **Where:** `.env.example` documents only `MRFOX_API` and `MRFOX_PROJECT`; code also reads `MRFOX_ALLOWED_ROOTS` (ingest) and `MRFOX_PORT` (run.py). (Rolls up m2+m3 from the env-file's perspective.)
- **Fix:** add both vars (commented, with defaults) so `.env.example` is the complete reference it claims to be.

---

## Verified consistent (no action needed)
- **HTTP API:** all contract endpoints exist in `core/api.py` with the documented method, params, and JSON shape — `/health` (`:138`), `/ingest` POST (`:144`, returns project/nodes/edges/files/skipped), `/tree` (`:168`), `/search` (`:193`), `/relevant` (`:208`, default `budget_tokens=1500`), `/context` POST→`{id}` (`:225`) + GET→`{events:[{id,kind,content,ts,refs}]}` (`:238`), and `/` + `/ui/*` static mounts (`:252-254`). CORS regex locked to 127.0.0.1/localhost (`:58`).
- **SQLite schema:** `core/store.py:47-108` matches the contract tables/columns (`project,node,edge,blob,event,embedding`) and FTS5 `fts_node(label,summary,content)` / `fts_event(content)` (extra `UNINDEXED` cols only).
- **Embeddings:** `core/embed.py` — fastembed `BAAI/bge-small-en-v1.5` 384d primary, deterministic 256d hashing fallback, `embed()` interface, backend reported in `/health`. Matches contract; `fastembed` is correctly optional in `requirements.txt:6` and `pyproject.toml:17-19`.
- **MCP tools:** exactly the 5 contract tools in `mcp/server.ts` (`get_knowledge_tree`, `search_knowledge`, `get_relevant_context`, `save_context`, `record_decision`) mapping to the right routes; loopback-only `MRFOX_API` guard, zod caps, 15s fetch timeout.
- **Hooks:** `session_start.py` (git context, budget 800) and `user_prompt.py` (stdin JSON prompt, budget 1200) match the contract + README; both fail-open; `settings.snippet.json` wires both with quoted absolute paths.
- **Dependencies:** every `core/*.py` import is stdlib or covered by `fastapi/uvicorn/pydantic` (+ optional `fastembed`); no import lacks a declared dep.
- **No `TODO`/`FIXME`/placeholder** strings remain in shipped source.
</content>
</invoke>
