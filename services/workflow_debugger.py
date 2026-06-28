# ForgePrompt Phase 7 — WorkflowDebugger
import logging
import json
import uuid
from typing import Any, Dict, Optional, List

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class WorkflowDebugger:
    def __init__(self, container: Any):
        self.container = container
        self._init_db()

    def _init_db(self):
        try:
            with self.container.storage_provider.get_session() as session:
                session.execute("""
                    CREATE TABLE IF NOT EXISTS debug_sessions (
                        session_id VARCHAR(100) NOT NULL,
                        organization_id VARCHAR(100) NOT NULL,
                        workflow_execution_id VARCHAR(100) NOT NULL,
                        breakpoint_id VARCHAR(255),
                        status VARCHAR(50) NOT NULL,
                        state_snapshot LONGTEXT,
                        variables LONGTEXT,
                        patched_variables LONGTEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        PRIMARY KEY (session_id, organization_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """)
                
                session.execute("""
                    SELECT COUNT(1) AS cnt 
                    FROM INFORMATION_SCHEMA.STATISTICS 
                    WHERE TABLE_SCHEMA = DATABASE() 
                      AND TABLE_NAME = 'debug_sessions' 
                      AND INDEX_NAME = 'idx_org_wf_exec';
                """)
                result = session.fetchone()
                if result and result.get('cnt', 0) == 0:
                    session.execute("CREATE INDEX idx_org_wf_exec ON debug_sessions (organization_id, workflow_execution_id);")

                session.commit()
        except Exception as e:
            logger.error(f"[WorkflowDebugger Error] DB initialization failed: {e}", exc_info=True)

    def create_session(self, organization_id: str, workflow_execution_id: str, conn=None) -> ServiceResult[str]:
        try:
            session_id = str(uuid.uuid4())
            sql = """
                INSERT INTO debug_sessions 
                (session_id, organization_id, workflow_execution_id, status, variables, patched_variables)
                VALUES (%s, %s, %s, 'RUNNING', '{}', '{}')
            """
            params = (session_id, organization_id, workflow_execution_id)
            
            if conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
            else:
                with self.container.storage_provider.get_session() as session:
                    session.execute(sql, params)
                    session.commit()
            return ServiceResult.success(session_id)
        except Exception as e:
            logger.error(f"[WorkflowDebugger Error] Failed to create debug session: {e}", exc_info=True)
            return ServiceResult.fail(
                error_code="DEBUG_SESSION_CREATE_ERROR",
                error_message=str(e)
            )

    def hit_breakpoint(self, organization_id: str, session_id: str, breakpoint_id: str, 
                       state_snapshot: Dict[str, Any], variables: Dict[str, Any], conn=None) -> ServiceResult[bool]:
        """Records that a breakpoint was hit and changes status to PAUSED."""
        try:
            sql = """
                UPDATE debug_sessions 
                SET breakpoint_id = %s, status = 'PAUSED', state_snapshot = %s, variables = %s
                WHERE session_id = %s AND organization_id = %s
            """
            params = (breakpoint_id, json.dumps(state_snapshot), json.dumps(variables), session_id, organization_id)
            
            if conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
            else:
                with self.container.storage_provider.get_session() as session:
                    session.execute(sql, params)
                    session.commit()
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"[WorkflowDebugger Error] Failed to hit breakpoint: {e}", exc_info=True)
            return ServiceResult.fail(
                error_code="DEBUG_BREAKPOINT_ERROR",
                error_message=str(e)
            )

    def patch_variable(self, organization_id: str, session_id: str, variable_name: str, 
                       new_value: Any, conn=None) -> ServiceResult[bool]:
        """Patches a variable's value while the workflow is PAUSED."""
        try:
            fetch_sql = "SELECT status, patched_variables FROM debug_sessions WHERE session_id = %s AND organization_id = %s FOR UPDATE"
            params = (session_id, organization_id)
            update_sql = "UPDATE debug_sessions SET patched_variables = %s WHERE session_id = %s AND organization_id = %s"
            
            def _patch(row):
                if not row or row.get('status') != 'PAUSED':
                    raise ValueError("Session is not PAUSED.")
                patched = json.loads(row.get('patched_variables', '{}') or '{}')
                patched[variable_name] = new_value
                return json.dumps(patched)

            if conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(fetch_sql, params)
                row = cursor.fetchone()
                patched_json = _patch(row)
                cursor.execute(update_sql, (patched_json, session_id, organization_id))
            else:
                with self.container.storage_provider.get_session() as session:
                    session.begin()
                    session.execute(fetch_sql, params)
                    row = session.fetchone()
                    patched_json = _patch(row)
                    session.execute(update_sql, (patched_json, session_id, organization_id))
                    session.commit()
            return ServiceResult.success(True)
        except ValueError as ve:
            return ServiceResult.fail(error_code="DEBUG_INVALID_STATE", error_message=str(ve))
        except Exception as e:
            logger.error(f"[WorkflowDebugger Error] Failed to patch variable: {e}", exc_info=True)
            return ServiceResult.fail(
                error_code="DEBUG_PATCH_ERROR",
                error_message=str(e)
            )

    def resume_execution(self, organization_id: str, session_id: str, conn=None) -> ServiceResult[Dict[str, Any]]:
        """Resumes execution, returns the patched variables."""
        try:
            fetch_sql = "SELECT patched_variables FROM debug_sessions WHERE session_id = %s AND organization_id = %s FOR UPDATE"
            update_sql = "UPDATE debug_sessions SET status = 'RUNNING', breakpoint_id = NULL WHERE session_id = %s AND organization_id = %s"
            params = (session_id, organization_id)
            
            patched = {}
            if conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(fetch_sql, params)
                row = cursor.fetchone()
                if row:
                    patched = json.loads(row.get('patched_variables', '{}') or '{}')
                cursor.execute(update_sql, params)
            else:
                with self.container.storage_provider.get_session() as session:
                    session.begin()
                    session.execute(fetch_sql, params)
                    row = session.fetchone()
                    if row:
                        patched = json.loads(row.get('patched_variables', '{}') or '{}')
                    session.execute(update_sql, params)
                    session.commit()
            return ServiceResult.success(patched)
        except Exception as e:
            logger.error(f"[WorkflowDebugger Error] Failed to resume execution: {e}", exc_info=True)
            return ServiceResult.fail(
                error_code="DEBUG_RESUME_ERROR",
                error_message=str(e)
            )

    def get_session_state(self, organization_id: str, session_id: str, conn=None) -> ServiceResult[Dict[str, Any]]:
        """Gets the current state of a debug session."""
        try:
            sql = """
                SELECT status, breakpoint_id, state_snapshot, variables, patched_variables
                FROM debug_sessions
                WHERE session_id = %s AND organization_id = %s
            """
            params = (session_id, organization_id)
            
            row = None
            if conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(sql, params)
                row = cursor.fetchone()
            else:
                with self.container.storage_provider.get_session() as db_session:
                    db_session.execute(sql, params)
                    row = db_session.fetchone()
                    
            if not row:
                return ServiceResult.fail(error_code="SESSION_NOT_FOUND", error_message="Session not found.")
                
            return ServiceResult.success({
                "status": row.get("status"),
                "breakpoint_id": row.get("breakpoint_id"),
                "state_snapshot": json.loads(row.get("state_snapshot", "{}") or "{}"),
                "variables": json.loads(row.get("variables", "{}") or "{}"),
                "patched_variables": json.loads(row.get("patched_variables", "{}") or "{}")
            })
        except Exception as e:
            logger.error(f"[WorkflowDebugger Error] Failed to get session state: {e}", exc_info=True)
            return ServiceResult.fail(
                error_code="DEBUG_STATE_ERROR",
                error_message=str(e)
            )
