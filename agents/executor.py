# ForgePrompt Phase 7 — AgentExecutor
import json
import logging
import time
from typing import Any, Dict, List, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class AgentExecutor:
    """
    Run driver managing the agent run loop, streaming output, backpressure, 
    and streaming checkpoints.
    """
    def __init__(self, container):
        self.container = container

    def execute_run(self, agent_run_id: int) -> ServiceResult:
        """
        Main run loop for an agent. 
        Resumes from checkpoints if interrupted.
        """
        start_time = time.time()
        
        try:
            # 1. Fetch the run info
            run_res = self.container.agent_service.get_agent_run(agent_run_id)
            if not run_res.success:
                return run_res
            run_info = run_res.data
            
            if run_info['status'] not in ('queued', 'executing', 'waiting'):
                return ServiceResult.fail(ForgeError(code="INVALID_RUN_STATE", message=f"Run {agent_run_id} is in terminal state"))

            # Update state to executing
            self.container.agent_service.update_agent_run_status(agent_run_id, 'executing')
            
            # 2. Get streaming checkpoint to resume
            checkpoint = self._get_checkpoint(run_info['run_id'] or agent_run_id, f"agent_{agent_run_id}")
            current_step = 0
            if checkpoint:
                current_step = checkpoint.get('stream_offset', 0)
                
            max_iterations = run_info.get('max_iterations', 20)
            final_result = None

            # 3. Agent Run Loop
            while current_step < max_iterations:
                step_start = time.time()
                
                # Fetch pending tasks or decide next action
                task_res = self._get_next_task(agent_run_id, current_step)
                if not task_res.success:
                    break
                    
                task = task_res.data
                if not task:
                    # No more tasks, we consider it done
                    final_result = "Agent finished all tasks successfully."
                    break

                # Execute step (simulate interaction with tools/llm)
                try:
                    result = self._execute_task(task)
                    self._mark_task_completed(task['id'], result)
                except Exception as e:
                    self._mark_task_failed(task['id'], str(e))
                    final_result = f"Failed at step {current_step}: {str(e)}"
                    break
                
                current_step += 1
                
                # Save streaming checkpoint
                self._save_checkpoint(
                    run_info['run_id'] or agent_run_id, 
                    f"agent_{agent_run_id}", 
                    current_step, 
                    json.dumps({"last_task_id": task['id'], "result": result})
                )
                
                # Simulate backpressure/yield
                time.sleep(0.01)

            if current_step >= max_iterations:
                final_result = "Max iterations reached."

            # Mark run completed
            self.container.agent_service.update_agent_run_status(
                agent_run_id, 
                'completed' if current_step < max_iterations else 'failed',
                final_output=final_result
            )

            duration_ms = int((time.time() - start_time) * 1000)
            return ServiceResult.ok({"final_result": final_result, "iterations": current_step}, duration_ms=duration_ms)

        except Exception as e:
            logger.error(f"[AgentExecutor Error] execute_run failed: {str(e)}")
            self.container.agent_service.update_agent_run_status(agent_run_id, 'failed', final_output=str(e))
            return ServiceResult.fail(ForgeError(code="AGENT_EXECUTION_FAILED", message=str(e)))

    def _get_checkpoint(self, run_id: int, node_id: str) -> Optional[Dict]:
        sql = "SELECT * FROM streaming_checkpoints WHERE run_id = %s AND node_id = %s"
        return self.container.storage_provider.execute_one(sql, (run_id, node_id))

    def _save_checkpoint(self, run_id: int, node_id: str, offset: int, partial: str):
        sql = """
            INSERT INTO streaming_checkpoints (run_id, node_id, stream_offset, partial_output)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE stream_offset = VALUES(stream_offset), partial_output = VALUES(partial_output), updated_at = CURRENT_TIMESTAMP
        """
        self.container.storage_provider.update(sql, (run_id, node_id, offset, partial))

    def _get_next_task(self, agent_run_id: int, step_index: int) -> ServiceResult:
        sql = "SELECT * FROM agent_tasks WHERE agent_run_id = %s AND step_index = %s AND status = 'pending' ORDER BY id ASC LIMIT 1"
        task = self.container.storage_provider.execute_one(sql, (agent_run_id, step_index))
        return ServiceResult.ok(task)

    def _execute_task(self, task: Dict) -> str:
        # In real life, calls LLM or Tool
        return f"Executed {task['task_description']}"

    def _mark_task_completed(self, task_id: int, result: str):
        sql = "UPDATE agent_tasks SET status = 'completed', result_json = %s WHERE id = %s"
        self.container.storage_provider.update(sql, (json.dumps({"output": result}), task_id))

    def _mark_task_failed(self, task_id: int, error_msg: str):
        sql = "UPDATE agent_tasks SET status = 'failed', result_json = %s WHERE id = %s"
        self.container.storage_provider.update(sql, (json.dumps({"error": error_msg}), task_id))
