# ForgePrompt Phase 7 - WorkflowVersionService
import json
import hashlib
import time
import logging
from typing import Dict, List, Optional, Any

from services.service_result import ServiceResult
from services.errors import ForgeError, NotFoundError, StorageError

logger = logging.getLogger(__name__)

class WorkflowVersionService:
    """
    Git-style versioning and immutable snapshots for workflows.
    Ensures that every change to a workflow is tracked via a content-addressable hash.
    """

    def __init__(self, container):
        self.container = container
        self.storage = container.get('storage_provider')
        self._ensure_tables()

    def _ensure_tables(self):
        """Creates the necessary tables if they don't exist."""
        # Check if table exists
        with self.storage.transaction() as session:
            try:
                session.execute("SHOW TABLES LIKE 'workflow_snapshots'")
                if not session.fetchall():
                    session.execute("""
                        CREATE TABLE workflow_snapshots (
                            snapshot_id VARCHAR(64) PRIMARY KEY,
                            workflow_id VARCHAR(64) NOT NULL,
                            parent_snapshot_id VARCHAR(64),
                            author_id VARCHAR(64) NOT NULL,
                            message TEXT,
                            nodes JSON,
                            edges JSON,
                            created_at BIGINT NOT NULL,
                            INDEX idx_workflow_id (workflow_id)
                        )
                    """)
                session.execute("SHOW TABLES LIKE 'workflow_tags'")
                if not session.fetchall():
                    session.execute("""
                        CREATE TABLE workflow_tags (
                            tag_name VARCHAR(64) NOT NULL,
                            workflow_id VARCHAR(64) NOT NULL,
                            snapshot_id VARCHAR(64) NOT NULL,
                            created_at BIGINT NOT NULL,
                            PRIMARY KEY (workflow_id, tag_name)
                        )
                    """)
            except Exception as e:
                logger.error(f"[WorkflowVersionService] Failed to initialize tables: {e}")

    def create_snapshot(self, workflow_id: str, nodes: List[Dict], edges: List[Dict], author_id: str, message: str, parent_snapshot_id: Optional[str] = None, conn=None) -> ServiceResult:
        """
        Creates an immutable snapshot of the workflow.
        Generates a SHA-256 hash based on content and parent.
        """
        try:
            # Sort nodes and edges to ensure deterministic hashing
            nodes_sorted = sorted(nodes, key=lambda x: x.get('id', ''))
            edges_sorted = sorted(edges, key=lambda x: f"{x.get('source', '')}-{x.get('target', '')}")
            
            content = {
                "workflow_id": workflow_id,
                "nodes": nodes_sorted,
                "edges": edges_sorted,
                "parent_snapshot_id": parent_snapshot_id
            }
            
            content_str = json.dumps(content, sort_keys=True).encode('utf-8')
            snapshot_id = hashlib.sha256(content_str).hexdigest()
            
            created_at = int(time.time() * 1000)
            
            snapshot_data = {
                "snapshot_id": snapshot_id,
                "workflow_id": workflow_id,
                "parent_snapshot_id": parent_snapshot_id,
                "author_id": author_id,
                "message": message,
                "nodes": json.dumps(nodes_sorted),
                "edges": json.dumps(edges_sorted),
                "created_at": created_at
            }
            
            if conn:
                self._insert_snapshot(snapshot_data, conn)
            else:
                with self.storage.transaction() as session:
                    self._insert_snapshot(snapshot_data, session)
                    
            logger.info(f"[WorkflowVersionService] Created snapshot {snapshot_id} for workflow {workflow_id}")
            return ServiceResult.ok({"snapshot_id": snapshot_id, "created_at": created_at})
            
        except Exception as e:
            logger.error(f"[WorkflowVersionService Error] Failed to create snapshot: {e}")
            return ServiceResult.fail(StorageError(f"Failed to create snapshot: {str(e)}"))

    def _insert_snapshot(self, snapshot_data: Dict, session) -> None:
        """Helper to insert snapshot into DB."""
        # Check if exists first
        session.execute("SELECT snapshot_id FROM workflow_snapshots WHERE snapshot_id = %s", (snapshot_data['snapshot_id'],))
        if session.fetchone():
            return # Already exists, content-addressable so it's identical
            
        session.execute("""
            INSERT INTO workflow_snapshots (snapshot_id, workflow_id, parent_snapshot_id, author_id, message, nodes, edges, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            snapshot_data['snapshot_id'],
            snapshot_data['workflow_id'],
            snapshot_data['parent_snapshot_id'],
            snapshot_data['author_id'],
            snapshot_data['message'],
            snapshot_data['nodes'],
            snapshot_data['edges'],
            snapshot_data['created_at']
        ))

    def get_snapshot(self, snapshot_id: str) -> ServiceResult:
        """Retrieves a specific snapshot by ID."""
        try:
            with self.storage.get_session() as session:
                session.execute("SELECT * FROM workflow_snapshots WHERE snapshot_id = %s", (snapshot_id,))
                row = session.fetchone()
                if not row:
                    return ServiceResult.fail(NotFoundError(f"Snapshot {snapshot_id} not found"))
                
                # Parse JSON
                if isinstance(row.get('nodes'), str):
                    row['nodes'] = json.loads(row['nodes'])
                if isinstance(row.get('edges'), str):
                    row['edges'] = json.loads(row['edges'])
                    
                return ServiceResult.ok(row)
        except Exception as e:
            logger.error(f"[WorkflowVersionService Error] Failed to get snapshot: {e}")
            return ServiceResult.fail(StorageError(f"Database error: {str(e)}"))

    def list_snapshots(self, workflow_id: str, limit: int = 50, offset: int = 0) -> ServiceResult:
        """Lists snapshots for a workflow, ordered by creation time descending."""
        try:
            with self.storage.get_session() as session:
                session.execute("""
                    SELECT snapshot_id, parent_snapshot_id, author_id, message, created_at 
                    FROM workflow_snapshots 
                    WHERE workflow_id = %s 
                    ORDER BY created_at DESC 
                    LIMIT %s OFFSET %s
                """, (workflow_id, limit, offset))
                rows = session.fetchall()
                return ServiceResult.ok(rows)
        except Exception as e:
            logger.error(f"[WorkflowVersionService Error] Failed to list snapshots: {e}")
            return ServiceResult.fail(StorageError(f"Database error: {str(e)}"))

    def tag_snapshot(self, workflow_id: str, snapshot_id: str, tag_name: str, conn=None) -> ServiceResult:
        """Tags a snapshot with a human-readable name (e.g., 'v1.0.0', 'production')."""
        try:
            created_at = int(time.time() * 1000)
            
            def do_tag(session):
                # Verify snapshot exists
                session.execute("SELECT snapshot_id FROM workflow_snapshots WHERE snapshot_id = %s", (snapshot_id,))
                if not session.fetchone():
                    raise NotFoundError(f"Snapshot {snapshot_id} not found")
                    
                # Upsert tag
                session.execute("""
                    INSERT INTO workflow_tags (tag_name, workflow_id, snapshot_id, created_at)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE snapshot_id = %s, created_at = %s
                """, (tag_name, workflow_id, snapshot_id, created_at, snapshot_id, created_at))

            if conn:
                do_tag(conn)
            else:
                with self.storage.transaction() as session:
                    do_tag(session)
                    
            logger.info(f"[WorkflowVersionService] Tagged snapshot {snapshot_id} as {tag_name}")
            return ServiceResult.ok({"tag": tag_name, "snapshot_id": snapshot_id})
            
        except NotFoundError as e:
            return ServiceResult.fail(e)
        except Exception as e:
            logger.error(f"[WorkflowVersionService Error] Failed to tag snapshot: {e}")
            return ServiceResult.fail(StorageError(f"Database error: {str(e)}"))

    def get_snapshot_by_tag(self, workflow_id: str, tag_name: str) -> ServiceResult:
        """Resolves a tag to its snapshot."""
        try:
            with self.storage.get_session() as session:
                session.execute("""
                    SELECT snapshot_id FROM workflow_tags 
                    WHERE workflow_id = %s AND tag_name = %s
                """, (workflow_id, tag_name))
                row = session.fetchone()
                
                if not row:
                    return ServiceResult.fail(NotFoundError(f"Tag {tag_name} not found for workflow {workflow_id}"))
                    
                return self.get_snapshot(row['snapshot_id'])
        except Exception as e:
            logger.error(f"[WorkflowVersionService Error] Failed to get tag: {e}")
            return ServiceResult.fail(StorageError(f"Database error: {str(e)}"))
