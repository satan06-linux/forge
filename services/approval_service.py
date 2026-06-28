# ForgePrompt Phase 7 — ApprovalService
import json
import logging
import time
import uuid

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class ApprovalService:
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
                CREATE TABLE IF NOT EXISTS approval_requests (
                    approval_id VARCHAR(64) PRIMARY KEY,
                    organization_id VARCHAR(64) NOT NULL,
                    workflow_id VARCHAR(64) NOT NULL,
                    step_id VARCHAR(64) NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
                    snapshot_data LONGTEXT,
                    compensation_action LONGTEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    resolved_at DATETIME,
                    resolved_by VARCHAR(64)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            cursor.execute("""
                SELECT COUNT(1) FROM INFORMATION_SCHEMA.STATISTICS 
                WHERE TABLE_SCHEMA = DATABASE() 
                  AND TABLE_NAME = 'approval_requests' 
                  AND INDEX_NAME = 'idx_approval_org_workflow'
            """)
            if cursor.fetchone()[0] == 0:
                cursor.execute("CREATE INDEX idx_approval_org_workflow ON approval_requests (organization_id, workflow_id)")
                
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS event_outbox (
                    event_id VARCHAR(64) PRIMARY KEY,
                    event_type VARCHAR(64) NOT NULL,
                    payload LONGTEXT,
                    delivered BOOLEAN DEFAULT FALSE,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"[ApprovalService Error] Failed to ensure tables: {e}")
        finally:
            if cursor:
                cursor.close()

    def create_approval_request(self, workflow_id, organization_id, step_id, snapshot_data, compensation_action, conn=None):
        start_time = time.time()
        local_conn = conn or self.storage.get_session()
        cursor = None
        try:
            cursor = local_conn.cursor()
            approval_id = str(uuid.uuid4())
            snapshot_str = json.dumps(snapshot_data) if snapshot_data else "{}"
            comp_str = json.dumps(compensation_action) if compensation_action else "{}"
            
            cursor.execute("""
                INSERT INTO approval_requests (approval_id, organization_id, workflow_id, step_id, status, snapshot_data, compensation_action)
                VALUES (%s, %s, %s, %s, 'PENDING', %s, %s)
            """, (approval_id, organization_id, workflow_id, step_id, snapshot_str, comp_str))
            
            # Register compensation hook if any
            saga = self.container.get('SagaCoordinator')
            if saga and compensation_action:
                saga.register_compensation(approval_id, 'approval_service', comp_str)
                
            cursor.execute("""
                INSERT INTO event_outbox (event_id, event_type, payload)
                VALUES (%s, %s, %s)
            """, (str(uuid.uuid4()), 'approval_requested', json.dumps({
                'approval_id': approval_id,
                'workflow_id': workflow_id,
                'organization_id': organization_id
            })))
            
            cursor.execute("""
                INSERT INTO lineage_events (event_id, entity_id, entity_type, operation, details)
                VALUES (%s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), approval_id, 'approval_request', 'created', json.dumps({'workflow_id': workflow_id})))
            
            if not conn:
                local_conn.commit()
                
            return ServiceResult.success(
                data={"approval_id": approval_id},
                duration_ms=int((time.time() - start_time) * 1000)
            )
        except Exception as e:
            if not conn:
                local_conn.rollback()
            logger.error(f"[ApprovalService Error] create_approval_request failed: {e}")
            return ServiceResult.fail(
                error=str(e),
                error_code="CREATE_APPROVAL_FAILED",
                duration_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            if cursor:
                cursor.close()

    def approve_request(self, approval_id, user_id, conn=None):
        return self._resolve_request(approval_id, user_id, 'approve', conn)

    def reject_request(self, approval_id, user_id, conn=None):
        return self._resolve_request(approval_id, user_id, 'reject', conn)

    def _resolve_request(self, approval_id, user_id, action, conn=None):
        start_time = time.time()
        local_conn = conn or self.storage.get_session()
        cursor = None
        try:
            cursor = local_conn.cursor()
            
            cursor.execute("""
                SELECT status, compensation_action, organization_id, workflow_id 
                FROM approval_requests 
                WHERE approval_id = %s FOR UPDATE
            """, (approval_id,))
            row = cursor.fetchone()
            if not row:
                raise ForgeError(f"Approval request {approval_id} not found")
                
            status, comp_action_str, org_id, wf_id = row
            if status != 'PENDING':
                raise ForgeError(f"Approval request already resolved: {status}")
                
            new_status = 'APPROVED' if action == 'approve' else 'REJECTED'
            
            cursor.execute("""
                UPDATE approval_requests
                SET status = %s, resolved_at = NOW(), resolved_by = %s
                WHERE approval_id = %s
            """, (new_status, user_id, approval_id))
            
            if new_status == 'REJECTED' and comp_action_str and comp_action_str != "{}":
                saga = self.container.get('SagaCoordinator')
                if saga:
                    saga.execute_compensation(approval_id, local_conn)
                    
            cursor.execute("""
                INSERT INTO event_outbox (event_id, event_type, payload)
                VALUES (%s, %s, %s)
            """, (str(uuid.uuid4()), f'approval_{action}d', json.dumps({
                'approval_id': approval_id,
                'workflow_id': wf_id,
                'organization_id': org_id,
                'resolved_by': user_id
            })))

            cursor.execute("""
                INSERT INTO lineage_events (event_id, entity_id, entity_type, operation, details)
                VALUES (%s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), approval_id, 'approval_request', action, json.dumps({'user_id': user_id})))
            
            if not conn:
                local_conn.commit()
                
            return ServiceResult.success(
                data={"approval_id": approval_id, "status": new_status},
                duration_ms=int((time.time() - start_time) * 1000)
            )
        except Exception as e:
            if not conn:
                local_conn.rollback()
            logger.error(f"[ApprovalService Error] _resolve_request failed: {e}")
            return ServiceResult.fail(
                error=str(e),
                error_code="RESOLVE_FAILED",
                duration_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            if cursor:
                cursor.close()
