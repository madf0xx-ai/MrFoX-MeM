# MrFoX-MeM Core — Security Audit (Python brain)

Scope: `core/store.py`, `embed.py`, `ingest.py`, `tree.py`, `api.py`, `run.py`.
Target: local-first FastAPI service on `127.0.0.1:8077`. Spec: `CONTRACT.md`.
Method: line-by-line read of actual code + empirical regex benchmark + grep sweeps.
Date: 2026-06-27.

## Severity tally
- Critical: 0
- High: 2 (both CONFIRMED)
- Medium: 1 (CONFIRMED)
- Low: 5
- Informational/positive controls: see "Confirmed-secure" section.

## Top 3 must-fix
1. **H-1 ReDoS** in the Java/C symbol regex — a single crafted file hangs ingestion for hours.
2. **H-2 No ingest allow-list** — `/etc`, `/`, `~/.ssh`, `~/.aws` are ingestable; contents land in the DB and are served back.
3. **M-1 Secret-detection gaps** — JWTs, Google/Stripe keys, URL/DB creds, `*.env` (non-`.env.*`) pass through and are stored + served in clear.

---

## H-1 — Catastrophic backtracking (ReDoS) in symbol regex  [CONFIRMED · High]
**Where:** `core/ingest.py:225`
```python
re.compile(r"^\s*(?:public|private|protected|static|\s)*(?:[A-Za-z_<>\[\]]+\s+)+([A-Za-z_]\w*)\s*\(", re.M)
```
Run via `parse_generic` (`ingest.py:234-254`) on every file whose extension is in
`CODE_EXTS` (`.java`, `.c`, `.cpp`, `.cs`, `.go`, …).

**Impact (measured):** the leading `^\s*` and the `(?: … |\s)*` group both match
whitespace, producing O(n²) backtracking on a long run of spaces that ends without
`(`. Benchmark on this regex:
```
spaces n=2000 : 0.18s
spaces n=5000 : 1.10s
spaces n=10000: 4.37s
spaces n=20000: 17.48s   # quadratic
```
`MAX_FILE_SIZE` is 1 MB, so a `evil.java` containing ~1,000,000 spaces on one line
extrapolates to multiple **hours** of pinned CPU per file. Ingestion is synchronous in
the request worker → one `/ingest` call wedges the service (DoS). Files of whitespace
pass `_looks_binary` (high text ratio) and the size cap, so nothing stops it.

**Fix:** rewrite without overlapping whitespace quantifiers (e.g. drop `\s` from the
first alternation and anchor token boundaries), and/or cap per-line length before regex
(`if len(line) > 2000: skip`), and/or wrap `parse_generic` in a wall-clock/`regex`-module
timeout. Reject lines/files over a sane symbol-scan width.

## H-2 — `/ingest` accepts any absolute directory (no allow-list)  [CONFIRMED · High]
**Where:** `core/ingest.py:260-272` (`validate_ingest_path`), reached from `api.py:121-132`.
The function enforces: string, absolute, `realpath` exists, is a dir, and top path is not
a symlink. It does **not** confine the path to any allowed root.

**Impact:** `path = "/etc"`, `"/"`, `os.path.expanduser("~/.ssh")`, `"~/.aws"` (abs form),
etc. are all accepted. The walker then reads every readable file (≤1 MB, ≤5 000 files) into
`blob`, `summary`, FTS and embeddings, and the data is served back verbatim via `/tree`,
`/search`, `/relevant`. This realizes the exact thing `CONTRACT.md:85-86` forbids
("NO arbitrary FS traversal beyond the chosen root"; allow-listed root only "recommended"
was left unimplemented). Browser-CSRF is largely blunted (JSON body forces a CORS-preflight
that the locked regex rejects), so the realistic abuser is a tricked user or any local
client — but the blast radius (read of `~/.ssh`, cloud creds, `/etc`) is large.

