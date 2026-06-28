import logging
import re
from typing import Any, Dict

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class AISecurityService:
    def __init__(self, container: Any):
        self.container = container
        # Simple patterns for demonstration. In production, this would use ML classifiers or robust regexes.
        self.injection_patterns = [
            re.compile(r"(?i)ignore\s+all\s+previous\s+instructions"),
            re.compile(r"(?i)you\s+are\s+now\s+in\s+developer\s+mode"),
            re.compile(r"(?i)bypass\s+security"),
            re.compile(r"(?i)system\s+prompt")
        ]
        self.pii_patterns = [
            # Basic SSN pattern
            re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
            # Basic Email pattern
            re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b")
        ]
        self.banned_words = ["harmful_instruction", "malicious_code"]

    def scan_input(self, prompt: str) -> ServiceResult:
        try:
            # 1. Prompt Injection Detection
            for pattern in self.injection_patterns:
                if pattern.search(prompt):
                    logger.warning("Prompt injection detected in input.")
                    return ServiceResult.fail(ForgeError(code="PROMPT_INJECTION_DETECTED", message="Potential prompt injection detected."))
            
            # 2. PII Scanning
            detected_pii = []
            for pattern in self.pii_patterns:
                matches = pattern.findall(prompt)
                if matches:
                    detected_pii.extend(matches)
            
            if detected_pii:
                logger.warning("PII detected in input.")
                # We could redact here, but for strictness we fail it or warn
                return ServiceResult.fail(ForgeError(code="PII_DETECTED", message="PII detected in input. Please remove sensitive information."))

            return ServiceResult.success({"status": "safe", "action": "allow"})
        except Exception as e:
            logger.error(f"Failed to scan input: {str(e)}")
            return ServiceResult.fail(ForgeError(code="SECURITY_SCAN_FAILED", message=f"Input scanning failed: {str(e)}"))

    def scan_output(self, output: str) -> ServiceResult:
        try:
            # 1. Guardrails check
            lower_output = output.lower()
            for word in self.banned_words:
                if word in lower_output:
                    logger.warning("Banned content detected in output.")
                    return ServiceResult.fail(ForgeError(code="OUTPUT_GUARDRAIL_VIOLATION", message="Model output violated safety guardrails."))
            
            # 2. PII Leakage Check
            for pattern in self.pii_patterns:
                if pattern.search(output):
                    logger.warning("PII leakage detected in output.")
                    return ServiceResult.fail(ForgeError(code="OUTPUT_PII_LEAKAGE", message="Model output contained potential PII."))

            return ServiceResult.success({"status": "safe", "action": "allow"})
        except Exception as e:
            logger.error(f"Failed to scan output: {str(e)}")
            return ServiceResult.fail(ForgeError(code="SECURITY_SCAN_FAILED", message=f"Output scanning failed: {str(e)}"))
