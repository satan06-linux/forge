# ForgePrompt Phase 7 — AgentMemory

import time
import uuid
import logging
from typing import List, Dict, Any, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class AgentMemory:
    def __init__(self, container):
        self.container = container
        self.working_memory: List[Dict[str, Any]] = []
        self.episodic_memory: List[Dict[str, Any]] = []
        self.semantic_memory: Dict[str, Any] = {}
        self.scratchpad: List[str] = []
        self.max_working_tokens = 8192

    def _estimate_tokens(self, text: str) -> int:
        # Simple estimation: ~4 chars per token for typical English text
        return len(text) // 4 + 1

    def add_working_memory(self, role: str, content: str, importance: float = 1.0) -> ServiceResult:
        try:
            entry = {
                "id": str(uuid.uuid4()),
                "role": role,
                "content": content,
                "timestamp": time.time(),
                "importance": importance,
                "tokens": self._estimate_tokens(content)
            }
            self.working_memory.append(entry)
            self._enforce_context_window()
            return ServiceResult.success(data=entry)
        except Exception as e:
            logger.error(f"[AgentMemory Error] add_working_memory failed: {str(e)}")
            return ServiceResult.fail(error_code="MEMORY_ADD_FAILED", message=str(e))

    def _enforce_context_window(self) -> None:
        total_tokens = sum(item["tokens"] for item in self.working_memory)
        while total_tokens > self.max_working_tokens and len(self.working_memory) > 1:
            # Keep system prompts if possible, evict lowest importance first, oldest first
            evict_candidates = [i for i in self.working_memory if i.get("role") != "system"]
            if not evict_candidates:
                # If only system prompts remain, we must break or evict system prompts
                evict_candidates = self.working_memory

            evict_candidates.sort(key=lambda x: (x.get("importance", 1.0), x.get("timestamp", 0.0)))
            evict_item = evict_candidates[0]
            
            # Archive to episodic memory before removal
            self.add_episodic_memory(
                event_type="memory_eviction",
                details=evict_item
            )
            
            self.working_memory.remove(evict_item)
            total_tokens -= evict_item["tokens"]

    def add_episodic_memory(self, event_type: str, details: Dict[str, Any]) -> ServiceResult:
        try:
            entry = {
                "id": str(uuid.uuid4()),
                "type": event_type,
                "details": details,
                "timestamp": time.time()
            }
            self.episodic_memory.append(entry)
            return ServiceResult.success(data=entry)
        except Exception as e:
            logger.error(f"[AgentMemory Error] add_episodic_memory failed: {str(e)}")
            return ServiceResult.fail(error_code="EPISODIC_ADD_FAILED", message=str(e))

    def update_semantic_memory(self, key: str, value: Any) -> ServiceResult:
        try:
            self.semantic_memory[key] = {
                "value": value,
                "updated_at": time.time()
            }
            return ServiceResult.success(data={"key": key, "value": value})
        except Exception as e:
            logger.error(f"[AgentMemory Error] update_semantic_memory failed: {str(e)}")
            return ServiceResult.fail(error_code="SEMANTIC_UPDATE_FAILED", message=str(e))

    def append_scratchpad(self, thought: str) -> ServiceResult:
        try:
            entry = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {thought}"
            self.scratchpad.append(entry)
            return ServiceResult.success(data={"thought": thought})
        except Exception as e:
            logger.error(f"[AgentMemory Error] append_scratchpad failed: {str(e)}")
            return ServiceResult.fail(error_code="SCRATCHPAD_APPEND_FAILED", message=str(e))

    def get_context(self) -> ServiceResult:
        try:
            context = {
                "working_memory": self.working_memory,
                "semantic_memory": self.semantic_memory,
                "scratchpad": self.scratchpad,
                "recent_episodes": sorted(self.episodic_memory, key=lambda x: x["timestamp"], reverse=True)[:10]
            }
            return ServiceResult.success(data=context)
        except Exception as e:
            logger.error(f"[AgentMemory Error] get_context failed: {str(e)}")
            return ServiceResult.fail(error_code="GET_CONTEXT_FAILED", message=str(e))

    def clear_working_memory(self) -> ServiceResult:
        try:
            self.working_memory.clear()
            return ServiceResult.success(data=True)
        except Exception as e:
            logger.error(f"[AgentMemory Error] clear_working_memory failed: {str(e)}")
            return ServiceResult.fail(error_code="CLEAR_MEMORY_FAILED", message=str(e))
