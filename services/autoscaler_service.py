# ForgePrompt Phase 7 — AutoscalerService
import time
import json
from typing import Dict, Any, List

from services.service_result import ServiceResult
from services.errors import ForgeError

class AutoscalerService:
    def __init__(self, container):
        self.container = container
        self.storage_provider = container.get('storage_provider')
        self.external_effect_ledger = container.get('external_effect_ledger', None)

    def initialize_schema(self, conn=None) -> ServiceResult:
        managed_conn = False
        if conn is None:
            conn = self.storage_provider.get_session()
            managed_conn = True
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS autoscaling_policies (
                    pool_id VARCHAR(64) PRIMARY KEY,
                    min_workers INT DEFAULT 1,
                    max_workers INT DEFAULT 10,
                    target_cpu_utilization FLOAT DEFAULT 70.0,
                    scale_up_cool_down BIGINT DEFAULT 60000,
                    scale_down_cool_down BIGINT DEFAULT 120000,
                    last_scaled_at BIGINT DEFAULT 0
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS external_effect_ledger (
                    effect_id VARCHAR(64) PRIMARY KEY,
                    effect_type VARCHAR(64) NOT NULL,
                    target VARCHAR(128) NOT NULL,
                    payload LONGTEXT,
                    status VARCHAR(32) NOT NULL,
                    created_at BIGINT
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)
            
            if managed_conn:
                conn.commit()
            return ServiceResult.success(True)
        except Exception as e:
            if managed_conn:
                conn.rollback()
            return ServiceResult.fail(ForgeError(code="AUTOSCALER_INIT_FAILED", message=str(e)))
        finally:
            if managed_conn and conn:
                conn.close()

    def register_policy(self, pool_id: str, min_workers: int, max_workers: int, target_cpu: float, conn=None) -> ServiceResult:
        start_time = time.time()
        managed_conn = False
        if conn is None:
            conn = self.storage_provider.get_session()
            managed_conn = True
            
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO autoscaling_policies (pool_id, min_workers, max_workers, target_cpu_utilization, last_scaled_at)
                VALUES (%s, %s, %s, %s, 0)
                ON DUPLICATE KEY UPDATE
                min_workers = %s, max_workers = %s, target_cpu_utilization = %s
            """, (pool_id, min_workers, max_workers, target_cpu, min_workers, max_workers, target_cpu))
            
            if managed_conn:
                conn.commit()
            return ServiceResult.success({"pool_id": pool_id}, duration_ms=(time.time() - start_time)*1000)
        except Exception as e:
            if managed_conn:
                conn.rollback()
            return ServiceResult.fail(ForgeError(code="POLICY_REGISTRATION_FAILED", message=str(e)))
        finally:
            if managed_conn and conn:
                conn.close()

    def evaluate_scaling(self, pool_id: str, current_workers: int, avg_cpu_utilization: float, queued_jobs: int, conn=None) -> ServiceResult:
        start_time = time.time()
        managed_conn = False
        if conn is None:
            conn = self.storage_provider.get_session()
            managed_conn = True
            
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM autoscaling_policies WHERE pool_id = %s FOR UPDATE", (pool_id,))
            policy = cursor.fetchone()
            
            if not policy:
                return ServiceResult.fail(ForgeError(code="POLICY_NOT_FOUND", message="Autoscaling policy not found"))
                
            now = int(time.time() * 1000)
            
            predictive_factor = 1.0
            if queued_jobs > current_workers * 5:
                predictive_factor = 1.5
                
            effective_utilization = avg_cpu_utilization * predictive_factor
            
            action = "NONE"
            desired_workers = current_workers
            
            if effective_utilization > policy['target_cpu_utilization']:
                if now - policy['last_scaled_at'] > policy['scale_up_cool_down']:
                    desired_workers = min(policy['max_workers'], current_workers + 1 + int(queued_jobs / 10))
                    if desired_workers > current_workers:
                        action = "SCALE_UP"
            elif effective_utilization < policy['target_cpu_utilization'] - 30.0:
                if now - policy['last_scaled_at'] > policy['scale_down_cool_down']:
                    desired_workers = max(policy['min_workers'], current_workers - 1)
                    if desired_workers < current_workers:
                        action = "SCALE_DOWN"
                        
            if desired_workers < policy['min_workers']:
                desired_workers = policy['min_workers']
                if desired_workers > current_workers:
                    action = "SCALE_UP"
                
            if action != "NONE" and desired_workers != current_workers:
                cursor.execute("UPDATE autoscaling_policies SET last_scaled_at = %s WHERE pool_id = %s", (now, pool_id))
                
                effect_id = f"scale_{pool_id}_{now}"
                cursor.execute("""
                    INSERT INTO external_effect_ledger (effect_id, effect_type, target, payload, status, created_at)
                    VALUES (%s, 'DEPLOYMENT', %s, %s, 'PENDING', %s)
                """, (effect_id, pool_id, json.dumps({"desired_workers": desired_workers, "action": action}), now))
                
                if managed_conn:
                    conn.commit()
                return ServiceResult.success({"action": action, "desired_workers": desired_workers}, duration_ms=(time.time() - start_time)*1000)
            
            if managed_conn:
                conn.rollback()
                
            return ServiceResult.success({"action": "NONE", "desired_workers": current_workers}, duration_ms=(time.time() - start_time)*1000)
            
        except Exception as e:
            if managed_conn:
                conn.rollback()
            return ServiceResult.fail(ForgeError(code="SCALING_EVALUATION_FAILED", message=str(e)))
        finally:
            if managed_conn and conn:
                conn.close()
