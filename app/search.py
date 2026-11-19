"""Search boundary used by persistence; HTTP and UI code do not know the engine.

Adapters return ordered canonical video IDs, never SQL fragments. All filters and
limits apply before IDs are returned. SQLite owns authoritative library content.
"""
from typing import Mapping, Protocol
from sqlite3 import Connection
from .search_sqlite import SQLiteSearch


class SearchBackend(Protocol):
    def initialize(self, transaction: Connection) -> None:
        """Initialize adapter storage and backfill existing saved documents."""
        ...

    def sync(self, transaction: Connection, video_id: str) -> None:
        """Reflect an upsert or removal within the caller's transaction.

        Remote adapters must enqueue an outbox event here; do not issue remote
        writes before the authoritative transaction commits.
        """
        ...

    def find(self, transaction: Connection, filters: Mapping[str, str], limit: int = 1000) -> list[str]:
        """Return unique saved video IDs in display order after applying filters."""
        ...


backend: SearchBackend = SQLiteSearch()


def initialize(transaction):
    backend.initialize(transaction)


def sync(transaction, video_id):
    backend.sync(transaction, video_id)


def find(transaction, filters, limit=1000):
    return backend.find(transaction, filters, limit)
