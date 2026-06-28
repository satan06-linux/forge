# ForgePrompt Phase 7 — PluginSandboxService
import json
import logging
import subprocess
import tempfile
import time
import os
import hashlib
from typing import Any, Dict

from services.service_result import ServiceResult
from services.errors import ForgeError, ValidationError

logger = logging.getLogger(__name__)

class PluginSandboxService:
    def __init__(self, container):
        self.container = container

    def validate_abi(self, plugin_manifest: Dict[str, Any], expected_abi: Dict[str, Any]) -> ServiceResult:
        """
        Validates if the plugin's exported schema (manifest) matches
        the required ABI for this extension point.
        """
        start_time = time.time()
        try:
            manifest_inputs = plugin_manifest.get('inputs', {})
            for req_key, req_type in expected_abi.get('inputs', {}).items():
                if req_key not in manifest_inputs:
                    raise ValidationError(f"Missing required input in ABI: {req_key}")
                if manifest_inputs[req_key] != req_type:
                    raise ValidationError(f"ABI type mismatch for {req_key}: expected {req_type}, got {manifest_inputs[req_key]}")

            manifest_outputs = plugin_manifest.get('outputs', {})
            for req_key, req_type in expected_abi.get('outputs', {}).items():
                if req_key not in manifest_outputs:
                    raise ValidationError(f"Missing required output in ABI: {req_key}")
                if manifest_outputs[req_key] != req_type:
                    raise ValidationError(f"ABI type mismatch for {req_key}: expected {req_type}, got {manifest_outputs[req_key]}")
                    
            abi_hash = hashlib.sha256(json.dumps(plugin_manifest, sort_keys=True).encode()).hexdigest()
            return ServiceResult.ok({"valid": True, "abi_hash": abi_hash}, duration_ms=int((time.time() - start_time) * 1000))
        except Exception as e:
            logger.error(f"[PluginSandboxService Error] ABI validation failed: {e}")
            return ServiceResult.fail(ForgeError(str(e)))

    def execute_plugin(self, plugin_code: str, input_data: Dict[str, Any], timeout_seconds: int = 10, conn=None) -> ServiceResult:
        """
        Runs plugin code in an isolated subprocess.
        """
        start_time = time.time()
        session = conn or self.container.storage_provider.get_session()
        owns_tx = False
        if conn is None:
            session.begin()
            owns_tx = True

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                # Use os.path.normpath and replace backslashes for python raw strings just in case
                input_file = os.path.join(temp_dir, 'input.json').replace('\\', '/')
                output_file = os.path.join(temp_dir, 'output.json').replace('\\', '/')
                code_file = os.path.join(temp_dir, 'plugin.py').replace('\\', '/')

                with open(input_file, 'w') as f:
                    json.dump(input_data, f)
                
                wrapper_code = f"""
import json
import sys

def main():
    try:
        with open('{input_file}', 'r') as f:
            input_data = json.load(f)
        
{self._indent_code(plugin_code, 8)}
        
        result = run(input_data)
        
        with open('{output_file}', 'w') as f:
            json.dump({{"success": True, "data": result}}, f)
    except Exception as e:
        with open('{output_file}', 'w') as f:
            json.dump({{"success": False, "error": str(e)}}, f)

if __name__ == '__main__':
    main()
"""
                with open(code_file, 'w') as f:
                    f.write(wrapper_code)

                try:
                    subprocess.run(
                        ["python", code_file],
                        capture_output=True,
                        text=True,
                        timeout=timeout_seconds,
                        check=True
                    )
                except subprocess.TimeoutExpired:
                    raise ForgeError("Plugin execution timed out")
                except subprocess.CalledProcessError as e:
                    raise ForgeError(f"Plugin execution crashed: {e.stderr}")

                with open(output_file, 'r') as f:
                    result_payload = json.load(f)

                if not result_payload.get('success'):
                    raise ForgeError(f"Plugin error: {result_payload.get('error')}")

                plugin_result = result_payload.get('data')

                session.execute(
                    "INSERT INTO lineage_events (entity_type, entity_id, event_type, details_json, created_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    ('plugin_execution', 0, 'EXECUTED', json.dumps({"input": input_data, "output": plugin_result}), time.time())
                )

                if owns_tx:
                    session.commit()

                return ServiceResult.ok({"result": plugin_result}, duration_ms=int((time.time() - start_time) * 1000))
        except Exception as e:
            if owns_tx:
                session.rollback()
            logger.error(f"[PluginSandboxService Error] {e}")
            return ServiceResult.fail(ForgeError(str(e)))

    def _indent_code(self, code: str, spaces: int) -> str:
        prefix = ' ' * spaces
        return '\n'.join(prefix + line if line.strip() else line for line in code.split('\n'))
