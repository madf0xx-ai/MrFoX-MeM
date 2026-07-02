# MrFoX-MeM — Contract Addendum: Session Feed + Runs (visualization)

Adds end-user visibility into **what memory/history was fetched every time a conversation starts**,
plus a lightweight **run** (workflow) concept to group a conversation's fetches + saved events.
Extends `CONTRACT.md`; everything else unchanged. Localhost-only, parameterized SQL, validated input.

## Concept
- A **run** = one conversation/session. At most ONE `active` run per project; starting a new run
  marks prior active runs `done`. (Server-managed "current run".)
- A **retrieval** = one `/relevant` fetch (by a hook, the MCP tool, or the UI). Logged with what it
  injected (node ids + event ids), the trigger source, prompt, token count, timestamp, and run_id.
- The UI shows a **live feed** of retrievals + a **runs timeline** (the workflow view).

## New SQLite tables (store.py)
- `run(id TEXT PK, project TEXT, source TEXT, label TEXT, status TEXT, started TEXT, updated TEXT, meta_json TEXT)`
  — status ∈ `active|done`. source ∈ `session_start|user_prompt|mcp|ui|manual`.
- `retrieval(id TEXT PK, project TEXT, run_id TEXT, source TEXT, prompt TEXT, node_ids TEXT, event_ids TEXT, token_estimate INT, ts TEXT)`
  — node_ids/event_ids are JSON arrays of ids.
- `event`: ADD nullable column `run_id TEXT` (tie saved decisions/notes to a run). Keep a migration
  guard so existing DBs upgrade (ALTER TABLE ADD COLUMN if missing).

## New / changed endpoints
- `POST /run` body `{project, source, label?}` → `{run_id, status:"active", started}`.
  Marks other active runs for the project `done` first.
- `GET /runs?project=<p>&k=20` → `{runs:[{id, source, label, status, started, updated,
  retrieval_count, event_count}]}` newest first.
- `GET /run/{run_id}` → `{run:{...}, steps:[ ... ordered by ts ]}` where each step is either
  `{type:"retrieval", ts, source, prompt, token_estimate, node_ids, event_ids}` or
  `{type:"event", ts, kind, content, id}`.
- `GET /retrievals?project=<p>&k=30` → `{retrievals:[{id, run_id, source, prompt, token_estimate,
  ts, node_ids:[...], event_ids:[...], nodes:[{id,label,kind}]}]}` newest first.
  (Include resolved `nodes` labels so the feed renders without extra calls.)
- `GET /relevant` — ADD optional query params:
  - `source` (default `ui`), validated against the source enum.
  - `run_id` (optional). If omitted, attach the retrieval to the project's current `active` run
    (if any); never auto-create a run here.
  Side effect: insert a `retrieval` row capturing the node ids + event ids it returned.
  Response: unchanged fields PLUS `retrieval_id`, `node_ids`, `event_ids`.
- `POST /context` — ADD optional `run_id`; if omitted, attach to the project's current active run
  (if any). Existing behavior otherwise unchanged.

Validation: `source` must match `^(session_start|user_prompt|mcp|ui|manual)$`; `run_id`/ids length-capped;
`/run/{run_id}` 404 if unknown. All new queries parameterized.

## Hooks / MCP wiring
- `hooks/session_start.py`: FIRST `POST /run {source:"session_start", label:<branch>}` → run_id; then
  `GET /relevant?...&source=session_start&run_id=<run_id>`. (Each conversation start = a new run.)
- `hooks/user_prompt.py`: `GET /relevant?...&source=user_prompt` (no run_id → server attaches to the
  active run). Fail-open unchanged.
- `mcp/server.ts`: `get_relevant_context` appends `&source=mcp`; `save_context`/`record_decision`
  unchanged (server attaches events to the active run).

## UI (ui/app.js + index.html)
Add a **"Sessions"** tab (alongside Details / Context):
- **Live Feed**: poll `GET /retrievals` every ~3s (toggle to pause). Newest first. Each entry shows:
  a trigger badge (session-start / prompt / mcp / ui), relative time, the prompt, token count,
  and "N nodes · M events". Clicking an entry **highlights its `node_ids` in the graph** and lists
  the injected events.
- **Runs**: `GET /runs` list; click a run → `GET /run/{id}` → ordered step timeline (retrievals +
  events). This is the lightweight "workflow" visualization.
- Use `textContent`/safe DOM building (no innerHTML with API data). Same-origin fetch only.
- Live polling must stop when the tab/panel is not visible (avoid runaway requests); cap entries shown.
