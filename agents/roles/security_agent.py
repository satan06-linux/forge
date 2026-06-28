import logging
import json
from typing import Dict, Any
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class SecurityAgent:
    """
    Red team agent searching for vulnerabilities and assessing security posture.
    """
    def __init__(self, container: Any):
        self.container = container
        self.storage_provider = container.get('StorageProvider')
        self.llm_service = container.get('LlmService')
        self.file_service = container.get('FileService')
        
    def analyze_vulnerabilities(self, file_path: str) -> ServiceResult[Dict[str, Any]]:
        try:
            if not file_path:
                return ServiceResult.fail(ForgeError(code="INVALID_INPUT", message="File path is required."))
                
            read_result = self.file_service.read_file(file_path)
            if not read_result.is_success:
                return ServiceResult.fail(ForgeError(code="FILE_READ_FAILED", message="Failed to read file for security analysis."))
                
            code_content = read_result.data
            
            prompt = (
                f"Analyze the following code for security vulnerabilities (e.g., OWASP top 10, injection, improper auth).\n\n"
                f"{code_content}\n\n"
                "Provide a JSON list of dictionaries. Each dictionary must contain keys: 'vulnerability', 'severity', 'line_number', and 'mitigation'."
            )
            
            llm_result = self.llm_service.generate(prompt=prompt, system_prompt="You are an expert Red Team Security Agent. Output valid JSON list only.")
            if not llm_result.is_success:
                return ServiceResult.fail(ForgeError(code="SECURITY_ANALYSIS_FAILED", message="Failed to perform security analysis."))
                
            raw_response = llm_result.data.strip()
            if raw_response.startswith("```json"):
                raw_response = raw_response[7:]
            if raw_response.endswith("```"):
                raw_response = raw_response[:-3]
            raw_response = raw_response.strip()
            
            try:
                vulnerabilities = json.loads(raw_response)
            except json.JSONDecodeError:
                logger.warning("Failed to parse security vulnerabilities as JSON.")
                vulnerabilities = [{"raw_analysis": raw_response, "severity": "unknown"}]
            
            record = {
                "file_path": file_path,
                "vulnerabilities_found": len(vulnerabilities),
                "details": vulnerabilities
            }
            self.storage_provider.save("security_audits", record)
            
            return ServiceResult.success({
                "file_path": file_path,
                "vulnerability_count": len(vulnerabilities),
                "vulnerabilities": vulnerabilities
            })
            
        except Exception as e:
            logger.error(f"Error in SecurityAgent.analyze_vulnerabilities: {str(e)}")
            return ServiceResult.fail(ForgeError(code="SECURITY_AGENT_ERROR", message=str(e)))
            
    def generate_threat_model(self, architecture_description: str) -> ServiceResult[Dict[str, Any]]:
        try:
            prompt = (
                f"Generate a threat model based on the following architecture description using STRIDE methodology:\n\n"
                f"{architecture_description}\n\n"
                "Return a JSON object detailing threats under each STRIDE category."
            )
            
            llm_result = self.llm_service.generate(prompt=prompt, system_prompt="You are a Cybersecurity Threat Modeling Agent. Return valid JSON only.")
            if not llm_result.is_success:
                 return ServiceResult.fail(ForgeError(code="THREAT_MODEL_FAILED", message="Failed to generate threat model."))
                 
            try:
                threat_model = json.loads(llm_result.data)
            except json.JSONDecodeError:
                threat_model = {"raw_threat_model": llm_result.data}
                
            self.storage_provider.save("threat_models", {"model": threat_model})
            
            return ServiceResult.success({"threat_model": threat_model})
            
        except Exception as e:
             logger.error(f"Error in SecurityAgent.generate_threat_model: {str(e)}")
             return ServiceResult.fail(ForgeError(code="SECURITY_AGENT_ERROR", message=str(e)))
