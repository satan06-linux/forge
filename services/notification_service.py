# ForgePrompt Phase 7 — NotificationService
import logging
import json
from typing import Dict, Any

from services.service_result import ServiceResult
from services.errors import ForgeError, StorageError, ValidationError

logger = logging.getLogger(__name__)

class NotificationService:
    def __init__(self, container):
        self.container = container
        self.storage = getattr(container, 'storage_provider', container.get('storage_provider') if hasattr(container, 'get') else None)
        self._ensure_tables()

    def _ensure_tables(self):
        try:
            with self.storage.get_session() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS notifications (
                        organization_id VARCHAR(36) NOT NULL,
                        notification_id VARCHAR(36) NOT NULL,
                        user_id VARCHAR(36),
                        type VARCHAR(64) NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        payload LONGTEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (organization_id, notification_id)
                    )
                """)
                cursor.execute("""
                    SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS 
                    WHERE TABLE_SCHEMA = DATABASE() 
                      AND TABLE_NAME = 'notifications' 
                      AND INDEX_NAME = 'idx_org_user'
                """)
                if cursor.fetchone()[0] == 0:
                    cursor.execute("CREATE INDEX idx_org_user ON notifications (organization_id, user_id)")
                
                # event inbox for idempotency
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS processed_events (
                        event_id VARCHAR(36) NOT NULL,
                        processor_name VARCHAR(64) NOT NULL,
                        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (event_id, processor_name)
                    )
                """)
                
                # external effect ledger dependency
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS external_effect_ledger (
                        organization_id VARCHAR(36) NOT NULL,
                        effect_id VARCHAR(36) NOT NULL,
                        effect_type VARCHAR(64) NOT NULL,
                        payload LONGTEXT NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        result LONGTEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (organization_id, effect_id)
                    )
                """)
                
                # lineage dependency
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS lineage_events (
                        organization_id VARCHAR(36) NOT NULL,
                        entity_id VARCHAR(128) NOT NULL,
                        entity_type VARCHAR(64) NOT NULL,
                        operation VARCHAR(64) NOT NULL,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"[NotificationService Error] Failed to ensure tables: {e}")

    def handle_notification_event(self, event_id: str, event_payload: Dict[str, Any], conn=None) -> ServiceResult:
        try:
            def _do_handle(c):
                cursor = c.cursor()
                # Rule 3: Every EventBus subscriber checks processed_events/event_inbox before acting
                cursor.execute("""
                    SELECT COUNT(*) FROM processed_events 
                    WHERE event_id = %s AND processor_name = 'notification_service'
                """, (event_id,))
                if cursor.fetchone()[0] > 0:
                    return ServiceResult.ok(metadata={"skipped": True, "reason": "Already processed"})
                
                organization_id = event_payload.get("organization_id")
                notification_id = event_payload.get("notification_id")
                user_id = event_payload.get("user_id")
                notif_type = event_payload.get("type", "in_app")
                payload_json = json.dumps(event_payload.get("payload", {}))

                if not organization_id or not notification_id:
                    raise ValidationError("Missing organization_id or notification_id")

                cursor.execute("""
                    INSERT INTO notifications (organization_id, notification_id, user_id, type, status, payload)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (organization_id, notification_id, user_id, notif_type, "PENDING", payload_json))

                cursor.execute("""
                    INSERT INTO processed_events (event_id, processor_name)
                    VALUES (%s, %s)
                """, (event_id, "notification_service"))
                
                # Rule 10: Every external side effect registers in external_effect_ledger before execution
                if notif_type == "webhook":
                    target_url = event_payload.get("webhook_url")
                    if target_url:
                        effect_payload = json.dumps({
                            "url": target_url,
                            "body": event_payload.get("payload")
                        })
                        cursor.execute("""
                            INSERT INTO external_effect_ledger (organization_id, effect_id, effect_type, payload, status)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (organization_id, notification_id, "WEBHOOK", effect_payload, "PENDING"))

                # Rule 12: Data lineage events
                cursor.execute("""
                    INSERT INTO lineage_events (organization_id, entity_id, entity_type, operation)
                    VALUES (%s, %s, %s, %s)
                """, (organization_id, notification_id, "NOTIFICATION", "CREATE"))

                return ServiceResult.ok()

            if conn:
                res = _do_handle(conn)
            else:
                with self.storage.get_session() as c:
                    res = _do_handle(c)
                    c.commit()
            return res
        except Exception as e:
            logger.error(f"[NotificationService Error] Failed to handle event: {e}")
            if isinstance(e, ForgeError):
                return ServiceResult.fail(e)
            return ServiceResult.fail(StorageError(str(e)))
            
    def get_user_notifications(self, organization_id: str, user_id: str, conn=None) -> ServiceResult:
        try:
            def _do_get(c):
                cursor = c.cursor(dictionary=True)
                cursor.execute("""
                    SELECT notification_id, type, status, payload, created_at 
                    FROM notifications
                    WHERE organization_id = %s AND user_id = %s
                    ORDER BY created_at DESC LIMIT 50
                """, (organization_id, user_id))
                return cursor.fetchall()
                
            results = _do_get(conn) if conn else None
            if not conn:
                with self.storage.get_session() as c:
                    results = _do_get(c)
            
            for row in results:
                if isinstance(row["payload"], str):
                    row["payload"] = json.loads(row["payload"])
            return ServiceResult.ok(data=results)
        except Exception as e:
            logger.error(f"[NotificationService Error] Failed to get notifications: {e}")
            return ServiceResult.fail(StorageError(str(e)))