**Fix:** introduce `MRFOX_ALLOWED_ROOTS` (default: user home or an explicit project dir);
require `os.path.commonpath([real, allowed]) == allowed` for at least one allowed root;
reject otherwise. Keep the existing symlink/realpath checks.

## M-1 — Secret-scanner coverage gaps  [CONFIRMED · Medium]
**Where:** `core/ingest.py:40-52` (filename + content patterns), applied at `ingest.py:377-388`.

**What is handled well (CONFIRMED good):** when a secret IS detected, redaction is
*whole-file* and happens *before* any write — `text=""`, `summary=REDACTION`,
`blob_text=REDACTION`, FTS `content=REDACTION`, embedding text = `fname\nREDACTION`, and
symbol/import extraction is skipped (`ingest.py:383-424`). So a *detected* secret never
reaches `blob`, `summary`, FTS, or embeddings. Good.

**The gap is detection recall.** Misses (each then stored in full and served):
- **JWTs** (`eyJ…` header) — no pattern.
- **Google API keys** `AIza[0-9A-Za-z_\-]{35}` — none.
- **Stripe / generic prefixed keys** `sk_live_…`, `rk_live_…`, `xoxe`, `glpat-`, `npm_…` — none (the generic kw-regex needs an `api_key|secret|password|token` keyword adjacent).
- **URL/DB credentials** `proto://user:pass@host` — none.
- **Filename misses:** `SECRET_FILENAME_PATTERNS` matches `.env.*` so `.env.local` is caught,
  but `prod.env` / `local.env` (suffix `.env`, not prefix `.env.`) are **not**; also
  `secrets.yaml`, `config.yaml`, `settings.py`, `.pgpass`, `*.kdbx` rely solely on content
  scan, which misses the above token classes.
- Generic kw-regex (`ingest.py:49`) requires value length ≥6 non-space — short secrets slip.

**Fix:** add the patterns above (JWT, AIza, `sk_live_`, `://user:pass@`, generic
high-entropy base64/hex ≥20 chars); broaden filename globs (`*.env`, `*secret*`, `*.pgpass`,
`*.kdbx`, `service-account*.json`). Consider a Shannon-entropy fallback on long tokens.

## L-1 — Stored-XSS seed not neutralized server-side  [CONFIRMED · Low]
**Where:** raw file content → `node.summary`/`snippet` (`tree.py:122-135`, `ingest.py:413-416`)
→ `/tree`, `/search`, `/relevant.context_md` with **no** HTML/markdown escaping anywhere in
core. A file containing `<img src=x onerror=…>` is stored and returned verbatim.

**Why only Low:** the shipped first-party UI is safe — `ui/app.js` inserts every API string
via `textContent` (`app.js:35,232-240,…`; grep shows zero `innerHTML`/`insertAdjacentHTML`),
and `context_md` is not HTML-rendered there. But neutralization is **client-side only**;
`context_md` is markdown injected into an LLM agent (per contract) and any future
markdown/HTML consumer would execute it.

**Fix:** treat as defense-in-depth — strip/escape control + `<…>` sequences in `summary`/
`snippet` server-side, and document that all `context_md` consumers must render as text/escape.

## L-2 — Sibling-dir prefix false-accept in walk guard  [SUSPECTED · Low]
**Where:** `core/ingest.py:327` — `os.path.realpath(current).startswith(root)` lacks a
trailing separator, so `root=/home/u/proj` would prefix-match `/home/u/proj-evil`. Guarded by
`realpath(current) != current`, which under `followlinks=False` (`ingest.py:323`) essentially
never fires, so exploitability is minimal. **Fix:** use `os.path.commonpath([real, root]) == root`.

## L-3 — TOCTOU between `islink`/`getsize` checks and `open`  [SUSPECTED · Low]
**Where:** `ingest.py:364-383` — a path checked as non-symlink/size-ok can be swapped before
`_read_text`/`open`. Single-user localhost → negligible. **Fix:** `O_NOFOLLOW` / open-then-fstat.

