# ForgePrompt Phase 7 — TenantIsolationService
import logging
from typing import Optional

from services.service_result import ServiceResult
from services.errors import ForgeError, QuotaExceededError

logger = logging.getLogger(__name__)

class TenantIsolationService:
    """
    Context isolation levels and quota boundary enforcement.
    """
    def __init__(self, container):
        self.container = container

    def check_quota(self, organization_id: int, resource_type: str, required_amount: float = 1.0, conn=None) -> ServiceResult:
        """
        Check if an organization has enough quota for a specific resource type.
        Resource types: 'worker_slots', 'gpu_slots', 'storage_gb', 'api_calls_per_hour', 'memory_mb'
        """
        try:
            sp = self.container.get('storage_provider')
            
            sql = """
                SELECT q.*, r.current_cpu_pct, r.current_memory_mb, r.active_worker_slots, r.active_gpu_slots, r.storage_used_gb
                FROM tenant_resource_quotas q
                LEFT JOIN tenant_resource_usage_realtime r ON q.organization_id = r.organization_id
                WHERE q.organization_id = %s
            """
            
            if conn:
                conn.execute(sql, (organization_id,))
                row = conn.fetchone()
            else:
                row = sp.execute_one(sql, (organization_id,))

            if not row:
                # If no quota configured, fail closed or default to some safe limits. Here we fail closed.
                return ServiceResult.fail(ForgeError("Tenant quota not configured", error_code="NOT_FOUND"))

            # Logic based on resource type
            if resource_type == 'worker_slots':
                used = row.get('active_worker_slots') or 0
                allowed = row.get('max_worker_slots') or 10
                if used + required_amount > allowed:
                    return ServiceResult.fail(QuotaExceededError(f"Worker slot quota exceeded. Max: {allowed}, Used: {used}"))

            elif resource_type == 'gpu_slots':
                used = row.get('active_gpu_slots') or 0
                allowed = row.get('max_gpu_slots') or 0
                if used + required_amount > allowed:
                    return ServiceResult.fail(QuotaExceededError(f"GPU slot quota exceeded. Max: {allowed}, Used: {used}"))

            elif resource_type == 'storage_gb':
                used = row.get('storage_used_gb') or 0
                allowed = row.get('max_storage_gb') or 10
                if float(used) + required_amount > float(allowed):
                    return ServiceResult.fail(QuotaExceededError(f"Storage quota exceeded. Max: {allowed}GB, Used: {used}GB"))

            elif resource_type == 'memory_mb':
                used = row.get('current_memory_mb') or 0
                allowed = row.get('max_memory_mb') or 2048
                if float(used) + required_amount > float(allowed):
                    return ServiceResult.fail(QuotaExceededError(f"Memory quota exceeded. Max: {allowed}MB, Used: {used}MB"))

            return ServiceResult.ok(data={"allowed": True})
        except Exception as e:
            logger.error(f"[TenantIsolationService Error] Quota check failed: {e}")
            return ServiceResult.fail(ForgeError(f"Quota check failed: {str(e)}", error_code="ISOLATION_ERROR"))

    def update_usage(self, organization_id: int, resource_type: str, delta: float, conn=None) -> ServiceResult:
        """
        Update the current usage of a given resource type.
        """
        try:
            sp = self.container.get('storage_provider')
            
            # Map resource types to DB columns
            col_map = {
                'worker_slots': 'active_worker_slots',
                'gpu_slots': 'active_gpu_slots',
                'storage_gb': 'storage_used_gb',
                'memory_mb': 'current_memory_mb'
            }
            
            if resource_type not in col_map:
                return ServiceResult.fail(ForgeError(f"Unknown resource type: {resource_type}"))
                
            col_name = col_map[resource_type]
            
            sql = f"""
                INSERT INTO tenant_resource_usage_realtime (organization_id, {col_name})
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE {col_name} = GREATEST(0, {col_name} + %s)
            """
            params = (organization_id, delta, delta)
            
            if conn:
                conn.execute(sql, params)
            else:
                sp.update(sql, params)
                
            return ServiceResult.ok()
        except Exception as e:
            logger.error(f"[TenantIsolationService Error] Usage update failed: {e}")
            return ServiceResult.fail(ForgeError(f"Usage update failed: {str(e)}", error_code="ISOLATION_ERROR"))

    def assert_context_isolation(self, active_tenant_id: int, resource_tenant_id: int) -> ServiceResult:
        """
        Ensure the active context matches the target resource tenant.
        """
        if active_tenant_id != resource_tenant_id:
            logger.warning(f"Tenant isolation breach attempt. Active: {active_tenant_id}, Target: {resource_tenant_id}")
            return ServiceResult.fail(ForgeError("Cross-tenant access denied", error_code="ISOLATION_BREACH"))
        return ServiceResult.ok()
