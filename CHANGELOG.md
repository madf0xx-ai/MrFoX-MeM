# Changelog

All notable changes to MrFoX-MeM are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); versioning is
[SemVer](https://semver.org/).

## [Unreleased]

### Added
- **Personalized-PageRank graph retrieval** (`core/graph.py`) — HippoRAG-style
  expansion over the code graph, seeded on the vector+FTS entry points. Pure
  Python, no dependencies.
- **Cross-encoder reranker** (`core/rerank.py`) — optional local
  `Xenova/ms-marco-MiniLM-L-6-v2` via fastembed; graceful no-op fallback.
- **Multi-language symbol extraction** (`core/treesit.py`) via tree-sitter
  (JS/TS/TSX/Go/Rust/Java/Ruby/C/C++), replacing regex heuristics when
  available, plus `references` edges (referencer → definer) from call sites.
- **Accurate token budgeting** (`core/tokens.py`) — tiktoken `o200k_base` with a
  deterministic `chars/4` fallback and an offline vocab cache.
- **Benchmark harness** (`scripts/benchmark.py`) — token-savings / latency /
  graph size, reproducible and local.
- **Test suite** (`tests/`, 49 tests) + **GitHub Actions CI** + `LICENSE`
  (MIT) + `CONTRIBUTING.md`.
- `/health` now reports `embed_dim`, `reranker_backend`, and a loud `degraded`
  flag (+ warning) when running the keyword-only hashing embedder.

### Changed
- **Retrieval fusion** switched from brittle min-max weighted sum to
  **Reciprocal Rank Fusion** (robust to the cosine-vs-BM25 scale mismatch,
  including FTS5's negative bm25).
- **Vectorized cosine** (optional numpy `M @ q`; pure-Python fallback) —
  ~100–1000× faster scan at scale.
- **Single-transaction ingest** (`Store.bulk()`) — one commit instead of
  thousands; re-ingest is now atomic.
- **Structurally-contextualized embedding text** (path / kind / signature) — a
  zero-LLM approximation of contextual retrieval.
- Default install now includes `fastembed` + `numpy` (real semantic memory out
  of the box); remove them for a pure-stdlib install.
- `requires-python` relaxed from `>=3.13` to `>=3.11` (the true floor).

### UI
- Graph: relationship-typed colored edges, degree-sized & kind-shaped nodes,
  halo spotlights, and depth/motion polish (no new dependencies).
- Health pill flags the degraded embedder.

### Fixed (adversarial-review pass)
- **DoS:** the secret-content scan is now bounded (per-line length guard, 512 KB
  cap, possessive regex) so a large crafted file can no longer stall ingest while
  holding the store lock.
- **Ingest resilience:** deeply-nested / crafted source (RecursionError /
  MemoryError from `ast.parse`) is caught, so one poisoned file can't roll back
  the whole single-transaction ingest.
- **Token budget:** `/relevant` hard-clamps the wrapped output to the requested
  budget (small budgets previously overflowed by ~2×); the minimum is raised to
  128 so the injection fence always fits without truncation.
- **Validation:** project / source / id checks use `fullmatch`, so a trailing
  newline no longer slips past the `$` anchor.
- **Honest docs:** "offline" reworded (one-time model download on first run), the
  test badge de-numbered, and the multi-language row marked opt-in.

### Performance & deeper grounding pass
- **`file:line` citations:** symbol nodes now persist their 1-based definition
  line (`node.line`, additive migration), and injected context cites
  `path:line` (e.g. `core/graph.py:29`) so the agent jumps straight to the
  definition to verify — the strongest anti-hallucination grounding.
- **Version-keyed read cache:** `Store.project_version()` generation counter;
  the vector matrix and graph node/edge lists are cached per project and rebuilt
  only when it changes, so repeat `/relevant` queries skip re-scanning + re-
  unpacking every vector (scales with node count).
- **Ingest embeds outside the write lock** (two-phase): the slow ONNX inference
  runs lock-free between commits, so a long ingest no longer stalls concurrent
  reads for its full duration. Incremental vector reuse preserved.

### Grounding · recall · interop · portability pass
- **Anti-hallucination grounding:** injected context now cites **repo-relative
  `path`s** (previously no provenance reached the agent, and absolute paths leaked
  `/Users/…`), and the fence carries a grounding instruction (verify each path,
  cite sources, don't invent, abstain if nothing relevant). Adds an explicit
  **"NO RELEVANT MEMORY FOUND"** signal + `hit_count` so the agent knows when NOT
  to rely on memory.
- **Episodic memory is now searchable** (`store.search_events`) — free-text
  notes/decisions surface by MEANING via FTS, not only when manually ref-tagged.
- **File content is embedded** (bounded slice), not just path+summary — lifts
  semantic recall of file bodies.
- **Interop:** `integrations/rules/AGENTS.md` template (one file → Codex/Cursor/
  Windsurf/Zed/Aider/Gemini/Copilot/goose/… ) + Cline `.clinerules`. The
  `/mrfox-mem` command is now cross-OS (curl `--data-urlencode`, no python) with a
  slug matching `ingest`.
- **Windows** credential-denylist parity (DPAPI Protect/Crypto/Vault).
- **Lightweight:** default `requirements.txt` no longer pulls fastembed/onnxruntime;
  `pip install "mrfox-mem[fastembed]"` opts into semantic quality (auto-fallback +
  `/health` degraded flag otherwise).

### vLLM-discipline pass (reverse-engineered vLLM's engineering standard, applied here)
- **Retrieval-quality eval harness** (`scripts/eval.py` + labeled `eval/queries.json`)
  — hit@k / recall@k / MRR with per-stage ablation (RRF → +graph → +rerank).
  Replaces the self-referential token-savings number as the credibility metric.
  On this repo: **100% hit@8, MRR 0.81**; the graph pass earns it, the reranker
  did not (→ next).
- **Incremental re-index** via a content-hash embedding cache (`embed_cache`
  table) — a re-ingest reuses vectors for unchanged content: measured
  **15.3s → 0.4s (38x), 309/328 vectors reused**. Freshness at near-zero cost.
- **Cross-encoder reranker now defaults OFF** — eval showed it added ~26x latency
  for no measurable gain on the set; still available via `use_reranker=True`.
- **Reference-edge correctness fix** — bare-name matching previously linked
  callers of common names (`run`/`get`/`add`) to an arbitrary definer, poisoning
  PageRank at weight 3.0; now guarded by a defs-per-name frequency cap + stoplist.
- **`DELETE /project/{name}` purge endpoint** — privacy / right-to-delete: drops
  a project's nodes, edges, embeddings, events, runs, and retrievals (incl. stored prompts).
- **`GET /projects` endpoint** + UI dropdown that actually switches projects (was
  stuck on one); **in-UI note capture** (Context tab) to feed memory during work.
- **CI lint gate** (`ruff`) + coverage reporting (`pytest --cov`, 78% on `core`);
  reranker failures now logged instead of silently swallowed.
- **Repository initialized** + tagged `v0.1.0` (the project now exists).
- **Global / any-project use:** per-directory project derivation (hooks + MCP),
  user-scope MCP registration, a `/mrfox-mem` Claude Code slash command
  (`integrations/commands/`), and `scripts/install-service.sh` to run the API as
  an always-on service (macOS launchd / Linux systemd; logs to `/tmp` to dodge
  the `~/Documents` TCC restriction). README "Ship it to another machine" guide.
- **Pluggable-backend registry** (`core/backends.py`) — embedders/rerankers
  register behind stable `Protocol` interfaces, selected by capability with
  graceful fallback and an env override (`MRFOX_EMBEDDER` / `MRFOX_RERANKER`).
  New models/backends are drop-ins with zero core edits; `/health` lists them.
  The platform seam for future vector-index (sqlite-vec) and parser backends.

## [0.1.0]
- Initial release: FastAPI core, SQLite store, local embeddings (fastembed +
  hashing fallback), hybrid vector+FTS retrieval, MCP server, Claude Code hooks,
  static Cytoscape UI, Sessions feed. Security-hardened (127.0.0.1 bind, CSP,
  secret redaction, path-traversal guards, prompt-injection fencing).
