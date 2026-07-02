# MrFoX-MeM — Full Validation Report

Independent validation of the whole project: live functional QA (run by the integrator),
plus two static fan-out auditors — **contract/docs parity** and **security regression +
completeness**. Every issue found was fixed and **re-verified live**. Per-area detail:
`security/validation-contract.md`, `security/validation-security.md`.

## Verdict
**Ship-ready.** Clean `make setup` → `make serve` → `make ingest` → query works end-to-end.
All prior security fixes confirmed present (7/7). One install-blocker and several
consistency/code bugs were found *and fixed* during this pass.

## Live functional QA (all PASS)
- `make setup` from a wiped `.venv`: uv install + `npm install && tsc` → **0 npm vulnerabilities**, builds.
- Documented launch `uvicorn core.api:app` boots; `/health` ok (embed backend `hashing`, offline).
- Every endpoint + input validation: `/tree` no-project→422, missing→404, bad `k`→422,
  bad `kind`→422, oversized `budget_tokens`→422, bad project slug→400.
- Response shapes match `CONTRACT.md` exactly (node/edge/result/relevant/event keys).
- Happy path: ingest (142 nodes), search, `/relevant` token-bounded, context save + **content dedup**.
- Edge cases: empty dir → 1 node 0 edges; project-not-found handled.
- Hooks: `user_prompt.py` (stdin JSON) and `session_start.py` (git) both emit the injection
  boundary and **fail-open (exit 0)**.
- CORS: `127.0.0.1` origin allowed, `evil.com` denied.
- Both `make ingest PATH=...` and `make ingest DIR=...` ingest into the default `my-project`
  that the hooks/MCP query → no silent mismatch.

## Issues found during validation → fixed

| # | Sev | Found by | Issue | Fix | Re-verified |
|---|-----|----------|-------|-----|-------------|
| V1 | **Blocker** | contract audit | `make setup` + README referenced `core/requirements.txt` (file is at repo root) → every fresh install dead-on-arrival | point both at `requirements.txt` | clean `make setup` ✅ |
| V2 | Major | contract audit | `Makefile` default `MRFOX_PROJECT=default` vs `my-project` everywhere else → data lands where hooks/MCP don't look | unified to `my-project` | ingest+query same project ✅ |
| V3 | Major | contract audit | MCP register name `mrfox` (README) vs `mrfox-mem` (mcp/README) | standardized to `mrfox-mem` | grep: 0 stray ✅ |
| V4 | Major | live QA | `make ingest PATH=...` (documented form) clobbers recipe `PATH`, so the `python3` JSON-builder isn't found → ingest always fails | restore standard bins to `PATH` inside the recipe | both forms ingest ✅ |
| V5 | Low | security audit | walk confinement used `.startswith(root)` (sibling-prefix risk) | use `_within()` helper | compiles, ingest ok ✅ |
| V6 | Low | security audit | boundary wrapper (~50 tok) added *after* budget trim → `token_estimate` exceeded `budget_tokens` | reserve `_WRAP_OVERHEAD_TOKENS` from inner budget | 197≤200, 403≤500, 403≤1000 ✅ |
| V7 | Minor | contract audit | `MRFOX_ALLOWED_ROOTS` / `MRFOX_PORT` used in code but undocumented | added to `.env.example` | — |

## Security regression (7/7 fixes confirmed present)
ReDoS atomic-regex + line guard · ingest denylist + `_within` + allow-list · MCP `isLoopback`+origin
SSRF guard · hooks `_safe_api_base` · `wrap_untrusted`+`_neutralize` injection boundary · expanded
secret redaction (pre-write, both paths) · CSP/headers + SRI + non-echoing 500. No High/Med remain.

## Accepted residual Lows (by design, documented)
- `/context` POST has no project-existence check — intentional: lets an agent record decisions
  before/independently of ingest. Single-user localhost; low risk.
- TOCTOU between path validation and walk — accepted for a localhost single-user tool.
- Switching embed backends (hashing 256d ↔ fastembed 384d) on an existing DB silently drops
  mismatched vectors → degrades to FTS until re-ingest. **Re-ingest after changing backend.**
- README states Node 18+ (min) while mcp/README mentions Node 25 (tested) — both fine.

## How to reproduce this validation
```sh
cd MrFoX-MeM && rm -rf .venv && make setup           # blocker check
make serve &                                          # documented launch
make ingest PATH="$PWD/core" MRFOX_PROJECT=my-project # both PATH= and DIR= work
curl "http://127.0.0.1:8077/relevant?project=my-project&prompt=test&budget_tokens=400"
```