## L-4 — `/context` POST: no project-existence check, unbounded growth  [CONFIRMED · Low]
**Where:** `api.py:199-209` — accepts any regex-valid `project`; creates events for
non-existent projects, no count/rate cap (each ≤100 KB, `api.py:89`). **Fix:** verify project
exists; cap events/project or rate-limit.

## L-5 — Exception text in 500 + absolute paths in responses  [CONFIRMED · Low]
**Where:** `api.py:131-132` `err(f"ingest failed: {e}", 500)` may surface a filesystem path
from an unexpected exception; `/tree` & `/search` also return absolute `path` fields by
contract design. Acceptable for localhost-single-user but note it. **Fix:** generic 500 message;
log details server-side only.

## INFO — UI CDN scripts lack Subresource Integrity
`ui/index.html:8` loads Cytoscape from jsdelivr with no `integrity=`/SRI (supply-chain). UI,
not core; listed for completeness.

---

## Confirmed-secure controls (positive findings)
- **SQL injection — none.** Every statement in `store.py` is parameterized (`?`
  placeholders, `int()`-cast `LIMIT`). No string-built SQL, no dynamic table/column names.
- **FTS5 MATCH injection — handled.** `_fts_query` (`store.py:398-410`) tokenizes the user
  query to `[A-Za-z0-9_]+`, caps at 32 tokens, quotes each, ORs them, and the result is bound
  as a parameter — neutralizing FTS5 operator/syntax injection. `OperationalError` is caught
  (`store.py:392-393`).
- **Code execution — none.** grep over `core/` for `eval(`, `exec(`, `compile(`, `__import__`,
  `importlib`, `pickle`, `yaml.load`, `subprocess`, `os.system`, `marshal`, `popen` → only
  benign `re.compile`. Python is handled by `ast.parse` only (`ingest.py:184`); project code is
  never imported or executed.
- **CORS regex — correct, not bypassable.** `api.py:35`
  `^https?://(127\.0\.0\.1|localhost)(:\d+)?$` is fully anchored; `127.0.0.1.evil.com`,
  `localhost.evil.com`, `evil.com/127.0.0.1` all fail to match. `allow_credentials=False`,
  methods limited to GET/POST/OPTIONS.
- **DoS caps largely enforced before read.** Size cap uses `getsize` *before* `_read_text`
  (`ingest.py:368-374`); `MAX_FILES` checked before each file (`ingest.py:360`); symlinked
  dirs and files skipped (`ingest.py:336,364`); `os.walk(followlinks=False)`. (Caveat: H-1.)
- **No decompression / archive bombs.** Archive extensions are in `BINARY_EXTS` and skipped;
  no archive library is opened anywhere.
- **Static serving — Starlette-protected.** `StaticFiles` (`api.py:226-228`) normalizes and
  rejects `../` traversal; `/ui/../../etc/passwd` cannot escape `_UI_DIR`.
- **Secret redaction ordering — correct** (see M-1): redaction precedes all persistence and
  is whole-file, not line-level.
- **Bind address — 127.0.0.1 only** (`run.py:13`), never `0.0.0.0`. No-auth localhost model is
  consistent with the contract.

## Attack-surface map
| Surface | Control present | Gap |
|---|---|---|
| `/ingest` path | abs/exists/dir/symlink checks | **no allow-list (H-2)** |
| Directory walk | followlinks=False, symlink skip, size/file caps | **ReDoS on symbol regex (H-1)**; sibling-prefix (L-2); TOCTOU (L-3) |
| Secret handling | whole-file redaction, FTS/blob/embed clean | **detection recall (M-1)** |
| SQL / FTS | fully parameterized, query sanitized | none |
| Code exec | ast.parse only | none |
| CORS / bind | anchored regex, 127.0.0.1 | none (no-auth by design) |
| Static files | Starlette traversal guard | none |
| API → UI render | client textContent | **server does not neutralize (L-1)** |
| `/context` write | typed/size-capped | no project check / unbounded (L-4) |
