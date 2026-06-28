import concurrent.futures
import logging
from typing import Any, Dict, List, Optional
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class ParallelExecutionService:
    """
    Dispatches requests to multiple models concurrently when beneficial,
    utilizing concurrent.futures for efficient execution.
    """
    def __init__(self, container: Any):
        self.container = container

    def execute_in_parallel(
        self, 
        prompt: str, 
        models: List[Dict[str, Any]], 
        system_prompt: Optional[str] = None, 
        max_tokens: int = 1000, 
        temperature: float = 0.7,
        timeout: float = 30.0
    ) -> ServiceResult[Dict[str, Any]]:
        """
        Executes the same prompt across multiple models concurrently.
        `models` should be a list of dicts containing 'provider_name' and 'model_name'.
        Returns a dictionary containing 'successful_results' and 'failed_models'.
        """
        if not models:
            return ServiceResult.fail(
                ForgeError(code="NO_MODELS_PROVIDED", message="No models provided for parallel execution.")
            )

        results = []
        errors = []

        def _call_model(model_config: Dict[str, Any]) -> Dict[str, Any]:
            provider = model_config.get("provider_name")
            model = model_config.get("model_name")
            
            if not provider or not model:
                raise ValueError("Model config must contain 'provider_name' and 'model_name'.")
            
            # Defer import to avoid circular dependencies
            from services.llm_service import LLMService
            
            response_text = LLMService.call(
                provider_name=provider,
                model_name=model,
                prompt=prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature
            )
            
            return {
                "provider_name": provider,
                "model_name": model,
                "response": response_text
            }

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(models)) as executor:
                future_to_model = {
                    executor.submit(_call_model, m): m for m in models
                }
                
                done, not_done = concurrent.futures.wait(
                    future_to_model.keys(), 
                    timeout=timeout,
                    return_when=concurrent.futures.ALL_COMPLETED
                )

                for future in done:
                    model_cfg = future_to_model[future]
                    try:
                        res = future.result()
                        results.append(res)
                    except Exception as e:
                        logger.error(f"Error calling model {model_cfg}: {e}", exc_info=True)
                        errors.append({"model": model_cfg, "error": str(e)})

                for future in not_done:
                    model_cfg = future_to_model[future]
                    logger.warning(f"Timeout calling model {model_cfg}")
                    errors.append({"model": model_cfg, "error": "Timeout exceeded"})
                    future.cancel()

            if not results:
                return ServiceResult.fail(ForgeError(
                    code="PARALLEL_EXECUTION_FAILED", 
                    message=f"All models failed during parallel execution. Errors: {errors}"
                ))

            return ServiceResult.success({
                "successful_results": results,
                "failed_models": errors
            })
        except Exception as e:
            logger.error(f"Parallel execution error: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="PARALLEL_EXEC_ERROR", message=str(e)))
