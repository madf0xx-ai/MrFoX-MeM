#!/usr/bin/env python3
"""MrFoX-MeM — cross-OS command-line entrypoint (stdlib only).

This is the canonical, OS-agnostic way to drive MrFoX-MeM on macOS, Linux, and
Windows. It needs nothing but a Python 3.13 interpreter on PATH — no make, no
bash, no curl. Every subprocess call is list-form with shell=False, every tool
is resolved via sys.executable / shutil.which (never a hardcoded /usr/bin path),
and every URL fetch uses urllib instead of curl.

Usage:
    python cli.py setup                         # venv + deps + build MCP server
    python cli.py serve                         # run the core API (127.0.0.1)
    python cli.py ingest --path /abs/dir [--project name]
    python cli.py mcp                           # run the MCP stdio server
    python cli.py open                          # open the UI in the browser
    python cli.py serve-open                    # serve, wait for health, open UI

Quickstart (any OS):
    python cli.py setup && python cli.py serve-open
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

# --- constants ---------------------------------------------------------------
# Project root = the directory holding this file, so every path is absolute and
# the CLI works no matter what the current working directory is.
ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
MCP_DIR = ROOT / "mcp"
REQUIREMENTS = ROOT / "requirements.txt"

HOST = "127.0.0.1"


def port() -> int:
    """Resolve the API port from MRFOX_PORT (default 8077)."""
    raw = os.environ.get("MRFOX_PORT", "8077").strip()
    try:
        return int(raw)
    except ValueError:
        sys.exit(f"error: MRFOX_PORT must be an integer, got {raw!r}")


def base_url() -> str:
    return f"http://{HOST}:{port()}"


# --- OS-aware path resolution ------------------------------------------------
def venv_python() -> Path:
    """Path to the venv's Python interpreter, per-OS.

    POSIX: .venv/bin/python   Windows: .venv\\Scripts\\python.exe
    """
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def require_venv() -> Path:
    """Return the venv python path or exit with a helpful message."""
    py = venv_python()
    if not py.exists():
        sys.exit(
            f"error: virtualenv not found at {VENV_DIR}\n"
            "Run 'python cli.py setup' first."
        )
    return py


def which_or_die(tool: str, hint: str) -> str:
    """Resolve an executable via PATH (handles npm.cmd on Windows) or exit."""
    found = shutil.which(tool)
    if not found:
        sys.exit(f"error: '{tool}' not found on PATH. {hint}")
    return found


def run(cmd: list[str], **kwargs) -> int:
    """Run a list-form command (shell=False) and return its exit code."""
    printable = " ".join(str(c) for c in cmd)
    print(f"[mrfox] $ {printable}", flush=True)
    return subprocess.run(cmd, shell=False, **kwargs).returncode  # noqa: S603


# --- subcommands -------------------------------------------------------------
def cmd_setup(_args: argparse.Namespace) -> int:
    """Create the venv, install Python deps, then build the MCP server."""
    # 1. Create the virtualenv with the *current* interpreter — no external uv
    #    needed, works identically on every OS.
    if venv_python().exists():
        print(f"[mrfox] venv already exists at {VENV_DIR}")
    else:
        print(f"[mrfox] creating venv at {VENV_DIR} ...")
        rc = run([sys.executable, "-m", "venv", str(VENV_DIR)])
        if rc != 0:
            return rc

    py = venv_python()
    if not py.exists():
        sys.exit(f"error: venv creation did not produce {py}")

    # 2. Install Python deps with the venv's own pip (resolved as a module so we
    #    never depend on a 'pip' shim being on PATH).
    print("[mrfox] upgrading pip ...")
    run([str(py), "-m", "pip", "install", "--upgrade", "pip"])
    print(f"[mrfox] installing Python deps from {REQUIREMENTS.name} ...")
    rc = run([str(py), "-m", "pip", "install", "-r", str(REQUIREMENTS)])
    if rc != 0:
        return rc

    # 3. Build the MCP server: npm install + npm run build inside mcp/.
    #    npm is 'npm.cmd' on Windows — shutil.which finds the right one.
    npm = which_or_die(
        "npm",
        "Install Node.js 18+ (which bundles npm): https://nodejs.org/",
    )
    # node isn't called here, but flag it early so 'mcp'/'serve-open' won't
    # surprise the user later.
    if not shutil.which("node"):
        print(
            "[mrfox] warning: 'node' not found on PATH — you'll need it to run "
            "the MCP server (python cli.py mcp). Install Node.js 18+."
        )
    print("[mrfox] installing MCP server deps (npm install) ...")
    rc = run([npm, "install"], cwd=str(MCP_DIR))
    if rc != 0:
        return rc
    print("[mrfox] building MCP server (npm run build) ...")
    rc = run([npm, "run", "build"], cwd=str(MCP_DIR))
    if rc != 0:
        return rc

    print(
        f"[mrfox] setup complete. Next: python cli.py serve-open "
        f"(or serve, then open {base_url()}/)"
    )
    return 0


def cmd_serve(_args: argparse.Namespace) -> int:
    """Run the core API via the venv's python -m uvicorn, bound to 127.0.0.1."""
    py = require_venv()
    cmd = [
        str(py), "-m", "uvicorn", "core.api:app",
        "--host", HOST, "--port", str(port()),
    ]
    print(f"[mrfox] serving core API on {base_url()} (Ctrl-C to stop) ...")
    # Run from project root so 'core.api:app' imports correctly.
    return run(cmd, cwd=str(ROOT))


