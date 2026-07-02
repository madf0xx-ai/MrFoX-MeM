"""Token-counting tests: deterministic fallback + real-encoder path."""
from __future__ import annotations

from core import tokens


def test_fallback_is_chars_over_four():
    # conftest forces the fallback (no tiktoken) -> chars/4, deterministic.
    assert tokens.count_tokens("") == 0
    assert tokens.count_tokens("abcd") == 1
    assert tokens.count_tokens("a" * 40) == 10


def test_uses_encoder_when_present(monkeypatch):
    class _Enc:
        def encode(self, text):
            return text.split()          # 1 "token" per whitespace word

    monkeypatch.setattr(tokens, "_TRIED", True)
    monkeypatch.setattr(tokens, "_ENC", _Enc())
    assert tokens.count_tokens("one two three four") == 4
