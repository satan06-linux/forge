import logging
from typing import Dict, Any

from services.service_result import ServiceResult
from services.errors import ForgeError
from services.smart_prompt_classifier import SmartPromptClassifier

logger = logging.getLogger(__name__)

class AIIntelligenceService:
    def __init__(self, container: Any = None):
        self.container = container
        self.classifier = SmartPromptClassifier(container)
        self.historical_baselines = {
            "Coding": 0.05,
            "Math": 0.03,
            "Reasoning": 0.04,
            "CreativeWriting": 0.02,
            "Translation": 0.015,
            "DataAnalysis": 0.06,
            "Default": 0.02
        }

    def analyze_prompt(self, prompt: str) -> ServiceResult[Dict[str, Any]]:
        try:
            if not prompt:
                return ServiceResult.fail(ForgeError(code="EMPTY_PROMPT", message="Prompt cannot be empty for analysis."))
                
            logger.info("Analyzing prompt intelligence for length %d", len(prompt))
            
            classification_res = self.classifier.classify_prompt(prompt)
            if not classification_res.is_success:
                return ServiceResult.fail(classification_res.error)
                
            domain_info = classification_res.value
            domain = domain_info.get("domain", "Unknown")
            
            intent = self._detect_intent(prompt, domain)
            complexity_score = self._calculate_complexity(prompt, domain)
            predicted_cost = self._predict_token_cost(prompt, domain, complexity_score)
            
            analysis_result = {
                "domain": domain,
                "confidence": domain_info.get("confidence", 0.0),
                "intent": intent,
                "complexity_score": complexity_score,
                "predicted_token_cost": predicted_cost,
                "metrics": {
                    "length": len(prompt),
                    "word_count": len(prompt.split())
                }
            }
            
            return ServiceResult.success(analysis_result)
            
        except Exception as e:
            logger.error("Error in AI intelligence analysis: %s", str(e))
            return ServiceResult.fail(ForgeError(code="INTELLIGENCE_ERROR", message=f"Failed to analyze prompt intelligence: {str(e)}"))

    def _detect_intent(self, prompt: str, domain: str) -> str:
        prompt_lower = prompt.lower()
        if any(word in prompt_lower for word in ["create", "generate", "make", "build"]):
            return "Creation"
        elif any(word in prompt_lower for word in ["explain", "what is", "how to", "teach"]):
            return "Information_Seeking"
        elif any(word in prompt_lower for word in ["fix", "debug", "error", "solve"]):
            return "Troubleshooting"
        elif any(word in prompt_lower for word in ["analyze", "compare", "evaluate"]):
            return "Analysis"
        elif any(word in prompt_lower for word in ["summarize", "shorten", "tl;dr"]):
            return "Summarization"
        return "General"

    def _calculate_complexity(self, prompt: str, domain: str) -> int:
        words = prompt.split()
        word_count = len(words)
        unique_words = len(set(words))
        
        richness = unique_words / word_count if word_count > 0 else 0
        base_score = min(word_count / 10.0, 50.0) + (richness * 30.0)
        
        if domain in ["Coding", "Math", "Scientific", "DataAnalysis"]:
            base_score += 15.0
        elif domain in ["Casual", "Roleplay"]:
            base_score -= 10.0
            
        final_score = int(max(1, min(100, base_score)))
        return final_score

    def _predict_token_cost(self, prompt: str, domain: str, complexity_score: int) -> float:
        word_count = len(prompt.split())
        estimated_tokens = word_count * 1.3
        
        cost_per_token = self.historical_baselines.get(domain, self.historical_baselines["Default"])
        
        complexity_multiplier = 1.0 + (complexity_score / 100.0)
        
        total_cost = estimated_tokens * cost_per_token * complexity_multiplier
        return round(total_cost, 4)
