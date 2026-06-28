import logging
import json
import uuid
from datetime import datetime
from services.service_result import ServiceResult
from services.errors import ForgeError, StorageError, NotFoundError

logger = logging.getLogger(__name__)

class AISelfImprovementService:
    """
    Phase 8: AI Self-Improvement Service.
    Records successful/failed prompts and auto-tunes routing logic based on historical performance.
    """
    def __init__(self, container):
        self.container = container

    def _get_storage(self):
        storage = self.container.get('storage_provider')
        if not storage:
            raise StorageError("Storage provider not available")
        return storage

    def record_prompt_outcome(self, prompt_id: str, prompt_text: str, model_id: str, success: bool, duration_ms: int, metrics: dict = None) -> ServiceResult:
        """Records the outcome of a prompt execution for self-improvement."""
        try:
            storage = self._get_storage()
            data = {
                'outcome_id': str(uuid.uuid4()),
                'prompt_id': prompt_id,
                'prompt_text': prompt_text,
                'model_id': model_id,
                'success': 1 if success else 0,
                'duration_ms': duration_ms,
                'metrics': json.dumps(metrics or {}),
                'created_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            }
            # Attempt to insert directly
            try:
                storage.insert('prompt_outcomes', data)
            except Exception as inner_e:
                # If table doesn't exist, we log and return error (in production, schema migrations handle this)
                logger.warning(f"Failed to insert into prompt_outcomes, ensure schema exists: {inner_e}")
                return ServiceResult.fail(ForgeError("Failed to record prompt outcome due to schema issues", error_code="SCHEMA_ERROR"))
                
            logger.info(f"Recorded prompt outcome for {prompt_id} on model {model_id} (success={success})")
            return ServiceResult.ok(data={'outcome_id': data['outcome_id']})
            
        except ForgeError as e:
            logger.error(f"ForgeError recording prompt outcome: {e}")
            return ServiceResult.fail(e)
        except Exception as e:
            logger.error(f"Error recording prompt outcome: {e}")
            return ServiceResult.fail(ForgeError(str(e), error_code="RECORDING_ERROR"))

    def auto_tune_routing(self, min_samples: int = 10) -> ServiceResult:
        """
        Auto-tunes routing logic based on historical performance.
        Calculates a priority score based on success rate and latency.
        """
        try:
            storage = self._get_storage()
            
            sql = """
                SELECT model_id, COUNT(*) as total, SUM(success) as successes, AVG(duration_ms) as avg_duration
                FROM prompt_outcomes
                GROUP BY model_id
            """
            
            try:
                stats = storage.execute(sql)
            except Exception as inner_e:
                logger.warning(f"Failed to fetch prompt_outcomes stats: {inner_e}")
                return ServiceResult.fail(ForgeError("Failed to fetch statistics", error_code="STATS_ERROR"))
            
            updates = []
            for stat in stats:
                total = stat['total']
                if total < min_samples:
                    continue
                    
                success_rate = float(stat['successes']) / float(total)
                avg_duration = float(stat['avg_duration'])
                
                # Simple tuning formula: Score is heavily weighted by success rate, lightly penalized by duration
                # E.g. 100% success = 100 points. 1000ms duration = -1 point.
                new_score = (success_rate * 100.0) - (avg_duration / 1000.0)
                # Cap the score between 0 and 100
                new_score = max(0.0, min(100.0, new_score))
                
                try:
                    update_sql = "UPDATE model_routing_rules SET priority_score = %s, last_tuned = %s WHERE model_id = %s"
                    updated = storage.update(update_sql, (new_score, datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), stat['model_id']))
                    
                    if updated > 0:
                        updates.append({'model_id': stat['model_id'], 'new_score': new_score})
                    else:
                        # If no rule exists, create one
                        storage.insert('model_routing_rules', {
                            'rule_id': str(uuid.uuid4()),
                            'model_id': stat['model_id'],
                            'priority_score': new_score,
                            'last_tuned': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                        })
                        updates.append({'model_id': stat['model_id'], 'new_score': new_score})
                        
                except Exception as update_e:
                    logger.warning(f"Failed to update routing rule for {stat['model_id']}: {update_e}")
                    
            logger.info(f"Auto-tuned routing logic for {len(updates)} models")
            return ServiceResult.ok(data={'tuned_models': len(updates), 'updates': updates})
            
        except ForgeError as e:
            logger.error(f"ForgeError auto-tuning routing: {e}")
            return ServiceResult.fail(e)
        except Exception as e:
            logger.error(f"Error auto-tuning routing: {e}")
            return ServiceResult.fail(ForgeError(str(e), error_code="AUTO_TUNE_ERROR"))
