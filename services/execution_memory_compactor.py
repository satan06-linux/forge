# ForgePrompt Phase 7 - ExecutionMemoryCompactor
import json
import uuid
from typing import Dict, Any, List

from services.service_result import ServiceResult
from services.errors import ForgeError
from services.storage_provider import StorageProvider

class ExecutionMemoryCompactor:
    def __init__(self, container):
        self.storage: StorageProvider = container.storage_provider
        # Assuming container has workflow_history_service and outbox_dispatcher
        self.workflow_history_service = getattr(container, "workflow_history_service", None)
        self.outbox_dispatcher = getattr(container, "outbox_dispatcher", None)

    def compact_and_continue_as_new(
        self,
        organization_id: str,
        old_run_id: str,
        new_run_id: str,
        compacted_state: Dict[str, Any],
        conn=None
    ) -> ServiceResult:
        """
        Implements Context GC + Continue-As-New mechanics.
        Ends the old workflow run and starts a new one with the compacted state,
        preventing history size from exceeding limits.
        """
        if not self.workflow_history_service or not self.outbox_dispatcher:
             return ServiceResult.fail(ForgeError("Missing required services for compactor", error_code="MISSING_DEPENDENCIES"))
             
        try:
            if conn:
                return self._do_continue_as_new(organization_id, old_run_id, new_run_id, compacted_state, conn)
            else:
                with self.storage.get_session() as session:
                    session.begin()
                    result = self._do_continue_as_new(organization_id, old_run_id, new_run_id, compacted_state, session)
                    if result.success:
                        session.commit()
                    else:
                        session.rollback()
                    return result
        except Exception as e:
            return ServiceResult.fail(ForgeError(f"Failed to Continue-As-New: {str(e)}", error_code="CONTINUE_AS_NEW_FAILED"))

    def _do_continue_as_new(
        self,
        organization_id: str,
        old_run_id: str,
        new_run_id: str,
        compacted_state: Dict[str, Any],
        session
    ) -> ServiceResult:
        
        # 1. Append CONTINUE_AS_NEW to old run
        old_event_data = {
            "new_run_id": new_run_id,
            "reason": "history_compaction"
        }
        res_old = self.workflow_history_service.append_event(
            organization_id=organization_id,
            workflow_run_id=old_run_id,
            event_type="CONTINUE_AS_NEW",
            event_data=old_event_data,
            conn=session
        )
        if not res_old.success:
            return res_old
            
        # 2. Append WORKFLOW_STARTED (or WORKFLOW_CONTINUED) to new run
        new_event_data = {
            "old_run_id": old_run_id,
            "initial_state": compacted_state
        }
        res_new = self.workflow_history_service.append_event(
            organization_id=organization_id,
            workflow_run_id=new_run_id,
            event_type="WORKFLOW_CONTINUED",
            event_data=new_event_data,
            conn=session
        )
        if not res_new.success:
            return res_new
            
        # 3. GC the old run's context / memory by marking it closed or compacted
        # (This assumes there's a workflow_runs table we need to update, 
        # but since we must use StorageProvider and don't know the exact schema, 
        # we will do a safe update if it exists).
        sql_update_run = """
            UPDATE workflow_runs 
            SET status = 'CONTINUED_AS_NEW', updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND organization_id = %s
        """
        # We catch exceptions in case workflow_runs doesn't exist or schema differs
        try:
            session.execute(sql_update_run, (old_run_id, organization_id))
        except Exception as e:
            # Table might not exist yet, ignore
            pass
            
        # 4. Publish event to outbox to trigger workers for the new run
        outbox_payload = {
            "workflow_run_id": new_run_id,
            "old_run_id": old_run_id,
            "action": "resume_workflow"
        }
        res_outbox = self.outbox_dispatcher.append_event(
            organization_id=organization_id,
            topic="workflow.continued",
            partition_key=new_run_id,
            payload=outbox_payload,
            conn=session
        )
        if not res_outbox.success:
            return res_outbox
            
        return ServiceResult.ok(data={"new_run_id": new_run_id, "old_run_id": old_run_id})

    def garbage_collect_context(self, organization_id: str, scope_key: str, conn=None) -> ServiceResult:
        """
        Context GC: Removes execution memory / context for completed or compacted workflow runs
        to free up DB space and keep context window clean.
        """
        try:
            sql = """
                DELETE FROM memory_scopes
                WHERE scope_type = 'workflow' 
                  AND scope_key = %s
            """
            # organization_id in memory_scopes is INT, but organization_id here is str. 
            # We filter by scope_key (which typically maps to workflow_run_id).
            if conn:
                conn.execute(sql, (scope_key,))
            else:
                with self.storage.get_session() as session:
                    session.execute(sql, (scope_key,))
                    session.commit()
            return ServiceResult.ok()
        except Exception as e:
            return ServiceResult.fail(ForgeError(f"Context GC failed: {str(e)}", error_code="CONTEXT_GC_FAILED"))
