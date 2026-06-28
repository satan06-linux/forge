import logging
from typing import Dict, Any, Optional
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class CodingAgent:
    """
    Software engineering agent capable of writing, refactoring, and reviewing code.
    """
    def __init__(self, container: Any):
        self.container = container
        self.storage_provider = container.get('StorageProvider')
        self.llm_service = container.get('LlmService')
        self.file_service = container.get('FileService')
        
    def write_code(self, task_description: str, file_path: str, context: Optional[str] = None) -> ServiceResult[Dict[str, Any]]:
        try:
            if not task_description or not file_path:
                return ServiceResult.fail(ForgeError(code="INVALID_INPUT", message="Task description and file path are required."))
                
            prompt = f"Write Python code for the following task:\nTask: {task_description}\nTarget File: {file_path}\nContext: {context or ''}\n\nReturn ONLY valid python code. No markdown formatting or explanations."
            
            llm_result = self.llm_service.generate(prompt=prompt, system_prompt="You are an expert Software Engineering Agent.")
            if not llm_result.is_success:
                return ServiceResult.fail(ForgeError(code="CODE_GENERATION_FAILED", message="Failed to generate code via LLM."))
                
            code_content = llm_result.data.strip()
            if code_content.startswith("```python"):
                code_content = code_content[9:]
            if code_content.endswith("```"):
                code_content = code_content[:-3]
            code_content = code_content.strip()
            
            file_result = self.file_service.write_file(file_path, code_content)
            if not file_result.is_success:
                 return ServiceResult.fail(ForgeError(code="FILE_WRITE_FAILED", message=f"Failed to write code to {file_path}."))
                 
            record = {
                "task": task_description,
                "file_path": file_path,
                "status": "completed"
            }
            self.storage_provider.save("coding_tasks", record)
            
            return ServiceResult.success({
                "file_path": file_path,
                "code_length": len(code_content),
                "status": "success"
            })
            
        except Exception as e:
            logger.error(f"Error in CodingAgent.write_code: {str(e)}")
            return ServiceResult.fail(ForgeError(code="CODING_ERROR", message=f"Code generation failed: {str(e)}"))

    def refactor_code(self, file_path: str, refactor_goal: str) -> ServiceResult[Dict[str, Any]]:
        try:
            read_result = self.file_service.read_file(file_path)
            if not read_result.is_success:
                return ServiceResult.fail(ForgeError(code="FILE_READ_FAILED", message=f"Could not read {file_path} for refactoring."))
                
            original_code = read_result.data
            prompt = f"Refactor the following code to achieve this goal: {refactor_goal}\n\n{original_code}\n\nReturn ONLY the updated python code without markdown formatting."
            
            llm_result = self.llm_service.generate(prompt=prompt, system_prompt="You are an expert Software Engineering Agent specializing in refactoring.")
            if not llm_result.is_success:
                 return ServiceResult.fail(ForgeError(code="REFACTORING_FAILED", message="LLM failed to refactor code."))
                 
            refactored_code = llm_result.data.strip()
            if refactored_code.startswith("```python"):
                refactored_code = refactored_code[9:]
            if refactored_code.endswith("```"):
                refactored_code = refactored_code[:-3]
            refactored_code = refactored_code.strip()
            
            write_result = self.file_service.write_file(file_path, refactored_code)
            if not write_result.is_success:
                 return ServiceResult.fail(ForgeError(code="FILE_WRITE_FAILED", message=f"Failed to save refactored code to {file_path}."))
                 
            self.storage_provider.save("refactoring_tasks", {
                "file_path": file_path,
                "goal": refactor_goal,
                "status": "completed"
            })
            
            return ServiceResult.success({"file_path": file_path, "status": "refactored"})
            
        except Exception as e:
            logger.error(f"Error in CodingAgent.refactor_code: {str(e)}")
            return ServiceResult.fail(ForgeError(code="REFACTOR_ERROR", message=str(e)))
