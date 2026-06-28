# ForgePrompt Phase 7 — ReasoningEngine

import logging
import json
from typing import Dict, Any, List, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError
from .memory import AgentMemory
from .tools import ToolRegistryAdapter

logger = logging.getLogger(__name__)

class ReasoningEngine:
    def __init__(self, container):
        self.container = container
        self.memory: AgentMemory = AgentMemory(container)
        self.tool_registry: ToolRegistryAdapter = ToolRegistryAdapter(container)
        self.max_iterations = 15

    def run_react_loop(self, task_input: str) -> ServiceResult:
        try:
            # Initialize context
            mem_res = self.memory.add_working_memory("user", task_input, importance=1.0)
            if not mem_res.is_success:
                return mem_res
            
            iteration = 0
            while iteration < self.max_iterations:
                # 1. THOUGHT
                thought_res = self._generate_thought()
                if not thought_res.is_success:
                    return thought_res
                
                thought = thought_res.data
                self.memory.append_scratchpad(f"Thought: {thought}")
                self.memory.add_working_memory("assistant", f"Thought: {thought}", importance=0.8)
                
                # Check completion heuristic
                if "Task Complete" in thought or "Finish" in thought:
                    break
                
                # 2. ACTION
                action_def_res = self._determine_action(thought)
                if not action_def_res.is_success:
                    return action_def_res
                
                action_def = action_def_res.data
                if not action_def:
                    msg = "No action determined. Exiting loop."
                    self.memory.append_scratchpad(f"System: {msg}")
                    break
                
                action_name = action_def.get("action")
                action_args = action_def.get("args", {})
                
                action_desc = f"Action: {action_name}({json.dumps(action_args)})"
                self.memory.add_working_memory("assistant", action_desc, importance=0.9)
                self.memory.append_scratchpad(action_desc)
                
                # 3. OBSERVATION
                obs_res = self.tool_registry.execute_tool(action_name, action_args)
                if obs_res.is_success:
                    observation = f"Observation: {obs_res.data}"
                else:
                    observation = f"Observation Failed: {obs_res.error_message}"
                
                self.memory.add_working_memory("system", observation, importance=0.9)
                self.memory.append_scratchpad(observation)
                
                iteration += 1

            if iteration >= self.max_iterations:
                return ServiceResult.fail(error_code="MAX_ITERATIONS_REACHED", message="ReAct loop exhausted")
            
            return ServiceResult.success(data={"status": "completed", "iterations": iteration})
            
        except Exception as e:
            logger.error(f"[ReasoningEngine Error] run_react_loop failed: {str(e)}")
            return ServiceResult.fail(error_code="REACT_LOOP_FAILED", message=str(e))

    def _generate_thought(self) -> ServiceResult:
        try:
            # Integration point for actual LLM. If present in container, use it.
            llm_service = getattr(self.container, "llm_service", None)
            if llm_service and hasattr(llm_service, "generate"):
                context_res = self.memory.get_context()
                context = context_res.data if context_res.is_success else {}
                prompt = f"Given context {json.dumps(context)}, generate the next ReAct thought."
                
                llm_res = llm_service.generate(prompt)
                if not llm_res.is_success:
                    return llm_res
                return ServiceResult.success(data=llm_res.data)
            else:
                # Fallback logic for standalone operation / testing
                logger.warning("No llm_service found in container. Returning mock thought.")
                return ServiceResult.success(data="I should execute an initial action to understand the environment. Task Complete.")
        except Exception as e:
            logger.error(f"[ReasoningEngine Error] _generate_thought failed: {str(e)}")
            return ServiceResult.fail(error_code="THOUGHT_GENERATION_FAILED", message=str(e))

    def _determine_action(self, thought: str) -> ServiceResult:
        try:
            # Here an LLM would extract JSON action structure from thought.
            # Using basic mock heuristic for now if LLM doesn't do structured output natively here.
            llm_service = getattr(self.container, "llm_service", None)
            if llm_service and hasattr(llm_service, "extract_action"):
                action_res = llm_service.extract_action(thought)
                return action_res if action_res.is_success else ServiceResult.success(data={})
            
            # Fallback mock heuristic
            action = {}
            if "environment" in thought.lower() or "ping" in thought.lower():
                action = {"action": "system_ping", "args": {}}
            
            return ServiceResult.success(data=action)
        except Exception as e:
            logger.error(f"[ReasoningEngine Error] _determine_action failed: {str(e)}")
            return ServiceResult.fail(error_code="ACTION_EXTRACTION_FAILED", message=str(e))
