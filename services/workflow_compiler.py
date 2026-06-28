# ForgePrompt Phase 7 - WorkflowCompiler
import json
import hashlib
import time
import logging
from typing import Dict, List, Optional, Any, Set
from collections import defaultdict, deque

from services.service_result import ServiceResult
from services.errors import ForgeError, ValidationError, StorageError, DeterminismError

logger = logging.getLogger(__name__)

class WorkflowCompiler:
    """
    Compiles a workflow snapshot into an optimized execution plan.
    Responsibilities:
    - Incremental compilation (diff against parent compiled plan)
    - DAG optimization (dead code elimination, concurrency hints)
    - Plan signing
    """

    def __init__(self, container):
        self.container = container
        self.storage = container.get('storage_provider')
        self.version_service = container.get('workflow_version_service')
        self.determinism_validator = container.get('determinism_validator')
        self._ensure_tables()

    def _ensure_tables(self):
        with self.storage.transaction() as session:
            try:
                session.execute("SHOW TABLES LIKE 'compiled_plans'")
                if not session.fetchall():
                    session.execute("""
                        CREATE TABLE compiled_plans (
                            plan_id VARCHAR(64) PRIMARY KEY,
                            workflow_id VARCHAR(64) NOT NULL,
                            snapshot_id VARCHAR(64) NOT NULL,
                            plan_data JSON,
                            signature VARCHAR(128) NOT NULL,
                            created_at BIGINT NOT NULL,
                            INDEX idx_snapshot (snapshot_id)
                        )
                    """)
            except Exception as e:
                logger.error(f"[WorkflowCompiler Error] Failed to initialize tables: {e}")

    def compile_workflow(self, workflow_id: str, snapshot_id: str) -> ServiceResult:
        """
        Compiles the given snapshot into an execution plan.
        """
        try:
            # 1. Fetch Snapshot
            if not self.version_service:
                # Retrieve from container if needed (late binding)
                self.version_service = self.container.get('workflow_version_service')
            
            snap_res = self.version_service.get_snapshot(snapshot_id)
            if not snap_res.success:
                return snap_res
                
            snapshot = snap_res.unwrap()
            nodes = snapshot.get('nodes', [])
            edges = snapshot.get('edges', [])
            parent_snapshot_id = snapshot.get('parent_snapshot_id')

            # 2. Incremental Compilation Check
            # If parent snapshot exists and has a compiled plan, we can do a diff
            # In a real heavy system, we'd only compile changed nodes. For now, 
            # we'll still build the full DAG but we could reuse compiled artifacts.
            
            # 3. Validate Determinism
            if self.determinism_validator:
                for node in nodes:
                    if 'code' in node:
                        det_res = self.determinism_validator.validate_code(node['code'])
                        if not det_res.success:
                            return ServiceResult.fail(
                                ValidationError(f"Determinism validation failed in node {node.get('id')}: {det_res.error.message}")
                            )

            # 4. Build and Optimize DAG
            dag = self._build_dag(nodes, edges)
            
            # Optimization 1: Cycle detection
            if self._has_cycles(dag):
                return ServiceResult.fail(ValidationError("Workflow contains cycles, which are not allowed."))

            # Optimization 2: Dead code elimination
            # Find nodes that don't contribute to any output/sink node
            optimized_nodes = self._eliminate_dead_code(dag, nodes)
            
            # Optimization 3: Concurrency Hints
            # Annotate nodes with execution layers (topological sorting)
            execution_layers = self._compute_execution_layers(dag, optimized_nodes)

            # 5. Assemble Plan
            plan_data = {
                "workflow_id": workflow_id,
                "snapshot_id": snapshot_id,
                "nodes": optimized_nodes,
                "edges": [e for e in edges if e.get('source') in [n['id'] for n in optimized_nodes] and e.get('target') in [n['id'] for n in optimized_nodes]],
                "execution_layers": execution_layers,
                "compiled_at": int(time.time() * 1000)
            }
            
            # 6. Sign Plan
            plan_str = json.dumps(plan_data, sort_keys=True).encode('utf-8')
            signature = hashlib.sha512(plan_str).hexdigest()
            plan_id = hashlib.sha256(plan_str).hexdigest()
            
            # 7. Store Plan
            with self.storage.transaction() as session:
                # Check if already compiled
                session.execute("SELECT plan_id FROM compiled_plans WHERE plan_id = %s", (plan_id,))
                if not session.fetchone():
                    session.execute("""
                        INSERT INTO compiled_plans (plan_id, workflow_id, snapshot_id, plan_data, signature, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (plan_id, workflow_id, snapshot_id, json.dumps(plan_data), signature, int(time.time() * 1000)))
                    
            logger.info(f"[WorkflowCompiler] Successfully compiled plan {plan_id} for snapshot {snapshot_id}")
            return ServiceResult.ok({
                "plan_id": plan_id,
                "signature": signature,
                "plan": plan_data
            })
            
        except ValidationError as e:
            return ServiceResult.fail(e)
        except Exception as e:
            logger.error(f"[WorkflowCompiler Error] Compilation failed: {e}")
            return ServiceResult.fail(ForgeError(f"Compilation failed: {str(e)}"))

    def get_compiled_plan(self, plan_id: str) -> ServiceResult:
        """Retrieves a compiled plan."""
        try:
            with self.storage.get_session() as session:
                session.execute("SELECT * FROM compiled_plans WHERE plan_id = %s", (plan_id,))
                row = session.fetchone()
                if not row:
                    return ServiceResult.fail(ValidationError(f"Plan {plan_id} not found"))
                
                if isinstance(row.get('plan_data'), str):
                    row['plan_data'] = json.loads(row['plan_data'])
                    
                return ServiceResult.ok(row)
        except Exception as e:
            return ServiceResult.fail(StorageError(f"Database error: {str(e)}"))

    def get_plan_for_snapshot(self, snapshot_id: str) -> ServiceResult:
        """Retrieves the compiled plan for a given snapshot."""
        try:
            with self.storage.get_session() as session:
                session.execute("SELECT * FROM compiled_plans WHERE snapshot_id = %s ORDER BY created_at DESC LIMIT 1", (snapshot_id,))
                row = session.fetchone()
                if not row:
                    return ServiceResult.fail(ValidationError(f"No compiled plan found for snapshot {snapshot_id}"))
                
                if isinstance(row.get('plan_data'), str):
                    row['plan_data'] = json.loads(row['plan_data'])
                    
                return ServiceResult.ok(row)
        except Exception as e:
            return ServiceResult.fail(StorageError(f"Database error: {str(e)}"))

    # --- Internals ---

    def _build_dag(self, nodes: List[Dict], edges: List[Dict]) -> Dict:
        """Builds adjacency list representations."""
        dag = {
            'adj': defaultdict(list),
            'rev_adj': defaultdict(list),
            'in_degree': defaultdict(int),
            'nodes': {n['id']: n for n in nodes if 'id' in n}
        }
        
        for n in nodes:
            if 'id' in n:
                dag['in_degree'][n['id']] = 0
                
        for e in edges:
            src = e.get('source')
            tgt = e.get('target')
            if src and tgt and src in dag['nodes'] and tgt in dag['nodes']:
                dag['adj'][src].append(tgt)
                dag['rev_adj'][tgt].append(src)
                dag['in_degree'][tgt] += 1
                
        return dag

    def _has_cycles(self, dag: Dict) -> bool:
        """Kahn's algorithm for cycle detection."""
        in_degree = dag['in_degree'].copy()
        queue = deque([n for n, d in in_degree.items() if d == 0])
        visited = 0
        
        while queue:
            node = queue.popleft()
            visited += 1
            for neighbor in dag['adj'].get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
                    
        return visited != len(dag['nodes'])

    def _eliminate_dead_code(self, dag: Dict, nodes: List[Dict]) -> List[Dict]:
        """
        Removes nodes that do not lead to a 'sink' or 'output' node.
        We define 'sink' as nodes with out-degree 0 or explicitly typed as 'output'.
        """
        # Find all terminal nodes
        terminal_nodes = set()
        for node_id, node in dag['nodes'].items():
            if node.get('type') == 'output' or len(dag['adj'].get(node_id, [])) == 0:
                terminal_nodes.add(node_id)
                
        # Backwards BFS to find all useful nodes
        useful_nodes = set(terminal_nodes)
        queue = deque(terminal_nodes)
        
        while queue:
            node = queue.popleft()
            for parent in dag['rev_adj'].get(node, []):
                if parent not in useful_nodes:
                    useful_nodes.add(parent)
                    queue.append(parent)
                    
        # Filter
        optimized_nodes = [n for n in nodes if n.get('id') in useful_nodes]
        return optimized_nodes

    def _compute_execution_layers(self, dag: Dict, optimized_nodes: List[Dict]) -> List[List[str]]:
        """
        Groups nodes into parallelizable execution layers using topological sort.
        """
        active_nodes = {n['id'] for n in optimized_nodes}
        
        in_degree = {n: 0 for n in active_nodes}
        for n in active_nodes:
            for neighbor in dag['adj'].get(n, []):
                if neighbor in active_nodes:
                    in_degree[neighbor] += 1
                    
        queue = deque([n for n in active_nodes if in_degree[n] == 0])
        layers = []
        
        while queue:
            layer_size = len(queue)
            current_layer = []
            for _ in range(layer_size):
                node = queue.popleft()
                current_layer.append(node)
                for neighbor in dag['adj'].get(node, []):
                    if neighbor in active_nodes:
                        in_degree[neighbor] -= 1
                        if in_degree[neighbor] == 0:
                            queue.append(neighbor)
            layers.append(current_layer)
            
        return layers

