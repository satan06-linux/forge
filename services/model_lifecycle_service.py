import logging
import time
from typing import Dict, Any, List, Optional
from enum import Enum

from services.service_result import ServiceResult
from services.errors import ForgeError, ValidationError, NotFoundError
from services.model_evaluator import ModelEvaluator
from services.model_compatibility_service import ModelCompatibilityService

logger = logging.getLogger(__name__)

class ModelStatus(str, Enum):
    REGISTERED = "REGISTERED"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"

class ModelLifecycleService:
    """
    Registers, activates, archives, and rolls back models. 
    Includes Automatic Benchmark Promotion logic.
    """
    def __init__(self, evaluator: ModelEvaluator, compatibility_service: ModelCompatibilityService):
        self.evaluator = evaluator
        self.compatibility_service = compatibility_service
        # Simulated database for model registry
        self._models: Dict[str, Dict[str, Any]] = {}
        self._active_model: Optional[str] = None
        self._promotion_threshold = 80.0  # Average score required for automatic promotion

    def register_model(self, model_id: str, model_path: str, context_window: int, required_memory_mb: int, features: Optional[List[str]] = None) -> ServiceResult:
        start_time = time.time()
        logger.info(f"Registering model {model_id}")
        
        try:
            if model_id in self._models:
                return ServiceResult.fail(ValidationError(f"Model {model_id} already exists"))
                
            # Verify compatibility first
            compat_result = self.compatibility_service.verify_compatibility(
                model_id=model_id,
                model_path=model_path,
                context_window=context_window,
                required_memory_mb=required_memory_mb,
                features=features
            )
            
            if compat_result.is_error:
                logger.warning(f"Compatibility failed for {model_id}")
                return compat_result
                
            model_record = {
                "model_id": model_id,
                "model_path": model_path,
                "context_window": context_window,
                "required_memory_mb": required_memory_mb,
                "features": features or [],
                "status": ModelStatus.REGISTERED.value,
                "registered_at": time.time(),
                "updated_at": time.time()
            }
            
            self._models[model_id] = model_record
            
            duration_ms = int((time.time() - start_time) * 1000)
            return ServiceResult.ok(data=model_record, duration_ms=duration_ms)
            
        except Exception as e:
            logger.error(f"Failed to register model {model_id}: {str(e)}")
            return ServiceResult.fail(ForgeError(f"Registration failed: {str(e)}"))

    def activate_model(self, model_id: str) -> ServiceResult:
        start_time = time.time()
        logger.info(f"Activating model {model_id}")
        
        try:
            if model_id not in self._models:
                return ServiceResult.fail(NotFoundError(f"Model {model_id} not found"))
                
            model = self._models[model_id]
            if model["status"] in [ModelStatus.ARCHIVED.value, ModelStatus.FAILED.value]:
                return ServiceResult.fail(ValidationError(f"Cannot activate model in {model['status']} state"))
                
            # Deactivate current if exists
            if self._active_model and self._active_model in self._models:
                self._models[self._active_model]["status"] = ModelStatus.REGISTERED.value
                self._models[self._active_model]["updated_at"] = time.time()
                
            model["status"] = ModelStatus.ACTIVE.value
            model["updated_at"] = time.time()
            self._active_model = model_id
            
            duration_ms = int((time.time() - start_time) * 1000)
            return ServiceResult.ok(data=model, duration_ms=duration_ms)
            
        except Exception as e:
            logger.error(f"Failed to activate model {model_id}: {str(e)}")
            return ServiceResult.fail(ForgeError(f"Activation failed: {str(e)}"))

    def archive_model(self, model_id: str) -> ServiceResult:
        start_time = time.time()
        logger.info(f"Archiving model {model_id}")
        
        try:
            if model_id not in self._models:
                return ServiceResult.fail(NotFoundError(f"Model {model_id} not found"))
                
            if self._active_model == model_id:
                self._active_model = None
                
            self._models[model_id]["status"] = ModelStatus.ARCHIVED.value
            self._models[model_id]["updated_at"] = time.time()
            
            duration_ms = int((time.time() - start_time) * 1000)
            return ServiceResult.ok(data=self._models[model_id], duration_ms=duration_ms)
            
        except Exception as e:
            logger.error(f"Failed to archive model {model_id}: {str(e)}")
            return ServiceResult.fail(ForgeError(f"Archival failed: {str(e)}"))

    def rollback_model(self, target_model_id: str) -> ServiceResult:
        start_time = time.time()
        logger.info(f"Rolling back to model {target_model_id}")
        
        try:
            if target_model_id not in self._models:
                return ServiceResult.fail(NotFoundError(f"Model {target_model_id} not found"))
                
            if self._active_model:
                self._models[self._active_model]["status"] = ModelStatus.ROLLED_BACK.value
                self._models[self._active_model]["updated_at"] = time.time()
                
            return self.activate_model(target_model_id)
            
        except Exception as e:
            logger.error(f"Failed to rollback to model {target_model_id}: {str(e)}")
            return ServiceResult.fail(ForgeError(f"Rollback failed: {str(e)}"))

    def automatic_benchmark_promotion(self, model_id: str) -> ServiceResult:
        """
        Evaluates the model and promotes it to ACTIVE if it meets the benchmark threshold.
        """
        start_time = time.time()
        logger.info(f"Running automatic benchmark promotion for {model_id}")
        
        try:
            if model_id not in self._models:
                return ServiceResult.fail(NotFoundError(f"Model {model_id} not found"))
                
            eval_result = self.evaluator.evaluate_model(model_id)
            if eval_result.is_error:
                self._models[model_id]["status"] = ModelStatus.FAILED.value
                return ServiceResult.fail(ForgeError(f"Evaluation failed during promotion step: {eval_result.error}"))
                
            eval_data = eval_result.unwrap()
            average_score = eval_data.get("average_score", 0.0)
            
            if average_score >= self._promotion_threshold:
                logger.info(f"Model {model_id} passed threshold ({average_score} >= {self._promotion_threshold}). Activating.")
                activation_result = self.activate_model(model_id)
                if activation_result.is_error:
                    return activation_result
                
                duration_ms = int((time.time() - start_time) * 1000)
                return ServiceResult.ok(
                    data={"promoted": True, "score": average_score, "model": activation_result.unwrap()},
                    duration_ms=duration_ms
                )
            else:
                logger.info(f"Model {model_id} failed threshold ({average_score} < {self._promotion_threshold}). Not activating.")
                duration_ms = int((time.time() - start_time) * 1000)
                return ServiceResult.ok(
                    data={"promoted": False, "score": average_score, "threshold": self._promotion_threshold},
                    duration_ms=duration_ms
                )
                
        except Exception as e:
            logger.error(f"Automatic benchmark promotion failed for {model_id}: {str(e)}")
            return ServiceResult.fail(ForgeError(f"Promotion failed: {str(e)}"))