def cmd_ingest(args: argparse.Namespace) -> int:
    """POST a project directory to the running API using urllib (not curl)."""
    target = Path(args.path).expanduser()
    if not target.exists():
        sys.exit(f"error: path does not exist: {target}")
    if not target.is_dir():
        sys.exit(f"error: not a directory: {target}")

    payload: dict[str, str] = {"path": str(target.resolve())}
    if args.project:
        payload["project"] = args.project

    url = f"{base_url()}/ingest"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    print(f"[mrfox] ingesting {target} into project "
          f"{payload.get('project', '(default)')!r} via {url} ...")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
        print(body)
        return 0
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        print(f"error: ingest failed (HTTP {exc.code}): {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(
            f"error: could not reach the API at {base_url()} ({exc.reason}).\n"
            "Is it running? Start it with 'python cli.py serve'.",
            file=sys.stderr,
        )
        return 1


def cmd_mcp(_args: argparse.Namespace) -> int:
    """Run the compiled MCP stdio server with node (resolved via PATH)."""
    node = which_or_die(
        "node",
        "Install Node.js 18+: https://nodejs.org/",
    )
    server = MCP_DIR / "dist" / "server.js"
    if not server.exists():
        sys.exit(
            f"error: {server} missing. Run 'python cli.py setup' first."
        )
    return run([node, str(server)], cwd=str(ROOT))


def cmd_open(_args: argparse.Namespace) -> int:
    """Open the UI in the default browser via the webbrowser module."""
    url = f"{base_url()}/"
    print(f"[mrfox] opening {url} ...")
    if webbrowser.open(url):
        return 0
    print(f"[mrfox] could not open a browser automatically — visit: {url}")
    return 0


def _wait_for_health(timeout: float = 30.0) -> bool:
    """Poll GET /health until it answers OK or the timeout elapses."""
    url = f"{base_url()}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    return False


def cmd_serve_open(_args: argparse.Namespace) -> int:
    """Start the API in a subprocess, wait for /health, then open the UI."""
    py = require_venv()
    cmd = [
        str(py), "-m", "uvicorn", "core.api:app",
        "--host", HOST, "--port", str(port()),
    ]
    print(f"[mrfox] starting core API on {base_url()} ...")
    proc = subprocess.Popen(cmd, shell=False, cwd=str(ROOT))  # noqa: S603
    try:
        if _wait_for_health(30.0):
            print("[mrfox] core API healthy.")
            cmd_open(_args)
        else:
            print(
                "[mrfox] warning: API did not become healthy in time; "
                "leaving it running. Try 'python cli.py open' manually.",
                file=sys.stderr,
            )
        print("[mrfox] press Ctrl-C to stop the server.")
        return proc.wait()
    except KeyboardInterrupt:
        print("\n[mrfox] stopping core API ...")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 0


# --- argument parsing --------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="MrFoX-MeM cross-OS CLI (macOS / Linux / Windows).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="create venv, install deps, build MCP server").set_defaults(
        func=cmd_setup
    )
    sub.add_parser("serve", help="run the core API on 127.0.0.1").set_defaults(
        func=cmd_serve
    )

    p_ingest = sub.add_parser("ingest", help="POST a project dir to the running API")
    p_ingest.add_argument("--path", required=True, help="absolute path to the project dir")
    p_ingest.add_argument("--project", default=None, help="project name (slug)")
    p_ingest.set_defaults(func=cmd_ingest)

    sub.add_parser("mcp", help="run the compiled MCP stdio server").set_defaults(
        func=cmd_mcp
    )
    sub.add_parser("open", help="open the UI in the default browser").set_defaults(
        func=cmd_open
    )
    sub.add_parser(
        "serve-open", help="serve, wait for /health, then open the UI"
    ).set_defaults(func=cmd_serve_open)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
