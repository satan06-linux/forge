import logging
import json
import time
from typing import Dict, Any, List, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError
from services.storage_provider import StorageProvider
from infrastructure.kafka_manager import KafkaManager

logger = logging.getLogger(__name__)

class AISessionRecoveryService:
    """
    Restores running prompts, current reasoning, tools, and agent progress 
    seamlessly from the DB/Kafka stream after power failures, crashes, 
    internet loss, or GPU restarts.
    """
    def __init__(self, storage_provider: StorageProvider, kafka_manager: KafkaManager, container=None):
        self.storage_provider = storage_provider
        self.kafka_manager = kafka_manager
        self.container = container
        self._ensure_tables_exist()

    def _ensure_tables_exist(self):
        try:
            with self.storage_provider.get_session() as session:
                session.execute("""
                    CREATE TABLE IF NOT EXISTS ai_session_checkpoints (
                        session_id VARCHAR(255) PRIMARY KEY,
                        agent_state JSON,
                        current_prompt TEXT,
                        reasoning_context JSON,
                        active_tools JSON,
                        status VARCHAR(50) DEFAULT 'running',
                        last_updated_at DOUBLE
                    )
                """)
                session.commit()
        except Exception as e:
            logger.error(f"Failed to create ai_session_checkpoints table: {e}")
            
    def save_checkpoint(self, session_id: str, agent_state: Dict[str, Any], current_prompt: str, 
                        reasoning_context: Dict[str, Any], active_tools: List[Dict[str, Any]], 
                        status: str = 'running') -> ServiceResult[bool]:
        """
        Saves a point-in-time checkpoint of an AI session to DB and emits a Kafka event.
        """
        try:
            current_time = time.time()
            with self.storage_provider.get_session() as session:
                session.execute("""
                    INSERT INTO ai_session_checkpoints 
                    (session_id, agent_state, current_prompt, reasoning_context, active_tools, status, last_updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE 
                        agent_state = VALUES(agent_state),
                        current_prompt = VALUES(current_prompt),
                        reasoning_context = VALUES(reasoning_context),
                        active_tools = VALUES(active_tools),
                        status = VALUES(status),
                        last_updated_at = VALUES(last_updated_at)
                """, (
                    session_id, 
                    json.dumps(agent_state), 
                    current_prompt, 
                    json.dumps(reasoning_context), 
                    json.dumps(active_tools), 
                    status,
                    current_time
                ))
                session.commit()
            
            # Emit Kafka event for streaming/DAG alternative
            payload = {
                "session_id": session_id,
                "agent_state": agent_state,
                "status": status,
                "timestamp": current_time
            }
            kafka_res = self.kafka_manager.produce_event(
                topic="ai-session-checkpoints",
                event_type="CheckpointSaved",
                payload=payload,
                key=session_id
            )
            
            if not kafka_res.is_success:
                logger.warning(f"Failed to emit Kafka event for session checkpoint {session_id}: {kafka_res.error}")
                # Do not fail DB checkpoint save if Kafka fails
                
            return ServiceResult.success(True)
            
        except Exception as e:
            logger.error(f"Failed to save session checkpoint for {session_id}: {e}")
            return ServiceResult.fail(ForgeError(f"Failed to save checkpoint: {str(e)}"))

    def restore_session(self, session_id: str) -> ServiceResult[Optional[Dict[str, Any]]]:
        """
        Restores a specific AI session from the latest DB checkpoint.
        """
        try:
            with self.storage_provider.get_session() as session:
                row = session.execute("""
                    SELECT session_id, agent_state, current_prompt, reasoning_context, active_tools, status, last_updated_at 
                    FROM ai_session_checkpoints 
                    WHERE session_id = %s
                """, (session_id,)).fetchone()
                
                if not row:
                    return ServiceResult.success(None)
                
                def safe_json_load(data):
                    if not data:
                        return None
                    if isinstance(data, (bytes, bytearray)):
                        return json.loads(data.decode('utf-8'))
                    if isinstance(data, str):
                        return json.loads(data)
                    return data
                    
                return ServiceResult.success({
                    "session_id": row['session_id'],
                    "agent_state": safe_json_load(row['agent_state']) or {},
                    "current_prompt": row['current_prompt'],
                    "reasoning_context": safe_json_load(row['reasoning_context']) or {},
                    "active_tools": safe_json_load(row['active_tools']) or [],
                    "status": row['status'],
                    "last_updated_at": row['last_updated_at']
                })
        except Exception as e:
            logger.error(f"Failed to restore session {session_id}: {e}")
            return ServiceResult.fail(ForgeError(f"Failed to restore session: {str(e)}"))

    def recover_all_pending_sessions(self, stale_timeout_seconds: float = 300.0) -> ServiceResult[List[Dict[str, Any]]]:
        """
        Finds all running sessions that haven't been updated in stale_timeout_seconds
        and prepares them for recovery (e.g. after a crash or power failure).
        """
        try:
            cutoff_time = time.time() - stale_timeout_seconds
            
            with self.storage_provider.get_session() as session:
                rows = session.execute("""
                    SELECT session_id, agent_state, current_prompt, reasoning_context, active_tools, status, last_updated_at 
                    FROM ai_session_checkpoints 
                    WHERE status = 'running' AND last_updated_at < %s
                """, (cutoff_time,)).fetchall()
                
                recovered_sessions = []
                
                def safe_json_load(data):
                    if not data:
                        return None
                    if isinstance(data, (bytes, bytearray)):
                        return json.loads(data.decode('utf-8'))
                    if isinstance(data, str):
                        return json.loads(data)
                    return data
                    
                for row in rows:
                    session_data = {
                        "session_id": row['session_id'],
                        "agent_state": safe_json_load(row['agent_state']) or {},
                        "current_prompt": row['current_prompt'],
                        "reasoning_context": safe_json_load(row['reasoning_context']) or {},
                        "active_tools": safe_json_load(row['active_tools']) or [],
                        "status": "recovering",
                        "last_updated_at": row['last_updated_at']
                    }
                    recovered_sessions.append(session_data)
                    
                    # Mark as recovering
                    session.execute("""
                        UPDATE ai_session_checkpoints 
                        SET status = 'recovering', last_updated_at = %s 
                        WHERE session_id = %s
                    """, (time.time(), row['session_id']))
                
                session.commit()
                
                # Emit recovery events to Kafka
                for sd in recovered_sessions:
                    self.kafka_manager.produce_event(
                        topic="ai-session-recovery",
                        event_type="SessionRecovered",
                        payload=sd,
                        key=sd['session_id']
                    )
                
                return ServiceResult.success(recovered_sessions)
                
        except Exception as e:
            logger.error(f"Failed to recover pending sessions: {e}")
            return ServiceResult.fail(ForgeError(f"Failed to recover pending sessions: {str(e)}"))

    def process_recovery_stream(self) -> ServiceResult[int]:
        """
        Listens to the Kafka stream for session checkpoints and can replicate them 
        or replay them if needed. (Stream processing fallback).
        """
        def handle_event(event: Dict[str, Any]) -> bool:
            try:
                event_type = event.get("event_type")
                if event_type == "CheckpointSaved":
                    logger.info(f"Processed stream checkpoint for session {event.get('payload', {}).get('session_id')}")
                elif event_type == "SessionRecovered":
                    logger.info(f"Initiating stream-based recovery workflow for session {event.get('payload', {}).get('session_id')}")
                return True
            except Exception as e:
                logger.error(f"Error handling recovery stream event: {e}")
                return False

        return self.kafka_manager.consume_events(
            topic="ai-session-checkpoints",
            group_id="ai-session-recovery-group",
            callback=handle_event,
            batch_size=50,
            timeout_ms=5000
        )
