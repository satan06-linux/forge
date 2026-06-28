# ForgePrompt Phase 7 — DeploymentService
import time
import json
import logging
from typing import Optional, Dict, Any, List

from services.service_result import ServiceResult
from services.errors import ForgeError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

class DeploymentService:
    def __init__(self, container):
        self.container = container

    def _get_next_sequence(self, session, aggregate_type: str, aggregate_id: str) -> int:
        session.execute(
            "INSERT INTO aggregate_sequences (aggregate_type, aggregate_id, seq_num) "
            "VALUES (%s, %s, 1) ON DUPLICATE KEY UPDATE seq_num = seq_num + 1",
            (aggregate_type, aggregate_id)
        )
        row = session.execute_one(
            "SELECT seq_num FROM aggregate_sequences WHERE aggregate_type=%s AND aggregate_id=%s",
            (aggregate_type, aggregate_id)
        )
        return row['seq_num']

    def create_pipeline(self, name: str, organization_id: int, config: Dict[str, Any], conn=None) -> ServiceResult:
        start_time = time.time()
        session = conn or self.container.storage_provider.get_session()
        owns_tx = False
        if conn is None:
            session.begin()
            owns_tx = True

        try:
            config_json = json.dumps(config)
            session.execute(
                "INSERT INTO deployment_pipelines (organization_id, name, config_json, created_at) "
                "VALUES (%s, %s, %s, %s)",
                (organization_id, name, config_json, time.time())
            )
            pipeline_id = session.lastrowid()

            session.execute(
                "INSERT INTO lineage_events (entity_type, entity_id, event_type, details_json, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                ('deployment_pipeline', pipeline_id, 'CREATED', config_json, time.time())
            )

            event_payload = json.dumps({"pipeline_id": pipeline_id, "name": name, "organization_id": organization_id})
            session.execute(
                "INSERT INTO event_outbox (event_type, payload_json, created_at) VALUES (%s, %s, %s)",
                ('PipelineCreated', event_payload, time.time())
            )

            if owns_tx:
                session.commit()

            return ServiceResult.ok({"pipeline_id": pipeline_id}, duration_ms=int((time.time()-start_time)*1000))
        except Exception as e:
            if owns_tx:
                session.rollback()
            logger.error(f"[DeploymentService Error] {e}")
            return ServiceResult.fail(ForgeError(str(e)))

    def create_snapshot(self, pipeline_id: int, organization_id: int, snapshot_data: Dict[str, Any], conn=None) -> ServiceResult:
        start_time = time.time()
        session = conn or self.container.storage_provider.get_session()
        owns_tx = False
        if conn is None:
            session.begin()
            owns_tx = True

        try:
            seq_num = self._get_next_sequence(session, 'pipeline_snapshot', str(pipeline_id))
            snapshot_json = json.dumps(snapshot_data)
            
            session.execute(
                "INSERT INTO config_snapshots (organization_id, pipeline_id, version_num, snapshot_json, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (organization_id, pipeline_id, seq_num, snapshot_json, time.time())
            )
            snapshot_id = session.lastrowid()

            session.execute(
                "INSERT INTO lineage_events (entity_type, entity_id, event_type, details_json, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                ('config_snapshot', snapshot_id, 'CREATED', json.dumps({"pipeline_id": pipeline_id, "version": seq_num}), time.time())
            )

            if owns_tx:
                session.commit()

            return ServiceResult.ok({"snapshot_id": snapshot_id, "version": seq_num}, duration_ms=int((time.time()-start_time)*1000))
        except Exception as e:
            if owns_tx:
                session.rollback()
            logger.error(f"[DeploymentService Error] {e}")
            return ServiceResult.fail(ForgeError(str(e)))

    def promote_snapshot(self, snapshot_id: int, organization_id: int, target_env: str, conn=None) -> ServiceResult:
        start_time = time.time()
        session = conn or self.container.storage_provider.get_session()
        owns_tx = False
        if conn is None:
            session.begin()
            owns_tx = True

        try:
            snapshot = session.execute_one(
                "SELECT id, pipeline_id, version_num, snapshot_json FROM config_snapshots WHERE id=%s AND organization_id=%s",
                (snapshot_id, organization_id)
            )
            if not snapshot:
                raise NotFoundError("Snapshot not found")

            session.execute(
                "INSERT INTO deployments (organization_id, pipeline_id, environment, active_snapshot_id, updated_at) "
                "VALUES (%s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE active_snapshot_id=%s, updated_at=%s",
                (organization_id, snapshot['pipeline_id'], target_env, snapshot_id, time.time(), snapshot_id, time.time())
            )
            
            comp_payload = json.dumps({"action": "rollback_promotion", "snapshot_id": snapshot_id, "env": target_env})
            session.execute(
                "INSERT INTO saga_coordinator (transaction_id, compensation_action, compensation_payload, status) "
                "VALUES (%s, %s, %s, %s)",
                (f"promote_{snapshot_id}_{target_env}_{int(time.time())}", "deployment_service.rollback", comp_payload, "REGISTERED")
            )

            ext_payload = json.dumps({"snapshot_id": snapshot_id, "environment": target_env})
            session.execute(
                "INSERT INTO external_effect_ledger (effect_type, target, payload_json, status, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                ("DEPLOYMENT", target_env, ext_payload, "PENDING", time.time())
            )

            event_payload = json.dumps({
                "snapshot_id": snapshot_id,
                "pipeline_id": snapshot['pipeline_id'],
                "environment": target_env,
                "organization_id": organization_id
            })
            session.execute(
                "INSERT INTO event_outbox (event_type, payload_json, created_at) VALUES (%s, %s, %s)",
                ('SnapshotPromoted', event_payload, time.time())
            )

            if owns_tx:
                session.commit()

            return ServiceResult.ok({"status": "promoted", "environment": target_env}, duration_ms=int((time.time()-start_time)*1000))
        except Exception as e:
            if owns_tx:
                session.rollback()
            logger.error(f"[DeploymentService Error] {e}")
            return ServiceResult.fail(ForgeError(str(e)))

    def handle_external_effect_completed(self, event_id: str, payload: Dict[str, Any], conn=None) -> ServiceResult:
        start_time = time.time()
        session = conn or self.container.storage_provider.get_session()
        owns_tx = False
        if conn is None:
            session.begin()
            owns_tx = True

        try:
            processed = session.execute_one(
                "SELECT 1 FROM processed_events WHERE event_id=%s AND subscriber=%s",
                (event_id, "deployment_service")
            )
            if processed:
                if owns_tx:
                    session.rollback()
                return ServiceResult.ok({"status": "already_processed"})

            session.execute(
                "INSERT INTO processed_events (event_id, subscriber, processed_at) VALUES (%s, %s, %s)",
                (event_id, "deployment_service", time.time())
            )

            effect_id = payload.get("effect_id")
            if effect_id:
                session.execute(
                    "UPDATE external_effect_ledger SET status='COMPLETED' WHERE id=%s AND effect_type='DEPLOYMENT'",
                    (effect_id,)
                )

            if owns_tx:
                session.commit()
            return ServiceResult.ok({}, duration_ms=int((time.time()-start_time)*1000))
        except Exception as e:
            if owns_tx:
                session.rollback()
            logger.error(f"[DeploymentService Error] {e}")
            return ServiceResult.fail(ForgeError(str(e)))
