"""Launch the MrFoX-MeM core API on 127.0.0.1:8077.

Run as a module so relative imports resolve::

    python -m core.run
"""
from __future__ import annotations

import os

import uvicorn

HOST = "127.0.0.1"  # never 0.0.0.0 — localhost-only single-user tool
PORT = int(os.environ.get("MRFOX_PORT", "8077"))


def main() -> None:
    uvicorn.run("core.api:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
