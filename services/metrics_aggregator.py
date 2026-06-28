# ForgePrompt Phase 7 — MetricsAggregator
import logging
import json
from typing import Dict, Any

from services.service_result import ServiceResult
from services.errors import ForgeError, StorageError

logger = logging.getLogger(__name__)

class MetricsAggregator:
    def __init__(self, container):
        self.container = container
        self.storage = getattr(container, 'storage_provider', container.get('storage_provider') if hasattr(container, 'get') else None)
        self._ensure_tables()

    def _ensure_tables(self):
        try:
            with self.storage.get_session() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS metrics_aggregated (
                        organization_id VARCHAR(36) NOT NULL,
                        metric_name VARCHAR(128) NOT NULL,
                        time_bucket TIMESTAMP NOT NULL,
                        metric_value FLOAT NOT NULL,
                        tags LONGTEXT NOT NULL,
                        PRIMARY KEY (organization_id, metric_name, time_bucket)
                    )
                """)
                cursor.execute("""
                    SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS 
                    WHERE TABLE_SCHEMA = DATABASE() 
                      AND TABLE_NAME = 'metrics_aggregated' 
                      AND INDEX_NAME = 'idx_org_time'
                """)
                if cursor.fetchone()[0] == 0:
                    cursor.execute("CREATE INDEX idx_org_time ON metrics_aggregated (organization_id, time_bucket)")
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS event_outbox (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        organization_id VARCHAR(36) NOT NULL,
                        event_type VARCHAR(128) NOT NULL,
                        payload LONGTEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Lineage table dependency
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS lineage_events (
                        organization_id VARCHAR(36) NOT NULL,
                        entity_id VARCHAR(128) NOT NULL,
                        entity_type VARCHAR(64) NOT NULL,
                        operation VARCHAR(64) NOT NULL,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"[MetricsAggregator Error] Failed to ensure tables: {e}")

    def record_metric(self, organization_id: str, metric_name: str, value: float, tags: Dict[str, str], conn=None) -> ServiceResult:
        try:
            tags_json = json.dumps(tags or {})
            
            def _do_record(c):
                cursor = c.cursor()
                cursor.execute("""
                    INSERT INTO metrics_aggregated (organization_id, metric_name, time_bucket, metric_value, tags)
                    VALUES (%s, %s, DATE_FORMAT(CURRENT_TIMESTAMP, '%%Y-%%m-%%d %%H:%%00:00'), %s, %s)
                    ON DUPLICATE KEY UPDATE metric_value = metric_value + VALUES(metric_value)
                """, (organization_id, metric_name, value, tags_json))

                # Rule 2: Events write to event_outbox inside transaction
                event_payload = {
                    "organization_id": organization_id,
                    "metric_name": metric_name,
                    "value": value,
                    "tags": tags
                }
                cursor.execute("""
                    INSERT INTO event_outbox (organization_id, event_type, payload)
                    VALUES (%s, %s, %s)
                """, (organization_id, "METRIC_RECORDED", json.dumps(event_payload)))

                # Rule 12: Data lineage events
                cursor.execute("""
                    INSERT INTO lineage_events (organization_id, entity_id, entity_type, operation)
                    VALUES (%s, %s, %s, %s)
                """, (organization_id, metric_name, "METRIC", "RECORD"))

            if conn:
                _do_record(conn)
            else:
                with self.storage.get_session() as c:
                    _do_record(c)
                    c.commit()

            return ServiceResult.ok()
        except Exception as e:
            logger.error(f"[MetricsAggregator Error] Failed to record metric: {e}")
            return ServiceResult.fail(StorageError(str(e)))
            
    def query_metrics(self, organization_id: str, metric_name: str, start_time: str, end_time: str, conn=None) -> ServiceResult:
        try:
            def _do_query(c):
                cursor = c.cursor(dictionary=True)
                cursor.execute("""
                    SELECT time_bucket, metric_value, tags FROM metrics_aggregated
                    WHERE organization_id = %s AND metric_name = %s 
                      AND time_bucket >= %s AND time_bucket <= %s
                    ORDER BY time_bucket ASC
                """, (organization_id, metric_name, start_time, end_time))
                return cursor.fetchall()
                
            results = _do_query(conn) if conn else None
            if not conn:
                with self.storage.get_session() as c:
                    results = _do_query(c)
                    
            for row in results:
                if isinstance(row["tags"], str):
                    row["tags"] = json.loads(row["tags"])
            return ServiceResult.ok(data=results)
        except Exception as e:
            logger.error(f"[MetricsAggregator Error] Failed to query metrics: {e}")
            return ServiceResult.fail(StorageError(str(e)))
