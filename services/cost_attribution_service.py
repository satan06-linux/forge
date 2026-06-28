# ForgePrompt Phase 7 — CostAttributionService
import json
import time
from typing import Optional, Dict, Any

from services.service_result import ServiceResult
from services.errors import StorageError

class CostAttributionService:
    def __init__(self, container):
        self.container = container
        self._run_migrations()

    def _run_migrations(self):
        try:
            session = self.container.storage_provider.get_session()
            session.execute("""
                CREATE TABLE IF NOT EXISTS cost_attribution (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    organization_id VARCHAR(255) NOT NULL,
                    department_id VARCHAR(255),
                    agent_id VARCHAR(255),
                    workflow_id VARCHAR(255),
                    resource_type VARCHAR(100) NOT NULL,
                    cost_amount DECIMAL(20, 6) NOT NULL,
                    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                    usage_units DECIMAL(20, 6) NOT NULL,
                    metadata LONGTEXT,
                    created_at INT NOT NULL
                ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            
            stat = session.execute("""
                SELECT COUNT(1) as cnt 
                FROM INFORMATION_SCHEMA.STATISTICS 
                WHERE table_name = 'cost_attribution' AND index_name = 'idx_org_dept_agent' AND table_schema = DATABASE()
            """).fetchone()
            
            if stat and stat['cnt'] == 0:
                session.execute("CREATE INDEX idx_org_dept_agent ON cost_attribution (organization_id, department_id, agent_id)")
                
            session.close()
        except Exception as e:
            print(f"[CostAttributionService Error] Migration failed: {e}")

    def record_cost(
        self,
        organization_id: str,
        resource_type: str,
        cost_amount: float,
        usage_units: float,
        department_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        currency: str = "USD",
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
                INSERT INTO cost_attribution 
                    (organization_id, department_id, agent_id, workflow_id, resource_type, cost_amount, currency, usage_units, metadata, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            session.execute(sql, (
                organization_id, department_id, agent_id, workflow_id, 
                resource_type, cost_amount, currency, usage_units, meta_json, now
            ))
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
                StorageError(f"[CostAttributionService Error] Failed to record cost: {e}"),
                duration_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            if own_transaction:
                session.close()

    def get_cost_summary(self, organization_id: str, start_time: Optional[int] = None, end_time: Optional[int] = None) -> ServiceResult:
        t_start = time.time()
        try:
            session = self.container.storage_provider.get_session()
            sql = """
                SELECT resource_type, SUM(cost_amount) as total_cost, SUM(usage_units) as total_units
                FROM cost_attribution
                WHERE organization_id = %s
            """
            params = [organization_id]
            if start_time is not None:
                sql += " AND created_at >= %s"
                params.append(start_time)
            if end_time is not None:
                sql += " AND created_at <= %s"
                params.append(end_time)
                
            sql += " GROUP BY resource_type"
            
            rows = session.execute(sql, tuple(params)).fetchall()
            session.close()
            return ServiceResult.ok(data=rows, duration_ms=int((time.time() - t_start) * 1000))
        except Exception as e:
            return ServiceResult.fail(
                StorageError(f"[CostAttributionService Error] Failed to get cost summary: {e}"),
                duration_ms=int((time.time() - t_start) * 1000)
            )
