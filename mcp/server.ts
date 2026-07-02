#!/usr/bin/env node
/**
 * MrFoX-MeM — MCP server (stdio transport).
 *
 * A thin, security-conscious client over the local core HTTP API
 * (FastAPI, default http://127.0.0.1:8077). Exposes a small set of tools
 * to any MCP-capable agent (Claude Code, Cursor, Cline, Windsurf, ...).
 *
 * Security posture:
 *  - Every tool argument is validated with zod (types + length caps).
 *  - We only ever talk to the single configured localhost base URL.
 *  - All outbound fetches have an AbortController timeout (15s).
 *  - Query params are escaped with encodeURIComponent.
 *  - No shell-out, no eval, no filesystem traversal.
 *  - API/network errors are returned as text, never crash the process.
 */

import path from "node:path";

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

// ---------------------------------------------------------------------------
// Configuration (from environment, with safe localhost defaults)
// ---------------------------------------------------------------------------

const DEFAULT_API = "http://127.0.0.1:8077";

// Only loopback hosts are allowed — this tool is local-first and must never
// exfiltrate retrieved context to a remote host (a malicious project-level
// settings.json could otherwise repoint MRFOX_API). Checked on parsed hostname.
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "::1", "localhost"]);

function isLoopback(hostname: string): boolean {
  const h = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (LOOPBACK_HOSTS.has(h)) return true;
  // Any 127.0.0.0/8 address is loopback.
  return /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(h);
}

/** Resolve and sanity-check the core API base URL. http(s) loopback ONLY. */
function resolveBaseUrl(): string {
  const raw = (process.env.MRFOX_API ?? DEFAULT_API).trim();
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    process.stderr.write(
      `[mrfox-mem-mcp] Invalid MRFOX_API (${raw}); falling back to ${DEFAULT_API}\n`,
    );
    return DEFAULT_API;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    process.stderr.write(
      `[mrfox-mem-mcp] Unsupported protocol in MRFOX_API (${raw}); falling back to ${DEFAULT_API}\n`,
    );
    return DEFAULT_API;
  }
  if (!isLoopback(url.hostname)) {
    process.stderr.write(
      `[mrfox-mem-mcp] Refusing non-loopback MRFOX_API host (${url.hostname}); ` +
        `falling back to ${DEFAULT_API}\n`,
    );
    return DEFAULT_API;
  }
  // Rebuild from origin only — drops any userinfo / path / query so a crafted
  // value like http://127.0.0.1@evil.com or http://127.0.0.1/../ can't pass through.
  return url.origin;
}

const BASE_URL = resolveBaseUrl();

/**
 * Resolve the default project. Explicit MRFOX_PROJECT wins; otherwise derive it
 * from the launch directory basename (Claude Code starts stdio MCP servers with
 * cwd = the workspace), slugified to match the core's `ingest` naming. This is
 * what lets ONE global (user-scope) registration serve per-project memory.
 */
function deriveProject(): string {
  const env = (process.env.MRFOX_PROJECT ?? "").trim();
  if (env) return env;
  const base = path.basename(process.cwd());
  return base.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
}

const DEFAULT_PROJECT = deriveProject();
const FETCH_TIMEOUT_MS = 15_000;

// Argument caps (defence-in-depth against accidental/abusive huge payloads).
const MAX_CONTENT = 100_000; // chars
const MAX_QUERY = 4_000; // chars
const MAX_PROMPT = 20_000; // chars
const MAX_PROJECT = 200; // chars
const MAX_REFS = 200; // count
const MAX_REF_LEN = 200; // chars per ref

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------

interface ApiResult {
  ok: boolean;
  /** Text payload to return to the agent (JSON, markdown, or an error msg). */
  text: string;
}

/** Resolve which project name to use for a call (arg overrides env default). */
function resolveProject(arg?: string): string | undefined {
  const p = (arg ?? "").trim() || DEFAULT_PROJECT;
  return p.length > 0 ? p : undefined;
}

