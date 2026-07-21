"""Self-learning memory: a deterministic termbase + a gated correction memory.

The non-repeat guarantee lives here, not in any model behavior. See
``guard.py`` (deterministic reject-guard), ``retrieval.py`` (exact span-scan +
fuzzy surfacing), and ``learn.py`` (the trusted write path)."""

from veriloqua.memory.records import (
    Kind,
    MemoryEntry,
    Provenance,
    RequestLogRecord,
    Scope,
    Status,
)
from veriloqua.memory.store_sqlite import SqliteMemoryStore

__all__ = [
    "SqliteMemoryStore",
    "MemoryEntry",
    "RequestLogRecord",
    "Kind",
    "Scope",
    "Status",
    "Provenance",
]
