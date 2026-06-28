# ForgePrompt Phase 7 — PolicyDslService
import ast
import time
import json
import logging
from typing import Any, Dict, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError, ValidationError, NotFoundError

logger = logging.getLogger(__name__)

class CelSafeEval(ast.NodeVisitor):
    def __init__(self, context: Dict[str, Any]):
        self.context = context

    def eval(self, node):
        return self.visit(node)

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_BoolOp(self, node):
        if isinstance(node.op, ast.And):
            return all(self.visit(v) for v in node.values)
        elif isinstance(node.op, ast.Or):
            return any(self.visit(v) for v in node.values)
        raise ValueError("Unsupported boolean operation")

    def visit_UnaryOp(self, node):
        if isinstance(node.op, ast.Not):
            return not self.visit(node.operand)
        raise ValueError("Unsupported unary operation")

    def visit_Compare(self, node):
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            right = self.visit(comparator)
            if isinstance(op, ast.Eq):
                if left != right: return False
            elif isinstance(op, ast.NotEq):
                if left == right: return False
            elif isinstance(op, ast.Gt):
                if not (left > right): return False
            elif isinstance(op, ast.Lt):
                if not (left < right): return False
            elif isinstance(op, ast.GtE):
                if not (left >= right): return False
            elif isinstance(op, ast.LtE):
                if not (left <= right): return False
            elif isinstance(op, ast.In):
                if not (left in right): return False
            elif isinstance(op, ast.NotIn):
                if left in right: return False
            else:
                raise ValueError("Unsupported comparison operator")
            left = right
        return True

    def visit_Attribute(self, node):
        obj = self.visit(node.value)
        if isinstance(obj, dict):
            return obj.get(node.attr)
        elif hasattr(obj, node.attr):
            return getattr(obj, node.attr)
        return None

    def visit_Name(self, node):
        if node.id in self.context:
            return self.context[node.id]
        if node.id == 'true':
            return True
        if node.id == 'false':
            return False
        if node.id == 'null':
            return None
        raise ValueError(f"Unknown variable: {node.id}")

    def visit_Constant(self, node):
        return node.value

    def visit_Dict(self, node):
        return {self.visit(k): self.visit(v) for k, v in zip(node.keys, node.values)}

    def visit_List(self, node):
        return [self.visit(e) for e in node.elts]

    def generic_visit(self, node):
        raise ValueError(f"Unsupported expression node: {type(node).__name__}")


class PolicyDslService:
    def __init__(self, container):
        self.container = container

    def _convert_cel_to_python(self, cel_expr: str) -> str:
        py_expr = cel_expr.replace('&&', ' and ').replace('||', ' or ')
        py_expr = py_expr.replace('!', ' not ')
        py_expr = py_expr.replace(' not =', '!=')
        return py_expr

    def create_policy(self, organization_id: int, name: str, expression: str, conn=None) -> ServiceResult:
        start_time = time.time()
        session = conn or self.container.storage_provider.get_session()
        owns_tx = False
        if conn is None:
            session.begin()
            owns_tx = True

        try:
            try:
                py_expr = self._convert_cel_to_python(expression)
                ast.parse(py_expr, mode='eval')
            except SyntaxError as e:
                raise ValidationError(f"Invalid policy syntax: {e}")

            session.execute(
                "INSERT INTO policies (organization_id, name, expression, created_at) "
                "VALUES (%s, %s, %s, %s)",
                (organization_id, name, expression, time.time())
            )
            policy_id = session.lastrowid()

            session.execute(
                "INSERT INTO lineage_events (entity_type, entity_id, event_type, details_json, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                ('policy', policy_id, 'CREATED', json.dumps({"expression": expression}), time.time())
            )

            event_payload = json.dumps({"policy_id": policy_id, "name": name, "organization_id": organization_id})
            session.execute(
                "INSERT INTO event_outbox (event_type, payload_json, created_at) VALUES (%s, %s, %s)",
                ('PolicyCreated', event_payload, time.time())
            )

            if owns_tx:
                session.commit()

            return ServiceResult.ok({"policy_id": policy_id}, duration_ms=int((time.time() - start_time) * 1000))
        except Exception as e:
            if owns_tx:
                session.rollback()
            logger.error(f"[PolicyDslService Error] {e}")
            return ServiceResult.fail(ForgeError(str(e)))

    def evaluate_policy(self, policy_id: int, organization_id: int, context: Dict[str, Any], conn=None) -> ServiceResult:
        start_time = time.time()
        session = conn or self.container.storage_provider.get_session()
        owns_tx = False
        if conn is None:
            session.begin()
            owns_tx = True

        try:
            policy_row = session.execute_one(
                "SELECT expression FROM policies WHERE id=%s AND organization_id=%s",
                (policy_id, organization_id)
            )
            if not policy_row:
                raise NotFoundError(f"Policy {policy_id} not found")

            expression = policy_row['expression']
            py_expr = self._convert_cel_to_python(expression)

            try:
                tree = ast.parse(py_expr, mode='eval')
                evaluator = CelSafeEval(context)
                result = evaluator.eval(tree)
            except Exception as e:
                raise ValidationError(f"Policy evaluation failed: {e}")

            session.execute(
                "INSERT INTO lineage_events (entity_type, entity_id, event_type, details_json, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                ('policy', policy_id, 'EVALUATED', json.dumps({"context": context, "result": result}), time.time())
            )

            event_payload = json.dumps({
                "policy_id": policy_id,
                "organization_id": organization_id,
                "result": result
            })
            session.execute(
                "INSERT INTO event_outbox (event_type, payload_json, created_at) VALUES (%s, %s, %s)",
                ('PolicyEvaluated', event_payload, time.time())
            )

            if owns_tx:
                session.commit()

            return ServiceResult.ok({"result": bool(result)}, duration_ms=int((time.time() - start_time) * 1000))
        except Exception as e:
            if owns_tx:
                session.rollback()
            logger.error(f"[PolicyDslService Error] {e}")
            return ServiceResult.fail(ForgeError(str(e)))

    def evaluate_expression_direct(self, expression: str, context: Dict[str, Any]) -> ServiceResult:
        start_time = time.time()
        try:
            py_expr = self._convert_cel_to_python(expression)
            tree = ast.parse(py_expr, mode='eval')
            evaluator = CelSafeEval(context)
            result = evaluator.eval(tree)
            return ServiceResult.ok({"result": bool(result)}, duration_ms=int((time.time() - start_time) * 1000))
        except Exception as e:
            logger.error(f"[PolicyDslService Error] direct evaluation failed: {e}")
            return ServiceResult.fail(ForgeError(str(e)))
