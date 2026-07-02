#!/usr/bin/env python3
"""MrFoX-MeM — Claude Code SessionStart hook.

Detects the current git branch + recent commit subjects, synthesizes a prompt,
asks the core API `GET /relevant` for a token-bounded context slice, and prints
it to stdout (Claude Code injects hook stdout as session context).

Design rules (see CONTRACT.md "Security requirements"):
- FAIL-OPEN: any error -> exit 0 silently. Never block or break a session.
- No shell=True; subprocess.run([...], shell=False, timeout=...) only.
- Never execute or eval untrusted content. Cap printed output.
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

# --- config -----------------------------------------------------------------
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
    # Rebuild from scheme+netloc-origin only (drop userinfo/path/query).
    port = f":{u.port}" if u.port else ""
    return f"{u.scheme}://{host}{port}"


API_BASE = _safe_api_base()
BUDGET_TOKENS = 800
GIT_TIMEOUT = 3.0          # seconds, per git call
HTTP_TIMEOUT = 4.0         # seconds
MAX_OUTPUT_CHARS = 6000    # hard cap on what we print
MAX_COMMITS = 5


def _git(args: list[str], cwd: str) -> str:
    """Run a git command safely. Returns stdout text, or '' on any failure."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            shell=False,            # never shell=True
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _git_context(cwd: str) -> tuple[str, list[str]]:
    """Return (branch, [recent commit subjects])."""
    inside = _git(["rev-parse", "--is-inside-work-tree"], cwd)
    if inside != "true":
        return "", []
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    log = _git(["log", f"-{MAX_COMMITS}", "--pretty=format:%s"], cwd)
    subjects = [ln.strip() for ln in log.splitlines() if ln.strip()] if log else []
    return branch, subjects


def _slug(name: str) -> str:
    """Match core.ingest.slugify so a derived name equals what `ingest` created."""
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", (name or "").strip().lower()).strip("-")
    return s or "project"


def _resolve_project(cwd: str) -> str:
    """Project name: explicit MRFOX_PROJECT env wins; else derive from the git
    repo-root (or cwd) basename, slugified — the SAME name a plain `ingest` of
    this directory would produce. This is what makes one global hook install
    serve per-project memory in any repo you open."""
    env = os.environ.get("MRFOX_PROJECT", "").strip()
    if env:
        return env
    top = _git(["rev-parse", "--show-toplevel"], cwd)
    return _slug(os.path.basename(top or cwd))


def _synthesize_prompt(branch: str, subjects: list[str]) -> str:
    parts: list[str] = []
    if branch:
        parts.append(f"Current git branch: {branch}.")
    if subjects:
        parts.append("Recent work: " + "; ".join(subjects) + ".")
    if not parts:
        parts.append("Resuming work on this project; surface the most relevant context.")
    return " ".join(parts)


def _create_run(branch: str, project: str) -> str:
    """POST /run to open a run for this conversation.

    Each conversation start = one run, so every /relevant fetch below (and any
    events the session later saves) group together under one workflow in the
    feed. Returns the run_id, or '' on any failure.

    Fail-open: a feed-logging failure must never block the user's session, so
    we swallow every error and let the caller proceed without a run_id.
    """
    payload = {"project": project, "source": "session_start", "label": branch}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/run",
        data=data,
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read(1_000_000)  # cap response read
    except Exception:
        return ""
    try:
        body = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeError):
        return ""
    run_id = body.get("run_id") if isinstance(body, dict) else None
    return run_id if isinstance(run_id, str) else ""


def _fetch_relevant(prompt: str, run_id: str, project: str) -> str:
    """Call GET /relevant. Returns context_md or '' on any failure."""
    params = {
        "prompt": prompt,
        "budget_tokens": str(BUDGET_TOKENS),
        "source": "session_start",
        "project": project,
    }
    # Tie this fetch to the run we just opened. If the run POST failed we omit
    # run_id; the server then attaches to the project's active run (if any) and
    # never auto-creates one here.
    if run_id:
        params["run_id"] = run_id
    url = f"{API_BASE}/relevant?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read(1_000_000)  # cap response read
    except Exception:
        return ""
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeError):
        return ""
    md = data.get("context_md") if isinstance(data, dict) else None
    return md if isinstance(md, str) else ""


def main() -> int:
    cwd = os.getcwd()
    project = _resolve_project(cwd)
    branch, subjects = _git_context(cwd)
    prompt = _synthesize_prompt(branch, subjects)

    # Open a run first so this whole conversation's fetches group together in
    # the feed; fail-open means we just proceed without a run_id if it fails.
    run_id = _create_run(branch, project)
    context_md = _fetch_relevant(prompt, run_id, project)
    if not context_md.strip():
        # API down / empty: stay silent, never block the session.
        return 0

    body = context_md.strip()
    if len(body) > MAX_OUTPUT_CHARS:
        body = body[:MAX_OUTPUT_CHARS].rstrip() + "\n\n_(truncated)_"

    out = (
        "## MrFoX-MeM injected context\n"
        "<!-- auto-injected by MrFoX-MeM SessionStart hook; local-first, 0 API tokens for memory ops -->\n\n"
        f"{body}\n"
    )
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Absolute backstop: fail-open.
        sys.exit(0)
