"""Token estimation. Model-agnostic, ~4 chars/token approximation.

Matches ContextForge's heuristic — good enough for budget arithmetic without
pulling tiktoken or a tokenizer per provider.
"""
from __future__ import annotations

import math

_CHARS_PER_TOKEN = 4.0


def estimate_tokens(text: str | None) -> int:
    """Estimate token count for a string. Conservative — rounds up."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / _CHARS_PER_TOKEN))


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens across a list of chat messages."""
    total = 0
    for msg in messages:
        # ~4 tokens overhead per message for role/formatting
        total += 4
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
    return total
