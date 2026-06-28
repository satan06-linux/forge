# ForgePrompt Phase 7 — ReplaySandboxService
import logging
import json
import uuid
from typing import Any, Dict, Optional, List

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class ReplaySandboxService:
    def __init__(self, container: Any):
        self.container = container
        self._init_db()

    def _init_db(self):
        try:
            with self.container.storage_provider.get_session() as session:
                session.execute("""
                    CREATE TABLE IF NOT EXISTS replay_executions (
                        replay_id VARCHAR(100) NOT NULL,
                        organization_id VARCHAR(100) NOT NULL,
                        original_execution_id VARCHAR(100) NOT NULL,
                        status VARCHAR(50) NOT NULL,
                        comparison_result VARCHAR(50),
                        diff_summary LONGTEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        PRIMARY KEY (replay_id, organization_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """)
                
                session.execute("""
                    SELECT COUNT(1) AS cnt 
                    FROM INFORMATION_SCHEMA.STATISTICS 
                    WHERE TABLE_SCHEMA = DATABASE() 
                      AND TABLE_NAME = 'replay_executions' 
                      AND INDEX_NAME = 'idx_org_orig_exec';
                """)
                result = session.fetchone()
                if result and result.get('cnt', 0) == 0:
                    session.execute("CREATE INDEX idx_org_orig_exec ON replay_executions (organization_id, original_execution_id);")

                session.commit()
        except Exception as e:
            logger.error(f"[ReplaySandboxService Error] DB initialization failed: {e}", exc_info=True)

    def create_replay(self, organization_id: str, original_execution_id: str, conn=None) -> ServiceResult[str]:
        """Creates a new replay execution in pending status."""
        try:
            replay_id = str(uuid.uuid4())
            sql = """
                INSERT INTO replay_executions 
                (replay_id, organization_id, original_execution_id, status)
                VALUES (%s, %s, %s, 'PENDING')
            """
            params = (replay_id, organization_id, original_execution_id)
            
            if conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
            else:
                with self.container.storage_provider.get_session() as session:
                    session.execute(sql, params)
                    session.commit()
            return ServiceResult.success(replay_id)
        except Exception as e:
            logger.error(f"[ReplaySandboxService Error] Failed to create replay: {e}", exc_info=True)
            return ServiceResult.fail(
                error_code="REPLAY_CREATE_ERROR",
                error_message=str(e)
            )

    def update_replay_status(self, organization_id: str, replay_id: str, status: str, conn=None) -> ServiceResult[bool]:
        """Updates the status of a replay execution."""
        try:
            sql = """
                UPDATE replay_executions 
                SET status = %s
                WHERE replay_id = %s AND organization_id = %s
            """
            params = (status, replay_id, organization_id)
            
            if conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
            else:
                with self.container.storage_provider.get_session() as session:
                    session.execute(sql, params)
                    session.commit()
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"[ReplaySandboxService Error] Failed to update replay status: {e}", exc_info=True)
            return ServiceResult.fail(
                error_code="REPLAY_STATUS_UPDATE_ERROR",
                error_message=str(e)
            )

    def record_comparison_result(self, organization_id: str, replay_id: str, 
                                 comparison_result: str, diff_summary: Dict[str, Any], conn=None) -> ServiceResult[bool]:
        """Records the result of the historical state comparison."""
        try:
            sql = """
                UPDATE replay_executions 
                SET comparison_result = %s, diff_summary = %s, status = 'COMPLETED'
                WHERE replay_id = %s AND organization_id = %s
            """
            params = (comparison_result, json.dumps(diff_summary), replay_id, organization_id)
            
            if conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
            else:
                with self.container.storage_provider.get_session() as session:
                    session.execute(sql, params)
                    session.commit()
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"[ReplaySandboxService Error] Failed to record comparison: {e}", exc_info=True)
            return ServiceResult.fail(
                error_code="REPLAY_COMPARISON_ERROR",
                error_message=str(e)
            )

    def get_replay_details(self, organization_id: str, replay_id: str, conn=None) -> ServiceResult[Dict[str, Any]]:
        """Retrieves details of a replay execution."""
        try:
            sql = """
                SELECT original_execution_id, status, comparison_result, diff_summary
                FROM replay_executions
                WHERE replay_id = %s AND organization_id = %s
            """
            params = (replay_id, organization_id)
            
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
                return ServiceResult.fail(error_code="REPLAY_NOT_FOUND", error_message="Replay execution not found.")
                
            return ServiceResult.success({
                "replay_id": replay_id,
                "original_execution_id": row.get("original_execution_id"),
                "status": row.get("status"),
                "comparison_result": row.get("comparison_result"),
                "diff_summary": json.loads(row.get("diff_summary", "{}") or "{}")
            })
        except Exception as e:
            logger.error(f"[ReplaySandboxService Error] Failed to get replay details: {e}", exc_info=True)
            return ServiceResult.fail(
                error_code="REPLAY_DETAILS_ERROR",
                error_message=str(e)
            )
