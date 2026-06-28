import logging
import uuid
import datetime
from typing import Any, Dict

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class HumanApprovalService:
    def __init__(self, container: Any):
        self.container = container
        self.storage_provider = self.container.get('StorageProvider') if hasattr(self.container, 'get') else None

    def request_approval(self, action_type: str, payload: Dict[str, Any], risk_score: float) -> ServiceResult:
        try:
            approval_id = str(uuid.uuid4())
            approval_record = {
                "approval_id": approval_id,
                "action_type": action_type,
                "payload": payload,
                "risk_score": risk_score,
                "status": "pending",
                "requested_at": datetime.datetime.utcnow().isoformat(),
                "reviewer_id": None,
                "resolved_at": None
            }
            
            if self.storage_provider:
                self.storage_provider.save("human_approvals", approval_id, approval_record)
                
            return ServiceResult.success(approval_record)
        except Exception as e:
            logger.error(f"Failed to request approval: {str(e)}")
            return ServiceResult.fail(ForgeError(code="APPROVAL_REQUEST_FAILED", message=f"Failed to request approval: {str(e)}"))

    def get_pending_approvals(self) -> ServiceResult:
        try:
            if not self.storage_provider:
                return ServiceResult.fail(ForgeError(code="STORAGE_UNAVAILABLE", message="Storage provider not found."))
            
            all_records = self.storage_provider.list("human_approvals")
            pending_records = [r for r in all_records if r.get("status") == "pending"]
            
            # Sort by risk score descending, then by timestamp
            pending_records.sort(key=lambda x: (x.get("risk_score", 0), x.get("requested_at", "")), reverse=True)
            
            return ServiceResult.success(pending_records)
        except Exception as e:
            logger.error(f"Failed to fetch pending approvals: {str(e)}")
            return ServiceResult.fail(ForgeError(code="APPROVAL_FETCH_FAILED", message=f"Failed to fetch pending approvals: {str(e)}"))

    def resolve_approval(self, approval_id: str, approved: bool, reviewer_id: str) -> ServiceResult:
        try:
            if not self.storage_provider:
                return ServiceResult.fail(ForgeError(code="STORAGE_UNAVAILABLE", message="Storage provider not found."))
            
            record = self.storage_provider.get("human_approvals", approval_id)
            if not record:
                return ServiceResult.fail(ForgeError(code="APPROVAL_NOT_FOUND", message=f"Approval request {approval_id} not found."))
            
            if record["status"] != "pending":
                return ServiceResult.fail(ForgeError(code="APPROVAL_ALREADY_RESOLVED", message=f"Approval request {approval_id} is already resolved as {record['status']}."))
            
            record["status"] = "approved" if approved else "rejected"
            record["reviewer_id"] = reviewer_id
            record["resolved_at"] = datetime.datetime.utcnow().isoformat()
            
            self.storage_provider.save("human_approvals", approval_id, record)
            
            return ServiceResult.success(record)
        except Exception as e:
            logger.error(f"Failed to resolve approval: {str(e)}")
            return ServiceResult.fail(ForgeError(code="APPROVAL_RESOLUTION_FAILED", message=f"Failed to resolve approval: {str(e)}"))
