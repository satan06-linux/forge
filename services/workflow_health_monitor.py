# ForgePrompt Phase 7 — WorkflowHealthMonitor
import json
import logging
import time
import uuid
from datetime import datetime

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class WorkflowHealthMonitor:
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

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS active_workflows (
                    workflow_id VARCHAR(64) PRIMARY KEY,
                    organization_id VARCHAR(64) NOT NULL,
                    node_id VARCHAR(64),
                    status VARCHAR(32) NOT NULL,
                    last_heartbeat DATETIME,
                    consecutive_checkpoint_failures INT DEFAULT 0,
                    retry_count INT DEFAULT 0,
                    resume_after DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS execution_queue (
                    workflow_id VARCHAR(64) PRIMARY KEY,
                    priority INT DEFAULT 0,
                    resume_after DATETIME
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pending_events (
                    event_id VARCHAR(64) PRIMARY KEY,
                    workflow_id VARCHAR(64) NOT NULL,
                    organization_id VARCHAR(64) NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    dispatch_time DATETIME
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"[WorkflowHealthMonitor Error] Failed to ensure tables: {e}")
        finally:
            if cursor:
                cursor.close()

    def detect_and_recover(self, conn=None):
        start_time = time.time()
        local_conn = conn or self.storage.get_session()
        cursor = None
        try:
            cursor = local_conn.cursor()
            
            recovery_actions = []
            
            # Pattern 1: Missing heartbeat -> Reassign node
            cursor.execute("""
                SELECT workflow_id, organization_id, node_id 
                FROM active_workflows 
                WHERE status = 'RUNNING' AND last_heartbeat < DATE_SUB(NOW(), INTERVAL 5 MINUTE)
            """)
            for row in cursor.fetchall():
                wf_id, org_id, node_id = row
                cursor.execute("""
                    UPDATE active_workflows SET status = 'QUEUED', node_id = NULL, last_heartbeat = NOW()
                    WHERE workflow_id = %s
                """, (wf_id,))
                self._record_recovery(cursor, wf_id, org_id, 'REASSIGN_NODE', f"Node {node_id} dead")
                recovery_actions.append(wf_id)
                
            # Pattern 2: Stuck in pending -> Bump priority
            cursor.execute("""
                SELECT workflow_id, organization_id 
                FROM active_workflows 
                WHERE status = 'PENDING' AND created_at < DATE_SUB(NOW(), INTERVAL 15 MINUTE)
            """)
            for row in cursor.fetchall():
                wf_id, org_id = row
                cursor.execute("""
                    UPDATE execution_queue SET priority = priority + 10 
                    WHERE workflow_id = %s
                """, (wf_id,))
                cursor.execute("""
                    UPDATE active_workflows SET created_at = NOW() 
                    WHERE workflow_id = %s
                """, (wf_id,))
                self._record_recovery(cursor, wf_id, org_id, 'BUMP_PRIORITY', "Queue jammed")
                recovery_actions.append(wf_id)

            # Pattern 3: Event handler timeout -> Resend event
            cursor.execute("""
                SELECT event_id, workflow_id, organization_id 
                FROM pending_events 
                WHERE status = 'DISPATCHED' AND dispatch_time < DATE_SUB(NOW(), INTERVAL 2 MINUTE)
            """)
            for row in cursor.fetchall():
                evt_id, wf_id, org_id = row
                cursor.execute("""
                    UPDATE pending_events SET status = 'QUEUED', dispatch_time = NULL 
                    WHERE event_id = %s
                """, (evt_id,))
                self._record_recovery(cursor, wf_id, org_id, 'RESEND_EVENT', f"Event {evt_id} timed out")
                recovery_actions.append(wf_id)

            # Pattern 4: Checkpoint failure loop -> Abort workflow
            cursor.execute("""
                SELECT workflow_id, organization_id 
                FROM active_workflows 
                WHERE status = 'RUNNING' AND consecutive_checkpoint_failures >= 3
            """)
            for row in cursor.fetchall():
                wf_id, org_id = row
                cursor.execute("""
                    UPDATE active_workflows SET status = 'ABORTED' 
                    WHERE workflow_id = %s
                """, (wf_id,))
                self._record_recovery(cursor, wf_id, org_id, 'ABORT_WORKFLOW', "Checkpoint failure loop")
                recovery_actions.append(wf_id)

            # Pattern 5: API rate limit stalled -> Exponential backoff + resume
            cursor.execute("""
                SELECT workflow_id, organization_id, retry_count 
                FROM active_workflows 
                WHERE status = 'RATE_LIMITED' AND updated_at < DATE_SUB(NOW(), INTERVAL 1 MINUTE)
            """)
            for row in cursor.fetchall():
                wf_id, org_id, retries = row
                backoff_minutes = 2 ** min(retries, 5)
                cursor.execute("""
                    UPDATE active_workflows 
                    SET status = 'QUEUED', 
                        resume_after = DATE_ADD(NOW(), INTERVAL %s MINUTE),
                        retry_count = retry_count + 1
                    WHERE workflow_id = %s
                """, (backoff_minutes, wf_id))
                self._record_recovery(cursor, wf_id, org_id, 'EXPONENTIAL_BACKOFF', "API 429 stalled")
                recovery_actions.append(wf_id)

            if not conn:
                local_conn.commit()
                
            return ServiceResult.success(
                data={"recovered_workflows": len(recovery_actions), "details": recovery_actions},
                duration_ms=int((time.time() - start_time) * 1000)
            )
        except Exception as e:
            if not conn:
                local_conn.rollback()
            logger.error(f"[WorkflowHealthMonitor Error] detect_and_recover failed: {e}")
            return ServiceResult.fail(
                error=str(e),
                error_code="RECOVERY_FAILED",
                duration_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            if cursor:
                cursor.close()

    def _record_recovery(self, cursor, workflow_id, organization_id, action, reason):
        event_id = str(uuid.uuid4())
        payload = json.dumps({'action': action, 'reason': reason})
        
        cursor.execute("""
            INSERT INTO event_outbox (event_id, event_type, payload)
            VALUES (%s, %s, %s)
        """, (event_id, 'workflow_recovery_executed', json.dumps({
            'workflow_id': workflow_id,
            'organization_id': organization_id,
            'action': action,
            'reason': reason
        })))
        
        cursor.execute("""
            INSERT INTO lineage_events (event_id, entity_id, entity_type, operation, details)
            VALUES (%s, %s, %s, %s, %s)
        """, (event_id, workflow_id, 'workflow', 'auto_recovery', payload))
