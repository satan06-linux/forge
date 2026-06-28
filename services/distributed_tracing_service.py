# ForgePrompt Phase 7 — DistributedTracingService
import logging
import json
import uuid
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class DistributedTracingService:
    def __init__(self, container: Any):
        self.container = container
        self._init_db()

    def _init_db(self):
        try:
            with self.container.storage_provider.get_session() as session:
                # Create table
                session.execute("""
                    CREATE TABLE IF NOT EXISTS trace_spans (
                        trace_id VARCHAR(100) NOT NULL,
                        span_id VARCHAR(100) NOT NULL,
                        organization_id VARCHAR(100) NOT NULL,
                        parent_span_id VARCHAR(100),
                        operation_name VARCHAR(255) NOT NULL,
                        start_time TIMESTAMP NOT NULL,
                        end_time TIMESTAMP NULL,
                        attributes LONGTEXT,
                        events LONGTEXT,
                        PRIMARY KEY (trace_id, span_id, organization_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """)
                
                # Check and create composite index
                session.execute("""
                    SELECT COUNT(1) AS cnt 
                    FROM INFORMATION_SCHEMA.STATISTICS 
                    WHERE TABLE_SCHEMA = DATABASE() 
                      AND TABLE_NAME = 'trace_spans' 
                      AND INDEX_NAME = 'idx_org_trace';
                """)
                result = session.fetchone()
                if result and result.get('cnt', 0) == 0:
                    session.execute("CREATE INDEX idx_org_trace ON trace_spans (organization_id, trace_id);")

                session.commit()
        except Exception as e:
            logger.error(f"[DistributedTracingService Error] DB initialization failed: {e}", exc_info=True)

    def start_span(self, organization_id: str, operation_name: str, 
                   trace_id: Optional[str] = None, parent_span_id: Optional[str] = None, 
                   attributes: Optional[Dict[str, Any]] = None, conn=None) -> ServiceResult[Dict[str, str]]:
        """Starts a new span. Returns span context."""
        try:
            if not trace_id:
                trace_id = str(uuid.uuid4())
            span_id = str(uuid.uuid4())
            start_time = datetime.utcnow()
            
            attrs_json = json.dumps(attributes) if attributes else None

            sql = """
                INSERT INTO trace_spans 
                (trace_id, span_id, organization_id, parent_span_id, operation_name, start_time, attributes, events)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            params = (trace_id, span_id, organization_id, parent_span_id, operation_name, start_time, attrs_json, json.dumps([]))
            
            if conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
            else:
                with self.container.storage_provider.get_session() as session:
                    session.execute(sql, params)
                    session.commit()
                    
            return ServiceResult.success({
                "trace_id": trace_id,
                "span_id": span_id
            })
        except Exception as e:
            logger.error(f"[DistributedTracingService Error] Failed to start span: {e}", exc_info=True)
            return ServiceResult.fail(
                error_code="SPAN_START_ERROR",
                error_message=str(e)
            )

    def end_span(self, organization_id: str, trace_id: str, span_id: str, conn=None) -> ServiceResult[bool]:
        """Ends a trace span."""
        try:
            end_time = datetime.utcnow()
            sql = """
                UPDATE trace_spans 
                SET end_time = %s
                WHERE trace_id = %s AND span_id = %s AND organization_id = %s
            """
            params = (end_time, trace_id, span_id, organization_id)
            
            if conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
            else:
                with self.container.storage_provider.get_session() as session:
                    session.execute(sql, params)
                    session.commit()
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"[DistributedTracingService Error] Failed to end span: {e}", exc_info=True)
            return ServiceResult.fail(
                error_code="SPAN_END_ERROR",
                error_message=str(e)
            )

    def add_event(self, organization_id: str, trace_id: str, span_id: str, event_name: str, 
                  event_attributes: Optional[Dict[str, Any]] = None, conn=None) -> ServiceResult[bool]:
        """Appends an event to the span."""
        try:
            fetch_sql = "SELECT events FROM trace_spans WHERE trace_id = %s AND span_id = %s AND organization_id = %s FOR UPDATE"
            fetch_params = (trace_id, span_id, organization_id)
            
            new_event = {
                "name": event_name,
                "timestamp": datetime.utcnow().isoformat(),
                "attributes": event_attributes or {}
            }

            def _update_events(current_events_json):
                events_list = json.loads(current_events_json) if current_events_json else []
                events_list.append(new_event)
                return json.dumps(events_list)

            update_sql = "UPDATE trace_spans SET events = %s WHERE trace_id = %s AND span_id = %s AND organization_id = %s"

            if conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(fetch_sql, fetch_params)
                row = cursor.fetchone()
                if row:
                    updated_events = _update_events(row.get('events'))
                    cursor.execute(update_sql, (updated_events, trace_id, span_id, organization_id))
            else:
                with self.container.storage_provider.get_session() as session:
                    session.begin()
                    session.execute(fetch_sql, fetch_params)
                    row = session.fetchone()
                    if row:
                        updated_events = _update_events(row.get('events'))
                        session.execute(update_sql, (updated_events, trace_id, span_id, organization_id))
                    session.commit()
            
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"[DistributedTracingService Error] Failed to add event: {e}", exc_info=True)
            return ServiceResult.fail(
                error_code="SPAN_ADD_EVENT_ERROR",
                error_message=str(e)
            )

    def get_trace(self, organization_id: str, trace_id: str, conn=None) -> ServiceResult[List[Dict[str, Any]]]:
        """Retrieves an entire trace's spans."""
        try:
            sql = """
                SELECT span_id, parent_span_id, operation_name, start_time, end_time, attributes, events
                FROM trace_spans
                WHERE trace_id = %s AND organization_id = %s
                ORDER BY start_time ASC
            """
            params = (trace_id, organization_id)
            
            results = []
            if conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(sql, params)
                results = cursor.fetchall()
            else:
                with self.container.storage_provider.get_session() as session:
                    session.execute(sql, params)
                    results = session.fetchall()
            
            # Format outputs
            formatted = []
            for row in results:
                formatted.append({
                    "span_id": row["span_id"],
                    "parent_span_id": row["parent_span_id"],
                    "operation_name": row["operation_name"],
                    "start_time": row["start_time"].isoformat() if row["start_time"] else None,
                    "end_time": row["end_time"].isoformat() if row["end_time"] else None,
                    "attributes": json.loads(row["attributes"]) if row["attributes"] else None,
                    "events": json.loads(row["events"]) if row["events"] else []
                })
            return ServiceResult.success(formatted)
        except Exception as e:
            logger.error(f"[DistributedTracingService Error] Failed to retrieve trace: {e}", exc_info=True)
            return ServiceResult.fail(
                error_code="TRACE_RETRIEVAL_ERROR",
                error_message=str(e)
            )
