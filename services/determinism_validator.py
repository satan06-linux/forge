# ForgePrompt Phase 7 - DeterminismValidator
import ast
from typing import List, Tuple
import logging

from services.service_result import ServiceResult
from services.errors import DeterminismError

logger = logging.getLogger(__name__)

class DeterminismValidator:
    """
    AST-based non-determinism detection.
    Prevents unseeded random usage, non-deterministic dates/times,
    and other sources of impurity in workflow nodes.
    """

    def __init__(self, container):
        self.container = container

        # Define forbidden modules and functions
        self.forbidden_imports = {
            "time": ["time", "time_ns", "perf_counter"],
            "datetime": ["now", "today", "utcnow"],
            "random": ["random", "randint", "choice", "shuffle", "sample", "uniform", "seed"],
            "uuid": ["uuid1", "uuid4"]
        }
        
    def validate_code(self, code: str) -> ServiceResult:
        """
        Validates the Python source code for non-deterministic operations.
        
        Args:
            code (str): The Python source code of the workflow node.
            
        Returns:
            ServiceResult: Success if deterministic, Failure with DeterminismError otherwise.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return ServiceResult.fail(
                DeterminismError(f"Syntax error in node code: {e}")
            )

        violations = []
        
        for node in ast.walk(tree):
            # Check for direct imports: import time, import random
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in self.forbidden_imports:
                        # For random, we might allow it if it's explicitly seeded, 
                        # but in strict mode we might ban it entirely or ban the module.
                        violations.append(f"Forbidden import '{alias.name}' detected at line {node.lineno}.")
            
            # Check for from imports: from datetime import now
            elif isinstance(node, ast.ImportFrom):
                if node.module in self.forbidden_imports:
                    forbidden_funcs = self.forbidden_imports[node.module]
                    for alias in node.names:
                        if alias.name in forbidden_funcs or alias.name == "*":
                            violations.append(f"Forbidden import '{alias.name}' from '{node.module}' detected at line {node.lineno}.")
                            
            # Check for function calls like datetime.now() or time.time()
            elif isinstance(node, ast.Call):
                func = node.func
                
                # e.g., time.time()
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    module_name = func.value.id
                    func_name = func.attr
                    
                    if module_name in self.forbidden_imports and func_name in self.forbidden_imports[module_name]:
                        violations.append(f"Forbidden function call '{module_name}.{func_name}()' detected at line {node.lineno}.")
                
                # e.g., now()
                elif isinstance(func, ast.Name):
                    func_name = func.id
                    # We can't strictly know if 'now' is from 'datetime' without deeper analysis,
                    # but if it matches a forbidden function and was imported, the import check will catch it.

        if violations:
            logger.warning(f"[DeterminismValidator] Violations found: {violations}")
            error_msg = "Non-deterministic operations detected: " + "; ".join(violations)
            return ServiceResult.fail(
                DeterminismError(error_msg, metadata={"violations": violations})
            )

        return ServiceResult.ok()

