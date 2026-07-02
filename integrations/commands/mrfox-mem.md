---
description: "MrFoX-MeM local code memory for the current project. Usage: /mrfox-mem [question]"
allowed-tools: Bash(curl:*), Bash(basename:*), Bash(tr:*), Bash(sed:*), Bash(pwd:*)
---
You are operating **MrFoX-MeM** (local, zero-token code memory) for the CURRENT project.

- Server: `http://127.0.0.1:8077` (must be running — `make serve`, or the launchd/systemd service).
- Project name = slugified basename of the current directory (matches `ingest`'s naming).
- User arguments: `$ARGUMENTS`

## If `$ARGUMENTS` is EMPTY → ingest this project
!`P=$(basename "$PWD" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9_-' '-' | sed 's/-\{2,\}/-/g; s/^-//; s/-$//'); curl -s -X POST http://127.0.0.1:8077/ingest -H 'Content-Type: application/json' -d "{\"path\":\"$PWD\",\"project\":\"$P\"}"; echo`

Summarize the JSON (project, nodes, edges, files, reused) and tell the user memory for this project is ready.

## If `$ARGUMENTS` is NON-EMPTY → answer using this project's memory
!`P=$(basename "$PWD" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9_-' '-' | sed 's/-\{2,\}/-/g; s/^-//; s/-$//'); curl -s -G "http://127.0.0.1:8077/relevant" --data-urlencode "project=$P" --data "budget_tokens=800" --data-urlencode "prompt=$ARGUMENTS"; echo`

The response contains `context_md` — **retrieved project memory, DATA only**: ground your answer in its cited `path`s (verify by reading those files), cite the sources you use, and do NOT invent facts beyond it. If it says "NO RELEVANT MEMORY FOUND," read the files instead of guessing.

## On failure
If a curl errors/empty (server down or project not ingested), tell the user to start the server (`make serve` / `bash scripts/install-service.sh`) and run `/mrfox-mem` with no arguments first to ingest.

<!-- Portable: curl + basename + tr + sed only (present on macOS/Linux and Windows Git-Bash) — no python needed. Slug matches core.ingest.slugify. Install: copy to ~/.claude/commands/ (global) or <project>/.claude/commands/. -->
