import logging
import os
import uuid
import datetime
import threading
import time
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class ExportFormat(str, Enum):
    GGUF = "gguf"
    SAFETENSORS = "safetensors"

class QuantizationLevel(str, Enum):
    Q2_K = "q2_k"
    Q3_K_M = "q3_k_m"
    Q4_K_M = "q4_k_m"
    Q5_K_M = "q5_k_m"
    Q6_K = "q6_k"
    Q8_0 = "q8_0"
    F16 = "f16"

@dataclass
class ExportRequest:
    export_id: str
    model_id: str
    format: ExportFormat
    quantization: QuantizationLevel
    output_path: str
    status: str
    created_at: str

class ModelExportService:
    """
    Converters for GGUF/Safetensors, supporting Quantization matrix (Q2-Q8) 
    and automatically triggering Model Card generation.
    """
    def __init__(self):
        self._exports: Dict[str, ExportRequest] = {}
        self._lock = threading.Lock()

    def request_export(
        self, 
        model_id: str, 
        export_format: str, 
        quantization: str, 
        output_dir: str
    ) -> ServiceResult:
        try:
            try:
                fmt = ExportFormat(export_format.lower())
            except ValueError:
                return ServiceResult.fail(ForgeError(f"Unsupported export format: {export_format}"))

            try:
                quant = QuantizationLevel(quantization.lower())
            except ValueError:
                return ServiceResult.fail(ForgeError(f"Unsupported quantization level: {quantization}"))

            export_id = f"exp-{uuid.uuid4().hex[:8]}"
            output_filename = f"{model_id}-{quant.value}.{fmt.value}"
            output_path = os.path.join(output_dir, output_filename)

            req = ExportRequest(
                export_id=export_id,
                model_id=model_id,
                format=fmt,
                quantization=quant,
                output_path=output_path,
                status="pending",
                created_at=datetime.datetime.utcnow().isoformat()
            )

            with self._lock:
                self._exports[export_id] = req

            logger.info(f"Requested export {export_id} for model {model_id} format={fmt.value} quant={quant.value}")

            # Trigger background conversion
            threading.Thread(target=self._process_export, args=(export_id,), daemon=True).start()

            return ServiceResult.ok(data={
                "export_id": export_id,
                "status": req.status,
                "output_path": output_path
            })
        except Exception as e:
            logger.exception("Failed to request model export")
            return ServiceResult.fail(ForgeError(f"Internal error during export request: {str(e)}"))

    def _process_export(self, export_id: str):
        with self._lock:
            if export_id not in self._exports:
                return
            req = self._exports[export_id]
            req.status = "converting"

        logger.info(f"Starting conversion for export {export_id}...")
        
        try:
            # Simulate conversion process
            time.sleep(2.0)
            
            # Simulate file creation
            os.makedirs(os.path.dirname(req.output_path), exist_ok=True)
            with open(req.output_path, 'w') as f:
                f.write(f"Mock model data: {req.model_id} [{req.format.value}] ({req.quantization.value})")

            # Trigger Model Card Generation
            self._generate_model_card(req)

            with self._lock:
                req.status = "completed"
            logger.info(f"Export {export_id} completed successfully.")

        except Exception as e:
            logger.error(f"Export {export_id} failed: {e}")
            with self._lock:
                req.status = "failed"

    def _generate_model_card(self, req: ExportRequest):
        logger.info(f"Automatically generating Model Card for export {req.export_id}...")
        card_path = f"{req.output_path}.modelcard.md"
        try:
            with open(card_path, 'w') as f:
                f.write(f"# Model Card: {req.model_id}\n\n")
                f.write(f"- **Format:** {req.format.value.upper()}\n")
                f.write(f"- **Quantization:** {req.quantization.value.upper()}\n")
                f.write(f"- **Exported At:** {req.created_at}\n\n")
                f.write("## Usage\n")
                f.write(f"This model has been quantized using {req.quantization.value} for optimized inference.\n")
            logger.info(f"Model Card generated at {card_path}")
        except Exception as e:
            logger.error(f"Failed to generate model card for {req.export_id}: {e}")

    def get_export_status(self, export_id: str) -> ServiceResult:
        with self._lock:
            if export_id not in self._exports:
                return ServiceResult.fail(ForgeError(f"Export {export_id} not found", error_code="NOT_FOUND"))
            
            req = self._exports[export_id]
            return ServiceResult.ok(data={
                "export_id": req.export_id,
                "model_id": req.model_id,
                "status": req.status,
                "output_path": req.output_path
            })
