from models import get_db_connection
from connectors.github_connector import GithubConnector
from connectors.notion_connector import NotionConnector
from connectors.google_connector import GoogleConnector
from services.event_bus import event_bus
import json
import threading
import time

class ConnectorService:
    @staticmethod
    def get_connector_instance(connector_id, connector_type, config_json):
        try:
            config_dict = json.loads(config_json) if config_json else {}
        except Exception:
            config_dict = {}

        if connector_type == "github":
            return GithubConnector(connector_id, config_dict)
        elif connector_type == "notion":
            return NotionConnector(connector_id, config_dict)
        elif connector_type == "google_drive" or connector_type == "google":
            return GoogleConnector(connector_id, config_dict)
        else:
            raise ValueError(f"Unknown connector type: {connector_type}")

    @staticmethod
    def sync_connector(connector_id):
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            # Create a sync job
            cursor.execute(
                "INSERT INTO connector_jobs (connector_id, status, progress_pct) VALUES (%s, 'running', 10)",
                (connector_id,)
            )
            job_id = cursor.lastrowid
            conn.commit()

            cursor.execute("SELECT type, config_json FROM connectors WHERE id = %s", (connector_id,))
            connector = cursor.fetchone()
            if not connector:
                raise Exception("Connector not found")

            instance = ConnectorService.get_connector_instance(
                connector_id, connector["type"], connector["config_json"]
            )
            
            cursor.execute("UPDATE connector_jobs SET progress_pct = 50 WHERE id = %s", (job_id,))
            conn.commit()

            result = instance.sync()
            
            if result["success"]:
                cursor.execute(
                    """
                    UPDATE connector_jobs 
                    SET status = 'completed', progress_pct = 100, last_sync = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (job_id,)
                )
                cursor.execute("UPDATE connectors SET status = 'active' WHERE id = %s", (connector_id,))
                conn.commit()
                event_bus.publish("ConnectorSynced", {"connector_id": connector_id, "synced_count": result["synced_count"]})
            else:
                raise Exception(result.get("error", "Connector sync returned success=False"))

        except Exception as e:
            print(f"[ConnectorService Error] Sync failed for connector {connector_id}: {e}")
            cursor.execute(
                "UPDATE connector_jobs SET status = 'failed', error_message = %s WHERE id = %s AND status = 'running'",
                (str(e), job_id)
            )
            cursor.execute("UPDATE connectors SET status = 'error' WHERE id = %s", (connector_id,))
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def sync_connector_async(connector_id):
        t = threading.Thread(target=ConnectorService.sync_connector, args=(connector_id,), daemon=True)
        t.start()
