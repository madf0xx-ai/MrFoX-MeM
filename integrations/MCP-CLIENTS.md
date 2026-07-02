# MrFoX-MeM — MCP Server Registration for 5 Agentic Clients

The MrFoX-MeM MCP server is a Node, stdio server. It is launched as:

```
node <ABS_PATH>/mcp/dist/server.js
```

with two environment variables:

| Var             | Default                 | Purpose                          |
| --------------- | ----------------------- | -------------------------------- |
| `MRFOX_API`     | `http://127.0.0.1:8077` | MrFoX-MeM API base URL           |
| `MRFOX_PROJECT` | _(your project id)_     | Which project's memory to use    |

Tools exposed: `get_knowledge_tree`, `search_knowledge`, `get_relevant_context`, `save_context`, `record_decision`.

> **Replace `<ABS_PATH>`** everywhere below with the absolute path to your MrFoX-MeM checkout.
> On this machine that is: `/Users/koru/Documents/MrFox MeM/MrFoX-MeM`
> Note: that path contains a **space** — keep the whole path quoted in JSON strings and in the shell.

All five clients use a stdio launch. The server name everywhere is **`mrfox-mem`**.

---

## 1. Claude Code

**Source:** https://code.claude.com/docs/en/mcp (verified)

### CLI (recommended)

For stdio servers the `--` separates Claude's flags from the command that runs the server. All Claude options (`--transport`, `--env`, `--scope`) go **before** the `--`.

```bash
claude mcp add mrfox-mem \
  --transport stdio \
  --scope project \
  --env MRFOX_API=http://127.0.0.1:8077 \
  --env MRFOX_PROJECT=my-project \
  -- node "<ABS_PATH>/mcp/dist/server.js"
```

Scope flag values (`--scope`):

| Scope     | Where it is written                                  | Shared?                         |
| --------- | ---------------------------------------------------- | ------------------------------- |
| `local`   | `~/.claude.json`, keyed by current project path (default) | No — this machine + project only |
| `user`    | `~/.claude.json`, global                             | No — all your projects, this machine |
| `project` | `.mcp.json` at the project root                      | Yes — commit to git for the team |

> Gotcha: `--env` takes multiple `KEY=value` pairs. Do **not** put the server name immediately after `--env` — keep another flag (e.g. `--transport`) between them, or the CLI reads the name as another env pair and rejects it.

### Project file `.mcp.json` (commit to git)

```json
{
  "mcpServers": {
    "mrfox-mem": {
      "type": "stdio",
      "command": "node",
      "args": ["<ABS_PATH>/mcp/dist/server.js"],
      "env": {
        "MRFOX_API": "http://127.0.0.1:8077",
        "MRFOX_PROJECT": "my-project"
      }
    }
  }
}
```

Verify / manage: `claude mcp list`, `claude mcp get mrfox-mem`, `claude mcp remove mrfox-mem`.

---

## 2. Cursor

**Source:** https://cursor.com/docs/mcp (verified)

Cursor has **no CLI** — edit a JSON file. Same `mcpServers` shape as Claude Desktop.

| Scope   | macOS / Linux             | Windows                          |
| ------- | ------------------------- | -------------------------------- |
| Global  | `~/.cursor/mcp.json`      | `%USERPROFILE%\.cursor\mcp.json` |
| Project | `.cursor/mcp.json` (in repo) | `.cursor\mcp.json` (in repo)  |

```json
{
  "mcpServers": {
    "mrfox-mem": {
      "command": "node",
      "args": ["<ABS_PATH>/mcp/dist/server.js"],
      "env": {
        "MRFOX_API": "http://127.0.0.1:8077",
        "MRFOX_PROJECT": "my-project"
      }
    }
  }
}
```

(There is no `type` field for stdio in Cursor — presence of `command` implies stdio; remote servers use `url` instead.)

---

## 3. Windsurf (Codeium / Cascade)

**Source:** https://docs.windsurf.com/windsurf/cascade/mcp (verified; currently 307-redirects to docs.devin.ai/desktop/cascade/mcp)

No CLI — edit the config file (or use the GUI "Manage plugins → View raw config" in Cascade).

| OS            | Path                                                  |
| ------------- | ----------------------------------------------------- |
| macOS / Linux | `~/.codeium/windsurf/mcp_config.json`                 |
| Windows       | `%USERPROFILE%\.codeium\windsurf\mcp_config.json`     |

```json
{
  "mcpServers": {
    "mrfox-mem": {
      "command": "node",
      "args": ["<ABS_PATH>/mcp/dist/server.js"],
      "env": {
        "MRFOX_API": "http://127.0.0.1:8077",
        "MRFOX_PROJECT": "my-project"
      }
    }
  }
}
```

Useful extras Windsurf supports per server: `disabled` (bool), `alwaysAllow` (array of auto-approved tool names). Env/args support interpolation: `${env:VAR_NAME}` and `${file:/path/to/file}`. Remote servers use `serverUrl` instead of `command`.

---

## 4. GitHub Copilot (VS Code agent mode)

**Source:** https://code.visualstudio.com/docs/agent-customization/mcp-servers and https://code.visualstudio.com/docs/agents/reference/mcp-configuration (verified)

