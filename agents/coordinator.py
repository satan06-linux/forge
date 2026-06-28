# ForgePrompt Phase 7 — AgentCoordinator
import json
import logging
import time
from typing import Any, Dict, List, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class AgentCoordinator:
    """
    Multi-agent supervisor, handling delegation and saga integration.
    """
    def __init__(self, container):
        self.container = container

    def delegate_task(self, parent_run_id: int, subagent_id: int, goal: str, 
                      organization_id: Optional[int] = None, conn=None) -> ServiceResult:
        """
        Delegates a task to a subagent and starts a saga to track it.
        """
        start_time = time.time()
        try:
            session = conn or self.container.storage_provider.get_session()
            owns_tx = False
            if conn is None:
                session.begin()
                owns_tx = True

            # Create agent run for subagent
            sql = """
                INSERT INTO agent_runs 
                (agent_id, organization_id, run_id, goal, status)
                VALUES (%s, %s, %s, %s, 'queued')
            """
            session.execute(sql, (subagent_id, organization_id, parent_run_id, goal))
            sub_run_id = session.lastrowid()

            # Start a saga for this delegation
            saga_sql = """
                INSERT INTO saga_executions (run_id, saga_type, status, organization_id)
                VALUES (%s, %s, 'running', %s)
            """
            session.execute(saga_sql, (parent_run_id, 'agent_delegation', organization_id))
            saga_id = session.lastrowid()

            # Create initial saga step
            step_sql = """
                INSERT INTO saga_steps (saga_id, step_name, step_order, status, forward_action_json)
                VALUES (%s, %s, %s, 'executing', %s)
            """
            action_payload = json.dumps({"subagent_run_id": sub_run_id, "goal": goal})
            session.execute(step_sql, (saga_id, 'delegate_to_subagent', 1, action_payload))
            
            # Write event to outbox to dispatch agent task (event-driven integration)
            seq_sql = """
                INSERT INTO aggregate_sequences (aggregate_type, aggregate_id, next_sequence)
                VALUES ('saga', %s, 1)
                ON DUPLICATE KEY UPDATE next_sequence = next_sequence + 1
            """
            session.execute(seq_sql, (saga_id,))
            
            get_seq_sql = "SELECT next_sequence FROM aggregate_sequences WHERE aggregate_type = 'saga' AND aggregate_id = %s"
            session.execute(get_seq_sql, (saga_id,))
            seq_row = session.fetchone()
            seq_num = seq_row['next_sequence'] if seq_row else 1

            outbox_sql = """
                INSERT INTO event_outbox (aggregate_type, aggregate_id, sequence_number, event_type, payload_json)
                VALUES ('saga', %s, %s, 'subagent_delegated', %s)
            """
            session.execute(outbox_sql, (saga_id, seq_num, json.dumps({"parent_run_id": parent_run_id, "sub_run_id": sub_run_id})))

            if owns_tx:
                session.commit()

            duration_ms = int((time.time() - start_time) * 1000)
            return ServiceResult.ok({"sub_run_id": sub_run_id, "saga_id": saga_id}, duration_ms=duration_ms)
        except Exception as e:
            if 'owns_tx' in locals() and owns_tx and 'session' in locals():
                session.rollback()
            logger.error(f"[AgentCoordinator Error] delegate_task failed: {str(e)}")
            return ServiceResult.fail(ForgeError(code="AGENT_DELEGATION_FAILED", message=str(e)))
        finally:
            if 'owns_tx' in locals() and owns_tx and 'session' in locals():
                session.close()

    def handle_subagent_result(self, saga_id: int, sub_run_id: int, status: str, 
                               result_output: Optional[str] = None, conn=None) -> ServiceResult:
        """
        Completes or fails the delegation saga based on subagent result.
        """
        start_time = time.time()
        try:
            session = conn or self.container.storage_provider.get_session()
            owns_tx = False
            if conn is None:
                session.begin()
                owns_tx = True

            saga_status = 'completed' if status == 'completed' else 'failed'
            
            upd_saga = "UPDATE saga_executions SET status = %s, completed_at = CURRENT_TIMESTAMP WHERE id = %s"
            session.execute(upd_saga, (saga_status, saga_id))

            upd_step = """
                UPDATE saga_steps SET status = %s, result_json = %s, executed_at = CURRENT_TIMESTAMP 
                WHERE saga_id = %s AND step_name = 'delegate_to_subagent'
            """
            result_payload = json.dumps({"output": result_output}) if result_output else None
            session.execute(upd_step, (saga_status, result_payload, saga_id))

            # Sequence
            seq_sql = """
                INSERT INTO aggregate_sequences (aggregate_type, aggregate_id, next_sequence)
                VALUES ('saga', %s, 1)
                ON DUPLICATE KEY UPDATE next_sequence = next_sequence + 1
            """
            session.execute(seq_sql, (saga_id,))
            
            get_seq_sql = "SELECT next_sequence FROM aggregate_sequences WHERE aggregate_type = 'saga' AND aggregate_id = %s"
            session.execute(get_seq_sql, (saga_id,))
            seq_row = session.fetchone()
            seq_num = seq_row['next_sequence'] if seq_row else 1

            # Dispatch saga_completed event
            outbox_sql = """
                INSERT INTO event_outbox (aggregate_type, aggregate_id, sequence_number, event_type, payload_json)
                VALUES ('saga', %s, %s, 'saga_finished', %s)
            """
            session.execute(outbox_sql, (saga_id, seq_num, json.dumps({"saga_status": saga_status, "sub_run_id": sub_run_id})))

            if owns_tx:
                session.commit()

            duration_ms = int((time.time() - start_time) * 1000)
            return ServiceResult.ok(True, duration_ms=duration_ms)
        except Exception as e:
            if 'owns_tx' in locals() and owns_tx and 'session' in locals():
                session.rollback()
            logger.error(f"[AgentCoordinator Error] handle_subagent_result failed: {str(e)}")
            return ServiceResult.fail(ForgeError(code="AGENT_COORDINATOR_HANDLE_FAILED", message=str(e)))
        finally:
            if 'owns_tx' in locals() and owns_tx and 'session' in locals():
                session.close()

    def process_subagent_event(self, event_id: int, saga_id: int, sub_run_id: int, status: str, result_output: Optional[str] = None) -> ServiceResult:
        """
        EventBus subscriber method to handle subagent completion event.
        Checks processed_events to ensure idempotency.
        """
        start_time = time.time()
        session = self.container.storage_provider.get_session()
        try:
            session.begin()

            # Check processed_events
            chk_sql = "SELECT 1 FROM processed_events WHERE subscriber_name = 'agent_coordinator' AND event_id = %s"
            session.execute(chk_sql, (event_id,))
            if session.fetchone():
                session.rollback()
                return ServiceResult.ok("Already processed", duration_ms=int((time.time() - start_time) * 1000))

            # Mark processed
            ins_sql = "INSERT INTO processed_events (subscriber_name, event_id) VALUES ('agent_coordinator', %s)"
            session.execute(ins_sql, (event_id,))

            # Delegate to handle_subagent_result
            res = self.handle_subagent_result(saga_id, sub_run_id, status, result_output, conn=session)
            if not res.success:
                session.rollback()
                return res

            session.commit()
            duration_ms = int((time.time() - start_time) * 1000)
            return ServiceResult.ok(True, duration_ms=duration_ms)
        except Exception as e:
            session.rollback()
            logger.error(f"[AgentCoordinator Error] process_subagent_event failed: {str(e)}")
            return ServiceResult.fail(ForgeError(code="AGENT_EVENT_PROCESS_FAILED", message=str(e)))
        finally:
            session.close()
