import logging
from typing import Any, Dict, List
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class ModelRouterService:
    """
    The main router executing the pipeline: 
    Planner -> Capability -> Registry -> Health -> Cost -> Selected Provider.
    """
    def __init__(self, container: Any):
        self.negotiator = container.get('CapabilityNegotiator')
        self.health_monitor = container.get('ModelHealthMonitor')
        self.cost_optimizer = container.get('CostOptimizerService')

    def route_request(self, requirements: List[str], min_context: int, 
                      input_tokens: int, expected_output_tokens: int, budget: float, 
                      optimize_for: str = "cost") -> ServiceResult[Dict[str, Any]]:
        """
        optimize_for: 'cost', 'latency', or 'balanced'
        """
        try:
            # Step 1: Capability & Registry mapping
            cap_res = self.negotiator.find_capable_models(requirements, min_context)
            if not cap_res.is_success:
                return ServiceResult.fail(cap_res.error)

            capable_models = cap_res.value

            # Step 2: Health Monitor
            healthy_models = []
            for model in capable_models:
                health_res = self.health_monitor.evaluate_health(model["model_id"])
                if health_res.is_success and health_res.value.get("is_healthy", False):
                    model["_health_metrics"] = health_res.value
                    healthy_models.append(model)

            if not healthy_models:
                return ServiceResult.fail(ForgeError(code="NO_HEALTHY_MODELS", message="No capable models are currently healthy."))

            # Step 3: Cost Optimizer
            cost_res = self.cost_optimizer.filter_by_budget(healthy_models, input_tokens, expected_output_tokens, budget)
            if not cost_res.is_success:
                return ServiceResult.fail(cost_res.error)

            affordable_models = cost_res.value

            # Step 4: Router selection logic based on optimization preference
            if optimize_for == "latency":
                best_model = min(affordable_models, key=lambda m: m.get("base_latency", 999.0))
            elif optimize_for == "cost":
                best_model = min(affordable_models, key=lambda m: m.get("_estimated_cost", 999.0))
            else:
                # Balanced: sort by a simple heuristic score (lower is better)
                best_model = min(affordable_models, key=lambda m: m.get("_estimated_cost", 999.0) * m.get("base_latency", 1.0))

            logger.info(f"Routed request to model {best_model['model_id']} (optimized for {optimize_for})")
            return ServiceResult.success(best_model)
        except Exception as e:
            logger.error(f"Routing request failed: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="ROUTING_ERROR", message=f"Pipeline execution failed: {str(e)}"))
