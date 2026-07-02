"""Token counting for budget assembly.

Uses ``tiktoken`` (``o200k_base``, the GPT-4o vocab) when it is importable, which
is far more accurate than a character heuristic — the ``chars/4`` rule has ~28%
average error and systematically *under*-counts code/JSON (~3 chars/token), which
risks overflowing the model's real window. When tiktoken is absent we fall back
to ``chars/4`` so the tool still runs fully offline with zero dependencies.

tiktoken is NOT offline on first use (it fetches the vocab), so we pin a
repo-local ``TIKTOKEN_CACHE_DIR``; after one warm-up the vocab is cached and all
subsequent counting is local.
"""
from __future__ import annotations

import os

_CHARS_PER_TOKEN = 4  # zero-dependency fallback divisor

# Cache the vocab under the project's data/ dir so counting works offline after
# the first fetch. ``setdefault`` lets a user/CI override the location.
_CACHE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "tiktoken_cache")
)
os.environ.setdefault("TIKTOKEN_CACHE_DIR", _CACHE_DIR)

_ENC = None
_TRIED = False


def _encoder():
    """Lazily load and cache the tiktoken encoder; None if unavailable."""
    global _ENC, _TRIED
    if _TRIED:
        return _ENC
    _TRIED = True
    try:
        os.makedirs(os.environ["TIKTOKEN_CACHE_DIR"], exist_ok=True)
        import tiktoken  # type: ignore

        _ENC = tiktoken.get_encoding("o200k_base")
    except Exception:
        _ENC = None
    return _ENC


def count_tokens(text: str) -> int:
    """Best-effort token count: real tokenizer when available, else chars/4."""
    if not text:
        return 0
    enc = _encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return max(0, len(text) // _CHARS_PER_TOKEN)
