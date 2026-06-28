# ForgePrompt Phase 7 — ProvenanceGraphService
import json
import time
from typing import Optional, Dict, Any, List

from services.service_result import ServiceResult
from services.errors import StorageError

class ProvenanceGraphService:
    def __init__(self, container):
        self.container = container
        self._run_migrations()

    def _run_migrations(self):
        try:
            session = self.container.storage_provider.get_session()
            session.execute("""
                CREATE TABLE IF NOT EXISTS provenance_nodes (
                    id VARCHAR(255) PRIMARY KEY,
                    organization_id VARCHAR(255) NOT NULL,
                    node_type VARCHAR(100) NOT NULL,
                    entity_id VARCHAR(255) NOT NULL,
                    metadata LONGTEXT,
                    created_at INT NOT NULL
                ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            
            stat_node = session.execute("""
                SELECT COUNT(1) as cnt 
                FROM INFORMATION_SCHEMA.STATISTICS 
                WHERE table_name = 'provenance_nodes' AND index_name = 'idx_org_entity' AND table_schema = DATABASE()
            """).fetchone()
            if stat_node and stat_node['cnt'] == 0:
                session.execute("CREATE INDEX idx_org_entity ON provenance_nodes (organization_id, entity_id)")

            session.execute("""
                CREATE TABLE IF NOT EXISTS provenance_edges (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    organization_id VARCHAR(255) NOT NULL,
                    from_node_id VARCHAR(255) NOT NULL,
                    to_node_id VARCHAR(255) NOT NULL,
                    relation_type VARCHAR(100) NOT NULL,
                    metadata LONGTEXT,
                    created_at INT NOT NULL
                ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            stat_edge = session.execute("""
                SELECT COUNT(1) as cnt 
                FROM INFORMATION_SCHEMA.STATISTICS 
                WHERE table_name = 'provenance_edges' AND index_name = 'idx_org_from_to' AND table_schema = DATABASE()
            """).fetchone()
            if stat_edge and stat_edge['cnt'] == 0:
                session.execute("CREATE INDEX idx_org_from_to ON provenance_edges (organization_id, from_node_id, to_node_id)")
                
            session.close()
        except Exception as e:
            print(f"[ProvenanceGraphService Error] Migration failed: {e}")

    def add_node(
        self,
        node_id: str,
        organization_id: str,
        node_type: str,
        entity_id: str,
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
                INSERT INTO provenance_nodes 
                    (id, organization_id, node_type, entity_id, metadata, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    node_type = VALUES(node_type),
                    metadata = VALUES(metadata)
            """
            session.execute(sql, (node_id, organization_id, node_type, entity_id, meta_json, now))
            
            if own_transaction:
                session.commit()
                
            return ServiceResult.ok(
                data={"node_id": node_id},
                duration_ms=int((time.time() - start_time) * 1000)
            )
        except Exception as e:
            if own_transaction:
                session.rollback()
            return ServiceResult.fail(
                StorageError(f"[ProvenanceGraphService Error] Failed to add node: {e}"),
                duration_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            if own_transaction:
                session.close()

    def add_edge(
        self,
        organization_id: str,
        from_node_id: str,
        to_node_id: str,
        relation_type: str,
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
                INSERT INTO provenance_edges 
                    (organization_id, from_node_id, to_node_id, relation_type, metadata, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """
            session.execute(sql, (organization_id, from_node_id, to_node_id, relation_type, meta_json, now))
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
                StorageError(f"[ProvenanceGraphService Error] Failed to add edge: {e}"),
                duration_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            if own_transaction:
                session.close()

    def get_ancestors(self, organization_id: str, node_id: str, max_depth: int = 5) -> ServiceResult:
        start_time = time.time()
        try:
            session = self.container.storage_provider.get_session()
            
            ancestors = []
            current_level = [node_id]
            
            for _ in range(max_depth):
                if not current_level:
                    break
                
                format_strings = ','.join(['%s'] * len(current_level))
                sql = f"""
                    SELECT from_node_id, to_node_id, relation_type 
                    FROM provenance_edges 
                    WHERE organization_id = %s AND to_node_id IN ({format_strings})
                """
                params = [organization_id] + current_level
                
                rows = session.execute(sql, tuple(params)).fetchall()
                if not rows:
                    break
                    
                ancestors.extend(rows)
                current_level = [row['from_node_id'] for row in rows]
                
            session.close()
            return ServiceResult.ok(data=ancestors, duration_ms=int((time.time() - start_time) * 1000))
        except Exception as e:
            return ServiceResult.fail(
                StorageError(f"[ProvenanceGraphService Error] Failed to get ancestors: {e}"),
                duration_ms=int((time.time() - start_time) * 1000)
            )
            
    def get_descendants(self, organization_id: str, node_id: str, max_depth: int = 5) -> ServiceResult:
        start_time = time.time()
        try:
            session = self.container.storage_provider.get_session()
            
            descendants = []
            current_level = [node_id]
            
            for _ in range(max_depth):
                if not current_level:
                    break
                
                format_strings = ','.join(['%s'] * len(current_level))
                sql = f"""
                    SELECT from_node_id, to_node_id, relation_type 
                    FROM provenance_edges 
                    WHERE organization_id = %s AND from_node_id IN ({format_strings})
                """
                params = [organization_id] + current_level
                
                rows = session.execute(sql, tuple(params)).fetchall()
                if not rows:
                    break
                    
                descendants.extend(rows)
                current_level = [row['to_node_id'] for row in rows]
                
            session.close()
            return ServiceResult.ok(data=descendants, duration_ms=int((time.time() - start_time) * 1000))
        except Exception as e:
            return ServiceResult.fail(
                StorageError(f"[ProvenanceGraphService Error] Failed to get descendants: {e}"),
                duration_ms=int((time.time() - start_time) * 1000)
            )
