"""PostgreSQL-backed compatibility adapter for BusinessNewsAgent.

The project historically used sqlite3. This module preserves the small subset
of that API used by the existing code while routing every connection to the
DATABASE_URL environment variable (Neon PostgreSQL). It never falls back to a
local SQLite database.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv


# Load only the local project's root .env file; platform environment variables
# remain authoritative because override=False is used.
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)


class Row:
    """Compatibility sentinel for legacy `connection.row_factory = Row` lines."""


Error = psycopg.Error
DatabaseError = psycopg.Error
OperationalError = psycopg.OperationalError
IntegrityError = psycopg.IntegrityError


class HybridRow(dict):
    """Mapping row supporting both row['column'] and row[0] access."""

    def __init__(self, names, values):
        values = tuple(values)
        super().__init__(zip(names, values))
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def _hybrid_row_factory(cursor):
    if cursor.description is None:
        return lambda values: values
    names = [column.name for column in cursor.description]

    def make_row(values):
        return HybridRow(names, values)

    return make_row


def _replace_qmark_placeholders(sql):
    """Convert sqlite-style ? placeholders to psycopg %s placeholders.

    Question marks inside SQL string literals or quoted identifiers are left
    unchanged. The application SQL does not use dollar-quoted strings.
    """
    sql = str(sql)
    output = []
    single_quoted = False
    double_quoted = False
    index = 0

    while index < len(sql):
        char = sql[index]

        if char == "'" and not double_quoted:
            output.append(char)
            if single_quoted and index + 1 < len(sql) and sql[index + 1] == "'":
                output.append(sql[index + 1])
                index += 2
                continue
            single_quoted = not single_quoted
            index += 1
            continue

        if char == '"' and not single_quoted:
            output.append(char)
            if double_quoted and index + 1 < len(sql) and sql[index + 1] == '"':
                output.append(sql[index + 1])
                index += 2
                continue
            double_quoted = not double_quoted
            index += 1
            continue

        if char == "?" and not single_quoted and not double_quoted:
            output.append("%s")
        else:
            output.append(char)

        index += 1

    return "".join(output)


class CompatCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=None):
        translated = _replace_qmark_placeholders(sql)
        if params is None:
            self._cursor.execute(translated)
        else:
            self._cursor.execute(translated, params)
        return self

    def executemany(self, sql, seq_of_params):
        translated = _replace_qmark_placeholders(sql)
        self._cursor.executemany(translated, seq_of_params)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size=None):
        if size is None:
            return self._cursor.fetchmany()
        return self._cursor.fetchmany(size)

    def close(self):
        self._cursor.close()

    def __iter__(self):
        return iter(self._cursor)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class CompatConnection:
    def __init__(self, connection):
        self._connection = connection
        self._legacy_row_factory = Row

    @property
    def row_factory(self):
        return self._legacy_row_factory

    @row_factory.setter
    def row_factory(self, value):
        # Psycopg's connection already uses _hybrid_row_factory. Legacy code
        # sets sqlite3.Row; retaining the value is enough for compatibility.
        self._legacy_row_factory = value

    def cursor(self, *args, **kwargs):
        return CompatCursor(self._connection.cursor(*args, **kwargs))

    def execute(self, sql, params=None):
        return self.cursor().execute(sql, params)

    def commit(self):
        return self._connection.commit()

    def rollback(self):
        return self._connection.rollback()

    def close(self):
        return self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False

    def __getattr__(self, name):
        return getattr(self._connection, name)


def connect(database=None, timeout=15.0, *args, **kwargs):
    """Open Neon PostgreSQL using DATABASE_URL; `database` is legacy-only."""
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add the Neon PostgreSQL connection string "
            "to the root .env file locally, or to the hosting provider's "
            "environment variables. This application will not fall back to SQLite."
        )

    if not database_url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError(
            "DATABASE_URL must be a PostgreSQL connection string."
        )

    timeout_seconds = max(1, int(timeout or 15))
    connection = psycopg.connect(
        database_url,
        connect_timeout=timeout_seconds,
        row_factory=_hybrid_row_factory,
    )
    return CompatConnection(connection)
