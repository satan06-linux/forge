import logging
from typing import Dict, Any, Optional
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class LocalProvider:
    """Provider for interacting with the Local AI Engine."""
    
    def __init__(self, container):
        self.container = container
        self.engine = container.get("local_ai_engine")
        
    def generate_text(self, prompt: str, model: str = "llama3", max_tokens: int = 1024, temperature: float = 0.7) -> ServiceResult:
        try:
            result = self.engine.generate(
                model_name=model, 
                prompt=prompt, 
                options={
                    "num_predict": max_tokens,
                    "temperature": temperature
                }
            )
            if not result.is_success:
                return result
                
            data = result.data
            response_text = data.get("response", "")
            return ServiceResult.success({
                "text": response_text,
                "usage": {
                    "prompt_tokens": data.get("prompt_eval_count", 0),
                    "completion_tokens": data.get("eval_count", 0)
                },
                "model": model
            })
        except Exception as e:
            logger.error(f"LocalProvider generation failed: {e}")
            return ServiceResult.fail(ForgeError(f"LocalProvider error: {e}"))
            
    def get_models(self) -> ServiceResult:
        try:
            # Query the engine's registry for available models
            result = self.engine.get_all_models()
            if result.is_success:
                return result
            return ServiceResult.fail(ForgeError("Failed to retrieve models from local engine."))
        except Exception as e:
            logger.error(f"LocalProvider get_models failed: {e}")
            return ServiceResult.fail(ForgeError(f"LocalProvider error: {e}"))
