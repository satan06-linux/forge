# ForgePrompt Phase 7 — MaintenanceCoordinator
import time
import threading
import socket
import random
import logging
from typing import Callable, Dict
from models import get_db_connection

logger = logging.getLogger(__name__)

class MaintenanceCoordinator:
    """
    Unified periodic job manager. Replaces scattered threading.Timer loops.
    Rule 13: All periodic maintenance operations MUST be registered with MaintenanceCoordinator.
    """

    def __init__(self, container=None):
        self.container = container
        self._jobs: Dict[str, dict] = {}
        self._running = False
        self._worker_thread = None
        self._node_id = socket.gethostname() + "-" + str(random.randint(1000, 9999))

    def register_job(self, job_type: str, fn: Callable, interval_sec: int, priority: int, jitter_sec: int = 0):
        """
        Register a background maintenance job.
        Higher priority value = executes earlier if multiple are due.
        """
        self._jobs[job_type] = {
            'fn': fn,
            'interval_sec': interval_sec,
            'priority': priority,
            'jitter_sec': jitter_sec,
            'last_run': 0
        }

    def start(self):
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._worker_thread.start()
        logger.info(f"[MaintenanceCoordinator] Started on node {self._node_id}")

    def stop(self):
        self._running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=5.0)

    def _run_loop(self):
        while self._running:
            now = time.time()
            due_jobs = []
            
            for job_type, spec in self._jobs.items():
                if now - spec['last_run'] >= spec['interval_sec']:
                    due_jobs.append((job_type, spec))
            
            # Sort by priority ascending (1 is highest priority)
            due_jobs.sort(key=lambda x: x[1]['priority'])
            
            for job_type, spec in due_jobs:
                if not self._running:
                    break
                self._execute_job(job_type, spec)
                
            time.sleep(1.0) # Check every second

    def _execute_job(self, job_type: str, spec: dict):
        # 1. Jitter
        if spec['jitter_sec'] > 0:
            time.sleep(random.uniform(0, spec['jitter_sec']))

        if not self._running:
            return

        # 2. Acquire Distributed Lock (skip if already running on another node)
        lock_service = self.container.get('lock_service') if self.container else None
        if not lock_service:
            # If no container/lock service yet, just run it locally
            self._do_run(job_type, spec)
            return

        lock_name = f"maint_job:{job_type}"
        with lock_service.acquire_context(lock_name, timeout_seconds=1):
            # 3. Mark in DB (maintenance_jobs)
            conn = get_db_connection()
            cursor = conn.cursor()
            job_id = None
            try:
                # Check if it was run very recently by another node
                cursor.execute("""
                    SELECT id FROM maintenance_jobs 
                    WHERE job_type = %s 
                    AND status IN ('running', 'completed') 
                    AND created_at > DATE_SUB(NOW(), INTERVAL %s SECOND)
                """, (job_type, spec['interval_sec'] - 1))
                if cursor.fetchone():
                    return # Already handled recently
                    
                cursor.execute("""
                    INSERT INTO maintenance_jobs (job_type, status, lease_owner)
                    VALUES (%s, 'running', %s)
                """, (job_type, self._node_id))
                conn.commit()
                job_id = cursor.lastrowid
            except Exception as e:
                logger.error(f"[MaintenanceCoordinator] DB error starting job {job_type}: {e}")
            finally:
                cursor.close()
                conn.close()

            # 4. Run
            success = False
            err_msg = None
            try:
                spec['fn']()
                success = True
            except Exception as e:
                err_msg = str(e)
                logger.error(f"[MaintenanceCoordinator] Error executing job {job_type}: {e}")
            finally:
                spec['last_run'] = time.time()
                
            # 5. Update DB
            if job_id is not None:
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    status = 'completed' if success else 'failed'
                    cursor.execute("""
                        UPDATE maintenance_jobs 
                        SET status = %s, completed_at = NOW(), error_message = %s
                        WHERE id = %s
                    """, (status, err_msg, job_id))
                    conn.commit()
                except Exception as e:
                    logger.error(f"[MaintenanceCoordinator] DB error finalizing job {job_type}: {e}")
                finally:
                    cursor.close()
                    conn.close()

    def _do_run(self, job_type: str, spec: dict):
        try:
            spec['fn']()
        except Exception as e:
            logger.error(f"[MaintenanceCoordinator] Error executing local job {job_type}: {e}")
        finally:
            spec['last_run'] = time.time()
