# MrFoX-MeM — Build Contract (single source of truth)

A **local-first context & knowledge-tree layer** that plugs into ANY agentic AI the user owns
(Claude Code, Cursor, Cline, Windsurf, any MCP client). It ingests a project, builds a knowledge
tree, stores all context locally, and smartly injects only the *relevant* slice into the agent —
minimizing tokens. Local embeddings → **zero API tokens** for memory ops.

## Components & ownership
- `core/` — Python brain (FastAPI). Owns the SQLite store, ingestion, local embeddings, tree,
  hybrid retrieval, token-bounded context assembly. Serves the UI.
- `mcp/` — TypeScript MCP server (stdio). Thin client over the core HTTP API. Exposes tools to agents.
- `ui/` — static web UI (no build step; CDN libs). Renders the interactive knowledge tree.
- `hooks/` — Claude Code hook scripts + settings snippet for smart auto-injection.

## Core HTTP API (FastAPI, default `http://127.0.0.1:8077`)
All JSON. `project` is a string name (slug). Errors → `{ "error": "msg" }` + proper HTTP code.

- `GET  /health` → `{ "status": "ok", "version": "0.1.0", "embed_backend": "fastembed|hashing",
  "embed_dim": <int>, "degraded": <bool>, "warning": "<str, only when degraded>" }`
  (`degraded` is true on the keyword-only hashing fallback.)
- `POST /ingest` body `{ "path": "<abs dir>", "project": "<name?>", "exclude": ["<glob>",...]? }`
  → walks path, builds nodes+edges+embeddings, returns
  `{ "project": "<name>", "nodes": <int>, "edges": <int>, "files": <int>, "skipped": <int> }`
- `GET  /tree?project=<name>` →
  `{ "project": "<name>",
     "nodes": [ { "id": "<str>", "label": "<str>", "kind": "dir|file|module|symbol|concept|doc",
                  "path": "<str|null>", "parent": "<id|null>", "summary": "<str>" } ],
     "edges": [ { "src": "<id>", "dst": "<id>", "rel": "contains|imports|references|decided_for" } ] }`
- `GET  /search?project=<name>&q=<text>&k=<int=8>` →
  `{ "results": [ { "node_id": "<id>", "label": "<str>", "kind": "<str>", "path": "<str>",
                    "snippet": "<str>", "score": <float> } ] }`  (hybrid: vector + FTS)
- `GET  /relevant?project=<name>&prompt=<text>&k=<int=8>&budget_tokens=<int=1500>` → THE smart-inject call.
  `{ "context_md": "<markdown, <= budget_tokens estimate>",
     "nodes": [ {node...} ], "events": [ {event...} ], "token_estimate": <int> }`
  context_md = compact markdown: relevant tree path + node summaries + recent decisions, trimmed to budget.
- `POST /context` body `{ "project": "<name>", "kind": "decision|work|note|prompt",
     "content": "<text>", "refs": ["<node_id>",...]? }` → `{ "id": "<str>" }`  (content-addressed, dedup)
- `GET  /context?project=<name>&k=<int=20>` → `{ "events": [ { "id","kind","content","ts","refs" } ] }`
- `GET  /` and `/ui/*` → serve `ui/` static files.

CORS: allow `http://127.0.0.1:*` and `localhost` only.

## SQLite schema (file `data/mrfox.db`, WAL mode)
- `project(id TEXT PK, name TEXT, root TEXT, created TEXT)`
- `node(id TEXT PK, project TEXT, kind TEXT, label TEXT, path TEXT, parent TEXT, summary TEXT,
        content_hash TEXT, created TEXT)`
