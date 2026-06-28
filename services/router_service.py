from models import get_db_connection

class RouterService:
    @staticmethod
    def route_task(task_type, organization_id=None):
        """
        Looks up model_routers database table for task_type, matching preferred and fallback models.
        Returns a dictionary containing provider names and model specifications.
        """
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            # Query Org-specific router first, fallback to global router
            query = """
                SELECT r.*, m.model_name as preferred_model, p.name as preferred_provider,
                       fm.model_name as fallback_model, fp.name as fallback_provider
                FROM model_routers r
                JOIN models m ON r.preferred_model_id = m.id
                JOIN providers p ON m.provider_id = p.id
                LEFT JOIN models fm ON r.fallback_model_id = fm.id
                LEFT JOIN providers fp ON fm.provider_id = fp.id
                WHERE (r.organization_id = %s OR r.organization_id IS NULL)
                  AND r.task_type = %s AND r.enabled = 1
                ORDER BY r.organization_id DESC LIMIT 1
            """
            cursor.execute(query, (organization_id, task_type))
            row = cursor.fetchone()
            if row:
                return {
                    "provider": row["preferred_provider"],
                    "model": row["preferred_model"],
                    "fallback_provider": row["fallback_provider"],
                    "fallback_model": row["fallback_model"],
                    "max_cost_limit": float(row["max_cost_limit"]),
                    "max_latency_ms": row["max_latency_ms"]
                }
            
            # Static fallbacks if no config is set in the DB
            if task_type in ("coding", "reasoning"):
                return {
                    "provider": "groq",
                    "model": "llama-3.3-70b-versatile",
                    "fallback_provider": "local",
                    "fallback_model": "prometheus-local",
                    "max_cost_limit": 0.05,
                    "max_latency_ms": 10000
                }
            else:
                return {
                    "provider": "groq",
                    "model": "llama3-8b-8192",
                    "fallback_provider": "local",
                    "fallback_model": "prometheus-local",
                    "max_cost_limit": 0.01,
                    "max_latency_ms": 5000
                }
        except Exception as e:
            print(f"[RouterService Error] {e}")
            return {
                "provider": "local",
                "model": "prometheus-local",
                "fallback_provider": None,
                "fallback_model": None,
                "max_cost_limit": 0.0,
                "max_latency_ms": 1000
            }
        finally:
            cursor.close()
            conn.close()
