# ForgePrompt Phase 7 — LocalActivityRunner
import logging
import json
import time
from typing import Callable, Any, Dict, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class LocalActivityRunner:
    """
    Executes activities inline without hopping through the worker queue.
    Results are recorded durably so that if the workflow fails later, 
    the local activity result is preserved.
    """
    def __init__(self, container):
        self.container = container

    def execute(self, 
                activity_fn: Callable[..., Any], 
                activity_name: str, 
                run_id: int, 
                node_id: str, 
                *args, 
                **kwargs) -> ServiceResult:
        """
        Executes an activity locally and records the result.
        """
        start_time = time.time()
        
        # Check if already executed (idempotency/durable history)
        try:
            sql = """
                SELECT payload_json FROM workflow_history 
                WHERE run_id = %s AND node_id = %s AND event_type = 'local_activity_completed'
            """
            existing = self.container.storage_provider.execute_one(sql, (run_id, node_id))
            if existing and existing.get('payload_json'):
                data = json.loads(existing['payload_json'])
                duration_ms = int((time.time() - start_time) * 1000)
                return ServiceResult.ok(data, duration_ms=duration_ms, cached=True)
        except Exception as e:
            logger.warning(f"[LocalActivityRunner Warning] Failed to check history: {e}")

        # Execute
        try:
            result = activity_fn(*args, **kwargs)
        except Exception as e:
            logger.error(f"[LocalActivityRunner Error] Activity {activity_name} failed: {e}")
            self._record_failure(run_id, node_id, activity_name, str(e))
            return ServiceResult.fail(ForgeError(code="LOCAL_ACTIVITY_FAILED", message=str(e)))

        # Record success
        duration_ms = int((time.time() - start_time) * 1000)
        try:
            self._record_success(run_id, node_id, activity_name, result)
        except Exception as e:
            logger.error(f"[LocalActivityRunner Error] Failed to record success for {activity_name}: {e}")
            return ServiceResult.fail(ForgeError(code="LOCAL_ACTIVITY_RECORD_FAILED", message=str(e)))

        return ServiceResult.ok(result, duration_ms=duration_ms)

    def _record_success(self, run_id: int, node_id: str, activity_name: str, result: Any):
        session = self.container.storage_provider.get_session()
        try:
            session.begin()
            
            # We use aggregate sequences to get the next sequence_number for this run_id
            seq_sql = """
                INSERT INTO aggregate_sequences (aggregate_type, aggregate_id, next_sequence)
                VALUES ('workflow_run', %s, 1)
                ON DUPLICATE KEY UPDATE next_sequence = next_sequence + 1
            """
            session.execute(seq_sql, (run_id,))
            
            get_seq_sql = "SELECT next_sequence FROM aggregate_sequences WHERE aggregate_type = 'workflow_run' AND aggregate_id = %s"
            session.execute(get_seq_sql, (run_id,))
            seq_row = session.fetchone()
            seq_num = seq_row['next_sequence'] if seq_row else 1

            payload = {
                "activity_name": activity_name,
                "result": result
            }
            
            hist_sql = """
                INSERT INTO workflow_history (run_id, sequence_number, event_type, node_id, payload_json)
                VALUES (%s, %s, 'local_activity_completed', %s, %s)
            """
            session.execute(hist_sql, (run_id, seq_num, node_id, json.dumps(payload)))
            
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _record_failure(self, run_id: int, node_id: str, activity_name: str, error_msg: str):
        session = self.container.storage_provider.get_session()
        try:
            session.begin()
            
            seq_sql = """
                INSERT INTO aggregate_sequences (aggregate_type, aggregate_id, next_sequence)
                VALUES ('workflow_run', %s, 1)
                ON DUPLICATE KEY UPDATE next_sequence = next_sequence + 1
            """
            session.execute(seq_sql, (run_id,))
            
            get_seq_sql = "SELECT next_sequence FROM aggregate_sequences WHERE aggregate_type = 'workflow_run' AND aggregate_id = %s"
            session.execute(get_seq_sql, (run_id,))
            seq_row = session.fetchone()
            seq_num = seq_row['next_sequence'] if seq_row else 1

            payload = {
                "activity_name": activity_name,
                "error": error_msg
            }
            
            hist_sql = """
                INSERT INTO workflow_history (run_id, sequence_number, event_type, node_id, payload_json)
                VALUES (%s, %s, 'local_activity_failed', %s, %s)
            """
            session.execute(hist_sql, (run_id, seq_num, node_id, json.dumps(payload)))
            
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()
