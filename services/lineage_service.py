# ForgePrompt Phase 7 — LineageService
import json
import time
from typing import Optional, Dict, Any

from services.service_result import ServiceResult
from services.errors import StorageError

class LineageService:
    def __init__(self, container):
        self.container = container
        self._run_migrations()

    def _run_migrations(self):
        try:
            session = self.container.storage_provider.get_session()
            session.execute("""
                CREATE TABLE IF NOT EXISTS lineage_events (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    organization_id VARCHAR(255) NOT NULL,
                    event_type VARCHAR(100) NOT NULL,
                    entity_id VARCHAR(255) NOT NULL,
                    source_id VARCHAR(255),
                    operation VARCHAR(255) NOT NULL,
                    metadata LONGTEXT,
                    created_at INT NOT NULL
                ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            
            stat = session.execute("""
                SELECT COUNT(1) as cnt 
                FROM INFORMATION_SCHEMA.STATISTICS 
                WHERE table_name = 'lineage_events' AND index_name = 'idx_org_entity' AND table_schema = DATABASE()
            """).fetchone()
            
            if stat and stat['cnt'] == 0:
                session.execute("CREATE INDEX idx_org_entity ON lineage_events (organization_id, entity_id)")
                
            session.close()
        except Exception as e:
            print(f"[LineageService Error] Migration failed: {e}")

    def append_event(
        self, 
        organization_id: str,
        event_type: str, 
        entity_id: str, 
        operation: str,
        source_id: Optional[str] = None, 
        metadata: Optional[Dict[str, Any]] = None, 
        conn=None
    ) -> ServiceResult:
        start_time = time.time()
        session = conn or self.container.storage_provider.get_session()
        own_transaction = (conn is None)
        
        try:
            if own_transaction:
                session.begin()
                
            meta_json = json.dumps(metadata) if metadata else "{}"
            now = int(time.time())
            
            sql = """
                INSERT INTO lineage_events 
                    (organization_id, event_type, entity_id, source_id, operation, metadata, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            session.execute(sql, (organization_id, event_type, entity_id, source_id, operation, meta_json, now))
            inserted_id = session.lastrowid()
            
            if own_transaction:
                session.commit()
                
            return ServiceResult.ok(
                data={"inserted_id": inserted_id},
                duration_ms=int((time.time() - start_time) * 1000)
            )
        except Exception as e:
            if own_transaction:
                session.rollback()
            return ServiceResult.fail(
                StorageError(f"[LineageService Error] Failed to append lineage event: {e}"),
                duration_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            if own_transaction:
                session.close()

    def get_lineage(self, organization_id: str, entity_id: str) -> ServiceResult:
        start_time = time.time()
        try:
            session = self.container.storage_provider.get_session()
            sql = """
                SELECT * FROM lineage_events 
                WHERE organization_id = %s AND entity_id = %s 
                ORDER BY created_at ASC
            """
            rows = session.execute(sql, (organization_id, entity_id)).fetchall()
            session.close()
            
            for row in rows:
                if isinstance(row.get('metadata'), str):
                    try:
                        row['metadata'] = json.loads(row['metadata'])
                    except json.JSONDecodeError:
                        pass
                        
            return ServiceResult.ok(data=rows, duration_ms=int((time.time() - start_time) * 1000))
        except Exception as e:
            return ServiceResult.fail(
                StorageError(f"[LineageService Error] Failed to get lineage events: {e}"),
                duration_ms=int((time.time() - start_time) * 1000)
            )
