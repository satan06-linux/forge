import os
import importlib.util
import inspect

class NodeExecutionContext:
    def __init__(self, run_id, workflow_id, variables, memory, artifacts, logger=None, provider=None, event_bus=None, org_id=None):
        self.run_id = run_id
        self.workflow_id = workflow_id
        self.variables = variables  # dict
        self.memory = memory  # dict
        self.artifacts = artifacts  # list
        self.logger = logger or print
        self.provider = provider
        self.event_bus = event_bus
        self.org_id = org_id

class BaseNodePlugin:
    api_version = "v1"
    plugin_version = "1.0.0"
    
    def __init__(self, config=None):
        self.config = config or {}

    def execute(self, ctx: NodeExecutionContext):
        raise NotImplementedError("Plugins must implement the execute method")

class PluginSDK:
    _plugins = {}  # Registry: name -> class

    @classmethod
    def register_plugin(cls, name, plugin_class):
        if not hasattr(plugin_class, "api_version") or plugin_class.api_version != "v1":
            print(f"[PluginSDK Warning] Incompatible API version for {name}. Expected 'v1'.")
            return
        cls._plugins[name] = plugin_class
        print(f"[PluginSDK] Registered plugin: {name} (v{getattr(plugin_class, 'plugin_version', '1.0.0')})")

    @classmethod
    def load_plugins_from_dir(cls, directory_path):
        if not os.path.exists(directory_path):
            os.makedirs(directory_path)
            return

        for filename in os.listdir(directory_path):
            if filename.endswith(".py") and filename != "base_plugin.py" and not filename.startswith("__"):
                filepath = os.path.join(directory_path, filename)
                module_name = filename[:-3]
                
                try:
                    spec = importlib.util.spec_from_file_location(module_name, filepath)
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    
                    for name, obj in inspect.getmembers(mod):
                        if inspect.isclass(obj) and issubclass(obj, BaseNodePlugin) and obj is not BaseNodePlugin:
                            cls.register_plugin(obj.__name__, obj)
                except Exception as e:
                    print(f"[PluginSDK Error] Failed to load plugin file {filename}: {e}")

    @classmethod
    def get_plugin(cls, name):
        return cls._plugins.get(name)
