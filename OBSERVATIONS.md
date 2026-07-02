# MrFoX-MeM — Review, Competitive Positioning & Roadmap

> External review + competitive research, 2026-07-01. Verdict, verified findings
> (with `file:line`), where MrFoX-MeM sits against the 2026 field, and a phased
> plan to make it the standout open-source **code-native memory** tool.

---

## 1. Verdict

**Strong bones; ships weak by default; not yet standout — but one narrow, real moat.**

The engineering is genuinely high-quality: clean `core / mcp / ui / hooks`
separation, a contract-driven design, cross-OS support, and — unusually —
**security built in, not bolted on**. It is *not* a toy. But out of the box the
retrieval quality is capped low (wrong default embedder), it will not scale past
a few thousand nodes (pure-Python brute-force vectors), and it is missing the
table-stakes an open-source project is judged on (tests, CI, license — added in
this pass). The good news: the competitive field has a wide-open seam that
MrFoX-MeM is unusually well-positioned to own.

---

## 2. What is already excellent (do not regress)

- **Security posture is the standout asset.** `127.0.0.1`-only bind
  (`core/run.py:13`), CORS + CSP + security headers (`core/api.py:35-62`),
  secret redaction on ingest (`core/ingest.py:41-60,488-504`), symlink-escape /
  denied-roots / allow-list path validation (`core/ingest.py:283-375`),
  ReDoS-safe atomic regexes (`core/ingest.py:235-244`), parameterized SQL
  everywhere, loopback-only SSRF guards in the MCP server and hooks
  (`mcp/server.ts:33-68`, `hooks/*.py`).
- **Prompt-injection fencing of injected memory** (`core/tree.py:37-56`,
  `wrap_untrusted` / `_neutralize`). Ingested repo content is treated as
  untrusted data, fenced, and the agent is told not to obey it. **No major
  competitor ships this** — it is a real, marketable differentiator.
- **Genuinely local & zero-token** for memory ops (deterministic ingest + local
  embeddings). This is the economic wedge (see §4).
- **The Sessions feed** (`ui/app.js`, `/runs` + `/retrievals`) — showing *exactly*
  what memory was injected per prompt — is a trust feature competitors lack.
- **Content-addressed blob dedup + WAL SQLite**, honest docs (`COMPATIBILITY.md`
  is refreshingly candid about the "only Claude Code gets auto-injection" limit).

---

## 3. Verified findings (code review)

Ranked by impact. Line numbers as of this review.

### Critical

1. **Ships on the *worst* embedder by default.** `fastembed` is optional and, in
   this checkout, **not installed** → the system runs the 256-dim stdlib
   **hashing bag-of-tokens** fallback (`core/embed.py:29-55`). That has no
   semantic understanding — it is hashed token overlap, which largely duplicates
   what FTS5 already does. The advertised "semantic memory" is, by default,
   keyword match. **Fix:** make a real embedder the default install path; keep
   the hashing backend only as a last-resort fallback.

2. **Pure-Python brute-force vector search, recomputed every query.**
   `_vector_scores` (`core/tree.py`) pulled *every* node vector via
   `get_embeddings`, `struct.unpack`ed each, and cosined in a Python loop on
   **every** `/relevant` call. Research measured pure-Python at **~100–1000×
   slower** than one batched matmul; at ~100k vectors it is ~1.5–3s/query
   (unusable). **Partially fixed this pass:** added a numpy-optional vectorized
   path (one `M @ q`, *not* per-row numpy — which benchmarks slower). *Still
   todo:* an in-memory vector cache + an ANN index (`sqlite-vec` / `usearch`)
   past ~100k nodes. (See §6, Phase 2.)

3. **Thin embedding signal.** Files embed only `"{filename}\n{summary}"` and
   symbols only `"{name} {summary}"` (`core/ingest.py:526,549,564`). The actual
   code body is never embedded. Retrieval quality is capped low *regardless* of
   which model you plug in. **Fix:** embed richer, contextualized chunks.

### High

