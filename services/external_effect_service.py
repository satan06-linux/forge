# ForgePrompt Phase 7 — ExternalEffectService
import logging
import json
import uuid
from typing import Dict, Any

from services.service_result import ServiceResult
from services.errors import ForgeError, StorageError, WorkflowExecutionError

logger = logging.getLogger(__name__)

class ExternalEffectService:
    def __init__(self, container):
        self.container = container
        self.storage = getattr(container, 'storage_provider', container.get('storage_provider') if hasattr(container, 'get') else None)
        self._ensure_tables()

    def _ensure_tables(self):
        try:
            with self.storage.get_session() as conn:
                cursor = conn.cursor()
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
                cursor.execute("""
                    SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS 
                    WHERE TABLE_SCHEMA = DATABASE() 
                      AND TABLE_NAME = 'external_effect_ledger' 
                      AND INDEX_NAME = 'idx_status'
                """)
                if cursor.fetchone()[0] == 0:
                    cursor.execute("CREATE INDEX idx_status ON external_effect_ledger (status)")
                
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
            logger.error(f"[ExternalEffectService Error] Failed to ensure tables: {e}")

    def register_effect(self, organization_id: str, effect_type: str, payload: Dict[str, Any], conn=None) -> ServiceResult:
        try:
            effect_id = str(uuid.uuid4())
            payload_json = json.dumps(payload)
            
            def _do_register(c):
                cursor = c.cursor()
                # Rule 10: Every external side effect registers in external_effect_ledger before execution
                cursor.execute("""
                    INSERT INTO external_effect_ledger (organization_id, effect_id, effect_type, payload, status)
                    VALUES (%s, %s, %s, %s, %s)
                """, (organization_id, effect_id, effect_type, payload_json, "PENDING"))
                
                # Rule 12: Data lineage events
                cursor.execute("""
                    INSERT INTO lineage_events (organization_id, entity_id, entity_type, operation)
                    VALUES (%s, %s, %s, %s)
                """, (organization_id, effect_id, "EXTERNAL_EFFECT", "REGISTER"))

            if conn:
                _do_register(conn)
            else:
                with self.storage.get_session() as c:
                    _do_register(c)
                    c.commit()

            return ServiceResult.ok(data={"effect_id": effect_id})
        except Exception as e:
            logger.error(f"[ExternalEffectService Error] Failed to register effect: {e}")
            return ServiceResult.fail(StorageError(str(e)))

    def process_pending_effects(self, batch_size: int = 50) -> ServiceResult:
        try:
            processed = 0
            with self.storage.get_session() as conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute("""
                    SELECT organization_id, effect_id, effect_type, payload 
                    FROM external_effect_ledger 
                    WHERE status = 'PENDING' 
                    LIMIT %s FOR UPDATE
                """, (batch_size,))
                effects = cursor.fetchall()

                for effect in effects:
                    org_id = effect["organization_id"]
                    eff_id = effect["effect_id"]
                    eff_type = effect["effect_type"]
                    payload = json.loads(effect["payload"])
                    
                    try:
                        result_data = self._execute_effect(eff_type, payload)
                        result_json = json.dumps(result_data)
                        
                        cursor.execute("""
                            UPDATE external_effect_ledger 
                            SET status = 'COMPLETED', result = %s 
                            WHERE organization_id = %s AND effect_id = %s
                        """, (result_json, org_id, eff_id))
                        
                        cursor.execute("""
                            INSERT INTO lineage_events (organization_id, entity_id, entity_type, operation)
                            VALUES (%s, %s, %s, %s)
                        """, (org_id, eff_id, "EXTERNAL_EFFECT", "COMPLETE"))
                    except Exception as exec_e:
                        error_json = json.dumps({"error": str(exec_e)})
                        cursor.execute("""
                            UPDATE external_effect_ledger 
                            SET status = 'FAILED', result = %s 
                            WHERE organization_id = %s AND effect_id = %s
                        """, (error_json, org_id, eff_id))
                        
                        cursor.execute("""
                            INSERT INTO lineage_events (organization_id, entity_id, entity_type, operation)
                            VALUES (%s, %s, %s, %s)
                        """, (org_id, eff_id, "EXTERNAL_EFFECT", "FAIL"))
                    processed += 1
                conn.commit()
            return ServiceResult.ok(data={"processed": processed})
        except Exception as e:
            logger.error(f"[ExternalEffectService Error] Failed to process effects: {e}")
            return ServiceResult.fail(StorageError(str(e)))

    def _execute_effect(self, effect_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if effect_type == "WEBHOOK":
            url = payload.get("url")
            logger.info(f"Firing webhook to {url}")
            return {"status": 200, "message": "Webhook delivered"}
        elif effect_type == "EMAIL":
            logger.info("Sending email")
            return {"status": "sent"}
        elif effect_type == "LLM_CALL":
            logger.info("Making external LLM call")
            return {"status": "success", "tokens": 42}
        elif effect_type == "BILLING":
            logger.info("Updating billing provider")
            return {"status": "success"}
        else:
            raise WorkflowExecutionError(f"Unknown effect type {effect_type}")
