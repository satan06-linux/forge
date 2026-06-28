# ForgePrompt Phase 7 — AdmissionController
import time
import json
from typing import Dict, Any

from services.service_result import ServiceResult
from services.errors import ForgeError

class AdmissionController:
    def __init__(self, container):
        self.container = container
        self.storage_provider = container.get('storage_provider')
        
        self.global_queue_limit = 10000 

    def initialize_schema(self, conn=None) -> ServiceResult:
        managed_conn = False
        if conn is None:
            conn = self.storage_provider.get_session()
            managed_conn = True
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS admission_rules (
                    rule_id VARCHAR(64) PRIMARY KEY,
                    organization_id VARCHAR(64) NOT NULL,
                    max_concurrent_requests INT DEFAULT 100,
                    requests_per_minute INT DEFAULT 600,
                    is_active BOOLEAN DEFAULT TRUE
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)
            
            cursor.execute("""
                SELECT COUNT(1) as count 
                FROM INFORMATION_SCHEMA.STATISTICS 
                WHERE table_schema = DATABASE() 
                AND table_name = 'admission_rules' 
                AND index_name = 'idx_org_rule'
            """)
            if cursor.fetchone()['count'] == 0:
                cursor.execute("CREATE INDEX idx_org_rule ON admission_rules(organization_id)")
                
            if managed_conn:
                conn.commit()
            return ServiceResult.success(True)
        except Exception as e:
            if managed_conn:
                conn.rollback()
            return ServiceResult.fail(ForgeError(code="ADMISSION_INIT_FAILED", message=str(e)))
        finally:
            if managed_conn and conn:
                conn.close()

    def evaluate_request(self, organization_id: str, request_size: int, conn=None) -> ServiceResult:
        start_time = time.time()
        managed_conn = False
        if conn is None:
            conn = self.storage_provider.get_session()
            managed_conn = True
            
        try:
            cursor = conn.cursor(dictionary=True)
            
            # Global capacity check
            cursor.execute("SELECT COUNT(*) as q_count FROM worker_queues WHERE status = 'PENDING'")
            row = cursor.fetchone()
            if row and row['q_count'] >= self.global_queue_limit:
                return ServiceResult.fail(ForgeError(
                    code="SYSTEM_SATURATED", 
                    message="Global queue limit reached. Please try again later."
                ))
                
            # Tenant specific rules
            cursor.execute("SELECT * FROM admission_rules WHERE organization_id = %s AND is_active = TRUE", (organization_id,))
            rule = cursor.fetchone()
            
            if rule:
                # Mock threshold checks using current queue limits.
                # In full implementation, this uses a rate limiting store (e.g. Redis).
                pass
                
            return ServiceResult.success({"admitted": True}, duration_ms=(time.time() - start_time)*1000)
            
        except Exception as e:
            return ServiceResult.success({"admitted": True, "warning": str(e)}, duration_ms=(time.time() - start_time)*1000)
        finally:
            if managed_conn and conn:
                conn.close()

    def set_organization_rule(self, organization_id: str, max_concurrent: int, rpm: int, conn=None) -> ServiceResult:
        start_time = time.time()
        managed_conn = False
        if conn is None:
            conn = self.storage_provider.get_session()
            managed_conn = True
            
        try:
            cursor = conn.cursor()
            rule_id = f"rule_{organization_id}"
            cursor.execute("""
                INSERT INTO admission_rules (rule_id, organization_id, max_concurrent_requests, requests_per_minute, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
                ON DUPLICATE KEY UPDATE
                max_concurrent_requests = %s, requests_per_minute = %s, is_active = TRUE
            """, (rule_id, organization_id, max_concurrent, rpm, max_concurrent, rpm))
            
            if managed_conn:
                conn.commit()
            return ServiceResult.success({"rule_id": rule_id}, duration_ms=(time.time() - start_time)*1000)
        except Exception as e:
            if managed_conn:
                conn.rollback()
            return ServiceResult.fail(ForgeError(code="RULE_SET_FAILED", message=str(e)))
        finally:
            if managed_conn and conn:
                conn.close()
