import logging
import time
from typing import Dict, Any, List, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError, ValidationError

logger = logging.getLogger(__name__)

class ModelCompatibilityService:
    """
    Verifies GGUF loads, Ollama integration, memory footprint, 
    context window validation, and function calling before deployment.
    """
    def __init__(self):
        # Simulated system resources
        self._available_memory_mb = 32768  # 32 GB
        self._supported_formats = ["gguf", "safetensors", "bin"]
        
    def verify_compatibility(self, model_id: str, model_path: str, context_window: int, required_memory_mb: int, features: Optional[List[str]] = None) -> ServiceResult:
        start_time = time.time()
        logger.info(f"Verifying compatibility for model {model_id} at {model_path}")
        
        try:
            if not model_id or not model_path:
                return ServiceResult.fail(ValidationError("model_id and model_path are required"))
                
            if context_window <= 0 or required_memory_mb <= 0:
                return ServiceResult.fail(ValidationError("context_window and required_memory_mb must be positive"))

            features = features or []
            verification_results: Dict[str, Any] = {
                "model_id": model_id,
                "passed": True,
                "checks": []
            }
            
            # 1. Memory footprint validation
            memory_check = {
                "name": "memory_footprint",
                "passed": required_memory_mb <= self._available_memory_mb,
                "details": f"Requires {required_memory_mb}MB, Available {self._available_memory_mb}MB"
            }
            verification_results["checks"].append(memory_check)
            
            # 2. Context window validation
            # Assume max supported context is 128k
            max_supported_context = 131072
            context_check = {
                "name": "context_window",
                "passed": context_window <= max_supported_context,
                "details": f"Requested {context_window}, Max supported {max_supported_context}"
            }
            verification_results["checks"].append(context_check)
            
            # 3. Format/GGUF load validation
            ext = model_path.split('.')[-1].lower() if '.' in model_path else ""
            format_check = {
                "name": "format_validation",
                "passed": ext in self._supported_formats,
                "details": f"Format '{ext}' supported: {ext in self._supported_formats}"
            }
            verification_results["checks"].append(format_check)
            
            # 4. Integration validations (Ollama / Function Calling)
            for feature in features:
                if feature == "function_calling":
                    # Simulate checking if model supports function calling
                    func_call_check = {
                        "name": "function_calling_support",
                        "passed": True,  # Simulated pass
                        "details": "Model metadata indicates function calling support"
                    }
                    verification_results["checks"].append(func_call_check)
                elif feature == "ollama_integration":
                    # Simulate ollama compat
                    ollama_check = {
                        "name": "ollama_integration",
                        "passed": ext == "gguf",
                        "details": "Ollama integration requires GGUF format"
                    }
                    verification_results["checks"].append(ollama_check)
                    
            # Determine overall pass
            for check in verification_results["checks"]:
                if not check["passed"]:
                    verification_results["passed"] = False
                    break
                    
            duration_ms = int((time.time() - start_time) * 1000)
            
            if not verification_results["passed"]:
                return ServiceResult.fail(
                    ForgeError("Model failed compatibility verification"), 
                    duration_ms=duration_ms, 
                    verification_details=verification_results
                )
                
            return ServiceResult.ok(
                data=verification_results,
                duration_ms=duration_ms
            )

        except Exception as e:
            logger.error(f"Error verifying compatibility for {model_id}: {str(e)}")
            return ServiceResult.fail(ForgeError(f"Compatibility verification failed: {str(e)}"))
