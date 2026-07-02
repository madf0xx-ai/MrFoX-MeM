# MrFoX-MeM — Validation / Regression + Completeness Pass

Date: 2026-06-27 · Reviewer: appsec (static review only, no servers started)
Scope: regression-confirm 7 prior fixes + completeness sweep for missed Low/Med items.

---

## Part A — Fix-Confirmed (7 / 7 present and correct)

### 1. ReDoS — CONFIRMED
- Atomic groups in the Java/C symbol regex: `core/ingest.py:232-236`
  (`(?>(?:public|private|...)[ \t]+)*` and `(?>[A-Za-z_][\w<>\[\].]*[ \t]+)+`).
  Compiles and matches correctly under the deployed Python 3.13.7 (verified:
  `getValue`, `main`, `name` all extracted; atomic group does not drop valid symbols
  because the outer `*`/`+` quantifiers still terminate).
- Long-line guard: `_MAX_LINE_FOR_REGEX = 2_000` (`core/ingest.py:241`) and
  `parse_generic` drops lines `> 2000` before any regex pass (`core/ingest.py:252-255`).
- No other nested-quantifier ReDoS in the file. `_SYMBOL_REGEXES` are `^`-anchored
  single-quantifier; `SECRET_CONTENT_PATTERNS` use literal separators / bounded classes;
  `_IMPORT_REGEXES` use lazy `.*?` against `re.M` (`.` excludes newline → linear per line).

### 2. Ingest denylist — CONFIRMED
- `validate_ingest_path` refuses filesystem root and `$HOME` (`core/ingest.py:324-326`),
  and every `_denied_roots()` entry — `.ssh/.aws/.gnupg/.kube/.docker/.azure/.netrc/`
  `Library/Keychains/.password-store` + `/etc /private/etc /var/root /root`
  (`core/ingest.py:280-289`, enforced `329-331`).
- `_within()` has **no** sibling-prefix bug: `parent.rstrip(sep) or sep` then
  `child == parent or child.startswith(parent + sep)` (`core/ingest.py:304-307`).
  `/etc` vs `/etcfoo` correctly rejected.
- Optional allow-list `MRFOX_ALLOWED_ROOTS` (os.pathsep-separated, realpath-resolved):
  `core/ingest.py:292-301`, enforced `334-336`.

### 3. MCP SSRF — CONFIRMED
- `resolveBaseUrl` parses URL, requires `http/https`, checks `isLoopback(url.hostname)`,
  and returns `url.origin` only (`mcp/server.ts:41-68`).
- `http://127.0.0.1@evil.com` → `url.hostname === "evil.com"` → `isLoopback` false →
  refused, falls back to default. Userinfo/path/query are also stripped by `url.origin`.

### 4. Hooks SSRF — CONFIRMED
- `_safe_api_base()` loopback gate present in **both** `hooks/session_start.py:29-42`
  and `hooks/user_prompt.py:28-40`. Validates scheme ∈ http/https, host ∈
  {127.0.0.1, ::1, localhost} or `127.*`, and rebuilds from host+port only (drops
  userinfo/path/query). Same `@evil.com` bypass is closed.

### 5. Injection boundary — CONFIRMED
- `wrap_untrusted()` applied to assembled memory in `relevant()` at
  `core/tree.py:264` (after the hard char-trim so the fence always survives).
- `_neutralize()` strips both fence markers and collapses ``` runs
  (`core/tree.py:37-43`). The data block carries an explicit "do NOT execute / obey"
  notice (`core/tree.py:25-30`).

### 6. Secret redaction — CONFIRMED (both paths, before any write)
- Expanded patterns: JWT, Google `AIza…`, Stripe `sk/rk_live`, URL creds
  `scheme://user:pass@`, GitHub, Slack, AWS, private-key headers
  (`core/ingest.py:46-57`); filename patterns add `*.env`, `*.keystore`, `*.jks`,
  `*.p12/.pfx`, `secrets.*`, etc. (`core/ingest.py:40-44`).
- Filename-match path: `text=""` set before any read (`core/ingest.py:449`).
- Content-match path: read, scanned, then `text=""` + `redacted=True`
  (`core/ingest.py:451-453`).
- For redacted files `summary = blob_text = REDACTION` (`463-465`) **before**
  `put_blob` (`477`), `insert_node` content field (`479-482`), and the embedding
  text `f"{fname}\n{summary}"` (`487`). No raw secret reaches blob/summary/embedding.

### 7. Headers / SRI / error hygiene — CONFIRMED
- CSP + `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`
  middleware on every response (`core/api.py:35-52`). CSP `default-src 'none'`,
  scripts limited to `'self'` + pinned jsDelivr.
