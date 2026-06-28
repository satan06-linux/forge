# ForgePrompt Phase 7 - WorkflowHistoryService
import json
from typing import Dict, Any, List

from services.service_result import ServiceResult
from services.errors import ForgeError
from services.storage_provider import StorageProvider

class WorkflowHistoryService:
    def __init__(self, container):
        self.storage: StorageProvider = container.storage_provider
        self._ensure_tables()

    def _ensure_tables(self):
        with self.storage.get_session() as session:
            # Table for aggregate sequence numbers
            session.execute("""
                CREATE TABLE IF NOT EXISTS aggregate_sequences (
                    organization_id VARCHAR(64) NOT NULL,
                    aggregate_type VARCHAR(64) NOT NULL,
                    aggregate_id VARCHAR(128) NOT NULL,
                    current_sequence BIGINT NOT NULL DEFAULT 0,
                    PRIMARY KEY (organization_id, aggregate_type, aggregate_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """)
            
            # Table for workflow history
            session.execute("""
                CREATE TABLE IF NOT EXISTS workflow_history (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    organization_id VARCHAR(64) NOT NULL,
                    workflow_run_id VARCHAR(128) NOT NULL,
                    sequence_number BIGINT NOT NULL,
                    event_type VARCHAR(128) NOT NULL,
                    event_data LONGTEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_org_run_seq (organization_id, workflow_run_id, sequence_number),
                    UNIQUE KEY uk_org_run_seq (organization_id, workflow_run_id, sequence_number)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """)
            session.commit()

    def _get_next_sequence(self, session, organization_id: str, aggregate_id: str) -> int:
        """
        Sequence numbers from aggregate_sequences — never SELECT MAX().
        """
        # Try to increment existing sequence
        sql_update = """
            UPDATE aggregate_sequences 
            SET current_sequence = current_sequence + 1 
            WHERE organization_id = %s AND aggregate_type = 'WORKFLOW_RUN' AND aggregate_id = %s
        """
        session.execute(sql_update, (organization_id, aggregate_id))
        
        if session.rowcount() == 0:
            # If it doesn't exist, insert it with sequence 1
            # We use INSERT IGNORE to handle concurrent inserts (though we are in a transaction,
            # another transaction might have inserted it right after our update failed. 
            # In that case, we should retry the update).
            sql_insert = """
                INSERT IGNORE INTO aggregate_sequences (organization_id, aggregate_type, aggregate_id, current_sequence)
                VALUES (%s, 'WORKFLOW_RUN', %s, 1)
            """
            session.execute(sql_insert, (organization_id, aggregate_id))
            if session.rowcount() > 0:
                return 1
            else:
                # Concurrent insert happened, update it now
                session.execute(sql_update, (organization_id, aggregate_id))
                
        # Now fetch the sequence number we just created/updated
        sql_select = """
            SELECT current_sequence FROM aggregate_sequences
            WHERE organization_id = %s AND aggregate_type = 'WORKFLOW_RUN' AND aggregate_id = %s
        """
        session.execute(sql_select, (organization_id, aggregate_id))
        row = session.fetchone()
        if not row:
            raise ForgeError("Failed to generate sequence number", error_code="SEQUENCE_GENERATION_FAILED")
        return row["current_sequence"]

    def append_event(
        self,
        organization_id: str,
        workflow_run_id: str,
        event_type: str,
        event_data: Dict[str, Any],
        conn=None
    ) -> ServiceResult:
        """
        Append-only history to workflow_history.
        """
        try:
            if conn:
                return self._do_append(organization_id, workflow_run_id, event_type, event_data, conn)
            else:
                with self.storage.get_session() as session:
                    session.begin()
                    result = self._do_append(organization_id, workflow_run_id, event_type, event_data, session)
                    if result.success:
                        session.commit()
                    else:
                        session.rollback()
                    return result
        except Exception as e:
            return ServiceResult.fail(ForgeError(f"Failed to append workflow history event: {str(e)}", error_code="WORKFLOW_HISTORY_APPEND_FAILED"))

    def _do_append(
        self,
        organization_id: str,
        workflow_run_id: str,
        event_type: str,
        event_data: Dict[str, Any],
        session
    ) -> ServiceResult:
        seq_num = self._get_next_sequence(session, organization_id, workflow_run_id)
        
        sql = """
            INSERT INTO workflow_history (organization_id, workflow_run_id, sequence_number, event_type, event_data)
            VALUES (%s, %s, %s, %s, %s)
        """
        session.execute(sql, (organization_id, workflow_run_id, seq_num, json.dumps(event_data)))
        
        return ServiceResult.ok(data={"sequence_number": seq_num})

    def get_history(
        self,
        organization_id: str,
        workflow_run_id: str,
        min_sequence: int = 0
    ) -> ServiceResult:
        """
        Retrieve workflow history for a run.
        """
        try:
            with self.storage.get_session() as session:
                sql = """
                    SELECT sequence_number, event_type, event_data, created_at
                    FROM workflow_history
                    WHERE organization_id = %s AND workflow_run_id = %s AND sequence_number > %s
                    ORDER BY sequence_number ASC
                """
                session.execute(sql, (organization_id, workflow_run_id, min_sequence))
                rows = session.fetchall()
                
                events = []
                for row in rows:
                    row_data = dict(row)
                    row_data["event_data"] = json.loads(row_data["event_data"])
                    events.append(row_data)
                    
                return ServiceResult.ok(data={"events": events})
        except Exception as e:
            return ServiceResult.fail(ForgeError(f"Failed to get workflow history: {str(e)}", error_code="WORKFLOW_HISTORY_FETCH_FAILED"))
