# ForgePrompt Phase 7 — BulkheadService
import logging
import threading
import concurrent.futures
from typing import Any, Callable, Dict, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class BulkheadService:
    def __init__(self, container: Any):
        self.container = container
        self._pools: Dict[str, concurrent.futures.ThreadPoolExecutor] = {}
        self._lock = threading.Lock()

    def register_pool(self, pool_name: str, max_workers: int) -> ServiceResult[bool]:
        """Registers a named thread pool for isolation."""
        try:
            with self._lock:
                if pool_name in self._pools:
                    return ServiceResult.fail(
                        error_code="POOL_ALREADY_EXISTS",
                        error_message=f"Pool {pool_name} already registered."
                    )
                self._pools[pool_name] = concurrent.futures.ThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix=f"bulkhead_{pool_name}"
                )
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"[BulkheadService Error] Failed to register pool {pool_name}: {e}", exc_info=True)
            return ServiceResult.fail(
                error_code="BULKHEAD_REGISTRATION_ERROR",
                error_message=str(e)
            )

    def submit_task(self, pool_name: str, func: Callable, *args, **kwargs) -> ServiceResult[concurrent.futures.Future]:
        """Submits a task to the named thread pool."""
        try:
            with self._lock:
                pool = self._pools.get(pool_name)
            
            if not pool:
                return ServiceResult.fail(
                    error_code="POOL_NOT_FOUND",
                    error_message=f"Pool {pool_name} not found."
                )
            
            future = pool.submit(func, *args, **kwargs)
            return ServiceResult.success(future)
        except concurrent.futures.CancelledError:
            return ServiceResult.fail(
                error_code="TASK_CANCELLED",
                error_message="Task was cancelled before execution."
            )
        except Exception as e:
            logger.error(f"[BulkheadService Error] Failed to submit task to {pool_name}: {e}", exc_info=True)
            return ServiceResult.fail(
                error_code="BULKHEAD_SUBMISSION_ERROR",
                error_message=str(e)
            )

    def shutdown_pool(self, pool_name: str, wait: bool = True) -> ServiceResult[bool]:
        """Shuts down a specific named thread pool."""
        try:
            with self._lock:
                pool = self._pools.pop(pool_name, None)
            
            if not pool:
                return ServiceResult.fail(
                    error_code="POOL_NOT_FOUND",
                    error_message=f"Pool {pool_name} not found."
                )
            
            pool.shutdown(wait=wait)
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"[BulkheadService Error] Failed to shutdown pool {pool_name}: {e}", exc_info=True)
            return ServiceResult.fail(
                error_code="BULKHEAD_SHUTDOWN_ERROR",
                error_message=str(e)
            )

    def get_pool_metrics(self, pool_name: str) -> ServiceResult[Dict[str, Any]]:
        """Returns metrics about a specific pool."""
        try:
            with self._lock:
                pool = self._pools.get(pool_name)
            
            if not pool:
                return ServiceResult.fail(
                    error_code="POOL_NOT_FOUND",
                    error_message=f"Pool {pool_name} not found."
                )
            
            # Using internal attribute _work_queue to estimate queue size
            queue_size = pool._work_queue.qsize() if hasattr(pool, "_work_queue") else 0
            
            return ServiceResult.success({
                "pool_name": pool_name,
                "max_workers": pool._max_workers if hasattr(pool, "_max_workers") else None,
                "queue_size": queue_size
            })
        except Exception as e:
            logger.error(f"[BulkheadService Error] Failed to get metrics for {pool_name}: {e}", exc_info=True)
            return ServiceResult.fail(
                error_code="BULKHEAD_METRICS_ERROR",
                error_message=str(e)
            )
