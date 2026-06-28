# ForgePrompt Phase 7 — PolicyService
from typing import Optional, Dict, Any, List, Tuple
from services.service_result import ServiceResult
from services.errors import StorageError, ForgeError, QuotaExceededError, ValidationError
import json
import ast
import operator
import re

class PolicyService:
    """
    Enforces governance policies using dynamically evaluated expressions.
    Tracks usage quotas and performs CEL-like delegation for rules.
    """
    def __init__(self, container):
        self.container = container
        self.storage = container.storage

    def create_policy(
        self, 
        organization_id: int, 
        policy_name: str, 
        policy_type: str, 
        expression: str, 
        enabled: bool = True,
        conn: Optional[Any] = None
    ) -> ServiceResult:
        """
        Create a new governance policy.
        """
        try:
            sql = """
                INSERT INTO organization_policies (
                    organization_id, policy_name, policy_type, expression, enabled
                ) VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    policy_type = VALUES(policy_type),
                    expression = VALUES(expression),
                    enabled = VALUES(enabled)
            """
            params = (organization_id, policy_name, policy_type, expression, int(enabled))
            
            if conn:
                conn.cursor.execute(sql, params)
                policy_id = conn.cursor.lastrowid
            else:
                with self.storage.transaction() as session:
                    session.cursor.execute(sql, params)
                    policy_id = session.cursor.lastrowid
                    
            return ServiceResult.ok(data={"policy_id": policy_id})
        except Exception as e:
            return ServiceResult.fail(StorageError(f"[PolicyService Error] Failed to create policy: {str(e)}"))

    def get_policies(self, organization_id: int) -> ServiceResult:
        try:
            sql = "SELECT id, policy_name, policy_type, expression, enabled FROM organization_policies WHERE organization_id = %s"
            with self.storage.get_session() as session:
                session.cursor.execute(sql, (organization_id,))
                policies = session.cursor.fetchall()
            return ServiceResult.ok(data={"policies": policies})
        except Exception as e:
            return ServiceResult.fail(StorageError(f"[PolicyService Error] Failed to get policies: {str(e)}"))

    def _get_org_usage(self, organization_id: int) -> Dict[str, Any]:
        """
        Fetches the current usage context from read models.
        """
        sql = "SELECT monthly_cost, monthly_tokens, active_runs, active_agents FROM rm_org_dashboards WHERE organization_id = %s"
        with self.storage.get_session() as session:
            session.cursor.execute(sql, (organization_id,))
            row = session.cursor.fetchone()
            if row:
                return {
                    "monthly_cost": float(row.get("monthly_cost", 0.0)),
                    "monthly_tokens": int(row.get("monthly_tokens", 0)),
                    "active_runs": int(row.get("active_runs", 0)),
                    "active_agents": int(row.get("active_agents", 0))
                }
            return {
                "monthly_cost": 0.0,
                "monthly_tokens": 0,
                "active_runs": 0,
                "active_agents": 0
            }

    def _evaluate_cel_expression(self, expression: str, context: Dict[str, Any]) -> bool:
        """
        Simplistic CEL evaluator delegation.
        In a full enterprise environment, this would call out to a CEL Go binary or use cel-python.
        Here we safely evaluate simple python-like logical conditions based on the context.
        """
        # Replace context variables in the expression with their literal values
        # This is a rudimentary and restricted evaluation
        # Allowed formats: `context.monthly_cost < 100.0`, `context.active_runs <= 10`
        eval_expr = expression
        for key, value in context.items():
            pattern = f"context\.{key}"
            eval_expr = re.sub(pattern, str(value), eval_expr)
            
        # Also support variables without 'context.' prefix
        for key, value in context.items():
            pattern = rf"\b{key}\b"
            if isinstance(value, str):
                eval_expr = re.sub(pattern, f"'{value}'", eval_expr)
            else:
                eval_expr = re.sub(pattern, str(value), eval_expr)
        
        # Safe eval using ast
        try:
            # We parse the ast and evaluate only safe nodes
            tree = ast.parse(eval_expr, mode='eval')
            
            def _eval(node):
                if isinstance(node, ast.Expression):
                    return _eval(node.body)
                elif isinstance(node, ast.Constant):
                    return node.value
                elif isinstance(node, ast.Compare):
                    left = _eval(node.left)
                    for op, comp in zip(node.ops, node.comparators):
                        right = _eval(comp)
                        if isinstance(op, ast.Lt) and not left < right: return False
                        if isinstance(op, ast.LtE) and not left <= right: return False
                        if isinstance(op, ast.Gt) and not left > right: return False
                        if isinstance(op, ast.GtE) and not left >= right: return False
                        if isinstance(op, ast.Eq) and not left == right: return False
                        if isinstance(op, ast.NotEq) and not left != right: return False
                        left = right
                    return True
                elif isinstance(node, ast.BoolOp):
                    if isinstance(node.op, ast.And):
                        return all(_eval(v) for v in node.values)
                    elif isinstance(node.op, ast.Or):
                        return any(_eval(v) for v in node.values)
                else:
                    raise ValueError(f"Unsupported node type: {type(node)}")
                    
            return bool(_eval(tree))
        except Exception as e:
            # If evaluation fails, we default to deny for safety
            self.container.logger.error(f"Policy evaluation failed for expr '{expression}': {str(e)}")
            return False

    def evaluate_policies(self, organization_id: int, request_context: Dict[str, Any] = None) -> ServiceResult:
        """
        Evaluate all enabled policies for the organization against current usage and request context.
        Returns ServiceResult.ok() if all pass.
        Returns ServiceResult.fail(QuotaExceededError) if any cost/rate policy fails.
        """
        try:
            res = self.get_policies(organization_id)
            if not res.success:
                return res
            policies = res.data.get("policies", [])
            
            if not policies:
                return ServiceResult.ok()
                
            org_usage = self._get_org_usage(organization_id)
            
            # Merge request context with org usage
            context = org_usage.copy()
            if request_context:
                context.update(request_context)

            failed_policies = []
            for policy in policies:
                if not policy['enabled']:
                    continue
                
                # We expect the expression to evaluate to TRUE if the action is PERMITTED.
                # If FALSE, policy is violated.
                expression = policy['expression']
                is_permitted = self._evaluate_cel_expression(expression, context)
                
                if not is_permitted:
                    failed_policies.append(policy['policy_name'])
                    
            if failed_policies:
                policy_names = ", ".join(failed_policies)
                return ServiceResult.fail(QuotaExceededError(f"Request blocked by policies: {policy_names}"))
                
            return ServiceResult.ok()
        except Exception as e:
            return ServiceResult.fail(ForgeError(f"[PolicyService Error] Policy evaluation failed: {str(e)}"))
