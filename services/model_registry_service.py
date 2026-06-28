import logging
import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class ModelRegistryService:
    """
    Dynamic registry maintaining capabilities, languages, context length, 
    latency, availability, and health records for all models.
    """
    def __init__(self, container: Any):
        self.storage = container.get('StorageProvider')
        self.prefix = "model_registry:"

    def _get_key(self, model_id: str) -> str:
        return f"{self.prefix}{model_id}"

    def _get_data(self, key: str) -> Optional[str]:
        if hasattr(self.storage, 'get'):
            return self.storage.get(key)
        if hasattr(self.storage, 'read'):
            return self.storage.read(key)
        return None

    def _set_data(self, key: str, value: str) -> None:
        if hasattr(self.storage, 'set'):
            self.storage.set(key, value)
        elif hasattr(self.storage, 'write'):
            self.storage.write(key, value)

    def _list_keys(self, prefix: str) -> List[str]:
        if hasattr(self.storage, 'list_keys'):
            return self.storage.list_keys(prefix)
        if hasattr(self.storage, 'keys'):
            keys_attr = self.storage.keys
            if callable(keys_attr):
                return [k for k in keys_attr() if k.startswith(prefix)]
            return [k for k in keys_attr if k.startswith(prefix)]
        if hasattr(self.storage, 'list'):
            return self.storage.list(prefix)
        return []

    def register_model(self, model_id: str, capabilities: List[str], languages: List[str], 
                       context_length: int, base_latency: float) -> ServiceResult[Dict[str, Any]]:
        try:
            model_data = {
                "model_id": model_id,
                "capabilities": capabilities,
                "languages": languages,
                "context_length": context_length,
                "base_latency": base_latency,
                "availability": 1.0,
                "health_records": [],
                "is_active": True,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
            self._set_data(self._get_key(model_id), json.dumps(model_data))
            logger.info(f"Successfully registered model: {model_id}")
            return ServiceResult.success(model_data)
        except Exception as e:
            logger.error(f"Failed to register model {model_id}: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="REGISTRY_ERROR", message=f"Registration failed: {str(e)}"))

    def get_model(self, model_id: str) -> ServiceResult[Dict[str, Any]]:
        try:
            data = self._get_data(self._get_key(model_id))
            if not data:
                return ServiceResult.fail(ForgeError(code="MODEL_NOT_FOUND", message=f"Model {model_id} not found."))
            return ServiceResult.success(json.loads(data))
        except Exception as e:
            logger.error(f"Failed to fetch model {model_id}: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="REGISTRY_ERROR", message=f"Fetch failed: {str(e)}"))

    def update_model(self, model_id: str, update_data: Dict[str, Any]) -> ServiceResult[Dict[str, Any]]:
        get_res = self.get_model(model_id)
        if not get_res.is_success:
            return get_res

        try:
            model = get_res.value
            model.update(update_data)
            model["updated_at"] = datetime.utcnow().isoformat()
            self._set_data(self._get_key(model_id), json.dumps(model))
            logger.info(f"Updated model: {model_id}")
            return ServiceResult.success(model)
        except Exception as e:
            logger.error(f"Failed to update model {model_id}: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="REGISTRY_ERROR", message=f"Update failed: {str(e)}"))

    def list_models(self) -> ServiceResult[List[Dict[str, Any]]]:
        try:
            keys = self._list_keys(self.prefix)
            models = []
            for k in keys:
                data = self._get_data(k)
                if data:
                    models.append(json.loads(data))
            return ServiceResult.success(models)
        except Exception as e:
            logger.error(f"Failed to list models: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="REGISTRY_ERROR", message=f"List failed: {str(e)}"))
