# ForgePrompt Phase 7 — DurableTimerService
import time
import json
from typing import Dict, Any

from services.service_result import ServiceResult
from services.errors import ForgeError, StorageError

class DurableTimerService:
    """
    Durable timers using timers table, checking for fired timers.
    """
    def __init__(self, container):
        self.storage = container.get('storage_provider')
        self._ensure_table()
        
    def _ensure_table(self):
        with self.storage.get_session() as session:
            session.begin()
            session.execute("""
                CREATE TABLE IF NOT EXISTS timers (
                    timer_id VARCHAR(255) PRIMARY KEY,
                    fire_at DOUBLE NOT NULL,
                    payload LONGTEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'pending',
                    created_at DOUBLE NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            # Check for indexes
            session.execute("""
                SELECT COUNT(1) AS cnt FROM INFORMATION_SCHEMA.STATISTICS 
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'timers' AND INDEX_NAME = 'idx_timers_fire_at_status'
            """)
            idx_exists = session.fetchone()['cnt'] > 0
            if not idx_exists:
                session.execute("CREATE INDEX idx_timers_fire_at_status ON timers(fire_at, status)")
            session.commit()
            
    def schedule_timer(self, timer_id: str, fire_in_seconds: float, payload: Dict[str, Any], conn=None) -> ServiceResult:
        session = conn if conn else self.storage.get_session()
        owns_session = conn is None
        try:
            if owns_session: session.begin()
            now = time.time()
            fire_at = now + fire_in_seconds
            payload_json = json.dumps(payload)
            session.execute("""
                INSERT INTO timers (timer_id, fire_at, payload, status, created_at)
                VALUES (%s, %s, %s, 'pending', %s)
            """, (timer_id, fire_at, payload_json, now))
            if owns_session: session.commit()
            return ServiceResult.ok(data={"timer_id": timer_id, "fire_at": fire_at})
        except Exception as e:
            if owns_session: session.rollback()
            return ServiceResult.fail(StorageError(f"[DurableTimerService Error] {str(e)}"))
        finally:
            if owns_session: session.close()
            
    def check_fired_timers(self, batch_size: int = 50, conn=None) -> ServiceResult:
        """
        Find timers that have fired, mark them as 'fired' and return them.
        """
        session = conn if conn else self.storage.get_session()
        owns_session = conn is None
        try:
            if owns_session: session.begin()
            now = time.time()
            # Select FOR UPDATE to lock rows
            session.execute("""
                SELECT timer_id, payload FROM timers 
                WHERE status = 'pending' AND fire_at <= %s
                ORDER BY fire_at ASC
                LIMIT %s FOR UPDATE
            """, (now, batch_size))
            rows = session.fetchall()
            
            fired_timers = []
            for row in rows:
                timer_id = row['timer_id']
                payload = json.loads(row['payload']) if row['payload'] else {}
                fired_timers.append({"timer_id": timer_id, "payload": payload})
                session.execute("UPDATE timers SET status = 'fired' WHERE timer_id = %s", (timer_id,))
                
            if owns_session: session.commit()
            return ServiceResult.ok(data={"fired_timers": fired_timers})
        except Exception as e:
            if owns_session: session.rollback()
            return ServiceResult.fail(StorageError(f"[DurableTimerService Error] {str(e)}"))
        finally:
            if owns_session: session.close()
            
    def cancel_timer(self, timer_id: str, conn=None) -> ServiceResult:
        session = conn if conn else self.storage.get_session()
        owns_session = conn is None
        try:
            if owns_session: session.begin()
            session.execute("UPDATE timers SET status = 'cancelled' WHERE timer_id = %s", (timer_id,))
            if owns_session: session.commit()
            return ServiceResult.ok()
        except Exception as e:
            if owns_session: session.rollback()
            return ServiceResult.fail(StorageError(f"[DurableTimerService Error] {str(e)}"))
        finally:
            if owns_session: session.close()
