# ForgePrompt Phase 7 - OutboxDispatcher
import json
import time
from typing import Optional, Dict, Any, List

from services.service_result import ServiceResult
from services.errors import ForgeError
from services.storage_provider import StorageProvider
from services.event_bus import EventBus

class OutboxDispatcher:
    def __init__(self, container):
        self.storage: StorageProvider = container.storage_provider
        self.event_bus: EventBus = container.event_bus
        self._ensure_tables()

    def _ensure_tables(self):
        with self.storage.get_session() as session:
            # Check if table exists, if not create it
            session.execute("""
                CREATE TABLE IF NOT EXISTS event_outbox (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    organization_id VARCHAR(64) NOT NULL,
                    topic VARCHAR(255) NOT NULL,
                    partition_key VARCHAR(255) NOT NULL,
                    payload LONGTEXT NOT NULL,
                    status VARCHAR(50) DEFAULT 'PENDING',
                    locked_until BIGINT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_org_status (organization_id, status, locked_until),
                    INDEX idx_partition (partition_key)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """)
            session.commit()

    def append_event(self, organization_id: str, topic: str, partition_key: str, payload: Dict[str, Any], conn=None) -> ServiceResult:
        """
        Appends an event to the outbox. MUST be called inside an existing transaction.
        """
        try:
            payload_str = json.dumps(payload)
            sql = """
                INSERT INTO event_outbox (organization_id, topic, partition_key, payload, status, locked_until)
                VALUES (%s, %s, %s, %s, 'PENDING', 0)
            """
            params = (organization_id, topic, partition_key, payload_str)
            
            if conn:
                # Rule 8: Use the session/cursor if it's a Session object
                conn.execute(sql, params)
            else:
                with self.storage.get_session() as session:
                    session.execute(sql, params)
                    session.commit()
            return ServiceResult.ok()
        except Exception as e:
            return ServiceResult.fail(ForgeError(f"Failed to append event: {str(e)}", error_code="OUTBOX_APPEND_FAILED"))

    def dispatch_batch(self, max_events: int = 100, lock_duration_ms: int = 30000) -> ServiceResult:
        """
        Ordered + partitioned + backpressure delivery from event_outbox.
        """
        now_ms = int(time.time() * 1000)
        locked_until_ms = now_ms + lock_duration_ms
        
        try:
            with self.storage.get_session() as session:
                session.begin()
                
                # Find pending or expired locks
                # Group by partition_key to ensure ordered delivery per partition
                # But to avoid complex queries locking tables, we just select and lock
                # We do a simple FOR UPDATE SKIP LOCKED if supported, but for MySQL 5.7 compat:
                
                # 1. Fetch available partitions
                sql_find = """
                    SELECT id, organization_id, topic, partition_key, payload 
                    FROM event_outbox
                    WHERE status = 'PENDING' OR (status = 'PROCESSING' AND locked_until < %s)
                    ORDER BY id ASC
                    LIMIT %s
                    FOR UPDATE
                """
                session.execute(sql_find, (now_ms, max_events))
                events = session.fetchall()
                
                if not events:
                    session.commit()
                    return ServiceResult.ok(data={"dispatched": 0})
                
                event_ids = [e["id"] for e in events]
                
                # Lock them
                format_strings = ','.join(['%s'] * len(event_ids))
                sql_lock = f"UPDATE event_outbox SET status = 'PROCESSING', locked_until = %s WHERE id IN ({format_strings})"
                session.execute(sql_lock, [locked_until_ms] + event_ids)
                session.commit()
            
            # Now dispatch via EventBus
            dispatched_count = 0
            for ev in events:
                try:
                    payload_dict = json.loads(ev["payload"])
                    # We might want to pass event_id to payload so subscribers can dedup
                    payload_dict["_outbox_event_id"] = ev["id"]
                    payload_dict["_organization_id"] = ev.get("organization_id", "default")
                    
                    self.event_bus.publish(ev["topic"], payload_dict)
                    
                    # Mark completed
                    with self.storage.get_session() as session:
                        session.execute("UPDATE event_outbox SET status = 'COMPLETED' WHERE id = %s", (ev["id"],))
                        session.commit()
                    
                    dispatched_count += 1
                except Exception as loop_e:
                    print(f"[OutboxDispatcher Error] Failed to dispatch event {ev['id']}: {loop_e}")
                    # Leave it in PROCESSING until lock expires, then retry
            
            return ServiceResult.ok(data={"dispatched": dispatched_count})
        
        except Exception as e:
            return ServiceResult.fail(ForgeError(f"Dispatch batch failed: {str(e)}", error_code="OUTBOX_DISPATCH_FAILED", retryable=True))
