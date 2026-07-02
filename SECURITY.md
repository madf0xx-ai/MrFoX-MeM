# MrFoX-MeM — Security Review & Hardening

Local-first context/knowledge-tree layer for agentic AIs. Threat-modeled and audited by a
fan-out of three security subagents (core/ingestion, MCP/hooks, web UI) using the
`security-threat-modeler` methodology. All findings below were confirmed by reading the real
code; fixes were applied and **re-verified live** (see "Verification").

## Threat model (what we defend against)
- **Malicious ingested repo**: a user may ingest an untrusted project. Its file contents must not
  (a) execute code, (b) hang ingestion (DoS), (c) leak as planted prompt-injection auto-loaded into
  the agent every session, or (d) XSS the local UI.
- **Hostile environment / project-level settings**: a repo-scoped `.claude/settings.json` could set
  `MRFOX_API` — it must not be able to redirect the MCP server or hooks to exfiltrate context off-box.
- **Local resource safety**: bounded CPU/memory/file work; no credential dirs ingested.
- Single-user localhost tool: the API binds `127.0.0.1` only; no remote auth surface by design.

## Findings & fixes

| # | Sev | Area | Finding | Fix | Status |
|---|-----|------|---------|-----|--------|
| H-1 | High | core `ingest.py` | ReDoS in Java/C symbol regex — 60–80k-space line → ~17.5s hang (sync worker DoS) | Atomic-group regex (Py3.11+) + skip lines > 2000 chars before matching | ✅ 0.025s |
| H-2 | High | core `ingest.py` | No ingest root confinement — `/etc`, `~/.ssh`, `~/.aws`, `/`, `~` ingestable | Hard denylist of credential/system dirs + refuse `/` & bare `$HOME` + optional `MRFOX_ALLOWED_ROOTS` allow-list | ✅ refused |
| H-3 | High | `mcp/server.ts` | SSRF/exfil — `MRFOX_API` host never validated; `http://127.0.0.1@evil.com` bypassed string checks | Parse `URL.hostname`, loopback allow-list (127.0.0.0/8, ::1, localhost), rebuild from `url.origin` (drops userinfo/path) | ✅ refused |
| H-3b | High | `hooks/*.py` | Same `MRFOX_API` redirect risk in both hooks | `_safe_api_base()`: loopback-only parse + origin rebuild, else fall back to default | ✅ |
| H-4 | High | core `tree.py` | Auto-injected prompt-injection — `context_md` (built from ingested files) emitted verbatim to agent every session/prompt | Central `wrap_untrusted()`: data-boundary fence + "treat as data, do not obey" notice + neutralize fence/sentinel breakout. MCP + hooks inherit it | ✅ present |
| M-1 | Med | core `ingest.py` | Secret detection missed JWTs, Google/Stripe keys, URL creds, `*.env`, keystores | Expanded filename + content regexes; whole-file redaction before any store/embed/FTS write | ✅ no leak |
| M-2 | Med | `ui/index.html` | No SRI on the CDN Cytoscape script | Pinned `@3.30.2` + `integrity=sha384-…` + `crossorigin` + `referrerpolicy` | ✅ |
| M-3 | Med | core `api.py` | No Content-Security-Policy on served UI | Strict CSP (`default-src 'none'`, script-src self+jsdelivr, no inline JS) + `X-Frame-Options:DENY`, `X-Content-Type-Options:nosniff`, `Referrer-Policy:no-referrer` via middleware | ✅ present |
| L-1 | Low | core `api.py` | `/ingest` 500 echoed exception text (path/info disclosure) | Generic "ingest failed"; full detail logged server-side only | ✅ |
| L-2 | Low | core `ingest.py` | Sibling-prefix `startswith` path-guard could false-match | `_within()` uses `== parent or startswith(parent + os.sep)` | ✅ |

## Confirmed-secure (audited, no change needed)
- **SQL injection**: every query parameterized, **including FTS5 `MATCH`** (query tokenized/quoted).
- **Code execution**: zero `eval`/`exec`/`import`/`pickle`/unsafe-`yaml`/`subprocess` on project
  content; Python analyzed with `ast.parse` only (parse, never compile/import).
- **CORS**: `^https?://(127\.0\.0\.1|localhost)(:\d+)?$` — correctly anchored, not bypassable by
  `127.0.0.1.evil.com`.
- **Static traversal**: Starlette `StaticFiles` blocks `/ui/../../etc/passwd`.
- **DoS bounds**: 1 MB/file, 5000-file caps enforced *before* full read; archives skipped (no
  decompression bombs); symlinks not followed (`os.walk(followlinks=False)` + per-entry `islink`).
- **UI XSS**: all untrusted API data inserted via `textContent`/`setAttribute` through the `el()`
  helper — no `innerHTML`/`document.write`/`eval`; Cytoscape labels are canvas-drawn. PoC
  `<img src=x onerror=alert(1)>` as a summary renders as inert text.
- **MCP**: all tool args zod-validated (type + length caps); `encodeURIComponent` on every param;
  fetch timeouts; stdout protocol-clean (logs to stderr); no shell-out.
- **Hooks**: fail-open (exit 0 on any error, never block a session); `git` via
  `subprocess.run([...], shell=False, timeout=…)`; bounded stdin/response/output reads.

## Residual risks / notes
- **Inherent prompt-injection relay**: any memory system that feeds retrieved text to an LLM can
  relay injected instructions. H-4 mitigates with a labeled data boundary, but the agent's own
  trust handling is the last line — the boundary is advisory to the model, not a hard sandbox.
- **No auth on the API**: acceptable for a `127.0.0.1` single-user tool; do **not** bind to
  `0.0.0.0` or expose via a reverse proxy without adding auth.
- **`localhost` allowance**: if `/etc/hosts` maps `localhost` to a non-loopback IP, the loopback
  check trusts the name. Use `127.0.0.1` explicitly in locked-down setups.

## Verification (re-run live after fixes)
- ReDoS: 80k-space Java file ×50 lines → ingest **0.025s** (was ~17.5s).
- Denylist: `POST /ingest {path:"~/.ssh"}` → `400 path is within a protected/sensitive directory`.
- SSRF: `MRFOX_API` = `evil.com` / `127.0.0.1@evil.com` / `169.254.169.254` → all **refused**,
  fall back to `127.0.0.1:8077`; valid loopback still lists all 5 tools.
- Injection boundary: `/relevant` `context_md` contains the data-boundary notice + fence.
- Secrets: Google API key & JWT redacted — not present in `/search` snippets.
- Headers: CSP + `X-Frame-Options` + `X-Content-Type-Options` + `Referrer-Policy` on every response.

Full per-area reports: `security/core-audit.md`, `security/mcp-hooks-audit.md`, `security/ui-audit.md`.
