import logging
from typing import Dict, Any, Optional
import requests
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class LocalAIEngine:
    def __init__(self, container):
        self.container = container
        self.gpu_scheduler = container.get("gpu_scheduler_service")
        self.storage_provider = container.get("storage_provider")
        self.ollama_base_url = container.get_config("ollama_base_url", "http://localhost:11434")

    def register_gguf_model(self, model_name: str, file_path: str, vram_required_mb: int) -> ServiceResult:
        try:
            db = self.storage_provider
            db.execute(
                "INSERT INTO gguf_model_registry (name, path, vram_required_mb) VALUES (?, ?, ?)",
                (model_name, file_path, vram_required_mb)
            )
            return ServiceResult.success({"model": model_name, "status": "registered"})
        except Exception as e:
            logger.error(f"Failed to register GGUF model {model_name}: {e}")
            return ServiceResult.fail(ForgeError(f"GGUF registration error: {e}"))

    def get_model_info(self, model_name: str) -> ServiceResult:
        try:
            db = self.storage_provider
            row = db.query_one(
                "SELECT name, path, vram_required_mb FROM gguf_model_registry WHERE name = ?",
                (model_name,)
            )
            if not row:
                # If not in registry, assume default ollama behavior or dynamic download.
                # Here we fallback to standard ollama query and estimate 8GB for an 8b model
                return ServiceResult.success({
                    "name": model_name,
                    "path": "ollama_internal",
                    "vram_required_mb": 8192
                })
            return ServiceResult.success({
                "name": row["name"],
                "path": row["path"],
                "vram_required_mb": row["vram_required_mb"]
            })
        except Exception as e:
            logger.error(f"Failed to fetch model info for {model_name}: {e}")
            return ServiceResult.fail(ForgeError(f"GGUF registry error: {e}"))
            
    def get_all_models(self) -> ServiceResult:
        try:
            db = self.storage_provider
            rows = db.query_all("SELECT name, path, vram_required_mb FROM gguf_model_registry")
            return ServiceResult.success([dict(row) for row in rows])
        except Exception as e:
            logger.error(f"Failed to fetch all models: {e}")
            return ServiceResult.fail(ForgeError(f"GGUF registry error: {e}"))

    def load_model(self, model_name: str) -> ServiceResult:
        info_res = self.get_model_info(model_name)
        if not info_res.is_success:
            return info_res
            
        vram_required = info_res.data["vram_required_mb"]
        allocation_res = self.gpu_scheduler.allocate(model_name, vram_required)
        if not allocation_res.is_success:
            return allocation_res
            
        try:
            # Tell Ollama/llama.cpp to preload the model
            response = requests.post(f"{self.ollama_base_url}/api/generate", json={
                "model": model_name,
                "keep_alive": "10m"
            }, timeout=60)
            if response.status_code != 200:
                self.gpu_scheduler.release(model_name)
                return ServiceResult.fail(ForgeError(f"Failed to load model in engine: {response.text}"))
            return ServiceResult.success({"model": model_name, "status": "loaded", "device": allocation_res.data.get("device", "cpu")})
        except Exception as e:
            logger.error(f"Error communicating with local AI engine: {e}")
            self.gpu_scheduler.release(model_name)
            return ServiceResult.fail(ForgeError(f"Engine connection error: {e}"))

    def generate(self, model_name: str, prompt: str, options: Optional[Dict[str, Any]] = None) -> ServiceResult:
        load_res = self.load_model(model_name)
        if not load_res.is_success:
            return load_res
            
        payload = {
            "model": model_name,
            "prompt": prompt,
            "stream": False
        }
        if options:
            payload["options"] = options
            
        try:
            response = requests.post(f"{self.ollama_base_url}/api/generate", json=payload, timeout=300)
            if response.status_code == 200:
                return ServiceResult.success(response.json())
            return ServiceResult.fail(ForgeError(f"Generation failed: {response.text}"))
        except Exception as e:
            logger.error(f"Generate error: {e}")
            return ServiceResult.fail(ForgeError(f"Generate exception: {e}"))

    def unload_model(self, model_name: str) -> ServiceResult:
        try:
            response = requests.post(f"{self.ollama_base_url}/api/generate", json={
                "model": model_name,
                "keep_alive": 0
            }, timeout=30)
            self.gpu_scheduler.release(model_name)
            if response.status_code == 200:
                return ServiceResult.success({"model": model_name, "status": "unloaded"})
            return ServiceResult.fail(ForgeError(f"Unload failed: {response.text}"))
        except Exception as e:
            logger.error(f"Unload error: {e}")
            return ServiceResult.fail(ForgeError(f"Unload exception: {e}"))
