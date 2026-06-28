import logging
import threading
import time
from typing import Dict, List, Any, Optional
from collections import deque
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class GPUSchedulerService:
    def __init__(self, container):
        self.container = container
        self.total_vram_mb = container.get_config("gpu_total_vram_mb", 24000)
        self.used_vram_mb = 0
        self.loaded_models: Dict[str, int] = {} # model_name -> vram_used
        self.model_access_times: Dict[str, float] = {} # for LRU swapping
        self.lock = threading.RLock()
        self.queue = deque()
        
    def _get_lru_model(self) -> Optional[str]:
        if not self.loaded_models:
            return None
        return min(self.model_access_times, key=self.model_access_times.get)

    def _free_vram(self, required_mb: int) -> bool:
        if self.total_vram_mb - self.used_vram_mb >= required_mb:
            return True
            
        while self.loaded_models and self.total_vram_mb - self.used_vram_mb < required_mb:
            lru_model = self._get_lru_model()
            if not lru_model:
                break
                
            freed = self.loaded_models.pop(lru_model)
            if lru_model in self.model_access_times:
                del self.model_access_times[lru_model]
            self.used_vram_mb -= freed
            logger.info(f"Swapped out LRU model {lru_model} to free {freed}MB VRAM. Remaining usage: {self.used_vram_mb}/{self.total_vram_mb}MB.")
            
        return self.total_vram_mb - self.used_vram_mb >= required_mb

    def allocate(self, model_name: str, required_vram_mb: int) -> ServiceResult:
        with self.lock:
            # CPU Fallback handling
            if required_vram_mb > self.total_vram_mb:
                logger.warning(f"Model {model_name} requires {required_vram_mb}MB which exceeds total GPU VRAM ({self.total_vram_mb}MB). Falling back to CPU scheduling.")
                return ServiceResult.success({"status": "cpu_fallback", "vram_mb": 0, "device": "cpu"})
                
            if model_name in self.loaded_models:
                self.model_access_times[model_name] = time.time()
                return ServiceResult.success({"status": "already_loaded", "vram_mb": required_vram_mb, "device": "gpu"})
                
            # Attempt to free VRAM for new allocation
            if self._free_vram(required_vram_mb):
                self.loaded_models[model_name] = required_vram_mb
                self.model_access_times[model_name] = time.time()
                self.used_vram_mb += required_vram_mb
                logger.info(f"Allocated {required_vram_mb}MB VRAM for {model_name}. Total used: {self.used_vram_mb}/{self.total_vram_mb}MB.")
                return ServiceResult.success({"status": "allocated", "vram_mb": required_vram_mb, "device": "gpu"})
            
            logger.warning(f"Could not free enough VRAM for {model_name}. Forcing CPU fallback to prevent VRAM overflow.")
            return ServiceResult.success({"status": "cpu_fallback", "vram_mb": 0, "device": "cpu"})

    def queue_task(self, model_name: str, task: Dict[str, Any]) -> ServiceResult:
        with self.lock:
            self.queue.append({"model_name": model_name, "task": task, "timestamp": time.time()})
            logger.info(f"Queued task for {model_name}. Queue length: {len(self.queue)}")
            return ServiceResult.success({"status": "queued", "queue_position": len(self.queue)})

    def get_next_batch(self, batch_size: int = 4) -> ServiceResult:
        with self.lock:
            if not self.queue:
                return ServiceResult.success({"status": "empty", "batch": []})
                
            batch = []
            for _ in range(min(batch_size, len(self.queue))):
                batch.append(self.queue.popleft())
                
            return ServiceResult.success({"status": "batched", "batch": batch})

    def release(self, model_name: str) -> ServiceResult:
        with self.lock:
            if model_name in self.loaded_models:
                freed = self.loaded_models.pop(model_name)
                if model_name in self.model_access_times:
                    del self.model_access_times[model_name]
                self.used_vram_mb -= freed
                logger.info(f"Released {freed}MB VRAM for {model_name}. Total used: {self.used_vram_mb}/{self.total_vram_mb}MB.")
                return ServiceResult.success({"status": "released", "freed_mb": freed})
            return ServiceResult.success({"status": "not_loaded", "freed_mb": 0})
            
    def get_status(self) -> ServiceResult:
        with self.lock:
            return ServiceResult.success({
                "total_vram_mb": self.total_vram_mb,
                "used_vram_mb": self.used_vram_mb,
                "free_vram_mb": self.total_vram_mb - self.used_vram_mb,
                "loaded_models": list(self.loaded_models.keys()),
                "queue_length": len(self.queue)
            })
