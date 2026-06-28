import logging
from typing import List, Dict, Any, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError, ValidationError, LLMProviderError
from services.llm_service import LLMService

logger = logging.getLogger(__name__)

class ContextCompressionEngine:
    """
    Implements LLM-backed auto-summarization, contextual compression, 
    and duplicate removal when conversation windows exceed context limits.
    """
    def __init__(self, container):
        self.container = container

    def estimate_tokens(self, text: str) -> int:
        """
        Approximates token count without a full tokenizer.
        """
        if not text:
            return 0
        return len(text) // 4

    def remove_duplicates(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """
        Removes exact duplicate messages (based on role and content),
        keeping the first occurrence to preserve sequential context.
        """
        seen = set()
        unique_messages = []
        for msg in messages:
            content = msg.get("content", "").strip()
            role = msg.get("role", "")
            key = f"{role}:{content}"
            if key not in seen:
                seen.add(key)
                unique_messages.append(msg)
        return unique_messages

    def summarize_messages(
        self, 
        messages: List[Dict[str, str]], 
        provider: str = "groq", 
        model: str = "llama3-8b-8192"
    ) -> ServiceResult:
        """
        Calls the LLM to summarize a block of messages.
        """
        if not messages:
            return ServiceResult.ok(data="")
            
        try:
            conversation_text = ""
            for msg in messages:
                role = msg.get("role", "unknown").upper()
                content = msg.get("content", "")
                conversation_text += f"{role}: {content}\n"

            prompt = f"Summarize the following conversation concisely while retaining key context and decisions:\n\n{conversation_text}"
            system_prompt = "You are an AI context manager. Summarize the conversation so it can be used as compressed context for future interactions."
            
            summary = LLMService.call(
                provider_name=provider,
                model_name=model,
                prompt=prompt,
                system_prompt=system_prompt,
                max_tokens=1000,
                temperature=0.3
            )
            return ServiceResult.ok(data=summary)
        except Exception as e:
            logger.error(f"[ContextCompressionEngine] Summarization failed: {e}", exc_info=True)
            return ServiceResult.fail(LLMProviderError(f"Summarization error: {str(e)}"))

    def compress_context(
        self, 
        messages: List[Dict[str, str]], 
        max_tokens: int,
        provider: str = "groq",
        model: str = "llama3-8b-8192",
        preserve_recent: int = 5
    ) -> ServiceResult:
        """
        Compresses a conversation history to fit within max_tokens limit.
        1. Removes duplicates.
        2. Estimates tokens.
        3. If over limit, summarizes older messages while preserving the N most recent messages.
        """
        if not messages:
            return ServiceResult.ok(data=[])
            
        try:
            # Step 1: Duplicate removal
            deduped = self.remove_duplicates(messages)
            
            def get_total_tokens(msgs: List[Dict[str, str]]) -> int:
                return sum(self.estimate_tokens(m.get("content", "")) for m in msgs)
                
            total_tokens = get_total_tokens(deduped)
            
            # Step 2: Check if compression is needed
            if total_tokens <= max_tokens:
                return ServiceResult.ok(
                    data=deduped, 
                    metadata={"compression_applied": False, "tokens": total_tokens}
                )
                
            # Step 3: Contextual Compression / Auto-summarization
            if len(deduped) <= preserve_recent:
                # Can't summarize without losing recent context
                return ServiceResult.ok(
                    data=deduped, 
                    metadata={"compression_applied": False, "warning": "Too few messages to compress", "tokens": total_tokens}
                )
                
            old_messages = deduped[:-preserve_recent]
            recent_messages = deduped[-preserve_recent:]
            
            # Summarize older messages
            summary_res = self.summarize_messages(old_messages, provider, model)
            if summary_res.is_error:
                return summary_res
                
            summary_text = summary_res.data
            
            compressed_messages = [
                {"role": "system", "content": f"Summary of previous conversation:\n{summary_text}"}
            ]
            compressed_messages.extend(recent_messages)
            
            new_tokens = get_total_tokens(compressed_messages)
            
            return ServiceResult.ok(
                data=compressed_messages, 
                metadata={
                    "compression_applied": True, 
                    "original_tokens": total_tokens,
                    "new_tokens": new_tokens,
                    "messages_summarized": len(old_messages)
                }
            )
            
        except Exception as e:
            logger.error(f"[ContextCompressionEngine] Error compressing context: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(f"Context compression failed: {str(e)}", error_code="COMPRESSION_FAILED"))

__all__ = ["ContextCompressionEngine"]
