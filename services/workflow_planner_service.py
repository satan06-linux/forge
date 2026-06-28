import logging
import uuid
from typing import List, Dict, Any

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class Subtask:
    def __init__(self, name: str, description: str, estimated_cost: float, parallelizable: bool = False):
        self.id = str(uuid.uuid4())
        self.name = name
        self.description = description
        self.estimated_cost = estimated_cost
        self.parallelizable = parallelizable
        self.dependencies: List[str] = []

    def add_dependency(self, subtask_id: str):
        if subtask_id not in self.dependencies:
            self.dependencies.append(subtask_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "estimated_cost": self.estimated_cost,
            "parallelizable": self.parallelizable,
            "dependencies": self.dependencies
        }


class WorkflowPlannerService:
    def __init__(self, container: Any = None):
        self.container = container

    def plan_workflow(self, task_description: str, budget: float = 100.0) -> ServiceResult[Dict[str, Any]]:
        try:
            if not task_description:
                return ServiceResult.fail(ForgeError(code="INVALID_TASK", message="Task description cannot be empty."))
                
            logger.info("Planning workflow for task: %s", task_description[:50])
            
            subtasks = self._deconstruct_task(task_description)
            dag = self._build_dag(subtasks)
            execution_plan = self._map_parallel_execution(dag)
            cost_plan = self._prepare_cost_optimization(subtasks, budget)
            
            if cost_plan["total_cost"] > budget:
                logger.warning("Workflow cost %f exceeds budget %f", cost_plan["total_cost"], budget)
                cost_plan = self._apply_cost_reduction(cost_plan, subtasks, budget)
                
            result = {
                "dag": [task.to_dict() for task in subtasks],
                "execution_plan": execution_plan,
                "cost_optimization_plan": cost_plan
            }
            
            return ServiceResult.success(result)
            
        except Exception as e:
            logger.error("Failed to plan workflow: %s", str(e))
            return ServiceResult.fail(ForgeError(code="WORKFLOW_PLAN_ERROR", message=f"Workflow planning failed: {str(e)}"))

    def _deconstruct_task(self, task_description: str) -> List[Subtask]:
        words = task_description.split()
        subtasks = []
        
        init_task = Subtask("Initialize", "Setup task context and environment", estimated_cost=0.5, parallelizable=False)
        subtasks.append(init_task)
        
        num_tasks = max(1, len(words) // 10)
        num_tasks = min(num_tasks, 5)
        
        prev_task_id = init_task.id
        for i in range(num_tasks):
            is_parallel = (i % 2 == 0)
            t = Subtask(f"Process part {i+1}", f"Process data chunk {i+1}", estimated_cost=2.0, parallelizable=is_parallel)
            t.add_dependency(prev_task_id)
            subtasks.append(t)
            if not is_parallel:
                prev_task_id = t.id
                
        final_task = Subtask("Finalize", "Compile results and cleanup", estimated_cost=1.0, parallelizable=False)
        final_task.add_dependency(subtasks[-1].id)
        subtasks.append(final_task)
        
        return subtasks

    def _build_dag(self, subtasks: List[Subtask]) -> Dict[str, List[str]]:
        dag = {t.id: t.dependencies for t in subtasks}
        return dag

    def _map_parallel_execution(self, dag: Dict[str, List[str]]) -> List[List[str]]:
        in_degree = {node: 0 for node in dag}
        for node, deps in dag.items():
            for dep in deps:
                if dep in in_degree:
                    in_degree[node] += 1
                else:
                    in_degree[node] = 1
                    
        execution_stages = []
        queue = [node for node, deg in in_degree.items() if deg == 0]
        
        while queue:
            execution_stages.append(queue)
            next_queue = []
            for node in queue:
                for target, deps in dag.items():
                    if node in deps:
                        in_degree[target] -= 1
                        if in_degree[target] == 0:
                            next_queue.append(target)
            queue = next_queue
            
        return execution_stages

    def _prepare_cost_optimization(self, subtasks: List[Subtask], budget: float) -> Dict[str, Any]:
        total = sum(t.estimated_cost for t in subtasks)
        return {
            "initial_budget": budget,
            "total_cost": total,
            "cost_breakdown": {t.id: t.estimated_cost for t in subtasks},
            "strategies_applied": []
        }

    def _apply_cost_reduction(self, cost_plan: Dict[str, Any], subtasks: List[Subtask], budget: float) -> Dict[str, Any]:
        reduction_factor = budget / cost_plan["total_cost"]
        new_total = 0.0
        new_breakdown = {}
        for t in subtasks:
            t.estimated_cost *= reduction_factor
            new_breakdown[t.id] = t.estimated_cost
            new_total += t.estimated_cost
            
        cost_plan["total_cost"] = new_total
        cost_plan["cost_breakdown"] = new_breakdown
        cost_plan["strategies_applied"].append("scaled_down_models")
        return cost_plan
