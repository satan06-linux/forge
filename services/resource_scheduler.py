# ForgePrompt Phase 7 — ResourceScheduler
import time
import json
from typing import Dict, Any, List, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError

class ResourceScheduler:
    def __init__(self, container):
        self.container = container
        self.storage_provider = container.get('storage_provider')
        self.worker_service = container.get('worker_service')
        self.event_bus = container.get('event_bus')

    def initialize_schema(self, conn=None) -> ServiceResult:
        managed_conn = False
        if conn is None:
            conn = self.storage_provider.get_session()
            managed_conn = True
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS job_assignments (
                    assignment_id VARCHAR(64) PRIMARY KEY,
                    job_id VARCHAR(64) NOT NULL,
                    worker_id VARCHAR(64) NOT NULL,
                    assigned_at BIGINT,
                    status VARCHAR(32)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS lineage_events (
                    event_id VARCHAR(64) PRIMARY KEY,
                    entity_type VARCHAR(64) NOT NULL,
                    entity_id VARCHAR(64) NOT NULL,
                    event_type VARCHAR(64) NOT NULL,
                    timestamp BIGINT,
                    metadata LONGTEXT
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)
            
            if managed_conn:
                conn.commit()
            return ServiceResult.success(True)
        except Exception as e:
            if managed_conn:
                conn.rollback()
            return ServiceResult.fail(ForgeError(code="SCHEDULER_INIT_FAILED", message=str(e)))
        finally:
            if managed_conn and conn:
                conn.close()

    def schedule_job(self, job_id: str, required_capabilities: List[str], conn=None) -> ServiceResult:
        start_time = time.time()
        managed_conn = False
        if conn is None:
            conn = self.storage_provider.get_session()
            managed_conn = True
            
        try:
            workers_result = self.worker_service.get_available_workers(required_capabilities, conn=conn)
            if not workers_result.success:
                return workers_result
                
            workers = workers_result.data
            if not workers:
                return ServiceResult.fail(ForgeError(code="NO_WORKERS_AVAILABLE", message="No workers available with required capabilities"))
                
            best_worker = self._select_best_worker(workers)
            if not best_worker:
                return ServiceResult.fail(ForgeError(code="WORKER_SELECTION_FAILED", message="Could not select a valid worker"))
                
            cursor = conn.cursor()
            now = int(time.time() * 1000)
            assignment_id = f"assign_{job_id}_{best_worker['worker_id']}_{now}"
            
            cursor.execute("""
                INSERT INTO job_assignments (assignment_id, job_id, worker_id, assigned_at, status)
                VALUES (%s, %s, %s, %s, 'ASSIGNED')
            """, (assignment_id, job_id, best_worker['worker_id'], now))
            
            cursor.execute("""
                INSERT INTO lineage_events (event_id, entity_type, entity_id, event_type, timestamp, metadata)
                VALUES (%s, 'JOB', %s, 'JOB_SCHEDULED', %s, %s)
            """, (f"lin_{assignment_id}", job_id, now, json.dumps({"worker_id": best_worker["worker_id"]})))
            
            # Emit event
            event_id = f"evt_sch_{now}_{job_id}"
            cursor.execute("""
                INSERT INTO event_outbox (event_id, topic, payload, created_at)
                VALUES (%s, 'job.scheduled', %s, %s)
            """, (event_id, json.dumps({"job_id": job_id, "worker_id": best_worker["worker_id"]}), now))
            
            if managed_conn:
                conn.commit()
                
            return ServiceResult.success({
                "assignment_id": assignment_id,
                "worker_id": best_worker['worker_id']
            }, duration_ms=(time.time() - start_time)*1000)
            
        except Exception as e:
            if managed_conn:
                conn.rollback()
            return ServiceResult.fail(ForgeError(code="SCHEDULING_FAILED", message=str(e)))
        finally:
            if managed_conn and conn:
                conn.close()

    def _select_best_worker(self, workers: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not workers:
            return None
            
        best_worker = None
        best_score = -1.0
        
        for w in workers:
            remaining_capacity = w['max_capacity'] - w['current_load']
            if remaining_capacity <= 0:
                continue
                
            score = (w['health_score'] / 100.0) * remaining_capacity
            if score > best_score:
                best_score = score
                best_worker = w
                
        return best_worker
