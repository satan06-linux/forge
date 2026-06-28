import logging
import json
from typing import Dict, Any, List, Optional
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class PlannerAgent:
    """
    High-level reasoning agent that breaks down tasks for subagents.
    """
    def __init__(self, container: Any):
        self.container = container
        self.storage_provider = container.get('StorageProvider')
        self.llm_service = container.get('LlmService')
        self.agent_registry = container.get('AgentRegistry')
        
    def plan_task(self, task_description: str, context: Optional[Dict[str, Any]] = None) -> ServiceResult[Dict[str, Any]]:
        try:
            if not task_description:
                return ServiceResult.fail(ForgeError(code="INVALID_INPUT", message="Task description cannot be empty."))
            
            context_str = json.dumps(context) if context else "{}"
            prompt = (
                f"Break down the following task into a structured plan for subagents.\n"
                f"Task: {task_description}\n"
                f"Context: {context_str}\n"
                "Return a JSON string detailing the steps, subagents required, and dependencies."
            )
            
            llm_result = self.llm_service.generate(prompt=prompt, system_prompt="You are an expert Planner Agent. Output valid JSON only.")
            if not llm_result.is_success:
                return ServiceResult.fail(ForgeError(code="LLM_GENERATION_FAILED", message="Failed to generate plan."))
                
            try:
                plan_data = json.loads(llm_result.data)
            except json.JSONDecodeError as je:
                logger.warning(f"Failed to parse LLM output as JSON: {je}")
                plan_data = {"raw_plan": llm_result.data}
                
            plan_record = {
                "task": task_description,
                "plan": plan_data,
                "status": "planned"
            }
            
            storage_result = self.storage_provider.save("agent_plans", plan_record)
            if not storage_result.is_success:
                return ServiceResult.fail(ForgeError(code="STORAGE_FAILED", message="Failed to store the generated plan."))
                
            return ServiceResult.success({
                "plan_id": storage_result.data.get("id"),
                "plan": plan_data,
                "task": task_description
            })
            
        except Exception as e:
            logger.error(f"Error in PlannerAgent.plan_task: {str(e)}")
            return ServiceResult.fail(ForgeError(code="PLANNER_ERROR", message=f"Planner execution failed: {str(e)}"))

    def decompose_step(self, step_id: str, step_details: Dict[str, Any]) -> ServiceResult[List[Dict[str, Any]]]:
        try:
            prompt = f"Decompose the following step into atomic actions:\nStep ID: {step_id}\nDetails: {json.dumps(step_details)}"
            llm_result = self.llm_service.generate(prompt=prompt, system_prompt="You are a Decomposition Agent. Output a JSON list of atomic actions.")
            
            if not llm_result.is_success:
                 return ServiceResult.fail(ForgeError(code="LLM_DECOMPOSITION_FAILED", message="Failed to decompose step."))
                 
            try:
                actions = json.loads(llm_result.data)
            except json.JSONDecodeError:
                return ServiceResult.fail(ForgeError(code="INVALID_JSON", message="LLM did not return valid JSON list of actions."))
                
            return ServiceResult.success(actions)
            
        except Exception as e:
             logger.error(f"Error in PlannerAgent.decompose_step: {str(e)}")
             return ServiceResult.fail(ForgeError(code="PLANNER_DECOMPOSE_ERROR", message=str(e)))
