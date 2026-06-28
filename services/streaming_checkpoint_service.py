# ForgePrompt Phase 7 — StreamingCheckpointService
import json
import time
from typing import Optional, Dict, Any

from services.service_result import ServiceResult
from services.errors import StorageError

class StreamingCheckpointService:
    def __init__(self, container):
        self.container = container
        self._run_migrations()

    def _run_migrations(self):
        try:
            session = self.container.storage_provider.get_session()
            session.execute("""
                CREATE TABLE IF NOT EXISTS streaming_checkpoints (
                    stream_id VARCHAR(255) PRIMARY KEY,
                    offset INT NOT NULL,
                    resume_token VARCHAR(255) NOT NULL,
                    metadata LONGTEXT,
                    created_at INT NOT NULL,
                    updated_at INT NOT NULL
                ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            session.close()
        except Exception as e:
            print(f"[StreamingCheckpointService Error] Migration failed: {e}")

    def save_checkpoint(
        self, 
        stream_id: str, 
        offset: int, 
        resume_token: str, 
        metadata: Optional[Dict[str, Any]] = None, 
        conn=None
    ) -> ServiceResult:
        start_time = time.time()
        session = conn or self.container.storage_provider.get_session()
        own_transaction = (conn is None)
        
        try:
            if own_transaction:
                session.begin()
                
            meta_json = json.dumps(metadata) if metadata else "{}"
            now = int(time.time())
            
            sql = """
                INSERT INTO streaming_checkpoints 
                    (stream_id, offset, resume_token, metadata, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    offset = VALUES(offset),
                    resume_token = VALUES(resume_token),
                    metadata = VALUES(metadata),
                    updated_at = VALUES(updated_at)
            """
            session.execute(sql, (stream_id, offset, resume_token, meta_json, now, now))
            
            if own_transaction:
                session.commit()
                
            return ServiceResult.ok(duration_ms=int((time.time() - start_time) * 1000))
        except Exception as e:
            if own_transaction:
                session.rollback()
            return ServiceResult.fail(
                StorageError(f"[StreamingCheckpointService Error] Failed to save checkpoint: {e}"),
                duration_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            if own_transaction:
                session.close()

    def get_checkpoint(self, stream_id: str) -> ServiceResult:
        start_time = time.time()
        try:
            session = self.container.storage_provider.get_session()
            sql = "SELECT * FROM streaming_checkpoints WHERE stream_id = %s"
            row = session.execute(sql, (stream_id,)).fetchone()
            session.close()
            
            if row:
                if isinstance(row.get('metadata'), str):
                    try:
                        row['metadata'] = json.loads(row['metadata'])
                    except json.JSONDecodeError:
                        pass
                return ServiceResult.ok(data=row, duration_ms=int((time.time() - start_time) * 1000))
            return ServiceResult.ok(data=None, duration_ms=int((time.time() - start_time) * 1000))
        except Exception as e:
            return ServiceResult.fail(
                StorageError(f"[StreamingCheckpointService Error] Failed to get checkpoint: {e}"),
                duration_ms=int((time.time() - start_time) * 1000)
            )

    def delete_checkpoint(self, stream_id: str, conn=None) -> ServiceResult:
        start_time = time.time()
        session = conn or self.container.storage_provider.get_session()
        own_transaction = (conn is None)
        
        try:
            if own_transaction:
                session.begin()
                
            sql = "DELETE FROM streaming_checkpoints WHERE stream_id = %s"
            session.execute(sql, (stream_id,))
            
            if own_transaction:
                session.commit()
                
            return ServiceResult.ok(duration_ms=int((time.time() - start_time) * 1000))
        except Exception as e:
            if own_transaction:
                session.rollback()
            return ServiceResult.fail(
                StorageError(f"[StreamingCheckpointService Error] Failed to delete checkpoint: {e}"),
                duration_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            if own_transaction:
                session.close()
