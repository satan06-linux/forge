import json
import logging
import time
import uuid
from datetime import datetime, timezone

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class SchedulerService:
    def __init__(self, container):
        self.container = container
        self.storage = container.get('StorageProvider')
        self._ensure_tables()

    def _ensure_tables(self):
        conn = None
        cursor = None
        try:
            conn = self.storage.get_session()
            cursor = conn.cursor()

            # Create scheduled_jobs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    job_id VARCHAR(64) PRIMARY KEY,
                    organization_id VARCHAR(64) NOT NULL,
                    cron_expr VARCHAR(128) NOT NULL,
                    payload LONGTEXT,
                    last_execution_time DATETIME,
                    next_execution_time DATETIME,
                    leader_node_id VARCHAR(64),
                    lease_expires_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # Index check for organization_id first
            cursor.execute("""
                SELECT COUNT(1) FROM INFORMATION_SCHEMA.STATISTICS 
                WHERE TABLE_SCHEMA = DATABASE() 
                  AND TABLE_NAME = 'scheduled_jobs' 
                  AND INDEX_NAME = 'idx_scheduled_jobs_org_next'
            """)
            if cursor.fetchone()[0] == 0:
                cursor.execute("CREATE INDEX idx_scheduled_jobs_org_next ON scheduled_jobs (organization_id, next_execution_time)")

            # Create clock_drift_monitor table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS clock_drift_monitor (
                    node_id VARCHAR(64) PRIMARY KEY,
                    last_reported_time DATETIME,
                    db_time DATETIME,
                    drift_ms INT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            
            # Ensure lineage_events exists for safety (though it should be centralized)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS lineage_events (
                    event_id VARCHAR(64) PRIMARY KEY,
                    entity_id VARCHAR(64) NOT NULL,
                    entity_type VARCHAR(64) NOT NULL,
                    operation VARCHAR(64) NOT NULL,
                    details LONGTEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"[SchedulerService Error] Failed to ensure tables: {e}")
        finally:
            if cursor:
                cursor.close()

    def schedule_job(self, job_id, organization_id, cron_expr, payload, next_execution_time, conn=None):
        start_time = time.time()
        local_conn = conn or self.storage.get_session()
        cursor = None
        try:
            cursor = local_conn.cursor()
            
            payload_str = json.dumps(payload) if payload else "{}"
            
            cursor.execute("""
                INSERT INTO scheduled_jobs (job_id, organization_id, cron_expr, payload, next_execution_time)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                cron_expr=VALUES(cron_expr), payload=VALUES(payload), next_execution_time=VALUES(next_execution_time)
            """, (job_id, organization_id, cron_expr, payload_str, next_execution_time))
            
            # Data lineage
            cursor.execute("""
                INSERT INTO lineage_events (event_id, entity_id, entity_type, operation, details)
                VALUES (%s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), job_id, 'scheduled_job', 'schedule', payload_str))
            
            if not conn:
                local_conn.commit()
            
            return ServiceResult.success(
                data={"job_id": job_id},
                duration_ms=int((time.time() - start_time) * 1000)
            )
        except Exception as e:
            if not conn:
                local_conn.rollback()
            logger.error(f"[SchedulerService Error] schedule_job failed: {e}")
            return ServiceResult.fail(
                error=str(e),
                error_code="SCHEDULE_FAILED",
                duration_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            if cursor:
                cursor.close()

    def elect_leader(self, job_id, node_id, lease_duration_seconds=60, conn=None):
        start_time = time.time()
        local_conn = conn or self.storage.get_session()
        cursor = None
        try:
            cursor = local_conn.cursor()
            
            # Check if job is available for lease
            cursor.execute("""
                UPDATE scheduled_jobs 
                SET leader_node_id = %s, 
                    lease_expires_at = DATE_ADD(NOW(), INTERVAL %s SECOND)
                WHERE job_id = %s 
                  AND (leader_node_id IS NULL OR lease_expires_at < NOW() OR leader_node_id = %s)
            """, (node_id, lease_duration_seconds, job_id, node_id))
            
            if cursor.rowcount > 0:
                if not conn:
                    local_conn.commit()
                return ServiceResult.success(
                    data={"leader": True, "job_id": job_id},
                    duration_ms=int((time.time() - start_time) * 1000)
                )
            else:
                if not conn:
                    local_conn.rollback()
                return ServiceResult.success(
                    data={"leader": False, "job_id": job_id},
                    duration_ms=int((time.time() - start_time) * 1000)
                )
        except Exception as e:
            if not conn:
                local_conn.rollback()
            logger.error(f"[SchedulerService Error] elect_leader failed: {e}")
            return ServiceResult.fail(
                error=str(e),
                error_code="ELECTION_FAILED",
                duration_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            if cursor:
                cursor.close()

    def monitor_clock_drift(self, node_id, current_node_time, conn=None):
        start_time = time.time()
        local_conn = conn or self.storage.get_session()
        cursor = None
        try:
            cursor = local_conn.cursor()
            
            cursor.execute("SELECT NOW()")
            db_time = cursor.fetchone()[0]
            
            drift_ms = int((current_node_time - db_time).total_seconds() * 1000)
            
            cursor.execute("""
                INSERT INTO clock_drift_monitor (node_id, last_reported_time, db_time, drift_ms)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                last_reported_time=VALUES(last_reported_time), db_time=VALUES(db_time), drift_ms=VALUES(drift_ms)
            """, (node_id, current_node_time, db_time, drift_ms))
            
            if not conn:
                local_conn.commit()
            
            return ServiceResult.success(
                data={"drift_ms": drift_ms, "db_time": str(db_time)},
                duration_ms=int((time.time() - start_time) * 1000)
            )
        except Exception as e:
            if not conn:
                local_conn.rollback()
            logger.error(f"[SchedulerService Error] monitor_clock_drift failed: {e}")
            return ServiceResult.fail(
                error=str(e),
                error_code="DRIFT_MONITOR_FAILED",
                duration_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            if cursor:
                cursor.close()

    def mark_job_executed(self, job_id, next_execution_time, conn=None):
        start_time = time.time()
        local_conn = conn or self.storage.get_session()
        cursor = None
        try:
            cursor = local_conn.cursor()
            
            cursor.execute("""
                UPDATE scheduled_jobs
                SET last_execution_time = NOW(),
                    next_execution_time = %s,
                    leader_node_id = NULL,
                    lease_expires_at = NULL
                WHERE job_id = %s
            """, (next_execution_time, job_id))
            
            if not conn:
                local_conn.commit()
                
            return ServiceResult.success(
                data={"job_id": job_id},
                duration_ms=int((time.time() - start_time) * 1000)
            )
        except Exception as e:
            if not conn:
                local_conn.rollback()
            logger.error(f"[SchedulerService Error] mark_job_executed failed: {e}")
            return ServiceResult.fail(
                error=str(e),
                error_code="MARK_EXECUTED_FAILED",
                duration_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            if cursor:
                cursor.close()
