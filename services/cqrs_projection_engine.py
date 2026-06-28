# ForgePrompt Phase 7 — CQRSProjectionEngine
from typing import Optional, Dict, Any
from services.service_result import ServiceResult
from services.errors import StorageError, ForgeError
import json

class CQRSProjectionEngine:
    """
    Service responsible for keeping CQRS read models updated based on workflow execution 
    events or explicit sync calls.
    """
    def __init__(self, container):
        self.container = container
        self.storage = container.storage

    def update_workflow_summary(
        self,
        workflow_id: int,
        organization_id: int,
        run_status: str,
        latency_ms: int,
        cost: float,
        tokens: int,
        run_at: str,
        conn: Optional[Any] = None
    ) -> ServiceResult:
        """
        Updates the materialized read model `rm_workflow_summaries`.
        If it doesn't exist, it creates it.
        """
        try:
            sql = """
                INSERT INTO rm_workflow_summaries (
                    workflow_id, organization_id, total_runs, successful_runs, failed_runs,
                    avg_latency_ms, total_cost, total_tokens, last_run_at, last_run_status
                ) VALUES (
                    %s, %s, 1, %s, %s,
                    %s, %s, %s, %s, %s
                )
                ON DUPLICATE KEY UPDATE
                    avg_latency_ms = (avg_latency_ms * total_runs + %s) / (total_runs + 1),
                    total_runs = total_runs + 1,
                    successful_runs = successful_runs + %s,
                    failed_runs = failed_runs + %s,
                    total_cost = total_cost + %s,
                    total_tokens = total_tokens + %s,
                    last_run_at = %s,
                    last_run_status = %s
            """
            
            is_success = 1 if run_status == 'completed' else 0
            is_fail = 1 if run_status == 'failed' else 0

            params = (
                workflow_id, organization_id, is_success, is_fail,
                latency_ms, cost, tokens, run_at, run_status,
                latency_ms, is_success, is_fail,
                cost, tokens, run_at, run_status
            )
            
            if conn:
                conn.cursor.execute(sql, params)
            else:
                with self.storage.transaction() as session:
                    session.cursor.execute(sql, params)
                    
            return ServiceResult.ok()
        except Exception as e:
            return ServiceResult.fail(StorageError(f"[CQRSProjectionEngine Error] Update failed: {str(e)}"))

    def update_agent_summary(
        self, 
        agent_id: int, 
        organization_id: int, 
        run_status: str, 
        cost: float, 
        conn: Optional[Any] = None
    ) -> ServiceResult:
        try:
            sql = """
                INSERT INTO rm_agent_summaries (
                    agent_id, organization_id, total_runs, success_rate, avg_cost
                ) VALUES (
                    %s, %s, 1, %s, %s
                )
                ON DUPLICATE KEY UPDATE
                    success_rate = ((success_rate * total_runs) + %s) / (total_runs + 1),
                    avg_cost = ((avg_cost * total_runs) + %s) / (total_runs + 1),
                    total_runs = total_runs + 1
            """
            is_success = 1.0 if run_status == 'completed' else 0.0
            
            params = (
                agent_id, organization_id, is_success, cost,
                is_success, cost
            )
            
            if conn:
                conn.cursor.execute(sql, params)
            else:
                with self.storage.transaction() as session:
                    session.cursor.execute(sql, params)
                    
            return ServiceResult.ok()
        except Exception as e:
            return ServiceResult.fail(StorageError(f"[CQRSProjectionEngine Error] Agent update failed: {str(e)}"))

    def update_org_dashboard(
        self, 
        organization_id: int, 
        active_runs_delta: int = 0, 
        queued_jobs_delta: int = 0, 
        active_agents_delta: int = 0, 
        dead_letter_delta: int = 0, 
        cost_delta: float = 0, 
        token_delta: int = 0, 
        conn: Optional[Any] = None
    ) -> ServiceResult:
        try:
            sql = """
                INSERT INTO rm_org_dashboards (
                    organization_id, active_runs, queued_jobs, active_agents,
                    dead_letter_count, monthly_cost, monthly_tokens
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s
                )
                ON DUPLICATE KEY UPDATE
                    active_runs = active_runs + %s,
                    queued_jobs = queued_jobs + %s,
                    active_agents = active_agents + %s,
                    dead_letter_count = dead_letter_count + %s,
                    monthly_cost = monthly_cost + %s,
                    monthly_tokens = monthly_tokens + %s
            """
            params = (
                organization_id, active_runs_delta, queued_jobs_delta, active_agents_delta, dead_letter_delta, cost_delta, token_delta,
                active_runs_delta, queued_jobs_delta, active_agents_delta, dead_letter_delta, cost_delta, token_delta
            )
            if conn:
                conn.cursor.execute(sql, params)
            else:
                with self.storage.transaction() as session:
                    session.cursor.execute(sql, params)
            return ServiceResult.ok()
        except Exception as e:
            return ServiceResult.fail(StorageError(f"[CQRSProjectionEngine Error] Org dashboard update failed: {str(e)}"))