4. **~Per-row commits on the ingest hot path** — *fixed this pass.* Was: blob +
   node + edge + per-symbol node/edge + per-node embedding each `commit()`ing
   (`core/store.py`, 10 sites). Now wrapped in a single transaction via
   `Store.bulk()` + `ingest()` (`core/store.py`, `core/ingest.py`).

5. **Brittle score fusion** — *fixed this pass.* Min-max-normalized weighted sum
   of cosine vs bm25 (different scales) replaced with **Reciprocal Rank Fusion**
   (`core/tree.py`, `hybrid_search`).

6. **"MMR" is a same-parent penalty, not real MMR** (`core/tree.py:132-150`).
   Diversity proxy = "did I already pick this node's parent," never compares
   candidate embeddings. Fine as a cheap heuristic; note it is not MMR.

7. **Memory only grows if the agent remembers to call `save_context`.** There is
   no automatic extraction of decisions/facts from a session. Passive memory =
   empty memory. Competitors (claude-mem, mem0) auto-capture.

8. **Full re-ingest every time** (`core/ingest.py`, `clear_project` then rewalk +
   re-embed). No incremental/Merkle indexing, so re-indexing a large repo after
   a one-file change re-embeds everything.

### Medium / Low

9. **`get_events_referencing` loads all project events and filters in Python**
   (`core/store.py:412-430`) — O(events) per retrieval.
10. **`chars/4` token estimate** (`core/tree.py:33`) — crude for code; can over/
    under-shoot a real tokenizer by 20–40%.
11. **Dir nodes get empty summaries** (`core/ingest.py:457`) — no hierarchical
    rollup, so a whole subtree contributes no summary signal.
12. **No dedup between SessionStart and UserPrompt injection** — overlapping
    context can be injected twice early in a session.
13. **Non-Python symbol extraction is regex heuristics** (`core/ingest.py:226-277`)
    — brittle vs a real parser (see tree-sitter, §6 Phase 2).

### Table-stakes gaps (all added this pass)

- **No tests** → added `tests/` (30 tests, all green: embed / store / ingest /
  tree / api).
- **No CI** → added `.github/workflows/ci.yml` (pytest + MCP build).
- **No `LICENSE` file** (pyproject declared MIT) → added `LICENSE`.
- **Not git-initialized** → recommend `git init` + first commit + push.

---

## 4. Competitive landscape (2026)

Research across three clusters (sources inline). The two facts that matter most:

**A. Every direct competitor pays an LLM to build memory, and none model code
structure.**

| Tool | Stars* | Memory model | LLM for memory ops? |
|---|---|---|---|
| **claude-mem** | ~85k | session **event log**, AI-compressed "observations" | **Yes** — spends *your* Claude tokens summarizing |
| **mem0** | ~55–60k | personal **fact store** (vector + Neo4j graph) | **Yes, required** (~6.9k tok/op; cloud by default) |
| **OpenMemory MCP** | (mem0) | personal fact store, local storage | **Yes** — OpenAI key by default ("local" ≠ local compute); now deprecated |
| **MrFoX-MeM** | new | **codebase structural knowledge tree** | **No — deterministic + local embeddings, $0/op, offline** |

