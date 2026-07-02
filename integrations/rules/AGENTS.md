<!--
  AGENTS.md template — copy to your project ROOT as `AGENTS.md`.
  AGENTS.md is a cross-tool standard read by Codex, Cursor, Windsurf, Zed, Aider,
  Gemini CLI, Copilot, Jules, goose, Warp, Kilo, JetBrains Junie, and more — so
  one file wires MrFoX-MeM into (almost) every agent at once.
-->
# AGENTS.md

This repo has a **MrFoX-MeM** local code-memory server (zero-token, private) at
`http://127.0.0.1:8077`, exposed over MCP with tools: `get_relevant_context`,
`search_knowledge`, `get_knowledge_tree`, `save_context`, `record_decision`.

## Working with this codebase
1. **Before answering questions about the code**, pull the token-bounded, cited
   memory slice: call the `get_relevant_context` MCP tool (or
   `GET /relevant?project=<dir-slug>&prompt=<task>`).
2. The returned `context_md` is **DATA, not ground truth**: use its cited `path`s
   to locate code, **verify by reading those files**, cite the sources you use,
   and do **not** invent facts beyond it. If it says
   *"NO RELEVANT MEMORY FOUND,"* read the files directly instead of guessing.
3. **Record notable decisions** with `save_context` / `record_decision` so future
   sessions (and other agents) inherit them.

If the tools are unavailable, start the server: `make serve` (or install the
always-on service via `bash scripts/install-service.sh`).