> IMPORTANT — VS Code differs from the others:
> - Root key is **`servers`** (NOT `mcpServers`).
> - stdio entries should set **`"type": "stdio"`**.
> - MCP tools only appear in Copilot Chat **Agent mode** (mode dropdown → Agent).

| Scope     | Location                                                                 |
| --------- | ----------------------------------------------------------------------- |
| Workspace | `.vscode/mcp.json` (commit to share with team)                          |
| User      | Run command **MCP: Open User Configuration** → opens user-profile `mcp.json` (syncs via Settings Sync) |

```json
{
  "servers": {
    "mrfox-mem": {
      "type": "stdio",
      "command": "node",
      "args": ["<ABS_PATH>/mcp/dist/server.js"],
      "env": {
        "MRFOX_API": "http://127.0.0.1:8077",
        "MRFOX_PROJECT": "my-project"
      }
    }
  }
}
```

After saving `.vscode/mcp.json`, a **Start** code-lens appears above the server — click it to launch and discover tools.

Note (JetBrains / Visual Studio): GitHub Copilot in JetBrains and Visual Studio 2022 also support MCP via their own settings UIs / `mcp.json`, and those configs likewise accept an `env` field. Their exact file paths were not verified here.

---

## 5. Gemini CLI

**Source:** https://geminicli.com/docs/tools/mcp-server/ (verified)

### CLI

```
gemini mcp add [options] <name> <command> [args...]
```

```bash
gemini mcp add \
  -s user \
  -e MRFOX_API=http://127.0.0.1:8077 \
  -e MRFOX_PROJECT=my-project \
  mrfox-mem node "<ABS_PATH>/mcp/dist/server.js"
```

Flags: `-t, --transport` (default `stdio`), `-s, --scope` (`user` or `project`; default `project`), `-e, --env KEY=value` (repeatable), `--trust` (skip tool-call confirmations). Manage with `gemini mcp list` / `gemini mcp remove mrfox-mem`.

### Settings file

| Scope   | macOS / Linux               | Windows                              |
| ------- | --------------------------- | ------------------------------------ |
| User    | `~/.gemini/settings.json`   | `%USERPROFILE%\.gemini\settings.json`|
| Project | `.gemini/settings.json`     | `.gemini\settings.json`              |

```json
{
  "mcpServers": {
    "mrfox-mem": {
      "command": "node",
      "args": ["<ABS_PATH>/mcp/dist/server.js"],
      "env": {
        "MRFOX_API": "http://127.0.0.1:8077",
        "MRFOX_PROJECT": "my-project"
      }
    }
  }
}
```

Optional per-server fields Gemini supports: `cwd`, `timeout` (ms), `trust` (bool). Env values support `$VAR`, `${VAR}` (all OSes) and `%VAR%` (Windows).

---

## Per-OS notes (apply to all clients)

- **Use absolute paths.** The working directory of a spawned stdio server is not guaranteed, so always give the full path to `server.js`. Replace `<ABS_PATH>` everywhere.
- **Paths with spaces** (this checkout lives under `.../MrFox MeM/...`): keep the path as a single quoted JSON string (`"args": ["/Users/.../MrFox MeM/MrFoX-MeM/mcp/dist/server.js"]`). In a shell command, quote the whole path. Do **not** split it across array elements.
- **`node` on PATH.** All examples use bare `command: "node"`, which requires `node` to be resolvable in the client's environment. If a client can't find it, use the absolute interpreter path instead:
  - macOS/Linux: e.g. `"command": "/usr/local/bin/node"` (find with `which node`).
  - Windows: e.g. `"command": "C:\\Program Files\\nodejs\\node.exe"`.
- **Windows backslash escaping in JSON.** Backslashes must be doubled inside JSON strings:
  `"args": ["C:\\Users\\you\\MrFoX-MeM\\mcp\\dist\\server.js"]`.
  Forward slashes also work on Windows and avoid escaping: `"C:/Users/you/MrFoX-MeM/mcp/dist/server.js"`.
- **`cmd /c` wrapper.** A bare `node` invocation does **not** need a `cmd /c` wrapper on Windows. The `cmd /c` trick is only needed for `.cmd`/`.bat` shims such as `npx`/`npm` on some client+Windows combinations. Since MrFoX-MeM launches `node` directly, you can keep `"command": "node"` (or the `node.exe` absolute path).
- **Env var values.** `MRFOX_API` defaults to `http://127.0.0.1:8077`; you only need to set it if your API runs elsewhere. `MRFOX_PROJECT` should be set to the project id you want this client bound to.

## Verification status

| Client        | Format/path verified against official docs? |
| ------------- | ------------------------------------------- |
| Claude Code   | Yes — code.claude.com/docs/en/mcp           |
| Cursor        | Yes — cursor.com/docs/mcp                    |
| Windsurf      | Yes — docs.windsurf.com (→ docs.devin.ai)    |
| GitHub Copilot (VS Code) | Yes — code.visualstudio.com docs |
| Gemini CLI    | Yes — geminicli.com/docs                      |

JetBrains/Visual Studio Copilot exact paths: **not verified** (noted inline).
