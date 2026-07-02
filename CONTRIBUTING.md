# Contributing to MrFoX-MeM

Thanks for helping make local-first agent memory better. This is a single-user,
localhost-only tool — **security and correctness come first** (see `SECURITY.md`).

## Development setup

```sh
# Core + dev/test deps (pytest, httpx, numpy).
pip install -r requirements-dev.txt         # or: uv pip install -r requirements-dev.txt

# Optional accelerators (recommended for real use, not required for tests):
pip install "fastembed>=0.4"                 # real embeddings + reranker
pip install tiktoken                         # accurate token budgeting
pip install "tree-sitter-language-pack>=1.10"  # multi-language symbols
```

The MCP server (TypeScript) lives in `mcp/`:

```sh
cd mcp && npm install && npm run build
```

## Running tests

```sh
pytest                     # 49 tests; runs fully offline in ~1s
```

The suite is **hermetic** (`tests/conftest.py` forces the dependency-free
backends), so it is deterministic whether or not the optional accelerators are
installed. Tests that exercise tree-sitter grammars skip cleanly when offline.

**Every change must keep the suite green, and new behavior must add a test.**

## Benchmark

```sh
python scripts/benchmark.py --path .        # token-savings / latency / graph size
```

## Ground rules

- **No network / no LLM in the memory path.** Ingestion is deterministic and
  static-parse only — it must never execute or import a scanned repo's code.
- **Security is not optional.** Keep parameterized SQL, the `127.0.0.1` bind, the
  secret-redaction scan, the path-traversal guards, and the untrusted-data
  fencing intact. Anything touching these needs a test.
- **Optional deps degrade gracefully.** If a backend (fastembed / tiktoken /
  tree-sitter) is missing, the tool must still run on the built-in fallback.
- **Match the surrounding style:** stdlib-first, small focused functions,
  comments that explain *why*.

## Pull requests

1. Branch from `main`.
2. Keep the diff focused; one concern per PR.
3. `pytest` green + a test for the new behavior.
4. Update `CHANGELOG.md` under `[Unreleased]`.
5. Describe the change and its security implications (if any) in the PR body.

## Reporting security issues

Please **do not** open a public issue for vulnerabilities — see `SECURITY.md`
for private disclosure.
