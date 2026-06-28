import logging
from typing import Any, Dict, List
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class ResponseFusionEngine:
    """
    Merges outputs, reduces hallucinations, and intelligently merges citations 
    across different model responses.
    """
    def __init__(self, container: Any):
        self.container = container

    def fuse_responses(self, responses: List[Dict[str, Any]], use_llm_fusion: bool = False) -> ServiceResult[Dict[str, Any]]:
        """
        Fuses multiple responses into a single coherent output.
        Each response dict should have 'response' and optionally 'citations'.
        """
        if not responses:
            return ServiceResult.fail(ForgeError(code="NO_RESPONSES", message="No responses provided for fusion."))

        if len(responses) == 1:
            return ServiceResult.success({
                "fused_response": responses[0].get("response", ""),
                "merged_citations": responses[0].get("citations", [])
            })

        try:
            merged_citations = self._merge_citations(responses)
            
            if use_llm_fusion:
                fused_text = self._llm_fuse_texts([str(r.get("response", "")) for r in responses])
            else:
                fused_text = self._heuristic_fuse_texts([str(r.get("response", "")) for r in responses])

            return ServiceResult.success({
                "fused_response": fused_text,
                "merged_citations": merged_citations
            })
        except Exception as e:
            logger.error(f"Error during response fusion: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="FUSION_ERROR", message=str(e)))

    def _merge_citations(self, responses: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """
        Extracts, deduplicates, and merges citations (e.g., URLs, document IDs) across all responses.
        """
        merged_citations_map = {}
        for r in responses:
            citations = r.get("citations", [])
            if isinstance(citations, list):
                for c in citations:
                    if isinstance(c, dict):
                        # Use url, text, or id as a unique key for deduplication
                        source_key = c.get("url") or c.get("text") or c.get("id", "")
                        if source_key and source_key not in merged_citations_map:
                            merged_citations_map[source_key] = c
            
        return list(merged_citations_map.values())

    def _heuristic_fuse_texts(self, texts: List[str]) -> str:
        """
        A heuristic approach to merge texts. Takes the most comprehensive base text
        and safely appends unique paragraphs from others to reduce hallucinations.
        """
        valid_texts = [t.strip() for t in texts if t.strip()]
        if not valid_texts:
            return ""
            
        # Select base text: longest valid response
        base_text = max(valid_texts, key=len)
        base_paragraphs = set(p.strip().lower() for p in base_text.split('\n\n') if p.strip())
        
        fused = [base_text]
        
        # Append unique paragraphs from other texts
        for t in valid_texts:
            if t == base_text:
                continue
            paragraphs = [p.strip() for p in t.split('\n\n') if p.strip()]
            for p in paragraphs:
                p_lower = p.lower()
                # Check for significant uniqueness
                is_unique = True
                for bp in base_paragraphs:
                    # If this paragraph is highly contained within an existing one, skip
                    if p_lower in bp or bp in p_lower:
                        is_unique = False
                        break
                
                if is_unique:
                    base_paragraphs.add(p_lower)
                    fused.append(p)
                    
        return "\n\n".join(fused)

    def _llm_fuse_texts(self, texts: List[str]) -> str:
        """
        Uses the LLMService to smartly fuse texts, resolving conflicts and dropping hallucinations.
        """
        try:
            from services.llm_service import LLMService
            
            prompt = "Merge the following AI responses into a single coherent, factual, and hallucination-free response. Resolve any conflicts logically.\n\n"
            for i, t in enumerate(texts):
                prompt += f"--- Response {i+1} ---\n{t}\n\n"
                
            response = LLMService.call(
                provider_name="local",
                model_name="default",
                prompt=prompt,
                system_prompt="You are an expert AI fusion engine. Output only the merged factual response, preserving citations where applicable.",
                max_tokens=2000,
                temperature=0.3
            )
            return response if response else self._heuristic_fuse_texts(texts)
        except Exception as e:
            logger.warning(f"LLM fusion failed, falling back to heuristic fusion: {e}")
            return self._heuristic_fuse_texts(texts)
