import logging
import importlib
import sys
import os
from typing import Dict, Any, Type
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class PluginSdkService:
    """
    Sandbox integration SDK allowing developers to dynamically load Custom Models, 
    Custom Tools, Custom Agents, and Custom APIs into the workspace.
    """
    def __init__(self, container: Any):
        self.container = container
        self.storage_provider = container.get('StorageProvider')
        self.loaded_plugins: Dict[str, Dict[str, Any]] = {}
        
    def load_plugin_from_path(self, plugin_name: str, plugin_path: str) -> ServiceResult[Dict[str, Any]]:
        """
        Dynamically load a plugin module from a specific file path.
        """
        try:
            if not plugin_name or not plugin_path:
                return ServiceResult.fail(ForgeError(code="INVALID_INPUT", message="Plugin name and path are required."))
                
            if not os.path.exists(plugin_path):
                return ServiceResult.fail(ForgeError(code="PLUGIN_NOT_FOUND", message=f"Plugin path {plugin_path} does not exist."))
                
            plugin_dir = os.path.dirname(plugin_path)
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)
                
            module_name = os.path.splitext(os.path.basename(plugin_path))[0]
            
            try:
                module = importlib.import_module(module_name)
                # Reload to ensure latest changes are applied in active workspace
                importlib.reload(module)
            except ImportError as e:
                return ServiceResult.fail(ForgeError(code="PLUGIN_IMPORT_ERROR", message=f"Failed to import plugin: {str(e)}"))
                
            if not hasattr(module, 'setup_plugin'):
                return ServiceResult.fail(ForgeError(
                    code="PLUGIN_SETUP_MISSING", 
                    message="Plugin module must define a 'setup_plugin(sdk_service)' function."
                ))
                
            try:
                # Pass self so the plugin can call register_* methods
                plugin_metadata = module.setup_plugin(self)
            except Exception as e:
                return ServiceResult.fail(ForgeError(code="PLUGIN_SETUP_ERROR", message=f"Error executing setup_plugin: {str(e)}"))
                
            self.loaded_plugins[plugin_name] = {
                "module": module,
                "metadata": plugin_metadata,
                "path": plugin_path
            }
            
            # Persist loaded plugin state
            if self.storage_provider:
                self.storage_provider.save("active_plugins", {
                    "plugin_name": plugin_name,
                    "plugin_path": plugin_path,
                    "status": "loaded"
                })
            
            return ServiceResult.success({"plugin_name": plugin_name, "metadata": plugin_metadata})
            
        except Exception as e:
            logger.error(f"Error in PluginSdkService.load_plugin_from_path: {str(e)}")
            return ServiceResult.fail(ForgeError(code="PLUGIN_SDK_ERROR", message=str(e)))
            
    def register_custom_model(self, model_name: str, model_class: Type) -> ServiceResult[bool]:
        """
        Register a custom LLM model into the system.
        """
        try:
            if not model_name or not model_class:
                 return ServiceResult.fail(ForgeError(code="INVALID_INPUT", message="Model name and class are required."))
                 
            llm_service = self.container.get('LlmService')
            if llm_service and hasattr(llm_service, 'register_model'):
                llm_service.register_model(model_name, model_class)
                return ServiceResult.success(True)
            else:
                return ServiceResult.fail(ForgeError(code="UNSUPPORTED_OPERATION", message="LlmService does not support custom model registration."))
                
        except Exception as e:
            logger.error(f"Error registering custom model {model_name}: {str(e)}")
            return ServiceResult.fail(ForgeError(code="MODEL_REGISTRATION_FAILED", message=str(e)))
            
    def register_custom_tool(self, tool_name: str, tool_handler: Any) -> ServiceResult[bool]:
        """
        Register a custom tool/action available to agents.
        """
        try:
            if not tool_name or not tool_handler:
                 return ServiceResult.fail(ForgeError(code="INVALID_INPUT", message="Tool name and handler are required."))
                 
            tool_registry = self.container.get('ToolRegistry')
            if tool_registry and hasattr(tool_registry, 'register'):
                tool_registry.register(tool_name, tool_handler)
                return ServiceResult.success(True)
            else:
                return ServiceResult.fail(ForgeError(code="UNSUPPORTED_OPERATION", message="ToolRegistry not found or does not support registration."))
                
        except Exception as e:
            logger.error(f"Error registering custom tool {tool_name}: {str(e)}")
            return ServiceResult.fail(ForgeError(code="TOOL_REGISTRATION_FAILED", message=str(e)))
            
    def register_custom_agent(self, agent_name: str, agent_class: Type) -> ServiceResult[bool]:
        """
        Register a custom agent role dynamically.
        """
        try:
            if not agent_name or not agent_class:
                 return ServiceResult.fail(ForgeError(code="INVALID_INPUT", message="Agent name and class are required."))
                 
            agent_registry = self.container.get('AgentRegistry')
            if agent_registry and hasattr(agent_registry, 'register'):
                agent_registry.register(agent_name, agent_class)
                return ServiceResult.success(True)
            else:
                return ServiceResult.fail(ForgeError(code="UNSUPPORTED_OPERATION", message="AgentRegistry not found or does not support registration."))
                
        except Exception as e:
            logger.error(f"Error registering custom agent {agent_name}: {str(e)}")
            return ServiceResult.fail(ForgeError(code="AGENT_REGISTRATION_FAILED", message=str(e)))
            
    def register_custom_api(self, route: str, handler: Any) -> ServiceResult[bool]:
        """
        Register a custom API endpoint in the workspace.
        """
        try:
            if not route or not handler:
                 return ServiceResult.fail(ForgeError(code="INVALID_INPUT", message="Route and handler are required."))
                 
            api_gateway = self.container.get('ApiGateway')
            if api_gateway and hasattr(api_gateway, 'add_route'):
                api_gateway.add_route(route, handler)
                return ServiceResult.success(True)
            else:
                return ServiceResult.fail(ForgeError(code="UNSUPPORTED_OPERATION", message="ApiGateway not found or does not support dynamic routes."))
                
        except Exception as e:
            logger.error(f"Error registering custom API {route}: {str(e)}")
            return ServiceResult.fail(ForgeError(code="API_REGISTRATION_FAILED", message=str(e)))
