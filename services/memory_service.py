from abc import ABC, abstractmethod
from models import get_db_connection

class BaseMemoryProvider(ABC):
    @abstractmethod
    def get_memory(self, scope_type, scope_key, memory_key):
        pass

    @abstractmethod
    def save_memory(self, scope_type, scope_key, memory_key, memory_value, user_id=None, organization_id=None):
        pass

    @abstractmethod
    def list_memories(self, scope_type, scope_key):
        pass

class SQLMemoryProvider(BaseMemoryProvider):
    def get_memory(self, scope_type, scope_key, memory_key):
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT memory_value FROM memory_scopes
                WHERE scope_type = %s AND scope_key = %s AND memory_key = %s
                """,
                (scope_type, scope_key, memory_key)
            )
            row = cursor.fetchone()
            return row["memory_value"] if row else None
        except Exception as e:
            print(f"[SQLMemoryProvider Error] {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    def save_memory(self, scope_type, scope_key, memory_key, memory_value, user_id=None, organization_id=None):
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # Let's ensure user_id and organization_id exist
            cursor.execute(
                """
                INSERT INTO memory_scopes (user_id, organization_id, scope_type, scope_key, memory_key, memory_value)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE memory_value = VALUES(memory_value)
                """,
                (user_id, organization_id, scope_type, scope_key, memory_key, memory_value)
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"[SQLMemoryProvider Error] {e}")
            return False
        finally:
            cursor.close()
            conn.close()

    def list_memories(self, scope_type, scope_key):
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT memory_key, memory_value FROM memory_scopes
                WHERE scope_type = %s AND scope_key = %s
                """,
                (scope_type, scope_key)
            )
            rows = cursor.fetchall()
            return {r["memory_key"]: r["memory_value"] for r in rows}
        except Exception as e:
            print(f"[SQLMemoryProvider Error] {e}")
            return {}
        finally:
            cursor.close()
            conn.close()

class MemoryService:
    _provider = SQLMemoryProvider()

    @classmethod
    def set_provider(cls, provider: BaseMemoryProvider):
        cls._provider = provider

    @classmethod
    def get(cls, scope_type, scope_key, memory_key):
        return cls._provider.get_memory(scope_type, scope_key, memory_key)

    @classmethod
    def save(cls, scope_type, scope_key, memory_key, memory_value, user_id=None, organization_id=None):
        return cls._provider.save_memory(scope_type, scope_key, memory_key, memory_value, user_id, organization_id)

    @classmethod
    def get_all(cls, scope_type, scope_key):
        return cls._provider.list_memories(scope_type, scope_key)
