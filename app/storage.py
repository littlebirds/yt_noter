"""Database connections and the small SQL differences shared by both stores."""
import sqlite3
from contextlib import closing, contextmanager

from . import config


class PostgreSQLConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=None):
        if params is None:
            return self.connection.execute(sql)
        return self.connection.execute(sql.replace('%', '%%').replace('?', '%s'), params)

    def executescript(self, sql):
        with self.connection.execute(sql):
            pass

    def executemany(self, sql, rows):
        with self.connection.cursor() as cursor:
            cursor.executemany(sql.replace('%', '%%').replace('?', '%s'), rows)


def postgres_row(cursor):
    columns = {column.name: index for index, column in enumerate(cursor.description or ())}

    class Row(tuple):
        def keys(self):
            return columns.keys()

        def __getitem__(self, key):
            return super().__getitem__(columns[key] if isinstance(key, str) else key)

    return Row


@contextmanager
def sqlite_connection(database, *, uri=False):
    with closing(sqlite3.connect(database, timeout=30, uri=uri)) as con:
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        with con:
            yield con


@contextmanager
def persistent_connection(database):
    url = config.DATABASE_URL
    if not url:
        with sqlite_connection(database) as con:
            yield con
        return
    if not url.startswith(('postgresql://', 'postgres://')):
        raise ValueError('DATABASE_URL must be a PostgreSQL URL or empty for SQLite.')
    import psycopg

    with psycopg.connect(url, connect_timeout=5, row_factory=postgres_row) as con:
        yield PostgreSQLConnection(con)


def begin_write(con):
    # The session lock serializes writers in the single app process. PostgreSQL
    # starts its transaction on the first statement, including routing reads.
    if isinstance(con, sqlite3.Connection):
        con.execute('BEGIN IMMEDIATE')


def job_source(con):
    if isinstance(con, PostgreSQLConnection):
        return "(CAST(payload_json AS json) ->> 'source_id')"
    return "json_extract(payload_json,'$.source_id')"
