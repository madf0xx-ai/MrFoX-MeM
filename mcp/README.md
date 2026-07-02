# MrFoX-MeM — MCP server

A thin, local-first **MCP server** (stdio transport) that exposes the MrFoX-MeM
knowledge/context layer to any MCP-capable agent (Claude Code, Cursor, Cline,
Windsurf, …). It is a stateless client over the core HTTP API — all storage,
embeddings, and retrieval live in `core/`.

## Tools

| Tool | Core call | Purpose |
| --- | --- | --- |
| `get_knowledge_tree({ project? })` | `GET /tree` | Full nodes + edges for a project. |
| `search_knowledge({ query, k? })` | `GET /search` | Hybrid (vector + FTS) search. |
| `get_relevant_context({ prompt, k?, budget_tokens? })` | `GET /relevant` | **The key tool** — returns token-bounded `context_md` to inject. |
| `save_context({ kind, content, refs? })` | `POST /context` | Persist a `decision`/`work`/`note`/`prompt` event. |
| `record_decision({ content })` | `POST /context` (kind=decision) | Convenience wrapper for decisions. |

Every tool also accepts an optional `project` argument that overrides the
`MRFOX_PROJECT` env default.

## Environment variables

| Var | Default | Meaning |
| --- | --- | --- |
| `MRFOX_API` | `http://127.0.0.1:8077` | Base URL of the core API (http/https localhost). |
| `MRFOX_PROJECT` | _(unset)_ | Default project slug used when a tool omits `project`. |

## Build

```bash
cd mcp
npm install
npm run build      # tsc -> dist/server.js
npm start          # node dist/server.js  (stdio MCP server)
```

Requires Node 25 (Node 18+ for the global `fetch`/`AbortController` used here)
and TypeScript 5 (strict).

## Register with Claude Code

```bash
claude mcp add mrfox-mem \
  --env MRFOX_API=http://127.0.0.1:8077 \
  --env MRFOX_PROJECT=my-project \
  -- node /absolute/path/to/MrFoX-MeM/mcp/dist/server.js
```

This registers a stdio server. List/verify with `claude mcp list`. The core API
must be running (`make serve`) for the tools to return data.

## Register with Cursor

Add to `~/.cursor/mcp.json` (global) or `<project>/.cursor/mcp.json` (per
project):

```json
{
  "mcpServers": {
    "mrfox-mem": {
      "command": "node",
      "args": ["/absolute/path/to/MrFoX-MeM/mcp/dist/server.js"],
      "env": {
        "MRFOX_API": "http://127.0.0.1:8077",
        "MRFOX_PROJECT": "my-project"
      }
    }
  }
}
```

Any other MCP client (Cline, Windsurf, …) uses the same shape: launch
`node dist/server.js` over stdio with the two env vars.

## Security notes

- Args are validated with **zod** (types + length caps: `content` ≤ 100k chars,
  `k` 1..50, bounded `query`/`prompt`/`refs`).
- Outbound `fetch` calls use an `AbortController` **15s timeout**.
- The server only talks to the single configured **localhost** `MRFOX_API`
  (http/https only; bad values fall back to the default).
- Query params are escaped with `encodeURIComponent`.
- No shell-out, no `eval`, no filesystem access. API/network errors are returned
  as plain text — the server never crashes on a bad request.
- `stdout` is reserved for the MCP protocol; all logging goes to `stderr`.
