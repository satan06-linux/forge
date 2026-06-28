# ForgePrompt Phase 7 - InboxProcessor
from typing import Callable, Any, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError
from services.storage_provider import StorageProvider

class InboxProcessor:
    def __init__(self, container):
        self.storage: StorageProvider = container.storage_provider
        self._ensure_tables()

    def _ensure_tables(self):
        with self.storage.get_session() as session:
            session.execute("""
                CREATE TABLE IF NOT EXISTS processed_events (
                    organization_id VARCHAR(64) NOT NULL,
                    subscriber_id VARCHAR(128) NOT NULL,
                    event_id BIGINT NOT NULL,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (organization_id, subscriber_id, event_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """)
            session.commit()

    def process_idempotently(
        self,
        organization_id: str,
        subscriber_id: str,
        event_id: int,
        processor_func: Callable[[Any], ServiceResult],
        conn=None
    ) -> ServiceResult:
        """
        Executes processor_func only if the event hasn't been processed by this subscriber yet.
        Ensures exactly-once processing by recording the event_id in processed_events.
        MUST be called inside an existing transaction if conn is provided.
        """
        try:
            # If a connection is passed, we assume the caller is managing the transaction
            # and we just do the insert. If the insert fails with duplicate key, we catch it.
            if conn:
                return self._do_process(organization_id, subscriber_id, event_id, processor_func, conn)
            else:
                # We manage the transaction
                with self.storage.get_session() as session:
                    session.begin()
                    result = self._do_process(organization_id, subscriber_id, event_id, processor_func, session)
                    if result.success:
                        session.commit()
                    else:
                        session.rollback()
                    return result

        except Exception as e:
            return ServiceResult.fail(ForgeError(f"Inbox processing failed: {str(e)}", error_code="INBOX_PROCESS_FAILED"))

    def _do_process(
        self,
        organization_id: str,
        subscriber_id: str,
        event_id: int,
        processor_func: Callable[[Any], ServiceResult],
        session
    ) -> ServiceResult:
        # Check if already processed
        # We can try to insert and catch duplicate key, but for database portability 
        # and to avoid rolling back the whole transaction in Postgres (though we are on MySQL),
        # a SELECT FOR UPDATE or simple SELECT first is safer.
        # However, a simple INSERT IGNORE or INSERT ... ON DUPLICATE KEY UPDATE is atomic.
        
        sql_check = """
            SELECT 1 FROM processed_events
            WHERE organization_id = %s AND subscriber_id = %s AND event_id = %s
            FOR UPDATE
        """
        session.execute(sql_check, (organization_id, subscriber_id, event_id))
        if session.fetchone():
            # Already processed
            return ServiceResult.ok(data={"skipped": True, "reason": "already_processed"})
            
        # Insert record
        sql_insert = """
            INSERT INTO processed_events (organization_id, subscriber_id, event_id)
            VALUES (%s, %s, %s)
        """
        session.execute(sql_insert, (organization_id, subscriber_id, event_id))
        
        # Execute business logic
        result = processor_func(session)
        if not result.success:
            return result
            
        # Ensure we return the data from processor_func, but indicate it was processed
        return ServiceResult.ok(data={"skipped": False, "result_data": result.data})
