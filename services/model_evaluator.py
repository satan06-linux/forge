import logging
import time
import uuid
from typing import Dict, Any, List, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError, ValidationError

logger = logging.getLogger(__name__)

class ModelEvaluator:
    """
    Automated benchmarking (Coding, Math, Speed) and model comparison leaderboard generation.
    """
    def __init__(self):
        # Simulated database for evaluation results
        self._evaluations: Dict[str, List[Dict[str, Any]]] = {}

    def evaluate_model(self, model_id: str, criteria: Optional[List[str]] = None) -> ServiceResult:
        """
        Runs automated benchmarking across specified criteria (Coding, Math, Speed).
        """
        start_time = time.time()
        logger.info(f"Starting evaluation for model_id={model_id}")
        
        try:
            if not model_id:
                return ServiceResult.fail(ValidationError("model_id is required"))

            criteria = criteria or ["coding", "math", "speed"]
            
            # Simulate benchmarking processes
            scores: Dict[str, float] = {}
            for crit in criteria:
                if crit.lower() == "coding":
                    scores["coding"] = 85.0
                elif crit.lower() == "math":
                    scores["math"] = 78.5
                elif crit.lower() == "speed":
                    scores["speed"] = 92.0
                else:
                    scores[crit] = 70.0
            
            average_score = sum(scores.values()) / len(scores) if scores else 0.0
            
            eval_id = str(uuid.uuid4())
            evaluation_record = {
                "eval_id": eval_id,
                "model_id": model_id,
                "scores": scores,
                "average_score": average_score,
                "timestamp": time.time(),
                "criteria_evaluated": criteria
            }
            
            # Store in simulated DB
            if model_id not in self._evaluations:
                self._evaluations[model_id] = []
            self._evaluations[model_id].append(evaluation_record)
            
            duration_ms = int((time.time() - start_time) * 1000)
            return ServiceResult.ok(
                data=evaluation_record,
                duration_ms=duration_ms,
                eval_id=eval_id
            )
            
        except Exception as e:
            logger.error(f"Error evaluating model {model_id}: {str(e)}")
            return ServiceResult.fail(ForgeError(f"Evaluation failed: {str(e)}"))

    def generate_leaderboard(self, model_ids: Optional[List[str]] = None) -> ServiceResult:
        """
        Generates a model comparison leaderboard based on the latest evaluation scores.
        """
        start_time = time.time()
        logger.info("Generating model leaderboard")
        
        try:
            target_models = model_ids if model_ids is not None else list(self._evaluations.keys())
            
            leaderboard = []
            for model_id in target_models:
                evals = self._evaluations.get(model_id, [])
                if not evals:
                    continue
                # Get latest evaluation
                latest_eval = sorted(evals, key=lambda x: x["timestamp"], reverse=True)[0]
                leaderboard.append({
                    "model_id": model_id,
                    "average_score": latest_eval["average_score"],
                    "scores": latest_eval["scores"],
                    "last_evaluated": latest_eval["timestamp"]
                })
            
            # Sort leaderboard by average score descending
            leaderboard.sort(key=lambda x: x["average_score"], reverse=True)
            
            # Add rankings
            for rank, entry in enumerate(leaderboard, start=1):
                entry["rank"] = rank
                
            duration_ms = int((time.time() - start_time) * 1000)
            return ServiceResult.ok(
                data={"leaderboard": leaderboard, "total_models": len(leaderboard)},
                duration_ms=duration_ms
            )
            
        except Exception as e:
            logger.error(f"Error generating leaderboard: {str(e)}")
            return ServiceResult.fail(ForgeError(f"Leaderboard generation failed: {str(e)}"))
