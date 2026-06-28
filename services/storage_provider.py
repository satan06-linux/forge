# ForgePrompt Phase 7 — StorageProvider
# Rule 8: All storage access goes through StorageProvider.get_session().
# No service imports models.get_db_connection() directly except infrastructure services.

from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Optional, List, Dict
import json
import time

from models import get_db_connection


class Session:
    """Wraps a mysql connection + cursor for use as a context manager."""

    def __init__(self, conn, dictionary=True):
        self._conn = conn
        self._cursor = conn.cursor(dictionary=dictionary)
        self._in_transaction = False

    @property
    def conn(self):
        return self._conn

    @property
    def cursor(self):
        return self._cursor

    def execute(self, sql: str, params=None) -> 'Session':
        self._cursor.execute(sql, params or ())
        return self

    def fetchone(self) -> Optional[Dict]:
        return self._cursor.fetchone()

    def fetchall(self) -> List[Dict]:
        return self._cursor.fetchall()

    def lastrowid(self) -> int:
        return self._cursor.lastrowid

    def rowcount(self) -> int:
        return self._cursor.rowcount

    def begin(self):
        self._conn.start_transaction()
        self._in_transaction = True

    def commit(self):
        self._conn.commit()
        self._in_transaction = False

    def rollback(self):
        self._conn.rollback()
        self._in_transaction = False

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None and self._in_transaction:
            self.rollback()
        self.close()
        return False


class StorageProvider(ABC):
    """
    Abstract storage interface.
    Swap MySQL -> Postgres -> CockroachDB without touching services.
    """

    @abstractmethod
    def get_session(self, dictionary: bool = True) -> Session:
        """Returns a Session context manager."""

    @abstractmethod
    def execute(self, sql: str, params=None) -> List[Dict]:
        """Execute a query and return all rows."""

    @abstractmethod
    def execute_one(self, sql: str, params=None) -> Optional[Dict]:
        """Execute a query and return the first row."""

    @abstractmethod
    def insert(self, table: str, data: Dict) -> int:
        """Insert a row and return the lastrowid."""

    @abstractmethod
    def update(self, sql: str, params=None) -> int:
        """Execute an UPDATE and return rowcount."""

    @abstractmethod
    def delete(self, sql: str, params=None) -> int:
        """Execute a DELETE and return rowcount."""

    @contextmanager
    def transaction(self):
        """Context manager for explicit transactions."""
        session = self.get_session()
        session.begin()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class MySQLStorageProvider(StorageProvider):
    """
    Phase 7 default: backed by mysql-connector-python.
    Thread-safe: creates a new connection per call (connection pool managed by MySQL connector).
    """

    def __init__(self, config=None):
        self._config = config or {}

    def get_session(self, dictionary: bool = True) -> Session:
        conn = get_db_connection()
        return Session(conn, dictionary=dictionary)

    def execute(self, sql: str, params=None) -> List[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(sql, params or ())
            return cursor.fetchall() or []
        finally:
            cursor.close()
            conn.close()

    def execute_one(self, sql: str, params=None) -> Optional[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(sql, params or ())
            return cursor.fetchone()
        finally:
            cursor.close()
            conn.close()

    def insert(self, table: str, data: Dict) -> int:
        cols = ', '.join(data.keys())
        placeholders = ', '.join(['%s'] * len(data))
        sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(sql, list(data.values()))
            conn.commit()
            return cursor.lastrowid
        finally:
            cursor.close()
            conn.close()

    def update(self, sql: str, params=None) -> int:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(sql, params or ())
            conn.commit()
            return cursor.rowcount
        finally:
            cursor.close()
            conn.close()

    def delete(self, sql: str, params=None) -> int:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(sql, params or ())
            conn.commit()
            return cursor.rowcount
        finally:
            cursor.close()
            conn.close()


class PostgresStorageProvider(StorageProvider):
    """
    Phase 8 stub: backed by psycopg2 / asyncpg.
    Interface complete; implementation deferred to Phase 8.
    """

    def __init__(self, dsn: str = None):
        raise NotImplementedError(
            "PostgresStorageProvider is a Phase 8 stub. "
            "Use MySQLStorageProvider for Phase 7. "
            "To activate: install psycopg2, configure PG_DSN, swap in ServiceContainer."
        )

    def get_session(self, dictionary: bool = True) -> Session:
        raise NotImplementedError

    def execute(self, sql: str, params=None) -> List[Dict]:
        raise NotImplementedError

    def execute_one(self, sql: str, params=None) -> Optional[Dict]:
        raise NotImplementedError

    def insert(self, table: str, data: Dict) -> int:
        raise NotImplementedError

    def update(self, sql: str, params=None) -> int:
        raise NotImplementedError

    def delete(self, sql: str, params=None) -> int:
        raise NotImplementedError


class CockroachDBStorageProvider(StorageProvider):
    """
    Phase 8 stub: backed by psycopg2 with CockroachDB dialect.
    Interface complete; implementation deferred to Phase 8.
    """

    def __init__(self, dsn: str = None):
        raise NotImplementedError(
            "CockroachDBStorageProvider is a Phase 8 stub. "
            "Use MySQLStorageProvider for Phase 7."
        )

    def get_session(self, dictionary: bool = True) -> Session:
        raise NotImplementedError

    def execute(self, sql: str, params=None) -> List[Dict]:
        raise NotImplementedError

    def execute_one(self, sql: str, params=None) -> Optional[Dict]:
        raise NotImplementedError

    def insert(self, table: str, data: Dict) -> int:
        raise NotImplementedError

    def update(self, sql: str, params=None) -> int:
        raise NotImplementedError

    def delete(self, sql: str, params=None) -> int:
        raise NotImplementedError


def get_storage_provider(provider_type: str = 'mysql', **kwargs) -> StorageProvider:
    """
    Factory function. Returns the configured StorageProvider.
    Phase 7: always returns MySQLStorageProvider.
    Phase 8: swap to PostgresStorageProvider or CockroachDBStorageProvider here.
    """
    if provider_type == 'mysql':
        return MySQLStorageProvider(**kwargs)
    elif provider_type == 'postgres':
        return PostgresStorageProvider(**kwargs)
    elif provider_type == 'cockroachdb':
        return CockroachDBStorageProvider(**kwargs)
    else:
        raise ValueError(f"Unknown storage provider: {provider_type}. Valid: mysql, postgres, cockroachdb")
