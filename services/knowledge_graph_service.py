import logging
import json
from typing import List, Dict, Any, Optional
from services.service_result import ServiceResult
from services.errors import ForgeError
from core.dependency_injection import container
from core.database import get_db_connection

logger = logging.getLogger(__name__)

class KnowledgeGraphService:
    def __init__(self):
        pass

    def add_node(self, node_id: str, label: str, properties: Dict[str, Any] = None) -> ServiceResult[bool]:
        try:
            props_json = json.dumps(properties or {})
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO kg_nodes (id, label, properties)
                    VALUES (?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET label=excluded.label, properties=excluded.properties
                    """,
                    (node_id, label, props_json)
                )
                conn.commit()
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"Error adding node to knowledge graph: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="KG_NODE_ERROR", message=f"Failed to add node: {e}"))

    def get_node(self, node_id: str) -> ServiceResult[Optional[Dict[str, Any]]]:
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, label, properties FROM kg_nodes WHERE id = ?", (node_id,))
                row = cursor.fetchone()
                if row:
                    return ServiceResult.success({
                        "id": row[0],
                        "label": row[1],
                        "properties": json.loads(row[2] or '{}')
                    })
                return ServiceResult.success(None)
        except Exception as e:
            logger.error(f"Error fetching node from knowledge graph: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="KG_GET_NODE_ERROR", message=f"Failed to get node: {e}"))

    def add_edge(self, source_id: str, target_id: str, relation: str, properties: Dict[str, Any] = None) -> ServiceResult[bool]:
        try:
            props_json = json.dumps(properties or {})
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO kg_edges (source_id, target_id, relation, properties)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(source_id, target_id, relation) DO UPDATE SET properties=excluded.properties
                    """,
                    (source_id, target_id, relation, props_json)
                )
                conn.commit()
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"Error adding edge to knowledge graph: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="KG_EDGE_ERROR", message=f"Failed to add edge: {e}"))

    def get_neighbors(self, node_id: str, relation: Optional[str] = None) -> ServiceResult[List[Dict[str, Any]]]:
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                if relation:
                    cursor.execute(
                        """
                        SELECT n.id, n.label, n.properties, e.relation, e.properties
                        FROM kg_edges e
                        JOIN kg_nodes n ON e.target_id = n.id
                        WHERE e.source_id = ? AND e.relation = ?
                        """,
                        (node_id, relation)
                    )
                else:
                    cursor.execute(
                        """
                        SELECT n.id, n.label, n.properties, e.relation, e.properties
                        FROM kg_edges e
                        JOIN kg_nodes n ON e.target_id = n.id
                        WHERE e.source_id = ?
                        """,
                        (node_id,)
                    )
                
                rows = cursor.fetchall()
                neighbors = []
                for row in rows:
                    neighbors.append({
                        "node": {
                            "id": row[0],
                            "label": row[1],
                            "properties": json.loads(row[2] or '{}')
                        },
                        "edge": {
                            "relation": row[3],
                            "properties": json.loads(row[4] or '{}')
                        }
                    })
            return ServiceResult.success(neighbors)
        except Exception as e:
            logger.error(f"Error fetching neighbors from knowledge graph: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="KG_GET_NEIGHBORS_ERROR", message=f"Failed to get neighbors: {e}"))

    def delete_node(self, node_id: str) -> ServiceResult[bool]:
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM kg_edges WHERE source_id = ? OR target_id = ?", (node_id, node_id))
                cursor.execute("DELETE FROM kg_nodes WHERE id = ?", (node_id,))
                conn.commit()
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"Error deleting node from knowledge graph: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="KG_DELETE_NODE_ERROR", message=f"Failed to delete node: {e}"))

    def delete_edge(self, source_id: str, target_id: str, relation: str) -> ServiceResult[bool]:
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM kg_edges WHERE source_id = ? AND target_id = ? AND relation = ?",
                    (source_id, target_id, relation)
                )
                conn.commit()
            return ServiceResult.success(True)
        except Exception as e:
            logger.error(f"Error deleting edge from knowledge graph: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="KG_DELETE_EDGE_ERROR", message=f"Failed to delete edge: {e}"))

    def query_subgraph(self, start_node_id: str, max_depth: int = 2) -> ServiceResult[Dict[str, Any]]:
        try:
            nodes = {}
            edges = []
            visited = set()
            queue = [(start_node_id, 0)]
            
            with get_db_connection() as conn:
                cursor = conn.cursor()
                while queue:
                    current_node_id, depth = queue.pop(0)
                    if current_node_id in visited or depth > max_depth:
                        continue
                    
                    visited.add(current_node_id)
                    
                    if current_node_id not in nodes:
                        cursor.execute("SELECT id, label, properties FROM kg_nodes WHERE id = ?", (current_node_id,))
                        row = cursor.fetchone()
                        if row:
                            nodes[current_node_id] = {
                                "id": row[0],
                                "label": row[1],
                                "properties": json.loads(row[2] or '{}')
                            }
                    
                    if depth < max_depth:
                        cursor.execute(
                            "SELECT source_id, target_id, relation, properties FROM kg_edges WHERE source_id = ?",
                            (current_node_id,)
                        )
                        outgoing = cursor.fetchall()
                        for edge_row in outgoing:
                            target_id = edge_row[1]
                            edges.append({
                                "source_id": edge_row[0],
                                "target_id": target_id,
                                "relation": edge_row[2],
                                "properties": json.loads(edge_row[3] or '{}')
                            })
                            queue.append((target_id, depth + 1))
                            
            return ServiceResult.success({
                "nodes": list(nodes.values()),
                "edges": edges
            })
        except Exception as e:
            logger.error(f"Error querying subgraph: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="KG_QUERY_ERROR", message=f"Failed to query subgraph: {e}"))
