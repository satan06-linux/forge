# ForgePrompt Phase 7 — WorkerService
import json
import time
from typing import Dict, Any, List, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError

class WorkerService:
    def __init__(self, container):
        self.container = container
        self.storage_provider = container.get('storage_provider')
        self.event_bus = container.get('event_bus')

    def initialize_schema(self, conn=None) -> ServiceResult:
        managed_conn = False
        if conn is None:
            conn = self.storage_provider.get_session()
            managed_conn = True
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS workers (
                    worker_id VARCHAR(64) PRIMARY KEY,
                    status VARCHAR(32) NOT NULL,
                    capabilities LONGTEXT,
                    current_load INT DEFAULT 0,
                    max_capacity INT DEFAULT 10,
                    health_score FLOAT DEFAULT 100.0,
                    last_heartbeat BIGINT,
                    warm_pool_id VARCHAR(64)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)
            
            # Check index
            cursor.execute("""
                SELECT COUNT(1) as count 
                FROM INFORMATION_SCHEMA.STATISTICS 
                WHERE table_schema = DATABASE() 
                AND table_name = 'workers' 
                AND index_name = 'idx_worker_status'
            """)
            if cursor.fetchone()['count'] == 0:
                cursor.execute("CREATE INDEX idx_worker_status ON workers(status, last_heartbeat)")
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS worker_queues (
                    queue_id VARCHAR(64) PRIMARY KEY,
                    shard_key VARCHAR(64) NOT NULL,
                    job_id VARCHAR(64) NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    payload LONGTEXT,
                    required_capabilities LONGTEXT,
                    priority INT DEFAULT 0,
                    created_at BIGINT
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS event_outbox (
                    event_id VARCHAR(64) PRIMARY KEY,
                    topic VARCHAR(128) NOT NULL,
                    payload LONGTEXT,
                    created_at BIGINT,
                    status VARCHAR(32) DEFAULT 'PENDING'
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
            return ServiceResult.fail(ForgeError(code="WORKER_INIT_FAILED", message=str(e)))
        finally:
            if managed_conn and conn:
                conn.close()

    def register_worker(self, worker_id: str, capabilities: List[str], max_capacity: int, warm_pool_id: str = None, conn=None) -> ServiceResult:
        start_time = time.time()
        managed_conn = False
        if conn is None:
            conn = self.storage_provider.get_session()
            managed_conn = True
        
        try:
            cursor = conn.cursor()
            cap_json = json.dumps(capabilities)
            now = int(time.time() * 1000)
            
            cursor.execute("""
                INSERT INTO workers (worker_id, status, capabilities, current_load, max_capacity, health_score, last_heartbeat, warm_pool_id)
                VALUES (%s, 'IDLE', %s, 0, %s, 100.0, %s, %s)
                ON DUPLICATE KEY UPDATE 
                status = 'IDLE', capabilities = %s, max_capacity = %s, last_heartbeat = %s, warm_pool_id = %s
            """, (worker_id, cap_json, max_capacity, now, warm_pool_id, cap_json, max_capacity, now, warm_pool_id))
            
            # Emit event
            event_id = f"evt_wrk_reg_{now}_{worker_id}"
            cursor.execute("""
                INSERT INTO event_outbox (event_id, topic, payload, created_at)
                VALUES (%s, 'worker.registered', %s, %s)
            """, (event_id, json.dumps({"worker_id": worker_id}), now))
            
            if managed_conn:
                conn.commit()
            return ServiceResult.success({"worker_id": worker_id}, duration_ms=(time.time() - start_time)*1000)
        except Exception as e:
            if managed_conn:
                conn.rollback()
            return ServiceResult.fail(ForgeError(code="WORKER_REGISTRATION_FAILED", message=str(e)))
        finally:
            if managed_conn and conn:
                conn.close()

    def update_health(self, worker_id: str, load: int, metrics: Dict[str, Any], conn=None) -> ServiceResult:
        start_time = time.time()
        managed_conn = False
        if conn is None:
            conn = self.storage_provider.get_session()
            managed_conn = True
            
        try:
            cursor = conn.cursor(dictionary=True)
            now = int(time.time() * 1000)
            
            cursor.execute("SELECT health_score FROM workers WHERE worker_id = %s", (worker_id,))
            row = cursor.fetchone()
            if not row:
                return ServiceResult.fail(ForgeError(code="WORKER_NOT_FOUND", message="Worker not found"))
                
            old_health = row['health_score']
            
            cpu_usage = metrics.get('cpu', 0)
            error_rate = metrics.get('errors', 0)
            
            new_health = old_health
            if error_rate > 5:
                new_health -= 10
            elif error_rate == 0 and new_health < 100:
                new_health = min(100.0, new_health + 2)
                
            if cpu_usage > 90:
                new_health -= 5
                
            cursor.execute("""
                UPDATE workers 
                SET last_heartbeat = %s, current_load = %s, health_score = %s
                WHERE worker_id = %s
            """, (now, load, new_health, worker_id))
            
            if managed_conn:
                conn.commit()
            return ServiceResult.success({"worker_id": worker_id, "health": new_health}, duration_ms=(time.time() - start_time)*1000)
        except Exception as e:
            if managed_conn:
                conn.rollback()
            return ServiceResult.fail(ForgeError(code="WORKER_HEALTH_UPDATE_FAILED", message=str(e)))
        finally:
            if managed_conn and conn:
                conn.close()

    def enqueue_job(self, job_id: str, shard_key: str, payload: Dict[str, Any], required_capabilities: List[str], priority: int = 0, conn=None) -> ServiceResult:
        start_time = time.time()
        managed_conn = False
        if conn is None:
            conn = self.storage_provider.get_session()
            managed_conn = True
            
        try:
            cursor = conn.cursor()
            queue_id = f"q_{shard_key}_{job_id}"
            now = int(time.time() * 1000)
            
            cursor.execute("""
                INSERT INTO worker_queues (queue_id, shard_key, job_id, status, payload, required_capabilities, priority, created_at)
                VALUES (%s, %s, %s, 'PENDING', %s, %s, %s, %s)
            """, (queue_id, shard_key, job_id, json.dumps(payload), json.dumps(required_capabilities), priority, now))
            
            # Lineage
            cursor.execute("""
                INSERT INTO lineage_events (event_id, entity_type, entity_id, event_type, timestamp, metadata)
                VALUES (%s, 'JOB', %s, 'JOB_ENQUEUED', %s, %s)
            """, (f"lin_{queue_id}_{now}", job_id, now, json.dumps({"queue_id": queue_id})))
            
            if managed_conn:
                conn.commit()
            return ServiceResult.success({"queue_id": queue_id}, duration_ms=(time.time() - start_time)*1000)
        except Exception as e:
            if managed_conn:
                conn.rollback()
            return ServiceResult.fail(ForgeError(code="ENQUEUE_FAILED", message=str(e)))
        finally:
            if managed_conn and conn:
                conn.close()

    def get_available_workers(self, required_capabilities: List[str], conn=None) -> ServiceResult:
        start_time = time.time()
        managed_conn = False
        if conn is None:
            conn = self.storage_provider.get_session()
            managed_conn = True
            
        try:
            cursor = conn.cursor(dictionary=True)
            now = int(time.time() * 1000)
            cutoff = now - 30000 
            
            cursor.execute("""
                SELECT worker_id, capabilities, current_load, max_capacity, health_score 
                FROM workers 
                WHERE last_heartbeat > %s AND health_score > 50.0 AND current_load < max_capacity
            """, (cutoff,))
            
            workers = cursor.fetchall()
            valid_workers = []
            
            req_set = set(required_capabilities)
            for w in workers:
                caps = set(json.loads(w['capabilities']))
                if req_set.issubset(caps):
                    valid_workers.append(w)
                    
            return ServiceResult.success(valid_workers, duration_ms=(time.time() - start_time)*1000)
        except Exception as e:
            return ServiceResult.fail(ForgeError(code="FETCH_WORKERS_FAILED", message=str(e)))
        finally:
            if managed_conn and conn:
                conn.close()

    def dequeue_job(self, shard_key: str, conn=None) -> ServiceResult:
        start_time = time.time()
        managed_conn = False
        if conn is None:
            conn = self.storage_provider.get_session()
            managed_conn = True
            
        try:
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("""
                SELECT queue_id, job_id, payload, required_capabilities
                FROM worker_queues
                WHERE shard_key = %s AND status = 'PENDING'
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                FOR UPDATE
            """, (shard_key,))
            
            job = cursor.fetchone()
            if not job:
                return ServiceResult.success(None)
                
            now = int(time.time() * 1000)
            cursor.execute("UPDATE worker_queues SET status = 'PROCESSING' WHERE queue_id = %s", (job['queue_id'],))
            
            cursor.execute("""
                INSERT INTO lineage_events (event_id, entity_type, entity_id, event_type, timestamp, metadata)
                VALUES (%s, 'JOB', %s, 'JOB_DEQUEUED', %s, %s)
            """, (f"lin_deq_{now}", job['job_id'], now, json.dumps({"queue_id": job['queue_id']})))
            
            if managed_conn:
                conn.commit()
            
            job['payload'] = json.loads(job['payload'])
            job['required_capabilities'] = json.loads(job['required_capabilities'])
            return ServiceResult.success(job, duration_ms=(time.time() - start_time)*1000)
        except Exception as e:
            if managed_conn:
                conn.rollback()
            return ServiceResult.fail(ForgeError(code="DEQUEUE_FAILED", message=str(e)))
        finally:
            if managed_conn and conn:
                conn.close()
