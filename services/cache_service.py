# ForgePrompt Phase 7 — CacheService
import time
import json
import threading
from typing import Any, Optional, Callable
from models import get_db_connection
import logging

logger = logging.getLogger(__name__)

class CacheService:
    def __init__(self, container=None):
        self.container = container
        self.L1_cache = {}
        self.locks = {}
        self._init_db()

    def _init_db(self):
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cache_store (
                    cache_key VARCHAR(512) NOT NULL,
                    namespace VARCHAR(100) NOT NULL DEFAULT 'default',
                    value_json LONGTEXT NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (cache_key, namespace),
                    INDEX idx_cache_expires (expires_at)
                ) ENGINE=InnoDB
            """)
            conn.commit()
        except Exception as e:
            logger.error(f"[CacheService Error] Failed to init cache_store table: {e}")
        finally:
            cursor.close()
            conn.close()

    def get(self, key: str, namespace: str = 'default') -> Optional[Any]:
        # L1 Check
        cache_key = f"{namespace}:{key}"
        if cache_key in self.L1_cache:
            entry = self.L1_cache[cache_key]
            if time.time() < entry['expires_at']:
                return entry['value']
            else:
                del self.L1_cache[cache_key]
        
        # L2 Check
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT value_json, UNIX_TIMESTAMP(expires_at) as exp_at FROM cache_store WHERE cache_key = %s AND namespace = %s AND expires_at > NOW()",
                (key, namespace)
            )
            row = cursor.fetchone()
            if row:
                val = json.loads(row['value_json'])
                # Populate L1
                self.L1_cache[cache_key] = {
                    'value': val,
                    'expires_at': row['exp_at']
                }
                return val
            return None
        except Exception as e:
            logger.error(f"[CacheService Error] Failed to get cache for {key}: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    def set(self, key: str, value: Any, ttl_seconds: int = 300, namespace: str = 'default'):
        cache_key = f"{namespace}:{key}"
        expires_at = time.time() + ttl_seconds
        
        # Set L1
        self.L1_cache[cache_key] = {
            'value': value,
            'expires_at': expires_at
        }
        
        # Set L2
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            val_json = json.dumps(value)
            cursor.execute(
                """
                INSERT INTO cache_store (cache_key, namespace, value_json, expires_at)
                VALUES (%s, %s, %s, FROM_UNIXTIME(%s))
                ON DUPLICATE KEY UPDATE value_json = VALUES(value_json), expires_at = VALUES(expires_at)
                """,
                (key, namespace, val_json, expires_at)
            )
            conn.commit()
        except Exception as e:
            logger.error(f"[CacheService Error] Failed to set cache for {key}: {e}")
        finally:
            cursor.close()
            conn.close()

    def delete(self, key: str, namespace: str = 'default'):
        cache_key = f"{namespace}:{key}"
        if cache_key in self.L1_cache:
            del self.L1_cache[cache_key]
            
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM cache_store WHERE cache_key = %s AND namespace = %s",
                (key, namespace)
            )
            conn.commit()
        except Exception as e:
            logger.error(f"[CacheService Error] Failed to delete cache for {key}: {e}")
        finally:
            cursor.close()
            conn.close()

    def invalidate_namespace(self, namespace: str):
        keys_to_del = [k for k in self.L1_cache.keys() if k.startswith(f"{namespace}:")]
        for k in keys_to_del:
            del self.L1_cache[k]
            
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM cache_store WHERE namespace = %s", (namespace,))
            conn.commit()
        except Exception as e:
            logger.error(f"[CacheService Error] Failed to invalidate namespace {namespace}: {e}")
        finally:
            cursor.close()
            conn.close()

    def get_or_set(self, key: str, factory: Callable, ttl_seconds: int = 300, namespace: str = 'default') -> Any:
        cache_key = f"{namespace}:{key}"
        
        val = self.get(key, namespace)
        if val is not None:
            return val
            
        # Stampede protection lock
        if cache_key not in self.locks:
            self.locks[cache_key] = threading.Lock()
            
        with self.locks[cache_key]:
            # Double check
            val = self.get(key, namespace)
            if val is not None:
                return val
                
            # Factory execution
            try:
                val = factory()
                if val is not None:
                    self.set(key, val, ttl_seconds, namespace)
                return val
            except Exception as e:
                logger.error(f"[CacheService Error] Factory failed for {key}: {e}")
                raise e
