"""Copy an existing SQLite Library to an empty PostgreSQL database."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from . import config, db, session_store, storage


SAVED_VIDEOS = 'SELECT video_id FROM library_items'
DURABLE_JOBS = f"kind='sync' OR video_id IN ({SAVED_VIDEOS})"
TABLES = (
    ('videos', f'id IN ({SAVED_VIDEOS})'),
    *((table, f'video_id IN ({SAVED_VIDEOS})') for table in session_store.VIDEO_TABLES),
    ('library_items', 'TRUE'),
    ('library_links', f'video_id IN ({SAVED_VIDEOS})'),
    ('sources', 'TRUE'),
    ('source_videos', 'TRUE'),
    ('jobs', DURABLE_JOBS),
    ('summary_checkpoints', f'job_id IN (SELECT id FROM jobs WHERE {DURABLE_JOBS})'),
)


def migrate(source_path):
    if not config.DATABASE_URL:
        raise ValueError('Set DATABASE_URL to the destination PostgreSQL database.')
    source_path = source_path.resolve()
    if not source_path.is_file():
        raise ValueError(f'SQLite database does not exist: {source_path}')

    with TemporaryDirectory() as directory:
        with storage.sqlite_connection(f'{source_path.as_uri()}?mode=ro', uri=True) as original:
            with storage.sqlite_connection(Path(directory) / 'library.sqlite3') as snapshot:
                original.backup(snapshot)
                # Apply historical schema upgrades to the private copy only.
                db.initialize(snapshot)
                return copy_library(snapshot)


def copy_library(source):
    with storage.persistent_connection(config.DB) as target:
        db.initialize(target)
        # Prevent a concurrent importer or writer from passing the empty check.
        tables = ','.join(table for table, _ in TABLES)
        target.execute(f'LOCK TABLE {tables} IN SHARE ROW EXCLUSIVE MODE')
        for table, _ in TABLES:
            if target.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
                raise ValueError('The PostgreSQL Library already contains data. Use an empty destination.')
        counts = {}
        for table, condition in TABLES:
            cursor = source.execute(f'SELECT * FROM {table} WHERE {condition}')
            columns = [column[0] for column in cursor.description]
            if table == 'mentions':
                # PostgreSQL allocates its own surrogate IDs. No table references them.
                columns.remove('id')
            counts[table] = 0
            while rows := cursor.fetchmany(100):
                session_store.insert_rows(target, table, columns, rows)
                counts[table] += len(rows)
        return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=config.DB, help='Path to the SQLite database')
    try:
        counts = migrate(parser.parse_args().source)
    except ValueError as exc:
        parser.exit(1, f'{exc}\n')
    print(f"Imported {counts['library_items']} saved videos and {counts['sources']} monitored feeds.")


if __name__ == '__main__':
    main()
