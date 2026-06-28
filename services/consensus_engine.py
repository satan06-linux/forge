import logging
from typing import Any, Dict, List, Optional
from services.service_result import ServiceResult
from services.errors import ForgeError
from collections import Counter

logger = logging.getLogger(__name__)

class ConsensusEngine:
    """
    Uses Majority Voting, Confidence Scoring, Truth Ranking, and Conflict Detection 
    to resolve conflicts when multiple models disagree.
    """
    def __init__(self, container: Any):
        self.container = container

    def resolve_consensus(self, candidates: List[Dict[str, Any]]) -> ServiceResult[Dict[str, Any]]:
        """
        Evaluates multiple candidate responses to find the most accurate/reliable output.
        `candidates` expects a list of dicts with at least 'response' and 'model_name',
        and optionally 'confidence' and 'truth_score'.
        """
        if not candidates:
            return ServiceResult.fail(
                ForgeError(code="NO_CANDIDATES", message="No candidates provided for consensus.")
            )

        try:
            # 1. Conflict Detection: Check if all candidates returned the same core response
            if self._all_agree(candidates):
                return ServiceResult.success({
                    "consensus_reached": True,
                    "resolution_method": "unanimous",
                    "final_response": candidates[0].get("response", ""),
                    "selected_model": candidates[0].get("model_name", "unknown")
                })
            
            # 2. Majority Voting: Check if there's a strict majority
            majority_winner = self._majority_voting(candidates)
            if majority_winner:
                return ServiceResult.success({
                    "consensus_reached": True,
                    "resolution_method": "majority_voting",
                    "final_response": majority_winner.get("response", ""),
                    "selected_model": majority_winner.get("model_name", "unknown")
                })

            # 3. Confidence Scoring & Truth Ranking: Rank by multi-factor heuristics
            best_candidate = self._rank_by_confidence_and_truth(candidates)
            if best_candidate:
                return ServiceResult.success({
                    "consensus_reached": True,
                    "resolution_method": "confidence_truth_ranking",
                    "final_response": best_candidate.get("response", ""),
                    "selected_model": best_candidate.get("model_name", "unknown")
                })
            
            # 4. Fallback: Select the first candidate if all else fails
            fallback = candidates[0]
            return ServiceResult.success({
                "consensus_reached": False,
                "resolution_method": "fallback_first",
                "final_response": fallback.get("response", ""),
                "selected_model": fallback.get("model_name", "unknown")
            })

        except Exception as e:
            logger.error(f"Consensus engine error: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="CONSENSUS_ERROR", message=str(e)))

    def _all_agree(self, candidates: List[Dict[str, Any]]) -> bool:
        """Detects if all candidates provided essentially the same response."""
        if len(candidates) <= 1:
            return True
        first_resp = str(candidates[0].get("response", "")).strip().lower()
        for c in candidates[1:]:
            other_resp = str(c.get("response", "")).strip().lower()
            if first_resp != other_resp:
                return False
        return True

    def _majority_voting(self, candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Identifies a strict majority winner if one exists."""
        response_map = {}
        counts = Counter()
        
        for c in candidates:
            resp_text = str(c.get("response", "")).strip().lower()
            counts[resp_text] += 1
            if resp_text not in response_map:
                response_map[resp_text] = c

        total_votes = len(candidates)
        most_common = counts.most_common(1)
        if most_common:
            best_resp, vote_count = most_common[0]
            if vote_count > total_votes / 2.0:
                return response_map[best_resp]
        
        return None

    def _rank_by_confidence_and_truth(self, candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Ranks candidates combining confidence score, truth score, and basic text heuristics.
        """
        def calculate_score(c: Dict[str, Any]) -> float:
            # Default to 0.5 if not provided
            conf = float(c.get("confidence", 0.5))
            truth = float(c.get("truth_score", 0.5))
            
            resp = str(c.get("response", "")).strip()
            length_penalty = 0.0
            
            # Heuristic checks for common failure modes
            if not resp or len(resp) < 5:
                length_penalty = -0.4
            
            lower_resp = resp.lower()
            if "i don't know" in lower_resp or "i do not know" in lower_resp:
                length_penalty -= 0.5
            if "as an ai" in lower_resp or "language model" in lower_resp:
                length_penalty -= 0.2
            if "error" in lower_resp:
                length_penalty -= 0.3
                
            return (conf * 0.5) + (truth * 0.5) + length_penalty

        ranked = sorted(candidates, key=calculate_score, reverse=True)
        return ranked[0] if ranked else None
