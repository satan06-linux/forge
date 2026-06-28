import logging
from typing import Any, Dict, List, Tuple, Optional
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class CostOptimizerService:
    """
    Calculates API pricing paths to fulfill tasks within budget.
    """
    def __init__(self, container: Any):
        self.storage = container.get('StorageProvider')
        self.default_input_price_per_1k = 0.01
        self.default_output_price_per_1k = 0.03

    def _get_data(self, key: str) -> Optional[str]:
        if hasattr(self.storage, 'get'):
            return self.storage.get(key)
        if hasattr(self.storage, 'read'):
            return self.storage.read(key)
        return None

    def _get_pricing(self, model_id: str) -> Tuple[float, float]:
        try:
            price_data = self._get_data(f"pricing:{model_id}")
            if price_data:
                import json
                data = json.loads(price_data)
                return data.get("input_1k", self.default_input_price_per_1k), data.get("output_1k", self.default_output_price_per_1k)
        except Exception:
            pass
        return self.default_input_price_per_1k, self.default_output_price_per_1k

    def estimate_cost(self, model_id: str, input_tokens: int, expected_output_tokens: int) -> ServiceResult[float]:
        try:
            in_price, out_price = self._get_pricing(model_id)
            cost = (input_tokens / 1000.0) * in_price + (expected_output_tokens / 1000.0) * out_price
            return ServiceResult.success(cost)
        except Exception as e:
            logger.error(f"Cost estimation failed for {model_id}: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="COST_ESTIMATION_ERROR", message=str(e)))

    def filter_by_budget(self, models: List[Dict[str, Any]], input_tokens: int, 
                         expected_output_tokens: int, budget: float) -> ServiceResult[List[Dict[str, Any]]]:
        try:
            affordable_models = []
            for model in models:
                model_id = model.get("model_id")
                if not model_id:
                    continue
                    
                cost_res = self.estimate_cost(model_id, input_tokens, expected_output_tokens)
                if cost_res.is_success:
                    estimated_cost = cost_res.value
                    if estimated_cost <= budget:
                        model["_estimated_cost"] = estimated_cost
                        affordable_models.append(model)
                    else:
                        logger.debug(f"Model {model_id} exceeded budget {budget} with cost {estimated_cost}")
            
            if not affordable_models:
                return ServiceResult.fail(ForgeError(
                    code="BUDGET_EXCEEDED", 
                    message=f"No models can fulfill the task within the budget of ${budget:.4f}"
                ))
                
            return ServiceResult.success(affordable_models)
        except Exception as e:
            logger.error(f"Failed to filter models by budget: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="COST_FILTER_ERROR", message=str(e)))
