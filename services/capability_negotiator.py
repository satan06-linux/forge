import logging
from typing import Any, Dict, List, Optional
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class CapabilityNegotiator:
    """
    Matches workflow requirements (e.g., supports_vision, supports_function_calling) 
    against models in the registry.
    """
    def __init__(self, container: Any):
        self.registry = container.get('ModelRegistryService')

    def find_capable_models(self, requirements: List[str], min_context: int, 
                            preferred_languages: Optional[List[str]] = None) -> ServiceResult[List[Dict[str, Any]]]:
        try:
            list_res = self.registry.list_models()
            if not list_res.is_success:
                return ServiceResult.fail(list_res.error)

            all_models = list_res.value
            capable_models = []
            req_set = set(requirements)

            for model in all_models:
                if not model.get("is_active", False):
                    continue

                caps = set(model.get("capabilities", []))
                context = model.get("context_length", 0)
                
                # Check required capabilities
                if not req_set.issubset(caps):
                    continue
                
                # Check context length
                if context < min_context:
                    continue

                # Check language support if requested
                if preferred_languages:
                    langs = set(model.get("languages", []))
                    if not any(pl in langs for pl in preferred_languages):
                        continue

                capable_models.append(model)

            if not capable_models:
                logger.warning(f"No capable models found for requirements={requirements}, context={min_context}")
                return ServiceResult.fail(ForgeError(
                    code="NO_CAPABLE_MODELS", 
                    message=f"No models meet the requested capabilities: {requirements} and context length: {min_context}."
                ))

            return ServiceResult.success(capable_models)
        except Exception as e:
            logger.error(f"Capability negotiation failed: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="NEGOTIATION_ERROR", message=f"Error matching capabilities: {str(e)}"))
