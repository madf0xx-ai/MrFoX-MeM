# MrFoX-MeM — build & run targets
# Local-first context layer for agentic AIs. Everything binds to 127.0.0.1.
#
# NOTE (Windows users): this Makefile needs make/bash/curl. For a no-make,
# cross-OS path use the stdlib Python CLI instead — it works identically on
# macOS, Linux, and Windows:
#     python cli.py setup            (equivalent to: make setup)
#     python cli.py serve            (equivalent to: make serve)
#     python cli.py ingest --path /abs/dir [--project name]
#     python cli.py mcp              (equivalent to: make mcp)
#     python cli.py serve-open       (serve + open the UI)
#
# Common targets:
#   make setup                 # create venv, install core deps, build MCP server
#   make serve                 # run the core API (FastAPI) on 127.0.0.1:8077
#   make ingest PATH=/abs/dir  # POST /ingest for a project directory (uses curl)
#   make mcp                   # run the MCP stdio server
#   make clean                 # remove venv, node_modules, dist, caches, db

SHELL       := /bin/bash
HOST        := 127.0.0.1
PORT        := 8077
API_BASE    ?= http://$(HOST):$(PORT)
MRFOX_PROJECT ?= my-project
VENV        := .venv
CURL        := /usr/bin/curl

# `make ingest PATH=...` is the documented form, but PATH is also the shell's
# executable-search variable — overriding it would break the recipe's tools.
# Detect when PATH was passed on the command line and route it to INGEST_DIR;
# otherwise accept the friendlier DIR= alias. Recipes call curl by absolute
# path so a polluted PATH can never break ingest.
ifeq ($(origin PATH),command line)
  INGEST_DIR := $(PATH)
else
  INGEST_DIR := $(DIR)
endif

.PHONY: setup serve ingest mcp clean help test test-deps bench

help:
	@echo "MrFoX-MeM targets: setup | serve | ingest PATH=/abs/dir | mcp | test | bench | clean"

# ---------------------------------------------------------------------------
# setup: Python venv (uv) + core deps, then MCP server install + build.
# ---------------------------------------------------------------------------
setup:
	@command -v uv >/dev/null 2>&1 || { echo "error: 'uv' not found. Install: https://docs.astral.sh/uv/"; exit 1; }
	uv venv $(VENV)
	uv pip install --python $(VENV) -r requirements.txt
	cd mcp && npm install && npm run build
	@echo "setup complete. Next: make serve (then open http://$(HOST):$(PORT)/ )"

# ---------------------------------------------------------------------------
# serve: start the core API. Binds to 127.0.0.1 only (never 0.0.0.0).
# ---------------------------------------------------------------------------
serve:
	uv run --python $(VENV) uvicorn core.api:app --host $(HOST) --port $(PORT)

# ---------------------------------------------------------------------------
# ingest: POST the project dir to the RUNNING core API via curl.
# We deliberately use the HTTP API (not core internals) so this stays decoupled.
# Start the API first (make serve) in another shell.
# ---------------------------------------------------------------------------
ingest:
	@test -n "$(INGEST_DIR)" || { echo "usage: make ingest PATH=/abs/project/dir [MRFOX_PROJECT=name]"; exit 2; }
	@test -d "$(INGEST_DIR)" || { echo "error: not a directory: $(INGEST_DIR)"; exit 2; }
	@echo "Ingesting '$(INGEST_DIR)' into project '$(MRFOX_PROJECT)' via $(API_BASE)/ingest ..."
	@# `make ingest PATH=...` overrides the shell's PATH for this recipe, so we
	@# restore standard bin dirs to keep python3 (JSON builder) resolvable. curl
	@# is already called by absolute path. Everything runs in one shell.
	@PATH="/usr/bin:/bin:/usr/local/bin:$(HOME)/.local/bin:$$PATH"; \
	  body=$$(python3 -c 'import json,sys; print(json.dumps({"path": sys.argv[1], "project": sys.argv[2]}))' "$(INGEST_DIR)" "$(MRFOX_PROJECT)") || { echo "error: python3 not found to build request body"; exit 1; }; \
	  $(CURL) --fail --silent --show-error \
	    -X POST "$(API_BASE)/ingest" \
	    -H 'Content-Type: application/json' \
	    --data-binary "$$body" \
	    && echo "" \
	    || { echo "ingest failed — is the API running? (make serve)"; exit 1; }

# ---------------------------------------------------------------------------
# mcp: run the compiled MCP stdio server (build first via `make setup`).
# ---------------------------------------------------------------------------
mcp:
	@test -f mcp/dist/server.js || { echo "error: mcp/dist/server.js missing. Run 'make setup' first."; exit 1; }
	node mcp/dist/server.js

# ---------------------------------------------------------------------------
# test: install dev deps + run the hermetic pytest suite (fully offline, ~1s).
# ---------------------------------------------------------------------------
test-deps:
	uv pip install --python $(VENV) -r requirements-dev.txt

test:
	uv run --python $(VENV) pytest

# ---------------------------------------------------------------------------
# bench: token-savings / latency / graph-size benchmark on a project dir.
# ---------------------------------------------------------------------------
bench:
	uv run --python $(VENV) python scripts/benchmark.py --path "$(if $(INGEST_DIR),$(INGEST_DIR),.)"

# ---------------------------------------------------------------------------
# clean: remove generated artifacts. Leaves source untouched.
# ---------------------------------------------------------------------------
clean:
	rm -rf $(VENV)
	rm -rf mcp/node_modules mcp/dist
	rm -f data/*.db data/*.db-wal data/*.db-shm
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "cleaned."
