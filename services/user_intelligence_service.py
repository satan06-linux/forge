import logging
import hashlib
import datetime
import uuid
from typing import Any, Dict, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class UserIntelligenceService:
    def __init__(self, container: Any):
        self.container = container
        self.storage_provider = self.container.get('StorageProvider') if hasattr(self.container, 'get') else None
        # In a real environment, this salt should come from secure configuration
        self.salt = "forge_secure_salt_789"

    def track_user_activity(self, ip_address: str, user_id: Optional[str] = None, action: str = "page_view") -> ServiceResult:
        try:
            device_hash = self._hash_ip(ip_address)
            
            activity_record = {
                "activity_id": str(uuid.uuid4()),
                "device_hash": device_hash,
                "user_id": user_id,
                "is_anonymous": user_id is None,
                "action": action,
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            
            if self.storage_provider:
                self.storage_provider.save("user_intelligence", activity_record["activity_id"], activity_record)
            
            return ServiceResult.success(activity_record)
        except Exception as e:
            logger.error(f"Failed to track user activity: {str(e)}")
            return ServiceResult.fail(ForgeError(code="USER_TRACKING_FAILED", message=f"Failed to track user activity: {str(e)}"))

    def get_retention_metrics(self) -> ServiceResult:
        try:
            if not self.storage_provider:
                return ServiceResult.fail(ForgeError(code="STORAGE_UNAVAILABLE", message="Storage provider not found."))
            
            all_records = self.storage_provider.list("user_intelligence")
            
            # Simple retention metric calculation (unique devices and users)
            unique_devices = set(r.get("device_hash") for r in all_records if r.get("device_hash"))
            unique_logged_in_users = set(r.get("user_id") for r in all_records if r.get("user_id") is not None)
            total_actions = len(all_records)
            anonymous_actions = sum(1 for r in all_records if r.get("is_anonymous"))
            
            metrics = {
                "total_unique_devices": len(unique_devices),
                "total_logged_in_users": len(unique_logged_in_users),
                "total_actions": total_actions,
                "anonymous_actions": anonymous_actions,
                "logged_in_actions": total_actions - anonymous_actions,
                "retention_score": self._calculate_retention_score(all_records)
            }
            
            return ServiceResult.success(metrics)
        except Exception as e:
            logger.error(f"Failed to calculate retention metrics: {str(e)}")
            return ServiceResult.fail(ForgeError(code="RETENTION_METRICS_FAILED", message=f"Failed to calculate retention metrics: {str(e)}"))

    def _hash_ip(self, ip_address: str) -> str:
        # Privacy-conscious hashing with salt to avoid reversing IP addresses
        raw_string = f"{self.salt}:{ip_address}"
        return hashlib.sha256(raw_string.encode('utf-8')).hexdigest()

    def _calculate_retention_score(self, records: list) -> float:
        # Dummy retention logic: fraction of users seen more than once
        if not records:
            return 0.0
        
        counts = {}
        for r in records:
            identifier = r.get("user_id") or r.get("device_hash")
            counts[identifier] = counts.get(identifier, 0) + 1
            
        retained = sum(1 for c in counts.values() if c > 1)
        total = len(counts)
        
        return retained / total if total > 0 else 0.0
