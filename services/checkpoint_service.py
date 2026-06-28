# ForgePrompt Phase 7 — CheckpointService
import json
import logging
import time
import uuid

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class CheckpointService:
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
                CREATE TABLE IF NOT EXISTS workflow_checkpoints (
                    checkpoint_id VARCHAR(64) PRIMARY KEY,
                    organization_id VARCHAR(64) NOT NULL,
                    workflow_id VARCHAR(64) NOT NULL,
                    step_id VARCHAR(64) NOT NULL,
                    seq_num INT NOT NULL,
                    state_data LONGTEXT,
                    metadata LONGTEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            
            cursor.execute("""
                SELECT COUNT(1) FROM INFORMATION_SCHEMA.STATISTICS 
                WHERE TABLE_SCHEMA = DATABASE() 
                  AND TABLE_NAME = 'workflow_checkpoints' 
                  AND INDEX_NAME = 'idx_checkpoints_org_workflow'
            """)
            if cursor.fetchone()[0] == 0:
                cursor.execute("CREATE INDEX idx_checkpoints_org_workflow ON workflow_checkpoints (organization_id, workflow_id, seq_num)")
                
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS aggregate_sequences (
                    aggregate_id VARCHAR(64) PRIMARY KEY,
                    seq_num INT NOT NULL DEFAULT 0
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"[CheckpointService Error] Failed to ensure tables: {e}")
        finally:
            if cursor:
                cursor.close()

    def _get_next_sequence(self, workflow_id, cursor):
        cursor.execute("""
            INSERT INTO aggregate_sequences (aggregate_id, seq_num)
            VALUES (%s, 1)
            ON DUPLICATE KEY UPDATE seq_num = seq_num + 1
        """, (workflow_id,))
        cursor.execute("SELECT seq_num FROM aggregate_sequences WHERE aggregate_id = %s", (workflow_id,))
        return cursor.fetchone()[0]

    def save_checkpoint(self, workflow_id, organization_id, step_id, state_data, metadata=None, conn=None):
        start_time = time.time()
        local_conn = conn or self.storage.get_session()
        cursor = None
        try:
            cursor = local_conn.cursor()
            
            seq_num = self._get_next_sequence(workflow_id, cursor)
            checkpoint_id = str(uuid.uuid4())
            state_str = json.dumps(state_data) if state_data else "{}"
            meta_str = json.dumps(metadata) if metadata else "{}"
            
            cursor.execute("""
                INSERT INTO workflow_checkpoints (checkpoint_id, organization_id, workflow_id, step_id, seq_num, state_data, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (checkpoint_id, organization_id, workflow_id, step_id, seq_num, state_str, meta_str))
            
            cursor.execute("""
                INSERT INTO lineage_events (event_id, entity_id, entity_type, operation, details)
                VALUES (%s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), workflow_id, 'workflow', 'checkpoint_saved', json.dumps({'checkpoint_id': checkpoint_id, 'seq_num': seq_num})))
            
            if not conn:
                local_conn.commit()
                
            return ServiceResult.success(
                data={"checkpoint_id": checkpoint_id, "seq_num": seq_num},
                duration_ms=int((time.time() - start_time) * 1000)
            )
        except Exception as e:
            if not conn:
                local_conn.rollback()
            logger.error(f"[CheckpointService Error] save_checkpoint failed: {e}")
            return ServiceResult.fail(
                error=str(e),
                error_code="SAVE_CHECKPOINT_FAILED",
                duration_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            if cursor:
                cursor.close()

    def load_latest_checkpoint(self, workflow_id, organization_id, conn=None):
        start_time = time.time()
        local_conn = conn or self.storage.get_session()
        cursor = None
        try:
            cursor = local_conn.cursor()
            
            cursor.execute("""
                SELECT checkpoint_id, step_id, seq_num, state_data, metadata 
                FROM workflow_checkpoints 
                WHERE organization_id = %s AND workflow_id = %s
                ORDER BY seq_num DESC LIMIT 1
            """, (organization_id, workflow_id))
            
            row = cursor.fetchone()
            if not row:
                return ServiceResult.success(data=None, duration_ms=int((time.time() - start_time) * 1000))
                
            data = {
                "checkpoint_id": row[0],
                "step_id": row[1],
                "seq_num": row[2],
                "state_data": json.loads(row[3]) if row[3] else {},
                "metadata": json.loads(row[4]) if row[4] else {}
            }
            
            return ServiceResult.success(data=data, duration_ms=int((time.time() - start_time) * 1000))
        except Exception as e:
            logger.error(f"[CheckpointService Error] load_latest_checkpoint failed: {e}")
            return ServiceResult.fail(
                error=str(e),
                error_code="LOAD_CHECKPOINT_FAILED",
                duration_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            if cursor:
                cursor.close()

    def rollback_to_checkpoint(self, workflow_id, organization_id, checkpoint_id, conn=None):
        start_time = time.time()
        local_conn = conn or self.storage.get_session()
        cursor = None
        try:
            cursor = local_conn.cursor()
            
            cursor.execute("""
                SELECT seq_num, state_data 
                FROM workflow_checkpoints 
                WHERE organization_id = %s AND workflow_id = %s AND checkpoint_id = %s
            """, (organization_id, workflow_id, checkpoint_id))
            
            row = cursor.fetchone()
            if not row:
                raise ForgeError("Checkpoint not found")
                
            target_seq_num = row[0]
            
            cursor.execute("""
                DELETE FROM workflow_checkpoints 
                WHERE organization_id = %s AND workflow_id = %s AND seq_num > %s
            """, (organization_id, workflow_id, target_seq_num))
            
            cursor.execute("""
                UPDATE aggregate_sequences SET seq_num = %s WHERE aggregate_id = %s
            """, (target_seq_num, workflow_id))
            
            cursor.execute("""
                INSERT INTO lineage_events (event_id, entity_id, entity_type, operation, details)
                VALUES (%s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), workflow_id, 'workflow', 'rolled_back', json.dumps({'checkpoint_id': checkpoint_id})))
            
            cursor.execute("""
                INSERT INTO event_outbox (event_id, event_type, payload)
                VALUES (%s, %s, %s)
            """, (str(uuid.uuid4()), 'workflow_rolled_back', json.dumps({
                'workflow_id': workflow_id,
                'organization_id': organization_id,
                'checkpoint_id': checkpoint_id
            })))
            
            if not conn:
                local_conn.commit()
                
            return ServiceResult.success(
                data={"checkpoint_id": checkpoint_id, "seq_num": target_seq_num},
                duration_ms=int((time.time() - start_time) * 1000)
            )
        except Exception as e:
            if not conn:
                local_conn.rollback()
            logger.error(f"[CheckpointService Error] rollback_to_checkpoint failed: {e}")
            return ServiceResult.fail(
                error=str(e),
                error_code="ROLLBACK_FAILED",
                duration_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            if cursor:
                cursor.close()

    def replay_from_checkpoint(self, workflow_id, organization_id, checkpoint_id, conn=None):
        start_time = time.time()
        local_conn = conn or self.storage.get_session()
        cursor = None
        try:
            cursor = local_conn.cursor()
            
            cursor.execute("""
                SELECT seq_num, step_id, state_data 
                FROM workflow_checkpoints 
                WHERE organization_id = %s AND workflow_id = %s AND checkpoint_id = %s
            """, (organization_id, workflow_id, checkpoint_id))
            
            row = cursor.fetchone()
            if not row:
                raise ForgeError("Checkpoint not found")
                
            target_seq_num, step_id, state_data_str = row
            
            cursor.execute("""
                INSERT INTO lineage_events (event_id, entity_id, entity_type, operation, details)
                VALUES (%s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), workflow_id, 'workflow', 'replay_initiated', json.dumps({'checkpoint_id': checkpoint_id})))
            
            cursor.execute("""
                INSERT INTO event_outbox (event_id, event_type, payload)
                VALUES (%s, %s, %s)
            """, (str(uuid.uuid4()), 'workflow_replay_requested', json.dumps({
                'workflow_id': workflow_id,
                'organization_id': organization_id,
                'checkpoint_id': checkpoint_id,
                'step_id': step_id,
                'state_data': json.loads(state_data_str) if state_data_str else {}
            })))
            
            if not conn:
                local_conn.commit()
                
            return ServiceResult.success(
                data={"checkpoint_id": checkpoint_id, "seq_num": target_seq_num, "step_id": step_id},
                duration_ms=int((time.time() - start_time) * 1000)
            )
        except Exception as e:
            if not conn:
                local_conn.rollback()
            logger.error(f"[CheckpointService Error] replay_from_checkpoint failed: {e}")
            return ServiceResult.fail(
                error=str(e),
                error_code="REPLAY_FAILED",
                duration_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            if cursor:
                cursor.close()