*Approximate, extraction-derived June/July 2026. Sources:
[claude-mem](https://github.com/thedotmack/claude-mem),
[mem0](https://github.com/mem0ai/mem0),
[OpenMemory](https://mem0.ai/blog/introducing-openmemory-mcp).*

**B. The coding-tool field is converging on exactly MrFoX-MeM's design — but
does it with heavier infra or not locally.**

- **Two camps:** embeddings-RAG (Cursor, Roo Code, Continue) vs agentic-no-index
  (Claude Code, Cline). Claude Code deliberately dropped RAG for *staleness,
  privacy, maintenance* reasons ([Boris Cherny](https://newsletter.pragmaticengineer.com/p/building-claude-code-with-boris-cherny)).
  **The seam MrFoX-MeM can own: the persistent semantic index Anthropic omits —
  but *fresh* (incremental), *private* (local), and *free* (no API).**
- **Cline** refuses embeddings (no semantic recall); **Roo Code** needs
  Docker + Qdrant + cloud keys. MrFoX-MeM = "Roo-style semantic recall without
  Qdrant/Docker/cloud keys, plus Cline-style privacy."
- **Aider** is the critical prior art: its repo-map builds a
  **referencer→definer graph and ranks with Personalized PageRank** within a
  token budget, rendering **collapsed signatures** (`repomap.py`). **MrFoX-MeM
  already has the import/reference edges Aider computes — it is one PageRank away
  from this.**
- **Serena** (~26k★) proves symbol-level **LSP** retrieval (`find_symbol`,
  `find_referencing_symbols`) beats file dumps. **tree-sitter-language-pack**
  (300+ grammars, pip-installable, no build) is the drop-in fix for MrFoX-MeM's
  regex-based non-Python extraction.
- **cognee / GraphRAG / Zep** add: typed `calls` edges + "vector finds the
  neighborhood, graph traversal finds the answer"; Leiden **community summaries**
  for "explain the architecture" questions; **bi-temporal edges** so stale code
  stops surfacing.

---

## 5. Positioning — the moat, in one sentence

> **The only fully-local, zero-token AI memory that understands your codebase's
> *structure* — not just your chat history — and can prove what it injected.**

Two moats, stated together:
- **Capability moat:** structural code-graph memory (no competitor in cluster A
  has it; cluster B has pieces but not locally + freely).
- **Economic moat:** genuinely $0/op, offline, no key — beating claude-mem (burns
  Claude tokens) and mem0/OpenMemory (require OpenAI).

Plus the trust features already shipped: **prompt-injection-fenced injection**
and the **"see exactly what was injected" feed**.

---

## 6. Roadmap (phased, by impact-per-effort)

### Phase 0 — Credibility (mostly done this pass)
- [x] Test suite (`tests/`, 30 tests) + CI + `LICENSE`.
- [ ] `git init`, first tagged release, README badges.
- [ ] **Publish a benchmark** (even a small codebase-QA harness): retrieval
  hit-rate and tokens-injected vs full-file dumps. mem0's credibility is its
  numbers; MrFoX-MeM needs its own. This is the single biggest *marketing* lever.

### Phase 1 — Fix the defaults (highest quality-per-effort)
*All five below are "adopt now" in the SOTA research — low effort, ≤1 new
optional dep each, and together they turn the stack into Anthropic's validated
`embeddings + contextual-BM25 + rank-fusion + rerank` pipeline, entirely local.*
1. **Real embedder by default.** Ship `fastembed` as the default install;
   auto-download on first run; keep hashing only as offline fallback; warn in
   `/health` + UI when degraded. For a **code** tool, prefer
   **IBM `granite-embedding-small-english-r2`** (47M, **384d — near drop-in for
   bge-small**, 8192 ctx, Apache-2.0, CoIR code-retrieval ~55 vs bge ~43) once an
   ONNX/fastembed build is confirmed; else `nomic-embed-text-v1.5` (already in
   fastembed). ([Granite R2](https://huggingface.co/ibm-granite/granite-embedding-english-r2), [CoIR](https://archersama.github.io/coir/))
2. **Local cross-encoder reranker** — `ms-marco-MiniLM-L6-v2` (ONNX int8) over
   the top ~20–50 fused candidates, MMR kept *after* for diversity. **The single
   biggest retrieval-quality lever** — this is Anthropic's 49%→67% failure-rate
   reduction step; sub-second single-thread on CPU. ([SBERT](https://sbert.net/docs/cross_encoder/pretrained_models.html), [Anthropic](https://www.anthropic.com/news/contextual-retrieval))
3. **Real tokenizer for the budget** — `tiktoken o200k_base` when importable
   (`chars/4` fallback), per-content divisor (prose ≈/4, code/JSON ≈/3), plus
   ~1.2–1.3× headroom (both under-count current Claude). `chars/4` today has
   **~28% error** and *under*-counts code → overflow risk. ([tiktoken](https://github.com/openai/tiktoken))
4. **Richer embedding text** (finding #3): embed
   `path + signature + docstring + leading body`, not just the filename — and
   **prepend cheap structural context** (heading breadcrumb / filename / date)
   before embed + FTS5: a zero-LLM approximation of Anthropic Contextual
   Retrieval. ([Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval))
5. **Matryoshka dim-truncation** (store 256d, optional 128d coarse tier) — free
   3–6× storage/speed — *but only after moving off bge-small* (not MRL-trained).

### Phase 2 — The differentiator: structural retrieval
*This is what makes MrFoX-MeM stand out. It exploits the graph you already have.*
1. **tree-sitter symbol extraction** (`tree-sitter-language-pack`) replacing the
   regex heuristics → real defs/refs for ~40 languages, and precise
   `calls`/def-use edges.
2. **Personalized PageRank over the code graph** (Aider `repomap.py` +
   HippoRAG): seed on query-mentioned / recently-edited / open files, propagate
   over `contains`/`imports`/`calls`/reference edges, rank nodes by PPR mass.
   **Single-pass multi-hop retrieval, zero extra LLM calls** — a near-perfect fit
   for a local-first tool. Port Aider's edge-weight heuristics
   (`×sqrt(refs)`, ×10 hot idents, ×0.1 ubiquitous/private).
3. **Two-stage retrieval:** vector+FTS+RRF finds seed nodes → **graph expansion**
   assembles the slice ("neighborhood → answer").
4. **Collapsed-signature rendering** (`grep_ast`-style: keep signatures, elide
   bodies with `…`) to maximize symbols-per-token in the injected block.
5. **ANN + vector cache** (finding #2): vectorized cosine shipped this pass; next
   is an in-memory cache of unpacked vectors (skip the per-query `struct.unpack`),
   then **`sqlite-vec`** (co-locates vectors in the existing SQLite DB, binary
   quant, ANN on roadmap — the natural fit) and `usearch` only past ~100k nodes.
   ([sqlite-vec](https://github.com/asg017/sqlite-vec))

### Phase 3 — Memory that fills itself
1. **Incremental re-index** (Merkle/hash + mtime, Cursor/Continue-style): re-embed
   only changed files. Kills finding #8 and neutralizes the "staleness"
   objection to indexing.
2. **Auto-capture** (finding #7): a `PostToolUse`/`Stop` hook that deterministically
   records edited files, decisions, and commands as events — **without an LLM**.
   Optionally an *opt-in* LLM summarizer for those who want richer notes.
3. **Repo "core memory" block** (Letta-style): a small, always-injected set of
   durable facts (build/test commands, conventions, "don't touch X"), editable by
   the agent via an MCP tool, surviving compaction.
4. **`compact`-matcher SessionStart hook** to re-inject after Claude Code
   compaction — fills the exact gap where Claude Code loses scoped context.

### Phase 4 — Sensemaking & reach
1. **Community summaries** (GraphRAG/Leiden) for "explain the architecture"
   questions and as a budget-friendly compression layer.
2. **Bi-temporal edges** (Zep) tied to git commit time → "as-of commit X"
   queries, stale code invalidated not deleted.
3. **AGENTS.md** emit/consume + auto-generated, user-approvable per-project
   rules; broaden first-class support beyond Claude Code.

---

## 7. Prompt & token optimization (specifics)

- **Injected block:** already fenced (good). Make it denser — collapsed
  signatures + subsystem summaries beat raw snippets per token (Phase 2.4).
- **De-duplicate** SessionStart vs UserPrompt injections within a run
  (finding #12): track injected node ids per run, skip re-injecting.
- **Budget honesty:** real tokenizer (Phase 1.3). `chars/4` has ~28% average
  error and systematically *under*-counts code/JSON (≈3 chars/token), so a
  budgeter that trims-to-fit can silently overflow the real window on code-heavy
  slices; add tiktoken + Claude headroom.
- **Progressive disclosure** (claude-mem's ~10× claim): expose
  `search → outline → fetch` MCP tools so the agent pulls detail only when
  needed, instead of one fat block. MrFoX-MeM's RRF + graph can do this without a
  vector DB.

---

## 8. Visuals (shipped this pass)

`ui/app.js` + `ui/style.css`, **no new CDN dependency** (respects the single
pinned+SRI Cytoscape dep):
- **Typed, colored edges** by relationship — `imports` (dashed gold),
  `references` (blue), `decided_for` (dotted purple), `contains` (quiet gray) —
  so the graph *carries information*, not just topology.
- **Degree-sized nodes** (a cheap centrality proxy — hubs read as bigger) and
  **kind-specific shapes** (dirs = round-rects, modules = hexagons, docs = tags,
  concepts = diamonds) → readable at a glance.
- **Halo spotlights** on search/retrieval hits (underlay glow) + highlighted
  connected edges, reusing the existing shared-spotlight affordance.
- **Depth & motion polish:** ambient corner-light wash, header sheen, graph
  vignette, frosted legend, gradient brand wordmark, card hover-lift, glowing
  active-tab underline, a breathing "Live" indicator — all
  `prefers-reduced-motion`-safe.

*Next (optional):* node size by **PageRank** once Phase 2 lands (structural
importance, not just degree); a small stats strip (nodes · edges · tokens saved);
a light-theme toggle.

---

## 9. Summary of changes made in this review pass

| Area | Change | Files |
|---|---|---|
| Speed | Single-transaction ingest (`bulk()`) — ends per-row commit storm | `core/store.py`, `core/ingest.py` |
| Speed | Vectorized cosine (numpy-optional `M @ q`, pure-Python fallback) | `core/tree.py`, `pyproject.toml` |
| Retrieval | Reciprocal Rank Fusion replaces brittle min-max weighting | `core/tree.py` |
| Retrieval | **Personalized-PageRank graph expansion** (HippoRAG-style, pure Python) | `core/graph.py`, `core/tree.py` |
| Retrieval | Structurally-contextualized embed text (path/kind/signature) | `core/ingest.py` |
| UX | `/health` flags the degraded hashing backend loudly (+ UI pill) | `core/api.py`, `ui/app.js`, `ui/style.css` |
| Tests | 30-test pytest suite (embed/store/ingest/tree/api), all green | `tests/` |
| CI | GitHub Actions: pytest + MCP build | `.github/workflows/ci.yml` |
| Licensing | Materialized the declared MIT license | `LICENSE` |
| Dev | Dev deps + pytest config | `requirements-dev.txt`, `pyproject.toml` |
| Visuals | Typed/colored edges, degree-sized & kind-shaped nodes, halos, polish | `ui/app.js`, `ui/style.css` |
| Docs | This review | `OBSERVATIONS.md` |

### Session 2 — the green-lit build (all shipped + tested; see `CHANGELOG.md`)

| Area | Change | Files |
|---|---|---|
| Differentiator | Personalized-PageRank graph retrieval (HippoRAG-style) | `core/graph.py`, `core/tree.py` |
| Retrieval quality | Cross-encoder reranker (fastembed, graceful no-op) | `core/rerank.py`, `core/tree.py` |
| Code model | tree-sitter symbols + `references` edges (9 languages) | `core/treesit.py`, `core/ingest.py` |
| Token accuracy | tiktoken budgeting (offline cache + fallback) | `core/tokens.py`, `core/tree.py` |
| Default quality | fastembed + numpy as default install (fallback kept) | `requirements.txt`, `pyproject.toml` |
| Adoption | `requires-python` `>=3.13` → `>=3.11`; PyPI metadata | `pyproject.toml`, docs |
| Credibility | Benchmark harness (93% token savings, measured) | `scripts/benchmark.py` |
| OSS | README overhaul, CONTRIBUTING, CHANGELOG, CI 3.11–3.13 | root |

Validated: **49 tests green**, MCP `tsc` builds, full HTTP pipeline
(`/health`→`/ingest`→`/relevant`→`/tree`) exercised on a live server with the
real fastembed + reranker stack.

**Still roadmapped (deliberately deferred — larger/opinionated, not rushed):**
incremental hash-based re-index (needs deterministic node ids first), auto-capture
hook, `sqlite-vec` ANN, community summaries, bi-temporal edges. Verified designs
for each are in the research digest.