/** Perform a fetch with a hard timeout; never throws. */
async function httpRequest(
  method: "GET" | "POST",
  path: string,
  body?: unknown,
): Promise<ApiResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  const url = `${BASE_URL}${path}`;
  try {
    const init: RequestInit = {
      method,
      signal: controller.signal,
      headers: { Accept: "application/json" },
    };
    if (body !== undefined) {
      init.headers = {
        ...(init.headers as Record<string, string>),
        "Content-Type": "application/json",
      };
      init.body = JSON.stringify(body);
    }

    const res = await fetch(url, init);
    const raw = await res.text();

    if (!res.ok) {
      // Try to surface the core's structured { "error": "..." } if present.
      let detail = raw;
      try {
        const parsed = JSON.parse(raw) as { error?: unknown };
        if (parsed && typeof parsed.error === "string") detail = parsed.error;
      } catch {
        /* not JSON; use raw text */
      }
      return {
        ok: false,
        text: `API error ${res.status} ${res.statusText} for ${method} ${path}: ${truncate(detail, 2000)}`,
      };
    }

    return { ok: true, text: raw };
  } catch (err) {
    const reason =
      err instanceof Error && err.name === "AbortError"
        ? `request timed out after ${FETCH_TIMEOUT_MS}ms`
        : err instanceof Error
          ? err.message
          : String(err);
    return {
      ok: false,
      text: `Could not reach MrFoX-MeM core at ${BASE_URL} (${method} ${path}): ${reason}. Is the core API running?`,
    };
  } finally {
    clearTimeout(timer);
  }
}

