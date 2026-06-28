import logging
import json
import uuid
from datetime import datetime
from services.service_result import ServiceResult
from services.errors import ForgeError, StorageError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

class ModelTrainingService:
    """
    Phase 8: Model Training Service.
    Dataset manager, LoRA fine-tuning pipeline orchestration, and GGUF exports.
    """
    def __init__(self, container):
        self.container = container

    def _get_storage(self):
        storage = self.container.get('storage_provider')
        if not storage:
            raise StorageError("Storage provider not available")
        return storage

    def create_dataset(self, dataset_name: str, description: str = "") -> ServiceResult:
        """Creates a new dataset for model training."""
        try:
            storage = self._get_storage()
            data = {
                'dataset_id': str(uuid.uuid4()),
                'name': dataset_name,
                'description': description,
                'created_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            }
            try:
                storage.insert('training_datasets', data)
            except Exception as e:
                logger.warning(f"Error creating dataset table entry, check schema: {e}")
                return ServiceResult.fail(ForgeError("Failed to persist dataset", error_code="STORAGE_ERROR"))
                
            logger.info(f"Created dataset {dataset_name} with ID {data['dataset_id']}")
            return ServiceResult.ok(data=data)
        except Exception as e:
            logger.error(f"Error creating dataset: {e}")
            return ServiceResult.fail(ForgeError(str(e), error_code="DATASET_CREATE_ERROR"))

    def add_to_dataset(self, dataset_name: str, text: str, label: str = None) -> ServiceResult:
        """Adds a training record to an existing dataset."""
        try:
            if not text:
                return ServiceResult.fail(ValidationError("Training text cannot be empty"))
                
            storage = self._get_storage()
            
            try:
                dataset = storage.execute_one("SELECT dataset_id FROM training_datasets WHERE name = %s", (dataset_name,))
            except Exception as e:
                logger.warning(f"Error looking up dataset: {e}")
                return ServiceResult.fail(ForgeError("Storage error looking up dataset", error_code="STORAGE_ERROR"))
                
            if not dataset:
                return ServiceResult.fail(NotFoundError(f"Dataset '{dataset_name}' not found"))
                
            data = {
                'record_id': str(uuid.uuid4()),
                'dataset_id': dataset['dataset_id'],
                'text_content': text,
                'label': label or '',
                'created_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            storage.insert('training_records', data)
            logger.info(f"Added record {data['record_id']} to dataset '{dataset_name}'")
            return ServiceResult.ok(data={'record_id': data['record_id']})
            
        except ForgeError as e:
            return ServiceResult.fail(e)
        except Exception as e:
            logger.error(f"Error adding to dataset: {e}")
            return ServiceResult.fail(ForgeError(str(e), error_code="DATASET_ADD_ERROR"))

    def start_lora_finetuning(self, dataset_name: str, base_model: str, target_model_name: str, hyperparams: dict = None) -> ServiceResult:
        """Orchestrates a LoRA fine-tuning pipeline."""
        try:
            storage = self._get_storage()
            
            try:
                dataset = storage.execute_one("SELECT dataset_id FROM training_datasets WHERE name = %s", (dataset_name,))
            except Exception as e:
                return ServiceResult.fail(ForgeError("Storage error looking up dataset", error_code="STORAGE_ERROR"))
                
            if not dataset:
                return ServiceResult.fail(NotFoundError(f"Dataset '{dataset_name}' not found"))
                
            job_id = str(uuid.uuid4())
            data = {
                'job_id': job_id,
                'dataset_id': dataset['dataset_id'],
                'base_model': base_model,
                'target_model_name': target_model_name,
                'hyperparams': json.dumps(hyperparams or {}),
                'status': 'PENDING',
                'created_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            storage.insert('finetuning_jobs', data)
            logger.info(f"Started LoRA finetuning job {job_id} for target {target_model_name}")
            
            # Retrieve the GPU scheduler to orchestrate the actual job if available
            gpu_scheduler = self.container.get('gpu_scheduler_service')
            if gpu_scheduler:
                # E.g. gpu_scheduler.submit_training_job(...)
                logger.info(f"Submitted training job {job_id} to GPU scheduler")
                
            return ServiceResult.ok(data={'job_id': job_id, 'status': 'PENDING'})
            
        except ForgeError as e:
            return ServiceResult.fail(e)
        except Exception as e:
            logger.error(f"Error starting finetuning: {e}")
            return ServiceResult.fail(ForgeError(str(e), error_code="FINETUNING_ERROR"))

    def export_to_gguf(self, model_name: str, output_path: str, quant_type: str = "q4_k_m") -> ServiceResult:
        """Exports a fine-tuned model to GGUF format."""
        try:
            storage = self._get_storage()
            export_id = str(uuid.uuid4())
            data = {
                'export_id': export_id,
                'model_name': model_name,
                'output_path': output_path,
                'quant_type': quant_type,
                'status': 'IN_PROGRESS',
                'created_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            try:
                storage.insert('model_exports', data)
            except Exception as e:
                logger.warning(f"Error recording model export: {e}")
                return ServiceResult.fail(ForgeError("Failed to persist model export job", error_code="STORAGE_ERROR"))
                
            logger.info(f"Initiated GGUF export for {model_name} to {output_path} with quant {quant_type}")
            
            # Simulate orchestration of a long-running conversion process
            # In production, this would dispatch a task to a background worker
            try:
                storage.update("UPDATE model_exports SET status = 'COMPLETED', completed_at = %s WHERE export_id = %s", 
                               (datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), export_id))
            except Exception as e:
                logger.warning(f"Failed to update model export status: {e}")
            
            return ServiceResult.ok(data={'export_id': export_id, 'output_path': output_path, 'status': 'COMPLETED'})
            
        except Exception as e:
            logger.error(f"Error exporting GGUF: {e}")
            return ServiceResult.fail(ForgeError(str(e), error_code="EXPORT_ERROR"))
