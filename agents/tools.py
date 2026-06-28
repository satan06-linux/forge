# ForgePrompt Phase 7 — ToolRegistryAdapter

import logging
from typing import List, Dict, Any, Callable

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class ToolRegistryAdapter:
    def __init__(self, container):
        self.container = container
        self.tools: Dict[str, Dict[str, Any]] = {}
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        self.register_tool(
            name="system_ping",
            description="Checks the current system state.",
            func=self._mock_system_ping
        )
        self.register_tool(
            name="memory_search",
            description="Searches semantic memory for specific keywords.",
            func=self._mock_memory_search
        )

    def register_tool(self, name: str, description: str, func: Callable) -> ServiceResult:
        try:
            if name in self.tools:
                raise ForgeError(f"Tool '{name}' already registered in the registry.")
            
            self.tools[name] = {
                "name": name,
                "description": description,
                "func": func
            }
            logger.info(f"Registered tool: {name}")
            return ServiceResult.success(data={"registered": name})
        except ForgeError as fe:
            logger.error(f"[ToolRegistryAdapter Error] register_tool failed: {str(fe)}")
            return ServiceResult.fail(error_code="TOOL_ALREADY_REGISTERED", message=str(fe))
        except Exception as e:
            logger.error(f"[ToolRegistryAdapter Error] register_tool failed: {str(e)}")
            return ServiceResult.fail(error_code="TOOL_REGISTRATION_FAILED", message=str(e))

    def get_available_tools(self) -> ServiceResult:
        try:
            tool_list = [
                {"name": t["name"], "description": t["description"]}
                for t in self.tools.values()
            ]
            return ServiceResult.success(data=tool_list)
        except Exception as e:
            logger.error(f"[ToolRegistryAdapter Error] get_available_tools failed: {str(e)}")
            return ServiceResult.fail(error_code="GET_TOOLS_FAILED", message=str(e))

    def execute_tool(self, name: str, args: Dict[str, Any]) -> ServiceResult:
        try:
            if name not in self.tools:
                raise ForgeError(f"Tool '{name}' not found in registry.")
            
            tool_func = self.tools[name]["func"]
            logger.info(f"Executing tool '{name}' with args: {args}")
            
            result = tool_func(**args)
            return ServiceResult.success(data=result)
        except ForgeError as fe:
            logger.warning(f"[ToolRegistryAdapter Error] Tool not found: {str(fe)}")
            return ServiceResult.fail(error_code="TOOL_NOT_FOUND", message=str(fe))
        except Exception as e:
            logger.error(f"[ToolRegistryAdapter Error] execute_tool '{name}' failed: {str(e)}")
            return ServiceResult.fail(error_code="TOOL_EXECUTION_FAILED", message=str(e))

    # --- Built-in mock tools ---

    def _mock_system_ping(self, **kwargs) -> Dict[str, Any]:
        return {"status": "online", "message": "System is operational"}

    def _mock_memory_search(self, query: str = "", **kwargs) -> Dict[str, Any]:
        return {"query": query, "results": [], "message": f"Simulated search for '{query}' completed."}
