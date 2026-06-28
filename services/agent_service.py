# ForgePrompt Phase 7 — AgentService
import json
import logging
import time
from typing import Any, Dict, List, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class AgentService:
    def __init__(self, container):
        self.container = container

    def create_agent(self, user_id: int, name: str, role: str,
                     organization_id: Optional[int] = None,
                     goals: Optional[str] = None,
                     instructions: Optional[str] = None,
                     preferred_model: str = 'llama3-8b-8192',
                     default_style: str = 'detailed',
                     tools: Optional[List[Dict[str, Any]]] = None,
                     conn=None) -> ServiceResult:
        start_time = time.time()
        try:
            session = conn or self.container.storage_provider.get_session()
            owns_tx = False
            if conn is None:
                session.begin()
                owns_tx = True

            tools_json = json.dumps(tools) if tools else None

            sql = """
                INSERT INTO agents 
                (user_id, organization_id, name, role, goals, instructions, preferred_model, default_style, tools_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            params = (user_id, organization_id, name, role, goals, instructions, preferred_model, default_style, tools_json)
            session.execute(sql, params)
            agent_id = session.lastrowid()

            # Create initial version
            v_sql = """
                INSERT INTO agent_versions (agent_id, version_number, config_snapshot_json, tools_json)
                VALUES (%s, %s, %s, %s)
            """
            config_snapshot = {
                "name": name,
                "role": role,
                "goals": goals,
                "instructions": instructions,
                "preferred_model": preferred_model,
                "default_style": default_style
            }
            session.execute(v_sql, (agent_id, 1, json.dumps(config_snapshot), tools_json))

            if owns_tx:
                session.commit()

            duration_ms = int((time.time() - start_time) * 1000)
            return ServiceResult.ok({"agent_id": agent_id, "version": 1}, duration_ms=duration_ms)
        except Exception as e:
            if 'owns_tx' in locals() and owns_tx and 'session' in locals():
                session.rollback()
            logger.error(f"[AgentService Error] create_agent failed: {str(e)}")
            return ServiceResult.fail(ForgeError(code="AGENT_CREATE_FAILED", message=str(e)))
        finally:
            if 'owns_tx' in locals() and owns_tx and 'session' in locals():
                session.close()

    def get_agent(self, agent_id: int) -> ServiceResult:
        start_time = time.time()
        try:
            sql = "SELECT * FROM agents WHERE id = %s AND deleted_at IS NULL"
            res = self.container.storage_provider.execute_one(sql, (agent_id,))
            if not res:
                return ServiceResult.fail(ForgeError(code="AGENT_NOT_FOUND", message=f"Agent {agent_id} not found"))
            
            duration_ms = int((time.time() - start_time) * 1000)
            return ServiceResult.ok(res, duration_ms=duration_ms)
        except Exception as e:
            logger.error(f"[AgentService Error] get_agent failed: {str(e)}")
            return ServiceResult.fail(ForgeError(code="AGENT_GET_FAILED", message=str(e)))

    def create_agent_run(self, agent_id: int, organization_id: Optional[int] = None,
                         run_id: Optional[int] = None, goal: Optional[str] = None, conn=None) -> ServiceResult:
        start_time = time.time()
        try:
            session = conn or self.container.storage_provider.get_session()
            owns_tx = False
            if conn is None:
                session.begin()
                owns_tx = True

            sql = """
                INSERT INTO agent_runs 
                (agent_id, organization_id, run_id, goal, status)
                VALUES (%s, %s, %s, %s, 'queued')
            """
            params = (agent_id, organization_id, run_id, goal)
            session.execute(sql, params)
            agent_run_id = session.lastrowid()

            if owns_tx:
                session.commit()

            duration_ms = int((time.time() - start_time) * 1000)
            return ServiceResult.ok({"agent_run_id": agent_run_id}, duration_ms=duration_ms)
        except Exception as e:
            if 'owns_tx' in locals() and owns_tx and 'session' in locals():
                session.rollback()
            logger.error(f"[AgentService Error] create_agent_run failed: {str(e)}")
            return ServiceResult.fail(ForgeError(code="AGENT_RUN_CREATE_FAILED", message=str(e)))
        finally:
            if 'owns_tx' in locals() and owns_tx and 'session' in locals():
                session.close()

    def update_agent_run_status(self, agent_run_id: int, status: str, final_output: Optional[str] = None, conn=None) -> ServiceResult:
        start_time = time.time()
        try:
            session = conn or self.container.storage_provider.get_session()
            owns_tx = False
            if conn is None:
                session.begin()
                owns_tx = True

            updates = ["status = %s"]
            params = [status]
            
            if final_output is not None:
                updates.append("final_output = %s")
                params.append(final_output)

            if status in ('completed', 'failed', 'cancelled'):
                updates.append("completed_at = CURRENT_TIMESTAMP")
            elif status == 'executing':
                updates.append("started_at = CURRENT_TIMESTAMP")

            params.append(agent_run_id)

            sql = f"UPDATE agent_runs SET {', '.join(updates)} WHERE id = %s"
            session.execute(sql, tuple(params))

            if owns_tx:
                session.commit()

            duration_ms = int((time.time() - start_time) * 1000)
            return ServiceResult.ok(True, duration_ms=duration_ms)
        except Exception as e:
            if 'owns_tx' in locals() and owns_tx and 'session' in locals():
                session.rollback()
            logger.error(f"[AgentService Error] update_agent_run_status failed: {str(e)}")
            return ServiceResult.fail(ForgeError(code="AGENT_RUN_UPDATE_FAILED", message=str(e)))
        finally:
            if 'owns_tx' in locals() and owns_tx and 'session' in locals():
                session.close()

    def get_agent_run(self, agent_run_id: int) -> ServiceResult:
        start_time = time.time()
        try:
            sql = "SELECT * FROM agent_runs WHERE id = %s"
            res = self.container.storage_provider.execute_one(sql, (agent_run_id,))
            if not res:
                return ServiceResult.fail(ForgeError(code="AGENT_RUN_NOT_FOUND", message=f"Agent run {agent_run_id} not found"))
            
            duration_ms = int((time.time() - start_time) * 1000)
            return ServiceResult.ok(res, duration_ms=duration_ms)
        except Exception as e:
            logger.error(f"[AgentService Error] get_agent_run failed: {str(e)}")
            return ServiceResult.fail(ForgeError(code="AGENT_RUN_GET_FAILED", message=str(e)))
