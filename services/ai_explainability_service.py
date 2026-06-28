import logging
import datetime
import uuid
from typing import Any, Dict, List, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class AIExplainabilityService:
    def __init__(self, container: Any):
        self.container = container
        self.storage_provider = self.container.get('StorageProvider') if hasattr(self.container, 'get') else None

    def generate_rationale(self, decision_type: str, chosen_option: str, alternatives: List[str], context: Dict[str, Any], entity_id: str) -> ServiceResult:
        try:
            # Analyze rationale (simulating an intelligent or heuristic generation)
            reasons = self._evaluate_reasons(decision_type, chosen_option, alternatives, context)
            
            audit_record = {
                "audit_id": str(uuid.uuid4()),
                "entity_id": entity_id,
                "decision_type": decision_type,
                "chosen_option": chosen_option,
                "alternatives_considered": alternatives,
                "context_summary": self._summarize_context(context),
                "rationale": reasons,
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            
            if self.storage_provider:
                self.storage_provider.save("ai_explainability", audit_record["audit_id"], audit_record)
            
            return ServiceResult.success(audit_record)
        except Exception as e:
            logger.error(f"Failed to generate rationale: {str(e)}")
            return ServiceResult.fail(ForgeError(code="EXPLAINABILITY_GENERATION_FAILED", message=f"Failed to generate rationale: {str(e)}"))

    def get_audit_trail(self, entity_id: str) -> ServiceResult:
        try:
            if not self.storage_provider:
                return ServiceResult.fail(ForgeError(code="STORAGE_UNAVAILABLE", message="Storage provider not found."))
            
            all_records = self.storage_provider.list("ai_explainability")
            entity_records = [r for r in all_records if r.get("entity_id") == entity_id]
            
            # Sort by timestamp
            entity_records.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            
            return ServiceResult.success(entity_records)
        except Exception as e:
            logger.error(f"Failed to fetch audit trail for entity {entity_id}: {str(e)}")
            return ServiceResult.fail(ForgeError(code="AUDIT_TRAIL_FETCH_FAILED", message=f"Failed to fetch audit trail: {str(e)}"))

    def _evaluate_reasons(self, decision_type: str, chosen_option: str, alternatives: List[str], context: Dict[str, Any]) -> str:
        # In a real system, this could query a lightweight LLM or rule engine
        # to justify the choice. Here we provide a structured mock response.
        score_diff = context.get('score_difference', 'unknown')
        return f"Selected '{chosen_option}' for '{decision_type}' over {len(alternatives)} alternatives based on context parameters. Margin of preference: {score_diff}."

    def _summarize_context(self, context: Dict[str, Any]) -> str:
        # Prevent huge context objects from bloating the audit trail
        keys = list(context.keys())
        return f"Context provided with {len(keys)} keys: {', '.join(keys[:5])}{'...' if len(keys) > 5 else ''}"
