# MrFoX-MeM — Compatibility

Two axes: **operating systems** and **agentic-AI clients**. Summary of what's supported, how, and
the honest limits.

## Operating systems

| Concern | macOS | Linux | Windows | How |
|---|---|---|---|---|
| Core API (Python/FastAPI) | ✅ | ✅ | ✅ | stdlib + uvicorn; `os.path`/`pathlib` everywhere; `data/` created with `os.makedirs(exist_ok=True)`; SQLite WAL works on all |
| Local embeddings | ✅ | ✅ | ✅ | hashing fallback is pure-Python; fastembed (optional) is ONNX/CPU cross-OS |
| MCP server (Node) | ✅ | ✅ | ✅ | stdio JSON-RPC; `node mcp/dist/server.js`; no OS calls |
| Web UI | ✅ | ✅ | ✅ | static + Cytoscape CDN (SRI), served same-origin |
| Hooks (Python stdlib) | ✅ | ✅ | ✅ | `subprocess.run([...], shell=False)`; fail-open |
| Ingest denylist | ✅ | ✅ | ✅ | platform-aware `_denied_roots()` (darwin/nt/linux) + `normcase` for Windows case-insensitivity |
| Launcher | `cli.py` / `make` / `run.sh` | `cli.py` / `make` / `run.sh` | **`cli.py`** / `run.ps1` | `make`/`bash`/`curl` aren't on stock Windows → use the stdlib CLI |

**Canonical cross-OS entrypoint** (no make/bash/curl needed):
```sh
python cli.py setup          # venv + deps + MCP build
python cli.py serve-open     # start API + open UI in default browser (webbrowser module)
python cli.py ingest --path /abs/project --project my-project
python cli.py mcp            # run the MCP server
```
`make` / `run.sh` remain as the Unix convenience path; `run.ps1` is the Windows shell path.
Browser opening uses Python's `webbrowser` (never macOS-only `open`).

## Agentic-AI clients

The MCP server is **client-agnostic** — same command + two env vars everywhere. Only the *config
location/shape* and the *auto-injection mechanism* differ. Exact, web-verified, per-OS configs:
**`integrations/MCP-CLIENTS.md`**. Native auto-context rule templates: **`integrations/rules/`** +
**`integrations/AUTO-CONTEXT.md`**.

| Client | MCP config location | Config root key | Auto-context "smart invoke" |
|---|---|---|---|
| **Claude Code** | `claude mcp add …` / `.mcp.json` | `mcpServers` | **Fully automatic** — SessionStart + UserPromptSubmit **hooks** inject `/relevant` every session/prompt |
| **Cursor** | `~/.cursor/mcp.json` or `.cursor/mcp.json` | `mcpServers` | Rule `.cursor/rules/mrfox-mem.mdc` (`alwaysApply`) → agent calls `get_relevant_context` |
| **GitHub Copilot** (VS Code agent) | `.vscode/mcp.json` / user `mcp.json` | **`servers`** ⚠️ | `.github/copilot-instructions.md` → agent calls the tool |
| **Gemini CLI** | `gemini mcp add …` / `~/.gemini/settings.json` | `mcpServers` | `GEMINI.md` (project or `~/.gemini/`) → agent calls the tool |
| **Windsurf** | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` | `.windsurf/rules/mrfox-mem.md` (`always_on`) → Cascade calls the tool |

### The honest limitation (read this)
**Only Claude Code gets *fully automatic* memory injection**, because only it has a hook system that
runs code on session start / every prompt. For the other four clients, "smart invoke" means: the
MCP tool exists, and a **native rules file instructs the agent to call `get_relevant_context` first**
— but it is **model-honored, not guaranteed**. The agent chooses to call it. That is a real
difference, not marketing. If you need deterministic injection on a non-Claude client, call the tool
explicitly or wrap the client in a script that hits `/relevant` before the prompt.

### Per-OS client gotchas (see `integrations/MCP-CLIENTS.md` for detail)
- Use an **absolute path** to `mcp/dist/server.js`; if it contains spaces, keep it one JSON string.
- Windows: `node` on PATH usually works; for npm/npx shims you'd need `cmd /c`, but bare `node` does not.
- Copilot/VS Code root key is `servers` (not `mcpServers`) and requires **Agent mode**.
- JetBrains/VS2022 Copilot MCP paths are **unverified** here — confirm against current docs.

## What "compatible with all OS / all agents" does and doesn't mean
- **Does**: the engine, server, UI, hooks, and launcher run on macOS/Linux/Windows; the MCP server
  works with every MCP-capable client; configs for the 5 named clients are documented per-OS.
- **Doesn't**: automatic per-prompt injection only on Claude Code; non-MCP clients are out of scope;
  JetBrains Copilot specifics unverified.
