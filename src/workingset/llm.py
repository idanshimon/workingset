"""LLM provider adapters — optional, for the ``--summarize`` paths.

By default workingset is deterministic and does no LLM calls. Pass
``--llm anthropic`` or ``--llm openai`` to compaction/brief commands to
use the ContextForge summarization prompt for archived blocks and
oversized briefs.

Both providers read API keys from the env (``ANTHROPIC_API_KEY``,
``OPENAI_API_KEY``). Models default to a small/cheap option per provider
and can be overridden via ``--llm-model``.
"""
from __future__ import annotations

import os
from typing import Callable, Optional

from .compact import COMPACT_PROMPT


DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-5-mini",
}


def get_summarizer(provider: str, *, model: Optional[str] = None) -> Callable[[str], str]:
    """Return a ``summarize(text) -> str`` callable for the named provider.

    Raises ``RuntimeError`` if the provider's SDK or API key isn't available.
    """
    p = provider.lower()
    if p not in DEFAULT_MODELS:
        raise ValueError(
            f"Unknown LLM provider: {provider!r}. Use 'anthropic' or 'openai'."
        )
    chosen_model: str = model or DEFAULT_MODELS[p]
    if p == "anthropic":
        return _anthropic_summarizer(chosen_model)
    return _openai_summarizer(chosen_model)


def _anthropic_summarizer(model: str) -> Callable[[str], str]:
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "Install with `pip install workingset[llm]` to use --llm anthropic"
        ) from e
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set in the environment")
    client = anthropic.Anthropic(api_key=api_key)

    def summarize(text: str) -> str:
        msg = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": COMPACT_PROMPT + text}],
        )
        # Concatenate any text blocks in the response.
        parts = []
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        return "".join(parts).strip()

    return summarize


def _openai_summarizer(model: str) -> Callable[[str], str]:
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "Install with `pip install workingset[llm]` to use --llm openai"
        ) from e
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in the environment")
    client = OpenAI(api_key=api_key)

    def summarize(text: str) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": COMPACT_PROMPT + text}],
            max_tokens=1024,
        )
        return (resp.choices[0].message.content or "").strip()

    return summarize
