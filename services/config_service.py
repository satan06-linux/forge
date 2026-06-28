import os
import config
from models import get_db_connection

class ConfigService:
    @staticmethod
    def get_groq_api_key():
        return os.getenv("GROQ_API_KEY", config.GROQ_API_KEY)

    @staticmethod
    def get_openai_api_key():
        return os.getenv("OPENAI_API_KEY", "")

    @staticmethod
    def get_secret_key():
        return config.SECRET_KEY

    @staticmethod
    def get_bootstrap_admin_email():
        return os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()

    @staticmethod
    def is_feature_enabled(org_id, feature_name):
        """
        Checks organization_features table. Defaults to True if not specifically configured.
        """
        if not org_id:
            return True
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT enabled FROM organization_features WHERE organization_id = %s AND feature_name = %s",
                (org_id, feature_name)
            )
            row = cursor.fetchone()
            if row:
                return bool(row["enabled"])
            return True
        except Exception as e:
            print(f"[ConfigService Error] {e}")
            return True
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def set_feature_enabled(org_id, feature_name, enabled):
        if not org_id:
            return False
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO organization_features (organization_id, feature_name, enabled)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE enabled = VALUES(enabled)
                """,
                (org_id, feature_name, int(enabled))
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"[ConfigService Error] {e}")
            return False
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_global_limits():
        return {
            "max_execution_time_sec": 300,
            "max_memory_mb": 512,
            "max_cost_limit": 5.0
        }
