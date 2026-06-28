# ForgePrompt Phase 7 — FeatureFlagService
import hashlib
from models import get_db_connection
import logging

logger = logging.getLogger(__name__)

class FeatureFlagService:
    def __init__(self, container=None):
        self.container = container
        self._init_db()

    def _init_db(self):
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS feature_flags (
                    flag_name VARCHAR(100) NOT NULL PRIMARY KEY,
                    enabled TINYINT(1) DEFAULT 1,
                    rollout_pct DECIMAL(5,2) DEFAULT 100.00,
                    description TEXT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB
            """)
            conn.commit()
        except Exception as e:
            logger.error(f"[FeatureFlagService Error] Failed to init feature_flags table: {e}")
        finally:
            cursor.close()
            conn.close()

    def is_enabled(self, flag_name: str, org_id: int = None, user_id: int = None, rollout_pct: float = None) -> bool:
        """
        Check if feature is enabled.
        1. Check organization_features table (org override)
        2. Check global feature_flags table for rollout_pct
        3. If rollout_pct set: deterministic hash(org_id or user_id) % 100 < rollout_pct
        4. Default: True
        """
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            # 1. Organization specific override
            if org_id is not None:
                cursor.execute(
                    "SELECT enabled FROM organization_features WHERE organization_id = %s AND feature_name = %s",
                    (org_id, flag_name)
                )
                org_row = cursor.fetchone()
                if org_row is not None:
                    return bool(org_row['enabled'])

            # 2. Global flag settings
            cursor.execute(
                "SELECT enabled, rollout_pct FROM feature_flags WHERE flag_name = %s",
                (flag_name,)
            )
            global_row = cursor.fetchone()
            
            # Use param rollout_pct if not found in db
            pct = 100.0
            is_globally_enabled = True
            
            if global_row is not None:
                is_globally_enabled = bool(global_row['enabled'])
                pct = float(global_row['rollout_pct'])
            elif rollout_pct is not None:
                pct = rollout_pct
                
            if not is_globally_enabled:
                return False
                
            # 3. Rollout percentage check
            if pct < 100.0:
                if org_id is not None:
                    hash_key = f"{flag_name}:org:{org_id}"
                elif user_id is not None:
                    hash_key = f"{flag_name}:user:{user_id}"
                else:
                    return False # Require org_id or user_id for percentage rollout

                # Deterministic hash to int 0-99
                hash_val = int(hashlib.md5(hash_key.encode('utf-8')).hexdigest(), 16) % 100
                return hash_val < pct

            # 4. Default
            return True
        except Exception as e:
            logger.error(f"[FeatureFlagService Error] Failed to check feature flag {flag_name}: {e}")
            return True
        finally:
            cursor.close()
            conn.close()

    def set(self, flag_name: str, enabled: bool, description: str = None, rollout_pct: float = 100.0):
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO feature_flags (flag_name, enabled, rollout_pct, description)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE enabled = VALUES(enabled), rollout_pct = VALUES(rollout_pct), description = VALUES(description)
                """,
                (flag_name, int(enabled), rollout_pct, description)
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"[FeatureFlagService Error] Failed to set feature flag {flag_name}: {e}")
            return False
        finally:
            cursor.close()
            conn.close()

    def set_for_org(self, flag_name: str, org_id: int, enabled: bool):
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO organization_features (organization_id, feature_name, enabled)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE enabled = VALUES(enabled)
                """,
                (org_id, flag_name, int(enabled))
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"[FeatureFlagService Error] Failed to set feature flag {flag_name} for org {org_id}: {e}")
            return False
        finally:
            cursor.close()
            conn.close()

    def list_flags(self, org_id: int = None) -> list:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM feature_flags")
            flags = cursor.fetchall() or []
            
            if org_id is not None:
                cursor.execute(
                    "SELECT feature_name, enabled FROM organization_features WHERE organization_id = %s",
                    (org_id,)
                )
                org_flags = cursor.fetchall() or []
                org_map = {row['feature_name']: bool(row['enabled']) for row in org_flags}
                
                for f in flags:
                    if f['flag_name'] in org_map:
                        f['org_override'] = org_map[f['flag_name']]
                        
            return flags
        except Exception as e:
            logger.error(f"[FeatureFlagService Error] Failed to list flags: {e}")
            return []
        finally:
            cursor.close()
            conn.close()