- `edge(id TEXT PK, project TEXT, src TEXT, dst TEXT, rel TEXT)`
- `blob(hash TEXT PK, bytes BLOB)`  — store any payload ONCE, reference by hash
- `event(id TEXT PK, project TEXT, kind TEXT, content_hash TEXT, ts TEXT, meta_json TEXT)`
- `embedding(node_id TEXT PK, dim INT, vec BLOB)` — float32 packed; (use sqlite-vec v0 table if available, else this)
- FTS5 virtual table `fts_node(label, summary, content)` and `fts_event(content)`.

## Local embeddings (`core/embed.py`) — pluggable, MUST run offline, 0 API tokens
- Backend A: `fastembed` (`BAAI/bge-small-en-v1.5`, 384d, ONNX/CPU) if importable.
- Backend B (fallback, no deps): deterministic hashing bag-of-tokens → 256d L2-normalized vector.
- Single interface: `embed(texts: list[str]) -> list[list[float]]`; `dim` exposed; report which backend in `/health`.

## Retrieval (`core/tree.py`)
Hybrid = vector cosine (over `embedding`) fused with FTS5 rank. Simple score fusion + MMR-lite for
diversity. `/relevant` then assembles token-bounded markdown: walk from top hits up to root for the
tree path, include node summaries + most-recent N decisions touching those nodes, trim to budget
(estimate tokens ≈ chars/4).

## MCP server (`mcp/server.ts`, `@modelcontextprotocol/sdk`, stdio transport)
Tools (each calls the core API; base URL from env `MRFOX_API` default `http://127.0.0.1:8077`):
- `get_knowledge_tree({ project? })` → GET /tree
- `search_knowledge({ query, k? })` → GET /search
- `get_relevant_context({ prompt, k?, budget_tokens? })` → GET /relevant  (the key tool)
- `save_context({ kind, content, refs? })` → POST /context
- `record_decision({ content })` → POST /context kind=decision
Default project read from env `MRFOX_PROJECT`. Validate/escape all inputs. Timeouts on fetch.

## Web UI (`ui/index.html` + `ui/app.js`)
- Cytoscape.js (CDN) graph of `/tree`. Color by `kind`. Click node → side panel: summary + related
  events (call `/search` or a node detail). Search box → `/search` highlights nodes.
- No build step. Pure static. Talks to same-origin core API.

## Hooks (`hooks/`)
- `session_start.py` — on Claude Code SessionStart: call `/relevant` with recent git/branch + last
  events, print the injected context block to stdout (Claude Code injects hook stdout as context).
- `user_prompt.py` — on UserPromptSubmit: read the prompt from stdin/env, call `/relevant?prompt=...`,
  print the token-bounded context block. THIS is "invoke smartly when user prompts."
- `settings.snippet.json` — the `.claude/settings.json` hooks config to wire both.

## Security requirements (BUILD THESE IN, not bolted on later)
- Bind API to `127.0.0.1` only. Never `0.0.0.0`.
- `path` in `/ingest` must be validated: absolute, exists, is a dir, and (recommended) under an
  allow-listed root; reject symlink escapes. NO arbitrary FS traversal beyond the chosen root.
- NEVER `eval`/`exec` on file content or user input. Parsing only.
- Parameterized SQL everywhere (no string-built queries). 
- Ingestion must NOT execute project code or import it; static parse only (ast.parse is fine, import is not).
- Secret-scanning on ingest: skip/redact files matching secret patterns (.env, *.pem, id_rsa, AWS keys)
  and do not store raw secret values in summaries/blobs.
- Size/΅count caps: max file size, max files, skip binaries — to bound resource use (DoS).
- MCP server: validate tool args (types, length caps), set fetch timeouts, no shell-out.
- CORS locked to localhost. No auth needed for a localhost-only single-user tool, but document the
  127.0.0.1 assumption.

## Run targets (`Makefile` / `run.sh` + `README.md`)
- `make setup` (uv venv + pip install core, npm install mcp), `make ingest PATH=...`,
  `make serve` (start core API + open UI), `make mcp` (start MCP server).
- README: quickstart + how to register the MCP server with Claude Code / Cursor + wire hooks.
