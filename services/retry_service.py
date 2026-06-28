# ForgePrompt Phase 7 — RetryService
import time
import random
from typing import Callable, Any

from services.service_result import ServiceResult
from services.errors import ForgeError, WorkflowExecutionError

class RetryService:
    """
    Exponential backoff with full jitter, delegating to circuit breaker.
    """
    def __init__(self, container):
        # Allow lazy load of circuit_breaker_service if not registered yet
        self.container = container
        
    @property
    def circuit_breaker(self):
        cb = self.container.get('circuit_breaker_service')
        if not cb:
            raise WorkflowExecutionError("[RetryService Error] circuit_breaker_service not found in container")
        return cb
        
    def execute_with_retry(
        self, 
        provider_id: str, 
        func: Callable[[], ServiceResult], 
        max_attempts: int = 3, 
        base_ms: float = 100.0, 
        max_ms: float = 5000.0
    ) -> ServiceResult:
        """
        Executes a callable that returns a ServiceResult.
        Retries on failure if result.retryable is True.
        Applies exponential backoff with full jitter.
        Checks circuit breaker before attempting.
        """
        attempt = 0
        last_error_result = None
        
        while attempt < max_attempts:
            attempt += 1
            
            # Check circuit breaker
            cb_check = self.circuit_breaker.check_circuit(provider_id)
            if not cb_check.success:
                return cb_check
                
            try:
                result = func()
            except Exception as e:
                # Catch any unhandled exceptions and wrap them as a failed ServiceResult
                result = ServiceResult.fail(ForgeError(f"[RetryService Error] Unhandled exception in func: {str(e)}", retryable=True))
            
            if result.success:
                # Record success
                self.circuit_breaker.record_success(provider_id)
                return result
                
            # Record failure
            self.circuit_breaker.record_failure(provider_id)
            last_error_result = result
            
            if not result.retryable or attempt >= max_attempts:
                break
                
            # Calculate sleep time: exponential backoff + full jitter
            temp = min(max_ms, base_ms * (2 ** (attempt - 1)))
            sleep_ms = random.uniform(0, temp)
            time.sleep(sleep_ms / 1000.0)
            
        return last_error_result or ServiceResult.fail(WorkflowExecutionError("Max attempts reached with no result"))
