# ForgePrompt Phase 7 — DeadLetterService
from typing import Optional, Dict, Any, List
from services.service_result import ServiceResult
from services.errors import StorageError, NotFoundError, ForgeError

class DeadLetterService:
    """
    Manages the lifecycle of jobs that exceed retry policies or fail determinism validations.
    """
    def __init__(self, container):
        self.container = container
        self.storage = container.storage

    def send_to_dead_letter(self, job_id: int, reason: str, tag: Optional[str] = None, conn: Optional[Any] = None) -> ServiceResult:
        try:
            sql = """
                UPDATE worker_queue 
                SET status = 'dead_letter', 
                    dead_letter_reason = %s, 
                    dead_letter_at = CURRENT_TIMESTAMP,
                    dead_letter_tag = %s
                WHERE id = %s
            """
            if conn:
                conn.cursor.execute(sql, (reason, tag, job_id))
            else:
                with self.storage.transaction() as session:
                    session.cursor.execute(sql, (reason, tag, job_id))
            return ServiceResult.ok()
        except Exception as e:
            return ServiceResult.fail(StorageError(f"[DeadLetterService Error] Failed to send job {job_id} to dead letter: {str(e)}"))

    def _record_action(self, job_id: int, action: str, actor_id: Optional[int], note: Optional[str], conn: Any) -> None:
        sql = """
            INSERT INTO dead_letter_actions (job_id, action, actor_id, note)
            VALUES (%s, %s, %s, %s)
        """
        conn.cursor.execute(sql, (job_id, action, actor_id, note))

    def replay_job(self, job_id: int, actor_id: Optional[int] = None, note: Optional[str] = None, conn: Optional[Any] = None) -> ServiceResult:
        try:
            def _replay(session):
                # Reset job to queued
                upd_sql = """
                    UPDATE worker_queue 
                    SET status = 'queued',
                        retries = 0,
                        dead_letter_reason = NULL,
                        dead_letter_at = NULL,
                        dead_letter_tag = NULL
                    WHERE id = %s AND status = 'dead_letter'
                """
                session.cursor.execute(upd_sql, (job_id,))
                if session.cursor.rowcount == 0:
                    raise NotFoundError(f"Job {job_id} is not in dead letter state or does not exist")
                self._record_action(job_id, 'replayed', actor_id, note, session)

            if conn:
                _replay(conn)
            else:
                with self.storage.transaction() as session:
                    _replay(session)
            return ServiceResult.ok()
        except ForgeError as fe:
            return ServiceResult.fail(fe)
        except Exception as e:
            return ServiceResult.fail(StorageError(f"[DeadLetterService Error] Failed to replay job {job_id}: {str(e)}"))

    def archive_job(self, job_id: int, actor_id: Optional[int] = None, note: Optional[str] = None, conn: Optional[Any] = None) -> ServiceResult:
        try:
            def _archive(session):
                upd_sql = "UPDATE worker_queue SET dead_letter_archived = 1 WHERE id = %s AND status = 'dead_letter'"
                session.cursor.execute(upd_sql, (job_id,))
                if session.cursor.rowcount == 0:
                    raise NotFoundError(f"Job {job_id} is not in dead letter state or does not exist")
                self._record_action(job_id, 'archived', actor_id, note, session)

            if conn:
                _archive(conn)
            else:
                with self.storage.transaction() as session:
                    _archive(session)
            return ServiceResult.ok()
        except ForgeError as fe:
            return ServiceResult.fail(fe)
        except Exception as e:
            return ServiceResult.fail(StorageError(f"[DeadLetterService Error] Failed to archive job {job_id}: {str(e)}"))

    def export_job(self, job_id: int, actor_id: Optional[int] = None, note: Optional[str] = None, conn: Optional[Any] = None) -> ServiceResult:
        try:
            def _export(session):
                upd_sql = "UPDATE worker_queue SET dead_letter_exported = 1 WHERE id = %s AND status = 'dead_letter'"
                session.cursor.execute(upd_sql, (job_id,))
                if session.cursor.rowcount == 0:
                    raise NotFoundError(f"Job {job_id} is not in dead letter state or does not exist")
                self._record_action(job_id, 'exported', actor_id, note, session)
                # In a real system we would extract the payload and push to S3/Cloud Storage here.

            if conn:
                _export(conn)
            else:
                with self.storage.transaction() as session:
                    _export(session)
            return ServiceResult.ok()
        except ForgeError as fe:
            return ServiceResult.fail(fe)
        except Exception as e:
            return ServiceResult.fail(StorageError(f"[DeadLetterService Error] Failed to export job {job_id}: {str(e)}"))

    def purge_job(self, job_id: int, actor_id: Optional[int] = None, note: Optional[str] = None, conn: Optional[Any] = None) -> ServiceResult:
        try:
            def _purge(session):
                # Delete from worker_queue
                del_sql = "DELETE FROM worker_queue WHERE id = %s AND status = 'dead_letter'"
                session.cursor.execute(del_sql, (job_id,))
                if session.cursor.rowcount == 0:
                    raise NotFoundError(f"Job {job_id} is not in dead letter state or does not exist")
                # dead_letter_actions has job_id index, we keep action logs even if job is purged, 
                # but might need to clear foreign keys if constrained. Let's assume no hard FK from actions to queue or cascading delete.
                self._record_action(job_id, 'purged', actor_id, note, session)

            if conn:
                _purge(conn)
            else:
                with self.storage.transaction() as session:
                    _purge(session)
            return ServiceResult.ok()
        except ForgeError as fe:
            return ServiceResult.fail(fe)
        except Exception as e:
            return ServiceResult.fail(StorageError(f"[DeadLetterService Error] Failed to purge job {job_id}: {str(e)}"))

    def tag_job(self, job_id: int, tag: str, actor_id: Optional[int] = None, note: Optional[str] = None, conn: Optional[Any] = None) -> ServiceResult:
        try:
            def _tag(session):
                upd_sql = "UPDATE worker_queue SET dead_letter_tag = %s WHERE id = %s AND status = 'dead_letter'"
                session.cursor.execute(upd_sql, (tag, job_id))
                if session.cursor.rowcount == 0:
                    raise NotFoundError(f"Job {job_id} is not in dead letter state or does not exist")
                self._record_action(job_id, 'tagged', actor_id, note, session)

            if conn:
                _tag(conn)
            else:
                with self.storage.transaction() as session:
                    _tag(session)
            return ServiceResult.ok()
        except ForgeError as fe:
            return ServiceResult.fail(fe)
        except Exception as e:
            return ServiceResult.fail(StorageError(f"[DeadLetterService Error] Failed to tag job {job_id}: {str(e)}"))