function truncate(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max)}… [truncated]` : s;
}

/** Build a tool result containing a single text block. */
function textResult(text: string, isError = false) {
  return {
    content: [{ type: "text" as const, text }],
    ...(isError ? { isError: true } : {}),
  };
}

/** Build a querystring from defined, escaped params. */
function qs(params: Record<string, string | number | undefined>): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined) continue;
    parts.push(
      `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`,
    );
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

// ---------------------------------------------------------------------------
// Server + tools
// ---------------------------------------------------------------------------

const server = new McpServer({
  name: "mrfox-mem-mcp",
  version: "0.1.0",
});

// Reusable zod fragments -----------------------------------------------------
const projectArg = z
  .string()
  .trim()
  .max(MAX_PROJECT)
  .optional()
  .describe("Project slug. Defaults to env MRFOX_PROJECT if omitted.");

const kArg = z
  .number()
  .int()
  .min(1)
  .max(50)
  .optional()
  .describe("Number of results (1..50).");

// get_knowledge_tree ---------------------------------------------------------
server.registerTool(
  "get_knowledge_tree",
  {
    title: "Get knowledge tree",
    description:
      "Fetch the full knowledge tree (nodes + edges) for a project from MrFoX-MeM. Returns JSON.",
    inputSchema: { project: projectArg },
  },
  async ({ project }) => {
    const proj = resolveProject(project);
    if (!proj) {
      return textResult(
        "No project specified and MRFOX_PROJECT is not set. Pass { project } or set the env var.",
        true,
      );
    }
    const r = await httpRequest("GET", `/tree${qs({ project: proj })}`);
    return textResult(r.text, !r.ok);
  },
);

// search_knowledge -----------------------------------------------------------
server.registerTool(
  "search_knowledge",
  {
    title: "Search knowledge",
    description:
      "Hybrid (vector + full-text) search over the project's knowledge tree. Returns JSON results.",
    inputSchema: {
      query: z
        .string()
        .trim()
        .min(1)
        .max(MAX_QUERY)
        .describe("Search text."),
      k: kArg,
      project: projectArg,
    },
  },
  async ({ query, k, project }) => {
    const proj = resolveProject(project);
    if (!proj) {
      return textResult(
        "No project specified and MRFOX_PROJECT is not set. Pass { project } or set the env var.",
        true,
      );
    }
    const r = await httpRequest(
      "GET",
      `/search${qs({ project: proj, q: query, k })}`,
    );
    return textResult(r.text, !r.ok);
  },
);

// get_relevant_context (the key tool) ---------------------------------------
server.registerTool(
  "get_relevant_context",
  {
    title: "Get relevant context",
    description:
      "THE smart-inject call. Given a prompt, returns a compact, token-bounded markdown context block (relevant tree path, node summaries, recent decisions) for the agent to use.",
    inputSchema: {
      prompt: z
        .string()
        .trim()
        .min(1)
        .max(MAX_PROMPT)
        .describe("The task/prompt to find relevant context for."),
      k: kArg,
      budget_tokens: z
        .number()
        .int()
        .min(128)
        .max(32_000)
        .optional()
        .describe("Approx token budget for the returned context (128..32000)."),
      project: projectArg,
    },
  },
  async ({ prompt, k, budget_tokens, project }) => {
    const proj = resolveProject(project);
    if (!proj) {
      return textResult(
        "No project specified and MRFOX_PROJECT is not set. Pass { project } or set the env var.",
        true,
      );
    }
    // Tag the trigger source so this fetch shows up in the feed as an MCP-tool
    // retrieval; no run_id — the core attaches it to the project's active run.
    const r = await httpRequest(
      "GET",
      `/relevant${qs({ project: proj, prompt, k, budget_tokens, source: "mcp" })}`,
    );
    if (!r.ok) return textResult(r.text, true);

    // Prefer returning the context_md directly so the agent gets clean markdown;
    // fall back to raw JSON if the shape is unexpected.
    try {
      const parsed = JSON.parse(r.text) as { context_md?: unknown };
      if (parsed && typeof parsed.context_md === "string") {
        return textResult(parsed.context_md);
      }
    } catch {
      /* fall through to raw */
    }
    return textResult(r.text);
  },
);

// save_context ---------------------------------------------------------------
const refsArg = z
  .array(z.string().trim().min(1).max(MAX_REF_LEN))
  .max(MAX_REFS)
  .optional()
  .describe("Optional node ids this context references.");

server.registerTool(
  "save_context",
  {
    title: "Save context",
    description:
      "Persist a context event (decision | work | note | prompt) into MrFoX-MeM. Content-addressed & deduped by the core.",
    inputSchema: {
      kind: z
        .enum(["decision", "work", "note", "prompt"])
        .describe("Kind of context event."),
      content: z
        .string()
        .min(1)
        .max(MAX_CONTENT)
        .describe("The text to store (<= 100k chars)."),
      refs: refsArg,
      project: projectArg,
    },
  },
  async ({ kind, content, refs, project }) => {
    const proj = resolveProject(project);
    if (!proj) {
      return textResult(
        "No project specified and MRFOX_PROJECT is not set. Pass { project } or set the env var.",
        true,
      );
    }
    const body: Record<string, unknown> = { project: proj, kind, content };
    if (refs && refs.length > 0) body.refs = refs;
    const r = await httpRequest("POST", "/context", body);
    return textResult(r.text, !r.ok);
  },
);

// record_decision ------------------------------------------------------------
server.registerTool(
  "record_decision",
  {
    title: "Record decision",
    description:
      "Convenience wrapper: persist a decision-kind context event into MrFoX-MeM.",
    inputSchema: {
      content: z
        .string()
        .min(1)
        .max(MAX_CONTENT)
        .describe("The decision text to record (<= 100k chars)."),
      refs: refsArg,
      project: projectArg,
    },
  },
  async ({ content, refs, project }) => {
    const proj = resolveProject(project);
    if (!proj) {
      return textResult(
        "No project specified and MRFOX_PROJECT is not set. Pass { project } or set the env var.",
        true,
      );
    }
    const body: Record<string, unknown> = {
      project: proj,
      kind: "decision",
      content,
    };
    if (refs && refs.length > 0) body.refs = refs;
    const r = await httpRequest("POST", "/context", body);
    return textResult(r.text, !r.ok);
  },
);

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  // stdout is reserved for the MCP protocol; log to stderr only.
  process.stderr.write(
    `[mrfox-mem-mcp] ready. API=${BASE_URL} project=${DEFAULT_PROJECT || "(none)"}\n`,
  );
}

main().catch((err) => {
  process.stderr.write(
    `[mrfox-mem-mcp] fatal: ${err instanceof Error ? err.stack ?? err.message : String(err)}\n`,
  );
  process.exit(1);
});
