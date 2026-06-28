import logging
import math
from typing import List, Dict, Any

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class SmartPromptClassifier:
    def __init__(self, container: Any = None):
        self.container = container
        self.domains = [
            "Coding", "Math", "Reasoning", "CreativeWriting", "Translation",
            "DataAnalysis", "Roleplay", "Summarization", "QA", "Medical",
            "Legal", "Financial", "Scientific", "Education", "Casual", "Unknown"
        ]
        
        self.heuristics = {
            "Coding": ["def ", "class ", "import ", "function", "code", "debug", "python", "javascript", "sql"],
            "Math": ["calculate", "equation", "solve", "math", "integral", "derivative", "algebra", "geometry"],
            "Reasoning": ["logic", "puzzle", "riddle", "deduce", "infer", "therefore", "premise"],
            "CreativeWriting": ["story", "poem", "write a", "character", "plot", "fantasy", "scifi", "tale"],
            "Translation": ["translate", "language", "spanish", "french", "german", "english", "meaning in"],
            "DataAnalysis": ["dataset", "statistics", "trend", "chart", "graph", "mean", "median", "csv", "json"],
            "Roleplay": ["act as", "pretend", "you are a", "persona", "scenario", "let's play"],
            "Summarization": ["summarize", "tl;dr", "tldr", "shorten", "brief", "main points", "in a nutshell"],
            "QA": ["what is", "how do", "why does", "when did", "who is", "explain"],
            "Medical": ["symptom", "disease", "treatment", "doctor", "patient", "medicine", "diagnosis", "health"],
            "Legal": ["law", "contract", "court", "judge", "attorney", "lawsuit", "legal", "statute"],
            "Financial": ["stock", "investment", "money", "finance", "tax", "budget", "revenue", "profit"],
            "Scientific": ["experiment", "physics", "chemistry", "biology", "hypothesis", "theory", "quantum"],
            "Education": ["teach", "learn", "lesson", "student", "teacher", "curriculum", "tutor", "explain to a 5 year old"],
            "Casual": ["hi", "hello", "how are you", "what's up", "good morning", "joke", "weather"]
        }

    def _get_pseudo_embedding(self, text: str) -> List[float]:
        counts = [0.0] * 26
        text_lower = text.lower()
        total_chars = max(1, len([c for c in text_lower if c.isalpha()]))
        for char in text_lower:
            if 'a' <= char <= 'z':
                counts[ord(char) - ord('a')] += 1.0
        return [c / total_chars for c in counts]

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        dot = sum(v1 * v2 for v1, v2 in zip(vec1, vec2))
        norm1 = math.sqrt(sum(v * v for v in vec1))
        norm2 = math.sqrt(sum(v * v for v in vec2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)
        
    def classify_prompt(self, prompt: str) -> ServiceResult[Dict[str, Any]]:
        try:
            if not prompt or not isinstance(prompt, str):
                return ServiceResult.fail(ForgeError(code="INVALID_PROMPT", message="Prompt must be a non-empty string."))
                
            logger.info("Classifying prompt of length %d", len(prompt))
            prompt_lower = prompt.lower()
            
            scores = {domain: 0.0 for domain in self.domains}
            
            for domain, keywords in self.heuristics.items():
                for kw in keywords:
                    if kw in prompt_lower:
                        scores[domain] += 1.0
            
            prompt_emb = self._get_pseudo_embedding(prompt)
            for domain, keywords in self.heuristics.items():
                domain_text = " ".join(keywords)
                domain_emb = self._get_pseudo_embedding(domain_text)
                sim = self._cosine_similarity(prompt_emb, domain_emb)
                scores[domain] += sim * 0.5
                
            best_domain = "Unknown"
            best_score = 0.0
            
            for domain, score in scores.items():
                if score > best_score:
                    best_score = score
                    best_domain = domain
                    
            if best_score < 0.2:
                best_domain = "Unknown"
                
            confidence = min(best_score / 3.0, 1.0) if best_domain != "Unknown" else 0.1
            
            result_data = {
                "domain": best_domain,
                "confidence": confidence,
                "all_scores": scores
            }
            
            return ServiceResult.success(result_data)
            
        except Exception as e:
            logger.error("Error classifying prompt: %s", str(e))
            return ServiceResult.fail(ForgeError(code="CLASSIFICATION_ERROR", message=f"Failed to classify prompt: {str(e)}"))

    def batch_classify(self, prompts: List[str]) -> ServiceResult[List[Dict[str, Any]]]:
        try:
            if not prompts or not isinstance(prompts, list):
                return ServiceResult.fail(ForgeError(code="INVALID_PROMPTS", message="Prompts must be a list of strings."))
                
            results = []
            for p in prompts:
                res = self.classify_prompt(p)
                if not res.is_success:
                    return ServiceResult.fail(res.error)
                results.append(res.value)
                
            return ServiceResult.success(results)
        except Exception as e:
            logger.error("Error in batch classify: %s", str(e))
            return ServiceResult.fail(ForgeError(code="BATCH_CLASSIFICATION_ERROR", message=f"Failed during batch classification: {str(e)}"))
