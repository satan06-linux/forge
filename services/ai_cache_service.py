import logging
import hashlib
import json
from typing import Optional, Dict, Any
from services.service_result import ServiceResult
from services.errors import ForgeError
from core.dependency_injection import container
from core.database import get_db_connection

logger = logging.getLogger(__name__)

class AICacheService:
    def __init__(self):
        pass

    def _generate_hash(self, data: Any) -> str:
        if isinstance(data, dict):
            data_str = json.dumps(data, sort_keys=True)
        else:
            data_str = str(data)
        return hashlib.sha256(data_str.encode('utf-8')).hexdigest()

    def get_prompt_cache(self, prompt: str) -> ServiceResult[Optional[str]]:
        try:
            prompt_hash = self._generate_hash(prompt)
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT response FROM prompt_cache WHERE prompt_hash = ?",
                    (prompt_hash,)
                )
                row = cursor.fetchone()
                if row:
                    return ServiceResult.success(row[0])
                return ServiceResult.success(None)
        except Exception as e:
            logger.error(f"Error retrieving prompt cache: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="CACHE_ERROR", message=f"Failed to get prompt cache: {e}"))

    def set_prompt_cache(self, prompt: str, response: str) -> ServiceResult[bool]:
        try:
            prompt_hash = self._generate_hash(prompt)
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO prompt_cache (prompt_hash, prompt_text, response)
                    VALUES (?, ?, ?)
                    ON CONFLICT(prompt_hash) DO UPDATE SET response=excluded.response
                    """,
                    (prompt_hash, prompt, response)
                )
                conn.commit()
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"Error setting prompt cache: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="CACHE_ERROR", message=f"Failed to set prompt cache: {e}"))

    def get_embedding_cache(self, text: str) -> ServiceResult[Optional[list[float]]]:
        try:
            text_hash = self._generate_hash(text)
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT embedding FROM embedding_cache WHERE text_hash = ?",
                    (text_hash,)
                )
                row = cursor.fetchone()
                if row:
                    embedding = json.loads(row[0])
                    return ServiceResult.success(embedding)
                return ServiceResult.success(None)
        except Exception as e:
            logger.error(f"Error retrieving embedding cache: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="CACHE_ERROR", message=f"Failed to get embedding cache: {e}"))

    def set_embedding_cache(self, text: str, embedding: list[float]) -> ServiceResult[bool]:
        try:
            text_hash = self._generate_hash(text)
            embedding_json = json.dumps(embedding)
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO embedding_cache (text_hash, text_content, embedding)
                    VALUES (?, ?, ?)
                    ON CONFLICT(text_hash) DO UPDATE SET embedding=excluded.embedding
                    """,
                    (text_hash, text, embedding_json)
                )
                conn.commit()
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"Error setting embedding cache: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="CACHE_ERROR", message=f"Failed to set embedding cache: {e}"))

    def get_semantic_cache(self, embedding: list[float], threshold: float = 0.95) -> ServiceResult[Optional[str]]:
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT response FROM semantic_cache ORDER BY vec_distance(embedding, ?) ASC LIMIT 1",
                    (json.dumps(embedding),)
                )
                row = cursor.fetchone()
                if row:
                    return ServiceResult.success(row[0])
                return ServiceResult.success(None)
        except Exception as e:
            logger.error(f"Error retrieving semantic cache: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="CACHE_ERROR", message=f"Failed to get semantic cache: {e}"))

    def set_semantic_cache(self, prompt: str, embedding: list[float], response: str) -> ServiceResult[bool]:
        try:
            prompt_hash = self._generate_hash(prompt)
            embedding_json = json.dumps(embedding)
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO semantic_cache (prompt_hash, prompt, embedding, response)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(prompt_hash) DO UPDATE SET response=excluded.response, embedding=excluded.embedding
                    """,
                    (prompt_hash, prompt, embedding_json, response)
                )
                conn.commit()
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"Error setting semantic cache: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="CACHE_ERROR", message=f"Failed to set semantic cache: {e}"))

    def get_tool_cache(self, tool_name: str, parameters: Dict[str, Any]) -> ServiceResult[Optional[Any]]:
        try:
            cache_key = self._generate_hash({"tool_name": tool_name, "parameters": parameters})
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT result FROM tool_cache WHERE cache_key = ?",
                    (cache_key,)
                )
                row = cursor.fetchone()
                if row:
                    return ServiceResult.success(json.loads(row[0]))
                return ServiceResult.success(None)
        except Exception as e:
            logger.error(f"Error retrieving tool cache: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="CACHE_ERROR", message=f"Failed to get tool cache: {e}"))

    def set_tool_cache(self, tool_name: str, parameters: Dict[str, Any], result: Any) -> ServiceResult[bool]:
        try:
            cache_key = self._generate_hash({"tool_name": tool_name, "parameters": parameters})
            result_json = json.dumps(result)
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO tool_cache (cache_key, tool_name, result)
                    VALUES (?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET result=excluded.result
                    """,
                    (cache_key, tool_name, result_json)
                )
                conn.commit()
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"Error setting tool cache: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="CACHE_ERROR", message=f"Failed to set tool cache: {e}"))

    def get_response_cache(self, request_payload: Dict[str, Any]) -> ServiceResult[Optional[Dict[str, Any]]]:
        try:
            req_hash = self._generate_hash(request_payload)
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT response_payload FROM response_cache WHERE req_hash = ?",
                    (req_hash,)
                )
                row = cursor.fetchone()
                if row:
                    return ServiceResult.success(json.loads(row[0]))
                return ServiceResult.success(None)
        except Exception as e:
            logger.error(f"Error retrieving response cache: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="CACHE_ERROR", message=f"Failed to get response cache: {e}"))

    def set_response_cache(self, request_payload: Dict[str, Any], response_payload: Dict[str, Any]) -> ServiceResult[bool]:
        try:
            req_hash = self._generate_hash(request_payload)
            res_json = json.dumps(response_payload)
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO response_cache (req_hash, response_payload)
                    VALUES (?, ?)
                    ON CONFLICT(req_hash) DO UPDATE SET response_payload=excluded.response_payload
                    """,
                    (req_hash, res_json)
                )
                conn.commit()
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"Error setting response cache: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="CACHE_ERROR", message=f"Failed to set response cache: {e}"))
