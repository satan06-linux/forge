# ForgePrompt Phase 7 — AgentPlanner

import time
import uuid
import logging
from typing import List, Dict, Any, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class AgentPlanner:
    def __init__(self, container):
        self.container = container
        self.goals: List[Dict[str, Any]] = []

    def set_main_goal(self, goal_description: str) -> ServiceResult:
        try:
            goal = {
                "id": str(uuid.uuid4()),
                "description": goal_description,
                "status": "pending",
                "subgoals": [],
                "created_at": time.time(),
                "updated_at": time.time()
            }
            self.goals.append(goal)
            return ServiceResult.success(data=goal)
        except Exception as e:
            logger.error(f"[AgentPlanner Error] set_main_goal failed: {str(e)}")
            return ServiceResult.fail(error_code="SET_GOAL_FAILED", message=str(e))

    def decompose_goal(self, goal_id: str, subgoals: List[str]) -> ServiceResult:
        try:
            goal = self._find_goal(goal_id)
            if not goal:
                raise ForgeError(f"Goal {goal_id} not found")
            
            for sg_desc in subgoals:
                sg = {
                    "id": str(uuid.uuid4()),
                    "description": sg_desc,
                    "status": "pending",
                    "created_at": time.time(),
                    "updated_at": time.time()
                }
                goal["subgoals"].append(sg)
            goal["updated_at"] = time.time()
            return ServiceResult.success(data=goal)
        except ForgeError as fe:
            logger.error(f"[AgentPlanner Error] decompose_goal failed: {str(fe)}")
            return ServiceResult.fail(error_code="GOAL_NOT_FOUND", message=str(fe))
        except Exception as e:
            logger.error(f"[AgentPlanner Error] decompose_goal failed: {str(e)}")
            return ServiceResult.fail(error_code="DECOMPOSE_FAILED", message=str(e))

    def generate_react_plan(self, goal_id: str) -> ServiceResult:
        try:
            goal = self._find_goal(goal_id)
            if not goal:
                raise ForgeError(f"Goal {goal_id} not found")
            
            plan = []
            for sg in goal.get("subgoals", []):
                # Standard ReAct structure for each subgoal
                plan.append({
                    "step_id": str(uuid.uuid4()),
                    "step_type": "thought",
                    "content": f"Analyze requirement for subgoal: {sg['description']}",
                    "subgoal_id": sg["id"],
                    "status": "pending"
                })
                plan.append({
                    "step_id": str(uuid.uuid4()),
                    "step_type": "action",
                    "content": "Execute necessary tool for subgoal",
                    "subgoal_id": sg["id"],
                    "status": "pending"
                })
                plan.append({
                    "step_id": str(uuid.uuid4()),
                    "step_type": "observation",
                    "content": "Review tool execution results",
                    "subgoal_id": sg["id"],
                    "status": "pending"
                })
            
            return ServiceResult.success(data=plan)
        except ForgeError as fe:
            logger.error(f"[AgentPlanner Error] generate_react_plan failed: {str(fe)}")
            return ServiceResult.fail(error_code="GOAL_NOT_FOUND", message=str(fe))
        except Exception as e:
            logger.error(f"[AgentPlanner Error] generate_react_plan failed: {str(e)}")
            return ServiceResult.fail(error_code="PLAN_GENERATION_FAILED", message=str(e))

    def update_subgoal_status(self, goal_id: str, subgoal_id: str, status: str) -> ServiceResult:
        try:
            goal = self._find_goal(goal_id)
            if not goal:
                raise ForgeError(f"Goal {goal_id} not found")
            
            for sg in goal["subgoals"]:
                if sg["id"] == subgoal_id:
                    sg["status"] = status
                    sg["updated_at"] = time.time()
                    goal["updated_at"] = time.time()
                    return ServiceResult.success(data=sg)
            
            raise ForgeError(f"Subgoal {subgoal_id} not found")
        except ForgeError as fe:
            logger.error(f"[AgentPlanner Error] update_subgoal_status failed: {str(fe)}")
            return ServiceResult.fail(error_code="SUBGOAL_NOT_FOUND", message=str(fe))
        except Exception as e:
            logger.error(f"[AgentPlanner Error] update_subgoal_status failed: {str(e)}")
            return ServiceResult.fail(error_code="UPDATE_STATUS_FAILED", message=str(e))

    def _find_goal(self, goal_id: str) -> Optional[Dict[str, Any]]:
        for g in self.goals:
            if g["id"] == goal_id:
                return g
        return None
