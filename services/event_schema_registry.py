# ForgePrompt Phase 7 — EventSchemaRegistry
from typing import Dict, Any, Optional
import json
import logging

from services.service_result import ServiceResult
from services.errors import ForgeError, ValidationError, StorageError, NotFoundError

logger = logging.getLogger(__name__)

class EventSchemaRegistry:
    def __init__(self, container):
        self.container = container
        self.storage = getattr(container, 'storage_provider', container.get('storage_provider') if hasattr(container, 'get') else None)
        self._ensure_tables()
        
    def _ensure_tables(self):
        try:
            with self.storage.get_session() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS event_schemas (
                        organization_id VARCHAR(36) NOT NULL,
                        event_type VARCHAR(128) NOT NULL,
                        version INT NOT NULL,
                        schema_payload LONGTEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (organization_id, event_type, version)
                    )
                """)
                cursor.execute("""
                    SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS 
                    WHERE TABLE_SCHEMA = DATABASE() 
                      AND TABLE_NAME = 'event_schemas' 
                      AND INDEX_NAME = 'idx_org_event_type'
                """)
                if cursor.fetchone()[0] == 0:
                    cursor.execute("CREATE INDEX idx_org_event_type ON event_schemas (organization_id, event_type)")
                
                # Lineage table dependency
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
            logger.error(f"[EventSchemaRegistry Error] Failed to ensure tables: {e}")

    def register_schema(self, organization_id: str, event_type: str, version: int, schema_payload: Dict[str, Any], conn=None) -> ServiceResult:
        try:
            # Compatibility check with previous version
            prev_schema_res = self.get_schema(organization_id, event_type, version - 1, conn=conn)
            if prev_schema_res.success:
                prev_schema = prev_schema_res.data
                compat_res = self._check_compatibility(prev_schema, schema_payload)
                if not compat_res.success:
                    return compat_res

            schema_json = json.dumps(schema_payload)
            def _do_insert(c):
                cursor = c.cursor()
                cursor.execute("""
                    INSERT INTO event_schemas (organization_id, event_type, version, schema_payload)
                    VALUES (%s, %s, %s, %s)
                """, (organization_id, event_type, version, schema_json))
                
                # Rule 12: Data lineage events
                cursor.execute("""
                    INSERT INTO lineage_events (organization_id, entity_id, entity_type, operation)
                    VALUES (%s, %s, %s, %s)
                """, (organization_id, f"{event_type}_v{version}", "EVENT_SCHEMA", "REGISTER"))
            
            if conn:
                _do_insert(conn)
            else:
                with self.storage.get_session() as c:
                    _do_insert(c)
                    c.commit()
            
            return ServiceResult.ok(data={"organization_id": organization_id, "event_type": event_type, "version": version})
        except Exception as e:
            logger.error(f"[EventSchemaRegistry Error] Failed to register schema: {e}")
            return ServiceResult.fail(StorageError(str(e)))

    def get_schema(self, organization_id: str, event_type: str, version: int, conn=None) -> ServiceResult:
        try:
            def _do_get(c):
                cursor = c.cursor(dictionary=True)
                cursor.execute("""
                    SELECT schema_payload FROM event_schemas 
                    WHERE organization_id = %s AND event_type = %s AND version = %s
                """, (organization_id, event_type, version))
                return cursor.fetchone()

            row = _do_get(conn) if conn else None
            if not conn:
                with self.storage.get_session() as c:
                    row = _do_get(c)

            if not row:
                return ServiceResult.fail(NotFoundError(f"Schema not found for {event_type} v{version}"))
            
            return ServiceResult.ok(data=json.loads(row["schema_payload"]))
        except Exception as e:
            logger.error(f"[EventSchemaRegistry Error] Failed to get schema: {e}")
            return ServiceResult.fail(StorageError(str(e)))

    def _check_compatibility(self, prev_schema: Dict[str, Any], new_schema: Dict[str, Any]) -> ServiceResult:
        prev_required = set(prev_schema.get("required", []))
        new_required = set(new_schema.get("required", []))
        
        missing = prev_required - new_required
        if missing:
            return ServiceResult.fail(ValidationError(f"Backwards compatibility broken: missing required fields {missing}"))
        return ServiceResult.ok()

    def validate_event(self, organization_id: str, event_type: str, version: int, payload: Dict[str, Any], conn=None) -> ServiceResult:
        schema_res = self.get_schema(organization_id, event_type, version, conn=conn)
        if not schema_res.success:
            return schema_res
        
        schema = schema_res.data
        required = schema.get("required", [])
        for req in required:
            if req not in payload:
                return ServiceResult.fail(ValidationError(f"Missing required field: {req}"))
        
        return ServiceResult.ok(data=payload)
