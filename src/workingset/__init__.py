"""workingset — vault-aware context compactor for LLM agents.

Treat your markdown vault like a working set, not a buffer. Point it at any
folder of markdown (customer notes, agent-os, plain Obsidian) and get:

- BM25/FTS5 index over the whole vault (one SQLite file, milliseconds to query)
- Per-folder ~500-token "brief.md" residual that any agent can load instead
  of reading 5 files / 220KB
- Working-set queries: ``ws query "renewal Q3"`` returns ranked branches under a
  token budget, ready to paste into a prompt
- Status-archive sweeper: stale "🔥 STATUS" blocks roll out to a separate
  archive file per ContextForge's 3000-tok compaction rule

Built on the same primitives as ContextForge (Derek Thomas / arXiv 2025), but
operates on the markdown layer — no model-side KV-cache access required.

Quickstart::

    pip install workingset
    cd ~/path/to/vault
    ws init
    ws query "renewal q3 budget"

See ``ws --help`` for the full command surface.
"""
from __future__ import annotations

__version__ = "0.1.0"

from .index import VaultIndex
from .tokens import estimate_tokens
from .vault import Vault

__all__ = ["Vault", "VaultIndex", "estimate_tokens", "__version__"]
