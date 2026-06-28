import logging
import datetime
from typing import Optional, Dict, Any, List

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class AIAnalyticsService:
    def __init__(self, container: Any):
        self.container = container
        self.storage_provider = self.container.get('StorageProvider') if hasattr(self.container, 'get') else None

    def track_usage(self, model_name: str, tokens_prompt: int, tokens_completion: int, cost: float, latency_ms: float, success: bool, hallucination_score: float = 0.0) -> ServiceResult:
        try:
            record = {
                "id": self._generate_id(),
                "model_name": model_name,
                "tokens_prompt": tokens_prompt,
                "tokens_completion": tokens_completion,
                "total_tokens": tokens_prompt + tokens_completion,
                "cost": cost,
                "latency_ms": latency_ms,
                "success": success,
                "hallucination_score": hallucination_score,
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            if self.storage_provider:
                self.storage_provider.save("ai_analytics", record["id"], record)
            return ServiceResult.success(record)
        except Exception as e:
            logger.error(f"Failed to track AI usage: {str(e)}")
            return ServiceResult.fail(ForgeError(code="ANALYTICS_TRACKING_FAILED", message=f"Failed to track AI usage: {str(e)}"))

    def get_metrics(self, model_name: Optional[str] = None) -> ServiceResult:
        try:
            if not self.storage_provider:
                return ServiceResult.fail(ForgeError(code="STORAGE_UNAVAILABLE", message="Storage provider not found."))
            
            all_records = self.storage_provider.list("ai_analytics")
            if model_name:
                filtered_records = [r for r in all_records if r.get("model_name") == model_name]
            else:
                filtered_records = all_records

            total_prompt_tokens = sum(r.get("tokens_prompt", 0) for r in filtered_records)
            total_completion_tokens = sum(r.get("tokens_completion", 0) for r in filtered_records)
            total_cost = sum(r.get("cost", 0.0) for r in filtered_records)
            success_count = sum(1 for r in filtered_records if r.get("success"))
            total_count = len(filtered_records)
            avg_latency = sum(r.get("latency_ms", 0.0) for r in filtered_records) / total_count if total_count > 0 else 0.0
            avg_hallucination = sum(r.get("hallucination_score", 0.0) for r in filtered_records) / total_count if total_count > 0 else 0.0

            metrics = {
                "model_name": model_name or "all",
                "total_requests": total_count,
                "success_rate": (success_count / total_count) if total_count > 0 else 0.0,
                "total_prompt_tokens": total_prompt_tokens,
                "total_completion_tokens": total_completion_tokens,
                "total_tokens": total_prompt_tokens + total_completion_tokens,
                "total_cost": total_cost,
                "average_latency_ms": avg_latency,
                "average_hallucination_score": avg_hallucination
            }
            return ServiceResult.success(metrics)
        except Exception as e:
            logger.error(f"Failed to get metrics: {str(e)}")
            return ServiceResult.fail(ForgeError(code="ANALYTICS_METRICS_FAILED", message=f"Failed to get metrics: {str(e)}"))

    def _generate_id(self) -> str:
        import uuid
        return str(uuid.uuid4())
