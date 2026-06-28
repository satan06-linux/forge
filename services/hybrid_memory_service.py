import logging
import json
from typing import Dict, Any, List, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError, StorageError, ValidationError

logger = logging.getLogger(__name__)

class HybridMemoryService:
    """
    Multi-tier memory management integrating Conversation, Semantic, Project,
    Team, and Long-term memory stores.
    """
    def __init__(self, container):
        self.container = container
        self._ensure_schema()

    @property
    def storage(self):
        return self.container.get("storage_provider")
        
    def _get_storage(self):
        storage = self.storage
        if not storage:
            raise ForgeError(message="Storage provider not found in container", error_code="STORAGE_UNAVAILABLE")
        return storage

    def _ensure_schema(self):
        """
        Idempotent schema setup for the hybrid_memory table.
        """
        try:
            storage = self._get_storage()
            sql = """
                CREATE TABLE IF NOT EXISTS hybrid_memory (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    memory_tier VARCHAR(50) NOT NULL,
                    entity_id VARCHAR(100) NOT NULL,
                    memory_key VARCHAR(255) NOT NULL,
                    memory_value LONGTEXT,
                    metadata LONGTEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_tier_entity_key (memory_tier, entity_id, memory_key)
                )
            """
            # Using update since it commits and doesn't try to fetch result sets on DDL
            storage.update(sql)
        except Exception as e:
            logger.warning(f"[HybridMemoryService] Could not ensure schema: {e}")

    def save_memory(
        self, 
        memory_tier: str, 
        entity_id: str, 
        key: str, 
        value: Any, 
        metadata: Optional[Dict[str, Any]] = None
    ) -> ServiceResult:
        """
        Saves a memory in the specified tier.
        Valid tiers: conversation, semantic, project, team, long_term.
        """
        valid_tiers = ["conversation", "semantic", "project", "team", "long_term"]
        if memory_tier not in valid_tiers:
            return ServiceResult.fail(ValidationError(f"Invalid memory tier: {memory_tier}"))
            
        if not entity_id or not key:
            return ServiceResult.fail(ValidationError("entity_id and key are required"))
            
        try:
            storage = self._get_storage()
            val_json = json.dumps(value) if value is not None else "null"
            meta_json = json.dumps(metadata) if metadata else "{}"
            
            sql = """
                INSERT INTO hybrid_memory (memory_tier, entity_id, memory_key, memory_value, metadata)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                    memory_value = VALUES(memory_value), 
                    metadata = VALUES(metadata)
            """
            storage.update(sql, (memory_tier, entity_id, key, val_json, meta_json))
            return ServiceResult.ok()
        except Exception as e:
            logger.error(f"[HybridMemoryService] Failed to save memory: {e}", exc_info=True)
            return ServiceResult.fail(StorageError(f"Database error: {str(e)}"))

    def get_memory(self, memory_tier: str, entity_id: str, key: str) -> ServiceResult:
        """
        Retrieves a specific memory by tier, entity_id, and key.
        """
        try:
            storage = self._get_storage()
            sql = """
                SELECT memory_value, metadata FROM hybrid_memory
                WHERE memory_tier = %s AND entity_id = %s AND memory_key = %s
            """
            row = storage.execute_one(sql, (memory_tier, entity_id, key))
            if not row:
                return ServiceResult.ok(data=None)
                
            val = json.loads(row["memory_value"]) if row.get("memory_value") else None
            meta = json.loads(row["metadata"]) if row.get("metadata") else {}
            return ServiceResult.ok(data={"value": val, "metadata": meta})
        except Exception as e:
            logger.error(f"[HybridMemoryService] Failed to get memory: {e}", exc_info=True)
            return ServiceResult.fail(StorageError(f"Database error: {str(e)}"))

    def list_memories(self, memory_tier: str, entity_id: str) -> ServiceResult:
        """
        Lists all memories for a specific tier and entity.
        """
        try:
            storage = self._get_storage()
            sql = """
                SELECT memory_key, memory_value, metadata FROM hybrid_memory
                WHERE memory_tier = %s AND entity_id = %s
            """
            rows = storage.execute(sql, (memory_tier, entity_id))
            
            results = {}
            for r in rows:
                k = r["memory_key"]
                v = json.loads(r["memory_value"]) if r.get("memory_value") else None
                m = json.loads(r["metadata"]) if r.get("metadata") else {}
                results[k] = {"value": v, "metadata": m}
                
            return ServiceResult.ok(data=results)
        except Exception as e:
            logger.error(f"[HybridMemoryService] Failed to list memories: {e}", exc_info=True)
            return ServiceResult.fail(StorageError(f"Database error: {str(e)}"))
            
    def delete_memory(self, memory_tier: str, entity_id: str, key: str) -> ServiceResult:
        """
        Deletes a specific memory.
        """
        try:
            storage = self._get_storage()
            sql = """
                DELETE FROM hybrid_memory
                WHERE memory_tier = %s AND entity_id = %s AND memory_key = %s
            """
            rows_affected = storage.update(sql, (memory_tier, entity_id, key))
            return ServiceResult.ok(data={"deleted": rows_affected > 0})
        except Exception as e:
            logger.error(f"[HybridMemoryService] Failed to delete memory: {e}", exc_info=True)
            return ServiceResult.fail(StorageError(f"Database error: {str(e)}"))

    def consolidate_memories(self, entity_id: str, source_tier: str, target_tier: str, keys: List[str]) -> ServiceResult:
        """
        Moves specified keys from source_tier to target_tier.
        Useful for migrating from short-term conversation memory to long_term memory.
        """
        if not keys:
            return ServiceResult.ok(data={"consolidated_keys": []})

        values_to_move = {}
        for k in keys:
            res = self.get_memory(source_tier, entity_id, k)
            if res.is_error:
                return res
            if res.data:
                values_to_move[k] = res.data
                
        for k, v in values_to_move.items():
            res = self.save_memory(target_tier, entity_id, k, v["value"], v.get("metadata", {}))
            if res.is_error:
                return res
                
        # Clean up source tier
        for k in values_to_move.keys():
            self.delete_memory(source_tier, entity_id, k)
        return ServiceResult.ok(data={"consolidated_keys": list(values_to_move.keys())})

__all__ = ["HybridMemoryService"]
