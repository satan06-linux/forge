# ForgePrompt Phase 7 — ForgeError Hierarchy
from __future__ import annotations
from typing import Optional

class ForgeError(Exception):
    default_error_code: str = "FORGE_ERROR"
    default_retryable: bool = False

    def __init__(self, message: str = "", *, error_code=None, retryable=None, metadata=None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code if error_code is not None else self.default_error_code
        self.retryable = retryable if retryable is not None else self.default_retryable
        self.metadata = metadata if metadata is not None else {}

    def with_meta(self, **kwargs):
        self.metadata.update(kwargs)
        return self

    def to_dict(self):
        return {"error_code": self.error_code, "message": self.message, "retryable": self.retryable, "metadata": self.metadata}

    def __repr__(self):
        return f"{self.__class__.__name__}(error_code={self.error_code!r}, message={self.message!r}, retryable={self.retryable!r})"

    def __str__(self):
        return f"[{self.error_code}] {self.message}"

class ValidationError(ForgeError):
    default_error_code = "VALIDATION_ERROR"
    default_retryable = False
    def __init__(self, message="Validation failed", **kwargs): super().__init__(message, **kwargs)

class NotFoundError(ForgeError):
    default_error_code = "NOT_FOUND"
    default_retryable = False
    def __init__(self, message="Resource not found", **kwargs): super().__init__(message, **kwargs)

class AuthorizationError(ForgeError):
    default_error_code = "AUTHORIZATION_ERROR"
    default_retryable = False
    def __init__(self, message="Authorization denied", **kwargs): super().__init__(message, **kwargs)

class QuotaExceededError(ForgeError):
    default_error_code = "QUOTA_EXCEEDED"
    default_retryable = True
    def __init__(self, message="Quota exceeded", **kwargs): super().__init__(message, **kwargs)

class RateLimitedError(ForgeError):
    default_error_code = "RATE_LIMITED"
    default_retryable = True
    def __init__(self, message="Rate limit exceeded", **kwargs): super().__init__(message, **kwargs)

class CircuitOpenError(ForgeError):
    default_error_code = "CIRCUIT_OPEN"
    default_retryable = True
    def __init__(self, message="Circuit breaker is open", **kwargs): super().__init__(message, **kwargs)

class LLMProviderError(ForgeError):
    default_error_code = "LLM_PROVIDER_ERROR"
    default_retryable = True
    def __init__(self, message="LLM provider error", **kwargs): super().__init__(message, **kwargs)

class WorkflowExecutionError(ForgeError):
    default_error_code = "WORKFLOW_EXECUTION_ERROR"
    default_retryable = False
    def __init__(self, message="Workflow execution failed", **kwargs): super().__init__(message, **kwargs)

class WorkflowTimeoutError(ForgeError):
    default_error_code = "WORKFLOW_TIMEOUT"
    default_retryable = True
    def __init__(self, message="Workflow execution timed out", **kwargs): super().__init__(message, **kwargs)

class SagaCompensationError(ForgeError):
    default_error_code = "SAGA_COMPENSATION_FAILED"
    default_retryable = False
    def __init__(self, message="Saga compensation failed", **kwargs): super().__init__(message, **kwargs)

class PluginError(ForgeError):
    default_error_code = "PLUGIN_ERROR"
    default_retryable = False
    def __init__(self, message="Plugin error", **kwargs): super().__init__(message, **kwargs)

class StorageError(ForgeError):
    default_error_code = "STORAGE_ERROR"
    default_retryable = True
    def __init__(self, message="Storage operation failed", **kwargs): super().__init__(message, **kwargs)

class SecretError(ForgeError):
    default_error_code = "SECRET_ERROR"
    default_retryable = False
    def __init__(self, message="Secret operation failed", **kwargs): super().__init__(message, **kwargs)

class DeterminismError(ForgeError):
    default_error_code = "DETERMINISM_VIOLATION"
    default_retryable = False
    def __init__(self, message="Determinism violation detected", **kwargs): super().__init__(message, **kwargs)

__all__ = [
    "ForgeError", "ValidationError", "NotFoundError", "AuthorizationError",
    "QuotaExceededError", "RateLimitedError", "CircuitOpenError", "LLMProviderError",
    "WorkflowExecutionError", "WorkflowTimeoutError", "SagaCompensationError",
    "PluginError", "StorageError", "SecretError", "DeterminismError",
]