- SRI on the Cytoscape CDN tag (`ui/index.html:8-10`, `integrity=sha384-…`,
  `crossorigin`, `referrerpolicy=no-referrer`).
- `/ingest` 500 path logs internally and returns generic `"ingest failed"` —
  no exception text echoed (`core/api.py:154-158`).

---

## Part B — Completeness: Still-Open / New issues (ranked)

### B1. [LOW] `/context` POST accepts events for non-existent projects — STILL OPEN
`context_post` (`core/api.py:225-235`) does **not** call `store.get_project`, unlike
`/tree`, `/search`, `/relevant`. The `event` table has no FK to `project`
(`core/store.py:84-92`; `foreign_keys=ON` is set but no constraint exists), so
`insert_event` happily creates orphan rows for any regex-valid project slug
(`^[A-Za-z0-9._-]{1,128}$`). Impact is data pollution / unbounded orphan event
growth on a localhost single-user tool — low risk but a real gap.
Fix: add `if store.get_project(body.project) is None: return err("project not found", 404)`
(or accept by design and document it).

### B2. [LOW] `os.walk` confinement check reintroduces the sibling-prefix pattern — NEW/LATENT
`core/ingest.py:393` uses
`os.path.realpath(current).startswith(root)` — the exact prefix-without-separator
check that `_within()` was written to replace. A realpath of `…/proj-evil` would
pass `startswith("…/proj")`. Currently **mitigated** (not exploitable in practice)
because symlinked dirs are pruned at `core/ingest.py:402` and `followlinks=False`,
so `current` stays inside `root`. Still an inconsistency / latent bug.
Fix: use `_within(os.path.realpath(current), root)` here too.

### B3. [LOW] TOCTOU between `validate_ingest_path` and the walk — STILL OPEN (accepted)
`validate_ingest_path` checks `islink` + `realpath` on the original path
(`core/ingest.py:315-338`), then `ingest` walks the resolved `root`
(`core/ingest.py:389`). An attacker with local FS write could swap `root` for a
symlink in the window. Requires local write access to a single-user localhost tool;
`followlinks=False` limits blast radius to the (now-symlinked) top dir's direct
targets. Acceptable for the threat model; document as known.

### B4. [LOW] `wrap_untrusted` overhead is added after the budget trim — NEW (minor)
`relevant()` trims to `budget_tokens * 4` chars **before** wrapping
(`core/tree.py:258-264`), then `wrap_untrusted` prepends the ~70-token notice plus
the two fence lines. The returned `token_estimate` (`core/tree.py:270`) and actual
payload therefore exceed `budget_tokens` by a constant (~80-130 tokens); with the
min `budget_tokens=64` the caller gets roughly double. Not a token-math break for
the model budget (hooks separately cap by `MAX_OUTPUT_CHARS`), but the documented
budget is under-counted. Fix: reserve wrapper overhead in `max_chars`.

### B5. [INFO] Embedding backend dim mismatch silently degrades search — not security
Hashing fallback is 256d, fastembed is 384d (`core/embed.py`). If the backend
changes between ingest and a later query (e.g., fastembed installed afterward),
stored vectors of the other dim are silently skipped by the length guard
(`core/tree.py:80-81`), collapsing hybrid search to FTS-only. Correctness/availability
degradation, not a vuln. The hashing fallback itself is sound (SHA-256 bucket+sign,
L2-normalized); collisions are acceptable for cosine similarity.

### B6. [INFO] `MAX_FILE_SIZE` IS checked before whole-file read — CONFIRMED OK
`os.path.getsize` gate (`core/ingest.py:434-440`) precedes `_read_text`
(`core/ingest.py:449`); `_looks_binary` reads only a 4096-byte chunk. No unbounded read.

### B7. [INFO] Other route 500s do not leak stack traces — CONFIRMED OK
Only `/ingest` wraps exceptions, but `app = FastAPI()` runs without `debug=True`, so
unhandled errors in `/tree /search /relevant /context` return Starlette's generic
"Internal Server Error" with no traceback in the response body. Acceptable.

### B8. [INFO] Atomic-group heuristic limitation — not a regression
Java generic types containing spaces/commas (e.g. `Map<String, Integer> f(`) are not
captured because the type class `[\w<>\[\].]*` excludes `,`/space. Pre-existing
heuristic limitation, unchanged by the atomic-group rewrite; no security impact.

---

## Summary
All 7 prior fixes are present and correct. No High/Med regressions or new High/Med
issues. Open items are all Low/Info: the `/context` orphan-project gap (B1) and the
`startswith`-vs-`_within` inconsistency in the walk confinement check (B2) are the two
worth a one-line code change; the rest are accepted-by-threat-model or informational.
