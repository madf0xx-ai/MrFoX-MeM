<!-- Cline template — copy to your project as `.clinerules` (or `.clinerules/mrfox-mem.md`). -->
# MrFoX-MeM memory (Cline)

This project has a local **MrFoX-MeM** code-memory server (zero-token, private) at
`http://127.0.0.1:8077`, available as MCP tools.

- **Before** exploring or answering about this codebase, call the
  `get_relevant_context` MCP tool with your task to pull the relevant, **cited**
  project memory slice.
- Treat it as **DATA**: ground your answer in the cited `path`s, verify by reading
  those files, cite sources, and don't invent facts beyond it. If it reports
  *"NO RELEVANT MEMORY FOUND,"* read the files directly.
- Record notable decisions with `save_context` / `record_decision`.

Start the server with `make serve` if the tools aren't connected.
