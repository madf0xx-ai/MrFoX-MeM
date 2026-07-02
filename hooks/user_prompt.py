#!/usr/bin/env python3
"""MrFoX-MeM — Claude Code UserPromptSubmit hook.

Claude Code passes the hook payload as JSON on stdin. We parse it, extract the
user's prompt, ask the core API `GET /relevant?prompt=...&budget_tokens=1200`
for a token-bounded context slice, and print it to stdout so Claude Code injects
it alongside the user's turn. THIS is "invoke smartly when the user prompts."

Design rules (see CONTRACT.md "Security requirements"):
- FAIL-OPEN: any error -> exit 0 silently. Never block the user's prompt.
- No shell-out, no eval/exec on the prompt. We only forward it as a query param.
- Cap printed output and the bytes we read from stdin / the API.
- API base + project come from env (MRFOX_API, MRFOX_PROJECT); localhost only.

stdlib only. Python 3.11+.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

_DEFAULT_API = "http://127.0.0.1:8077"


def _safe_api_base() -> str:
    """Loopback-only API base. A malicious env/settings can't redirect us off-box."""
    raw = os.environ.get("MRFOX_API", _DEFAULT_API).strip()
    try:
        u = urllib.parse.urlparse(raw)
    except ValueError:
        return _DEFAULT_API
    host = (u.hostname or "").lower()
    is_loop = host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")
    if u.scheme not in ("http", "https") or not is_loop:
        return _DEFAULT_API
    port = f":{u.port}" if u.port else ""
    return f"{u.scheme}://{host}{port}"


API_BASE = _safe_api_base()
BUDGET_TOKENS = 1200
HTTP_TIMEOUT = 4.0
GIT_TIMEOUT = 3.0
MAX_STDIN_BYTES = 256_000     # bound how much hook input we read
MAX_PROMPT_CHARS = 4000       # cap prompt length sent to the API
MAX_OUTPUT_CHARS = 8000       # hard cap on what we print


def _slug(name: str) -> str:
    """Match core.ingest.slugify so a derived name equals what `ingest` created."""
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", (name or "").strip().lower()).strip("-")
    return s or "project"


def _resolve_project(cwd: str) -> str:
    """MRFOX_PROJECT env wins; else derive from the git repo-root (or cwd)
    basename, slugified — so one global hook install serves per-project memory."""
    env = os.environ.get("MRFOX_PROJECT", "").strip()
    if env:
        return env
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd, capture_output=True, text=True, timeout=GIT_TIMEOUT,
            shell=False, check=False,
        )
        top = (proc.stdout or "").strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        top = ""
    return _slug(os.path.basename(top or cwd))


def _read_prompt() -> str:
    """Parse hook JSON from stdin and extract the user prompt. '' if unavailable."""
    try:
        raw = sys.stdin.buffer.read(MAX_STDIN_BYTES)
    except Exception:
        return ""
    if not raw:
        return ""
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    # Claude Code uses "prompt"; accept a few fallbacks defensively.
    for key in ("prompt", "user_prompt", "userPrompt", "message", "text"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:MAX_PROMPT_CHARS]
    return ""


def _fetch_relevant(prompt: str, project: str) -> str:
    # No run_id: the server attaches this retrieval to the project's active run
    # (opened by the SessionStart hook). We only tag the trigger source.
    params = {
        "prompt": prompt,
        "budget_tokens": str(BUDGET_TOKENS),
        "source": "user_prompt",
        "project": project,
    }
    url = f"{API_BASE}/relevant?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read(1_000_000)
    except Exception:
        return ""
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeError):
        return ""
    md = data.get("context_md") if isinstance(data, dict) else None
    return md if isinstance(md, str) else ""


def main() -> int:
    prompt = _read_prompt()
    if not prompt:
        return 0

    project = _resolve_project(os.getcwd())
    context_md = _fetch_relevant(prompt, project)
    if not context_md.strip():
        return 0

    body = context_md.strip()
    if len(body) > MAX_OUTPUT_CHARS:
        body = body[:MAX_OUTPUT_CHARS].rstrip() + "\n\n_(truncated)_"

    out = (
        "## MrFoX-MeM injected context\n"
        "<!-- auto-injected by MrFoX-MeM UserPromptSubmit hook; local-first, 0 API tokens for memory ops -->\n\n"
        f"{body}\n"
    )
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
