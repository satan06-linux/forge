import logging
import time
import uuid
import threading
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class TrainingProfile(str, Enum):
    CODE_EXPERT = "code_expert"
    CHATBOT = "chatbot"
    MATH_REASONING = "math_reasoning"
    GENERAL_INSTRUCT = "general_instruct"
    CREATIVE_WRITING = "creative_writing"

@dataclass
class TrainingMetrics:
    epoch: float = 0.0
    loss: float = 0.0
    tokens_per_sec: float = 0.0
    learning_rate: float = 0.0

@dataclass
class TrainingJob:
    job_id: str
    model_id: str
    dataset_path: str
    profile: TrainingProfile
    config: Dict[str, Any]
    status: str = "pending"
    metrics_history: List[TrainingMetrics] = field(default_factory=list)
    nodes: List[str] = field(default_factory=list)

class LoraTrainingService:
    """
    Distributed training engine across local GPU clusters supporting QLoRA/PEFT, 
    streaming real-time metrics (Tokens/sec, Epoch, Loss) into the Training History database.
    """
    def __init__(self):
        self._jobs: Dict[str, TrainingJob] = {}
        self._history_db: Dict[str, List[TrainingMetrics]] = {}
        self._lock = threading.Lock()

    def submit_training_job(
        self, 
        model_id: str, 
        dataset_path: str, 
        profile: str, 
        peft_config: Optional[Dict[str, Any]] = None,
        gpu_nodes: Optional[List[str]] = None
    ) -> ServiceResult:
        """
        Submits a distributed QLoRA/PEFT training job.
        """
        try:
            try:
                training_profile = TrainingProfile(profile)
            except ValueError:
                return ServiceResult.fail(ForgeError(f"Invalid training profile: {profile}"))

            job_id = f"job-{uuid.uuid4().hex[:8]}"
            
            peft_config = peft_config or {
                "r": 16,
                "lora_alpha": 32,
                "target_modules": ["q_proj", "v_proj"],
                "lora_dropout": 0.05,
                "bias": "none",
                "task_type": "CAUSAL_LM"
            }
            
            nodes = gpu_nodes or ["localhost:gpu:0"]

            job = TrainingJob(
                job_id=job_id,
                model_id=model_id,
                dataset_path=dataset_path,
                profile=training_profile,
                config=peft_config,
                status="initializing",
                nodes=nodes
            )

            with self._lock:
                self._jobs[job_id] = job
                self._history_db[job_id] = []

            logger.info(f"Submitted training job {job_id} for model {model_id} using profile {training_profile.value}.")
            
            # Start simulated training thread
            threading.Thread(target=self._simulate_training, args=(job_id,), daemon=True).start()
            
            return ServiceResult.ok(data={"job_id": job_id, "status": job.status})
        except Exception as e:
            logger.exception("Failed to submit training job.")
            return ServiceResult.fail(ForgeError(f"Internal error submitting training job: {str(e)}"))

    def _simulate_training(self, job_id: str):
        """Simulates a training run streaming metrics."""
        with self._lock:
            if job_id not in self._jobs:
                return
            job = self._jobs[job_id]
            job.status = "running"
        
        logger.info(f"Job {job_id} is running across nodes: {job.nodes}")

        epochs = 3
        steps_per_epoch = 10
        current_loss = 2.5
        lr = 2e-4

        try:
            for epoch in range(epochs):
                for step in range(steps_per_epoch):
                    if job.status in ["cancelled", "failed"]:
                        return
                    
                    time.sleep(0.5) # Simulate time taken per step
                    current_epoch = epoch + (step / steps_per_epoch)
                    current_loss = max(0.5, current_loss * 0.95) # simulate learning
                    tps = 4500.0 + (step * 10)
                    
                    metrics = TrainingMetrics(
                        epoch=round(current_epoch, 3),
                        loss=round(current_loss, 4),
                        tokens_per_sec=round(tps, 2),
                        learning_rate=lr
                    )
                    
                    self.stream_metrics(job_id, metrics)
            
            with self._lock:
                job.status = "completed"
                logger.info(f"Training job {job_id} completed successfully.")

        except Exception as e:
            logger.error(f"Training job {job_id} failed: {e}")
            with self._lock:
                job.status = "failed"

    def stream_metrics(self, job_id: str, metrics: TrainingMetrics) -> ServiceResult:
        """Streams real-time metrics to the Training History database."""
        with self._lock:
            if job_id not in self._jobs:
                return ServiceResult.fail(ForgeError(f"Job {job_id} not found"))
            
            self._jobs[job_id].metrics_history.append(metrics)
            self._history_db[job_id].append(metrics)
            
            logger.debug(f"Metrics for {job_id}: Epoch={metrics.epoch}, Loss={metrics.loss}, TPS={metrics.tokens_per_sec}")
        
        return ServiceResult.ok(data={"streamed": True})

    def get_job_status(self, job_id: str) -> ServiceResult:
        with self._lock:
            if job_id not in self._jobs:
                return ServiceResult.fail(ForgeError(f"Job {job_id} not found", error_code="NOT_FOUND"))
            
            job = self._jobs[job_id]
            
            # Fetch latest metrics if any
            latest_metrics = job.metrics_history[-1] if job.metrics_history else None
            
            data = {
                "job_id": job.job_id,
                "model_id": job.model_id,
                "status": job.status,
                "nodes": job.nodes,
                "latest_metrics": latest_metrics.__dict__ if latest_metrics else None
            }
            return ServiceResult.ok(data=data)
            
    def cancel_job(self, job_id: str) -> ServiceResult:
        with self._lock:
            if job_id not in self._jobs:
                return ServiceResult.fail(ForgeError(f"Job {job_id} not found", error_code="NOT_FOUND"))
            
            job = self._jobs[job_id]
            if job.status in ["completed", "failed"]:
                return ServiceResult.fail(ForgeError(f"Job {job_id} is already {job.status}"))
                
            job.status = "cancelled"
            logger.info(f"Training job {job_id} was cancelled.")
            return ServiceResult.ok(data={"job_id": job_id, "status": job.status})
