import logging
import time
from typing import Any, Dict
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class ModelHealthMonitor:
    """
    Computes availability, tokens/sec, failure rates, and auto-evicts unhealthy models.
    """
    def __init__(self, container: Any):
        self.registry = container.get('ModelRegistryService')
        self.max_records = 100
        self.failure_threshold = 0.3  # 30% failure rate triggers eviction
        self.min_availability = 0.5   # 50% availability triggers eviction

    def record_interaction(self, model_id: str, success: bool, tokens: int, latency_sec: float) -> ServiceResult[bool]:
        try:
            model_res = self.registry.get_model(model_id)
            if not model_res.is_success:
                return ServiceResult.fail(model_res.error)

            model = model_res.value
            records = model.get("health_records", [])

            record = {
                "timestamp": time.time(),
                "success": success,
                "tokens": tokens,
                "latency_sec": latency_sec
            }
            records.append(record)

            if len(records) > self.max_records:
                records = records[-self.max_records:]
                
            model["health_records"] = records
            
            update_res = self.registry.update_model(model_id, {"health_records": records})
            if not update_res.is_success:
                return ServiceResult.fail(update_res.error)
                
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"Failed to record interaction for {model_id}: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="HEALTH_MONITOR_ERROR", message=f"Error recording interaction: {str(e)}"))

    def evaluate_health(self, model_id: str) -> ServiceResult[Dict[str, Any]]:
        try:
            model_res = self.registry.get_model(model_id)
            if not model_res.is_success:
                return ServiceResult.fail(model_res.error)

            model = model_res.value
            records = model.get("health_records", [])

            if not records:
                return ServiceResult.success({
                    "model_id": model_id,
                    "is_healthy": model.get("is_active", True),
                    "failure_rate": 0.0,
                    "tokens_per_sec": 0.0,
                    "availability": model.get("availability", 1.0)
                })

            total_requests = len(records)
            failed_requests = sum(1 for r in records if not r.get("success", False))
            failure_rate = failed_requests / total_requests

            total_tokens = sum(r.get("tokens", 0) for r in records if r.get("success", False))
            total_time = sum(r.get("latency_sec", 0.0) for r in records if r.get("success", False))
            tokens_per_sec = (total_tokens / total_time) if total_time > 0 else 0.0

            availability = model.get("availability", 1.0)
            
            is_healthy = True
            if failure_rate >= self.failure_threshold or availability < self.min_availability:
                is_healthy = False
                logger.warning(f"Model {model_id} marked unhealthy. Failure rate: {failure_rate:.2f}, Availability: {availability:.2f}")

            # Auto-evict if not healthy
            if not is_healthy and model.get("is_active", True):
                self.registry.update_model(model_id, {"is_active": False})
                logger.info(f"Auto-evicted unhealthy model: {model_id}")
            # Auto-restore if healthy again and previously evicted
            elif is_healthy and not model.get("is_active", True):
                self.registry.update_model(model_id, {"is_active": True})
                logger.info(f"Restored healthy model: {model_id}")

            return ServiceResult.success({
                "model_id": model_id,
                "is_healthy": is_healthy,
                "failure_rate": failure_rate,
                "tokens_per_sec": tokens_per_sec,
                "availability": availability
            })
        except Exception as e:
            logger.error(f"Health evaluation failed for {model_id}: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="HEALTH_MONITOR_ERROR", message=f"Evaluation failed: {str(e)}"))
