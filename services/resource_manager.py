import logging
from typing import Dict, Optional, Set

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class ResourceMonitorError(ForgeError):
    default_error_code = "RESOURCE_MONITOR_ERROR"
    default_retryable = True

class ResourceManager:
    """
    Monitors System Resources: CPU, RAM, GPU (VRAM, Temp, Power).
    Provides methods to evaluate the system state and automatically
    throttle or pause training jobs if the system is overloaded.
    """
    def __init__(self, thresholds: Optional[Dict[str, float]] = None):
        self.thresholds = thresholds or {
            "cpu_percent_critical": 95.0,
            "cpu_percent_throttle": 85.0,
            "ram_percent_critical": 95.0,
            "ram_percent_throttle": 85.0,
            "gpu_vram_percent_critical": 95.0,
            "gpu_vram_percent_throttle": 90.0,
            "gpu_temp_c_critical": 88.0,
            "gpu_temp_c_throttle": 80.0,
            "gpu_power_w_critical": 300.0,
            "gpu_power_w_throttle": 250.0,
        }
        self.paused_jobs: Set[str] = set()

    def _get_cpu_ram_stats(self) -> Dict[str, float]:
        stats = {"cpu_percent": 0.0, "ram_percent": 0.0}
        try:
            import psutil
            stats["cpu_percent"] = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory()
            stats["ram_percent"] = ram.percent
        except ImportError:
            logger.warning("psutil not installed. Returning mock CPU/RAM stats.")
            stats["cpu_percent"] = 50.0
            stats["ram_percent"] = 60.0
        except Exception as e:
            logger.error(f"Failed reading CPU/RAM via psutil: {e}")
        return stats

    def _get_gpu_stats(self) -> Dict[str, float]:
        stats = {"vram_percent": 0.0, "temp_c": 0.0, "power_w": 0.0}
        try:
            import pynvml
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            stats["vram_percent"] = (mem_info.used / float(mem_info.total)) * 100.0
            
            stats["temp_c"] = float(pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU))
            
            power_mw = pynvml.nvmlDeviceGetPowerUsage(handle)
            stats["power_w"] = power_mw / 1000.0
            
        except ImportError:
            logger.debug("pynvml not installed. Using mock GPU stats.")
            stats["vram_percent"] = 40.0
            stats["temp_c"] = 65.0
            stats["power_w"] = 120.0
        except Exception as e:
            logger.debug(f"pynvml reading failed: {e}. Using mock GPU stats.")
            stats["vram_percent"] = 40.0
            stats["temp_c"] = 65.0
            stats["power_w"] = 120.0
            
        return stats

    def get_system_stats(self) -> ServiceResult:
        """
        Retrieves real-time system utilization statistics.
        Returns a ServiceResult containing cpu, ram, and gpu metrics.
        """
        try:
            cpu_ram = self._get_cpu_ram_stats()
            gpu_stats = self._get_gpu_stats()
            
            stats = {
                "cpu_percent": cpu_ram["cpu_percent"],
                "ram_percent": cpu_ram["ram_percent"],
                "gpu_vram_percent": gpu_stats["vram_percent"],
                "gpu_temp_c": gpu_stats["temp_c"],
                "gpu_power_w": gpu_stats["power_w"],
            }
            return ServiceResult.ok(stats)
        except Exception as e:
            logger.error(f"Failed to gather system statistics: {e}", exc_info=True)
            return ServiceResult.fail(ResourceMonitorError(f"Failed to get system stats: {e}"))

    def evaluate_resources(self, stats: Dict[str, float]) -> str:
        """
        Evaluates current stats against thresholds to determine action:
        Returns 'pause', 'throttle', or 'continue'.
        """
        if (stats["cpu_percent"] >= self.thresholds["cpu_percent_critical"] or
            stats["ram_percent"] >= self.thresholds["ram_percent_critical"] or
            stats["gpu_vram_percent"] >= self.thresholds["gpu_vram_percent_critical"] or
            stats["gpu_temp_c"] >= self.thresholds["gpu_temp_c_critical"] or
            stats["gpu_power_w"] >= self.thresholds["gpu_power_w_critical"]):
            return "pause"
            
        if (stats["cpu_percent"] >= self.thresholds["cpu_percent_throttle"] or
            stats["ram_percent"] >= self.thresholds["ram_percent_throttle"] or
            stats["gpu_vram_percent"] >= self.thresholds["gpu_vram_percent_throttle"] or
            stats["gpu_temp_c"] >= self.thresholds["gpu_temp_c_throttle"] or
            stats["gpu_power_w"] >= self.thresholds["gpu_power_w_throttle"]):
            return "throttle"
            
        return "continue"

    def enforce_resource_limits(self, job_id: str) -> ServiceResult:
        """
        Maintains health of a given job ID. Should be called periodically during training.
        """
        stats_result = self.get_system_stats()
        if not stats_result.success:
            return stats_result
            
        stats = stats_result.data
        action = self.evaluate_resources(stats)
        
        result_data = {
            "job_id": job_id,
            "action_taken": action,
            "stats": stats
        }
        
        try:
            if action == "pause":
                if job_id not in self.paused_jobs:
                    logger.warning(f"System overloaded. Pausing job {job_id}. Stats: {stats}")
                    self.paused_jobs.add(job_id)
            elif action == "throttle":
                logger.warning(f"System under heavy load. Throttling job {job_id}. Stats: {stats}")
            elif action == "continue":
                if job_id in self.paused_jobs:
                    logger.info(f"System resources normalized. Resuming job {job_id}.")
                    self.paused_jobs.remove(job_id)
                    result_data["action_taken"] = "resume"
                    
            return ServiceResult.ok(result_data)
        except Exception as e:
            logger.error(f"Error enforcing limits for job {job_id}: {e}", exc_info=True)
            return ServiceResult.fail(ResourceMonitorError(f"Error enforcing limits: {e}"))
