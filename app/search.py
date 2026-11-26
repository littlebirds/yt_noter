"""Search boundary used by persistence; HTTP and UI code do not know the engine.

Adapters return ordered canonical video IDs, never SQL fragments. All filters and
limits apply before IDs are returned.
"""
from typing import Mapping, Protocol
from . import storage
from .search_query import ASCII_LOWER, ASCII_UPPER, find_videos
from .search_sqlite import SQLiteSearch


class SearchBackend(Protocol):
    def initialize(self, transaction) -> None:
        """Initialize adapter storage and backfill existing saved documents."""
        ...

    def sync(self, transaction, video_id: str) -> None:
        """Reflect an upsert or removal within the caller's transaction.

        Remote adapters must enqueue an outbox event here; do not issue remote
        writes before the authoritative transaction commits.
        """
        ...

    def find(self, transaction, filters: Mapping[str, str], limit: int = 1000) -> list[str]:
        """Return unique saved video IDs in display order after applying filters."""
        ...


backend: SearchBackend = SQLiteSearch()


def initialize(transaction):
    if not isinstance(transaction, storage.PostgreSQLConnection):
        backend.initialize(transaction)


def sync(transaction, video_id):
    if not isinstance(transaction, storage.PostgreSQLConnection):
        backend.sync(transaction, video_id)


def find(transaction, filters, limit=1000):
    if isinstance(transaction, storage.PostgreSQLConnection):
        return find_videos(transaction, filters, postgres_text_filter, limit)
    return backend.find(transaction, filters, limit)


def postgres_text_filter(text):
    # Match SQLite's ASCII-only case folding and literal substring behavior.
    fields = ('v.title', 't.text', 's.markdown')
    checks = [f"strpos(translate({field}, '{ASCII_UPPER}', '{ASCII_UPPER.lower()}'), ?) > 0" for field in fields]
    return ['(' + ' OR '.join(checks) + ')'], [text.translate(ASCII_LOWER)] * len(fields)
