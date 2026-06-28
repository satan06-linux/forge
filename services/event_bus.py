# ForgePrompt Phase 7 — EventBus
import json
import time
from typing import Any, Dict, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError

class EventBus:
    """
    Transactional Outbox implementation of the EventBus.
    Instead of immediate callbacks, events are persisted to the event_outbox table.
    The OutboxDispatcher will pick them up after the transaction commits.
    """
    
    def __init__(self, container: Any):
        self.container = container
        self._storage = container.get("storage_provider")

    def publish(self, aggregate_type: str, aggregate_id: str, event_type: str, payload: Dict[str, Any], conn=None) -> ServiceResult:
        """
        Appends an event to the event_outbox table.
        """
        try:
            payload_json = json.dumps(payload)
            sql = """
                INSERT INTO event_outbox (aggregate_type, aggregate_id, event_type, payload_json, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """
            params = (aggregate_type, str(aggregate_id), event_type, payload_json, 'pending', int(time.time()))
            
            if conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                # Let the caller's transaction commit
            else:
                # If no connection provided, use storage provider transaction
                with self._storage.transaction() as session:
                    session.execute(sql, params)
                    
            return ServiceResult.ok(data={"status": "appended"})
            
        except Exception as e:
            print(f"[EventBus Error] Failed to publish event {event_type}: {e}")
            error = ForgeError(message=f"Event publishing failed: {str(e)}", error_code="PUBLISH_FAILED", retryable=True)
            return ServiceResult.fail(error)

    def subscribe(self, event_type: str, callback: Any) -> ServiceResult:
        """
        Subscribe is no longer functional in the outbox model, but stubbed for compatibility.
        Subscribers should be registered with the OutboxDispatcher instead.
        """
        error = ForgeError(message="Immediate subscribe is disabled in Phase 7. Use OutboxDispatcher.", error_code="SUBSCRIBE_DISABLED", retryable=False)
        return ServiceResult.fail(error)

    def unsubscribe(self, event_type: str, callback: Any) -> ServiceResult:
        error = ForgeError(message="Immediate unsubscribe is disabled in Phase 7.", error_code="UNSUBSCRIBE_DISABLED", retryable=False)
        return ServiceResult.fail(error)

# Legacy export for Phase 6 compatibility
class DummyEventBus:
    def publish(self, *args, **kwargs): pass
    def subscribe(self, *args, **kwargs): pass
    def unsubscribe(self, *args, **kwargs): pass
event_bus = DummyEventBus()
