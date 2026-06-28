# ForgePrompt Phase 7 — LockService
from contextlib import contextmanager
from models import get_db_connection
import logging

logger = logging.getLogger(__name__)

class LockService:
    def __init__(self, container=None):
        self.container = container

    def acquire(self, lock_name: str, timeout_seconds: int = 30) -> bool:
        """Uses MySQL GET_LOCK. Returns True if acquired."""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT GET_LOCK(%s, %s)", (lock_name, timeout_seconds))
            result = cursor.fetchone()
            if result and result[0] == 1:
                return True
            return False
        except Exception as e:
            logger.error(f"[LockService Error] Failed to acquire lock {lock_name}: {e}")
            return False
        finally:
            cursor.close()
            conn.close()

    def release(self, lock_name: str) -> bool:
        """Uses MySQL RELEASE_LOCK."""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
            result = cursor.fetchone()
            if result and result[0] == 1:
                return True
            return False
        except Exception as e:
            logger.error(f"[LockService Error] Failed to release lock {lock_name}: {e}")
            return False
        finally:
            cursor.close()
            conn.close()

    def is_locked(self, lock_name: str) -> bool:
        """Uses MySQL IS_FREE_LOCK."""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT IS_FREE_LOCK(%s)", (lock_name,))
            result = cursor.fetchone()
            # IS_FREE_LOCK returns 1 if free, 0 if in use
            if result and result[0] == 0:
                return True
            return False
        except Exception as e:
            logger.error(f"[LockService Error] Failed to check lock status for {lock_name}: {e}")
            return False
        finally:
            cursor.close()
            conn.close()

    @contextmanager
    def acquire_context(self, lock_name: str, timeout_seconds: int = 30):
        """Context manager: acquire on enter, release on exit."""
        # Using a dedicated connection for the context manager duration
        # because MySQL locks are tied to the connection.
        conn = get_db_connection()
        cursor = conn.cursor()
        acquired = False
        try:
            cursor.execute("SELECT GET_LOCK(%s, %s)", (lock_name, timeout_seconds))
            result = cursor.fetchone()
            if result and result[0] == 1:
                acquired = True
                yield True
            else:
                yield False
        finally:
            if acquired:
                try:
                    cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                except Exception as e:
                    logger.error(f"[LockService Error] Failed to release lock in context for {lock_name}: {e}")
            cursor.close()
            conn.close()
