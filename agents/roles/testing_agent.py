import logging
from typing import Dict, Any, List
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class TestingAgent:
    """
    QA and validation agent responsible for generating and executing tests.
    """
    def __init__(self, container: Any):
        self.container = container
        self.storage_provider = container.get('StorageProvider')
        self.llm_service = container.get('LlmService')
        self.execution_service = container.get('ExecutionService')
        self.file_service = container.get('FileService')
        
    def generate_tests(self, target_file_path: str, test_file_path: str) -> ServiceResult[Dict[str, Any]]:
        try:
            if not target_file_path or not test_file_path:
                return ServiceResult.fail(ForgeError(code="INVALID_INPUT", message="Target and test file paths are required."))
                
            read_result = self.file_service.read_file(target_file_path)
            if not read_result.is_success:
                return ServiceResult.fail(ForgeError(code="FILE_READ_FAILED", message="Failed to read target file."))
                
            code_content = read_result.data
            
            prompt = (
                f"Write comprehensive pytest unit tests for the following Python code:\n\n{code_content}\n\n"
                f"Return ONLY valid python test code without markdown wrappers."
            )
            llm_result = self.llm_service.generate(prompt=prompt, system_prompt="You are an expert QA and Testing Agent.")
            
            if not llm_result.is_success:
                return ServiceResult.fail(ForgeError(code="TEST_GENERATION_FAILED", message="Failed to generate tests."))
                
            test_content = llm_result.data.strip()
            if test_content.startswith("```python"):
                test_content = test_content[9:]
            if test_content.endswith("```"):
                test_content = test_content[:-3]
            test_content = test_content.strip()
            
            write_result = self.file_service.write_file(test_file_path, test_content)
            if not write_result.is_success:
                 return ServiceResult.fail(ForgeError(code="FILE_WRITE_FAILED", message="Failed to write test file."))
                 
            record = {
                "target_file": target_file_path,
                "test_file": test_file_path,
                "status": "generated"
            }
            self.storage_provider.save("test_generation", record)
            
            return ServiceResult.success({"test_file": test_file_path, "status": "generated"})
            
        except Exception as e:
            logger.error(f"Error in TestingAgent.generate_tests: {str(e)}")
            return ServiceResult.fail(ForgeError(code="TESTING_AGENT_ERROR", message=str(e)))

    def run_tests(self, test_file_path: str) -> ServiceResult[Dict[str, Any]]:
        try:
            exec_result = self.execution_service.run_command(f"pytest {test_file_path} -v --tb=short")
            
            record = {
                "test_file": test_file_path,
                "execution_success": exec_result.is_success,
                "output": exec_result.data if exec_result.is_success else exec_result.error.message
            }
            self.storage_provider.save("test_runs", record)
            
            if not exec_result.is_success:
                return ServiceResult.fail(ForgeError(
                    code="TEST_EXECUTION_FAILED", 
                    message=f"Tests in {test_file_path} failed.", 
                    details={"output": exec_result.error.message}
                ))
                
            return ServiceResult.success({"test_file": test_file_path, "output": exec_result.data, "status": "passed"})
            
        except Exception as e:
            logger.error(f"Error in TestingAgent.run_tests: {str(e)}")
            return ServiceResult.fail(ForgeError(code="TEST_RUNNER_ERROR", message=str(e)))
