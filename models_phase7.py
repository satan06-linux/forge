# ForgePrompt Phase 7 — Database Schema Migration
from models import get_db_connection
import logging

logger = logging.getLogger(__name__)

def init_phase7_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # ---------------------------------------------------------
        # Group 1 — Infrastructure
        # ---------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS aggregate_sequences (
                aggregate_type VARCHAR(50) NOT NULL,
                aggregate_id INT NOT NULL DEFAULT 0,
                next_sequence BIGINT NOT NULL DEFAULT 1,
                PRIMARY KEY (aggregate_type, aggregate_id)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS distributed_locks (
                lock_name VARCHAR(255) PRIMARY KEY,
                owner VARCHAR(100) NOT NULL,
                acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                version INT DEFAULT 0,
                INDEX idx_locks_expires (expires_at)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cache_store (
                cache_key VARCHAR(512) NOT NULL,
                namespace VARCHAR(100) NOT NULL DEFAULT 'default',
                value_json LONGTEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (cache_key, namespace),
                INDEX idx_cache_expires (expires_at)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feature_flags (
                flag_name VARCHAR(100) PRIMARY KEY,
                enabled TINYINT(1) DEFAULT 1,
                rollout_pct DECIMAL(5,2) DEFAULT 100.00,
                description TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_events (
                subscriber_name VARCHAR(100) NOT NULL,
                event_id INT NOT NULL,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (subscriber_name, event_id)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rate_limit_buckets (
                bucket_key VARCHAR(255) PRIMARY KEY,
                tokens DECIMAL(15,4) NOT NULL DEFAULT 0,
                capacity DECIMAL(15,4) NOT NULL,
                refill_rate DECIMAL(15,4) NOT NULL,
                last_refill_at TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6),
                version INT DEFAULT 0
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS maintenance_jobs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                job_type VARCHAR(100) NOT NULL,
                status ENUM('pending','running','completed','failed','skipped') DEFAULT 'pending',
                lease_owner VARCHAR(100) NULL,
                lease_until TIMESTAMP NULL,
                started_at TIMESTAMP NULL,
                completed_at TIMESTAMP NULL,
                error_message TEXT NULL,
                metadata_json LONGTEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_maint_type (job_type, status),
                INDEX idx_maint_lease (lease_until)
            ) ENGINE=InnoDB
        """)

        # ---------------------------------------------------------
        # Group 2 — Event Bus
        # ---------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_outbox (
                id INT AUTO_INCREMENT PRIMARY KEY,
                aggregate_type VARCHAR(50) NOT NULL,
                aggregate_id INT NOT NULL,
                sequence_number BIGINT NOT NULL,
                partition_key INT NOT NULL DEFAULT 0,
                event_type VARCHAR(100) NOT NULL,
                event_version INT NOT NULL DEFAULT 1,
                payload_json LONGTEXT NOT NULL,
                status ENUM('pending','dispatched','failed') DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                dispatched_at TIMESTAMP NULL,
                INDEX idx_outbox_pending (partition_key, status, created_at),
                INDEX idx_outbox_aggregate (aggregate_type, aggregate_id, sequence_number),
                UNIQUE KEY uq_outbox_seq (aggregate_type, aggregate_id, sequence_number)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_inbox (
                id INT AUTO_INCREMENT PRIMARY KEY,
                outbox_id INT NOT NULL,
                subscriber_name VARCHAR(100) NOT NULL,
                event_type VARCHAR(100) NOT NULL,
                payload_json LONGTEXT NOT NULL,
                status ENUM('pending','processing','delivered','failed') DEFAULT 'pending',
                attempt_count INT DEFAULT 0,
                last_error TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                delivered_at TIMESTAMP NULL,
                INDEX idx_inbox_subscriber (subscriber_name, status, created_at),
                INDEX idx_inbox_outbox (outbox_id)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_schemas (
                id INT AUTO_INCREMENT PRIMARY KEY,
                event_type VARCHAR(100) NOT NULL,
                version INT NOT NULL,
                schema_json LONGTEXT NOT NULL,
                compatibility ENUM('BACKWARD','FORWARD','FULL','NONE') DEFAULT 'BACKWARD',
                deprecated TINYINT(1) DEFAULT 0,
                sunset_at TIMESTAMP NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_event_version (event_type, version),
                INDEX idx_schema_type (event_type)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_schema_migrations (
                id INT AUTO_INCREMENT PRIMARY KEY,
                event_type VARCHAR(100) NOT NULL,
                from_version INT NOT NULL,
                to_version INT NOT NULL,
                migration_fn VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_schema_migration (event_type, from_version, to_version)
            ) ENGINE=InnoDB
        """)

        # ---------------------------------------------------------
        # Group 3 — Workflow Engine
        # ---------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS workflow_snapshots (
                id INT AUTO_INCREMENT PRIMARY KEY,
                workflow_id INT NOT NULL,
                version_id INT NULL,
                nodes_json LONGTEXT NOT NULL,
                edges_json LONGTEXT NOT NULL,
                variables_json LONGTEXT NULL,
                checksum CHAR(64) NOT NULL,
                snapshot_mode ENUM('full','delta') DEFAULT 'full',
                base_snapshot_id INT NULL,
                delta_nodes_json LONGTEXT NULL,
                environment VARCHAR(50) DEFAULT 'production',
                is_active TINYINT(1) DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_snap_workflow (workflow_id, environment, is_active),
                FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS compiled_plans (
                id INT AUTO_INCREMENT PRIMARY KEY,
                workflow_id INT NOT NULL,
                version_id INT NOT NULL,
                checksum CHAR(64) NOT NULL,
                plan_blob LONGBLOB NOT NULL,
                plan_signature VARBINARY(512) NULL,
                signer_key_id VARCHAR(64) NULL,
                node_count INT NOT NULL,
                edge_count INT NOT NULL,
                compiled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_plan_version (workflow_id, version_id),
                INDEX idx_plan_checksum (checksum)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS workflow_history (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                run_id INT NOT NULL,
                sequence_number BIGINT NOT NULL,
                event_type VARCHAR(50) NOT NULL,
                node_id VARCHAR(128) NULL,
                payload_json LONGTEXT NULL,
                trace_id CHAR(32) NULL,
                span_id CHAR(16) NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_history_run (run_id, sequence_number),
                UNIQUE KEY uq_history_seq (run_id, sequence_number),
                FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS timers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                run_id INT NOT NULL,
                node_id VARCHAR(128) NOT NULL,
                fire_at TIMESTAMP NOT NULL,
                status ENUM('pending','fired','cancelled') DEFAULT 'pending',
                payload_json LONGTEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_timers_pending (status, fire_at),
                FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS streaming_checkpoints (
                id INT AUTO_INCREMENT PRIMARY KEY,
                run_id INT NOT NULL,
                node_id VARCHAR(128) NOT NULL,
                stream_offset INT NOT NULL DEFAULT 0,
                resume_token LONGTEXT NULL,
                partial_output LONGTEXT NULL,
                kv_cache_ref VARCHAR(512) NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_stream_cp (run_id, node_id),
                FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        # ---------------------------------------------------------
        # Group 4 — Worker Queue
        # ---------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS worker_queue (
                id INT AUTO_INCREMENT PRIMARY KEY,
                queue_name VARCHAR(50) NOT NULL DEFAULT 'default',
                job_type VARCHAR(100) NOT NULL,
                payload_json LONGTEXT NOT NULL,
                serialization_format ENUM('json','msgpack','protobuf') DEFAULT 'json',
                status ENUM('queued','leased','running','completed','failed','dead_letter') DEFAULT 'queued',
                priority INT NOT NULL DEFAULT 5,
                attempt_count INT DEFAULT 0,
                max_attempts INT DEFAULT 3,
                worker_id VARCHAR(100) NULL,
                lease_expires_at TIMESTAMP NULL,
                lease_version INT DEFAULT 0,
                run_id INT NULL,
                node_id VARCHAR(128) NULL,
                organization_id INT NULL,
                required_capabilities_json LONGTEXT NULL,
                min_engine_version VARCHAR(20) NULL,
                placement_tags_json LONGTEXT NULL,
                dead_letter_reason VARCHAR(255) NULL,
                dead_letter_at TIMESTAMP NULL,
                dead_letter_tag VARCHAR(100) NULL,
                dead_letter_archived TINYINT(1) DEFAULT 0,
                dead_letter_exported TINYINT(1) DEFAULT 0,
                scheduled_at TIMESTAMP NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP NULL,
                INDEX idx_wq_dispatch (queue_name, status, created_at),
                INDEX idx_wq_lease (status, lease_expires_at),
                INDEX idx_wq_org (organization_id, status, created_at),
                INDEX idx_wq_run (run_id, node_id),
                FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE SET NULL,
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS worker_registry (
                id INT AUTO_INCREMENT PRIMARY KEY,
                worker_id VARCHAR(100) UNIQUE NOT NULL,
                queue_subscriptions_json LONGTEXT NULL,
                capabilities_json LONGTEXT NULL,
                engine_version VARCHAR(20) NULL,
                node_schema_version INT NULL,
                checkpoint_version INT NULL,
                tags_json LONGTEXT NULL,
                status ENUM('active','idle','draining','offline') DEFAULT 'active',
                current_job_id INT NULL,
                health_score INT DEFAULT 100,
                jobs_completed INT DEFAULT 0,
                jobs_failed INT DEFAULT 0,
                avg_latency_ms INT DEFAULT 0,
                is_warm_pool TINYINT(1) DEFAULT 0,
                warm_queue_name VARCHAR(50) NULL,
                last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_worker_status (status, last_heartbeat)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS worker_capabilities (
                worker_id INT NOT NULL,
                capability VARCHAR(100) NOT NULL,
                value VARCHAR(255) NULL,
                PRIMARY KEY (worker_id, capability),
                FOREIGN KEY (worker_id) REFERENCES worker_registry(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dead_letter_actions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                job_id INT NOT NULL,
                action ENUM('replayed','moved','archived','purged','exported','tagged') NOT NULL,
                actor_id INT NULL,
                note TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_dl_job (job_id)
            ) ENGINE=InnoDB
        """)

        # ---------------------------------------------------------
        # Group 5 — Agents
        # ---------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_versions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                agent_id INT NOT NULL,
                version_number INT NOT NULL,
                config_snapshot_json LONGTEXT NOT NULL,
                tools_json LONGTEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_agent_version (agent_id, version_number),
                FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_runs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                agent_id INT NOT NULL,
                run_id INT NULL,
                organization_id INT NULL,
                status ENUM('queued','planning','executing','waiting','completed','failed','cancelled') DEFAULT 'queued',
                goal TEXT NULL,
                final_output LONGTEXT NULL,
                total_tokens INT DEFAULT 0,
                total_cost DECIMAL(10,6) DEFAULT 0,
                iteration_count INT DEFAULT 0,
                max_iterations INT DEFAULT 20,
                trace_id CHAR(32) NULL,
                span_id CHAR(16) NULL,
                parent_span_id CHAR(16) NULL,
                started_at TIMESTAMP NULL,
                completed_at TIMESTAMP NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_agent_run_org (organization_id, status, created_at),
                FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE,
                FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE SET NULL,
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_tasks (
                id INT AUTO_INCREMENT PRIMARY KEY,
                agent_run_id INT NOT NULL,
                task_description TEXT NOT NULL,
                status ENUM('pending','running','completed','failed','skipped') DEFAULT 'pending',
                result_json LONGTEXT NULL,
                tool_used VARCHAR(100) NULL,
                tokens_used INT DEFAULT 0,
                cost DECIMAL(10,6) DEFAULT 0,
                step_index INT NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_task_run (agent_run_id, step_index),
                FOREIGN KEY (agent_run_id) REFERENCES agent_runs(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        # ---------------------------------------------------------
        # Group 6 — Scheduling
        # ---------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                workflow_id INT NOT NULL,
                organization_id INT NULL,
                user_id INT NOT NULL,
                name VARCHAR(100) NOT NULL,
                cron_expression VARCHAR(100) NOT NULL,
                enabled TINYINT(1) DEFAULT 1,
                next_fire_at TIMESTAMP NULL,
                last_fire_at TIMESTAMP NULL,
                last_run_id INT NULL,
                input_json LONGTEXT NULL,
                cron_lease_owner VARCHAR(100) NULL,
                cron_lease_until TIMESTAMP NULL,
                cron_lease_version INT DEFAULT 0,
                retry_count INT DEFAULT 0,
                max_retries INT DEFAULT 3,
                drift_seconds INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_sched_fire (enabled, next_fire_at),
                INDEX idx_sched_org (organization_id, enabled),
                FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE,
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        # ---------------------------------------------------------
        # Group 7 — Approvals & Deployments
        # ---------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS approval_requests (
                id INT AUTO_INCREMENT PRIMARY KEY,
                run_id INT NOT NULL,
                node_id VARCHAR(128) NOT NULL,
                organization_id INT NULL,
                requested_by_user_id INT NULL,
                status ENUM('pending','approved','rejected','timed_out') DEFAULT 'pending',
                context_json LONGTEXT NULL,
                snapshot_binding_json LONGTEXT NULL,
                resolver_user_id INT NULL,
                resolver_note TEXT NULL,
                timeout_at TIMESTAMP NULL,
                resolved_at TIMESTAMP NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_approval_run (run_id),
                INDEX idx_approval_org (organization_id, status, created_at),
                FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE,
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deployments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                workflow_id INT NOT NULL,
                organization_id INT NULL,
                version_id INT NOT NULL,
                environment VARCHAR(50) NOT NULL DEFAULT 'production',
                status ENUM('pending','promoting','active','rolled_back','failed') DEFAULT 'pending',
                promoted_by_user_id INT NULL,
                snapshot_json LONGTEXT NULL,
                snapshot_checksum CHAR(64) NULL,
                config_snapshot_json LONGTEXT NULL,
                migration_approved TINYINT(1) DEFAULT 0,
                rollback_deployment_id INT NULL,
                promoted_at TIMESTAMP NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_deploy_workflow (workflow_id, environment, status),
                FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE,
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deployment_config_snapshots (
                id INT AUTO_INCREMENT PRIMARY KEY,
                deployment_id INT NOT NULL,
                feature_flags_json LONGTEXT NULL,
                secret_versions_json LONGTEXT NULL,
                node_schema_versions_json LONGTEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (deployment_id) REFERENCES deployments(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        # ---------------------------------------------------------
        # Group 8 — Audit & Secrets
        # ---------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                organization_id INT NULL,
                actor_id INT NULL,
                action VARCHAR(100) NOT NULL,
                resource_type VARCHAR(50) NOT NULL,
                resource_id INT NULL,
                metadata_json LONGTEXT NULL,
                ip_address VARCHAR(45) NULL,
                entry_hash CHAR(64) NULL,
                previous_hash CHAR(64) NULL,
                signature VARBINARY(512) NULL,
                signer_key_id VARCHAR(64) NULL,
                timestamp_token LONGTEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_audit_org (organization_id, created_at),
                INDEX idx_audit_actor (actor_id, created_at),
                INDEX idx_audit_resource (resource_type, resource_id)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS secrets (
                id INT AUTO_INCREMENT PRIMARY KEY,
                organization_id INT NULL,
                name VARCHAR(100) NOT NULL,
                encrypted_dek LONGTEXT NOT NULL,
                ciphertext LONGTEXT NOT NULL,
                iv VARCHAR(64) NOT NULL,
                auth_tag VARCHAR(64) NOT NULL,
                kms_provider VARCHAR(50) DEFAULT 'local',
                key_version INT DEFAULT 1,
                version INT DEFAULT 1,
                is_active TINYINT(1) DEFAULT 1,
                rotation_due_at TIMESTAMP NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_org_secret_version (organization_id, name, version),
                INDEX idx_secrets_org (organization_id, name, is_active),
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS secret_rotation_policies (
                id INT AUTO_INCREMENT PRIMARY KEY,
                organization_id INT NULL,
                secret_name VARCHAR(100) NOT NULL,
                rotation_interval_days INT DEFAULT 90,
                grace_period_days INT DEFAULT 7,
                auto_revoke TINYINT(1) DEFAULT 1,
                auto_cleanup TINYINT(1) DEFAULT 0,
                last_rotated_at TIMESTAMP NULL,
                UNIQUE KEY uq_rot_policy (organization_id, secret_name)
            ) ENGINE=InnoDB
        """)

        # ---------------------------------------------------------
        # Group 9 — Observability
        # ---------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trace_spans (
                id INT AUTO_INCREMENT PRIMARY KEY,
                trace_id CHAR(32) NOT NULL,
                span_id CHAR(16) NOT NULL,
                parent_span_id CHAR(16) NULL,
                service_name VARCHAR(100) NOT NULL,
                operation_name VARCHAR(100) NOT NULL,
                resource_type VARCHAR(50) NULL,
                resource_id INT NULL,
                status ENUM('ok','error','timeout') DEFAULT 'ok',
                duration_ms INT DEFAULT 0,
                metadata_json LONGTEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_trace_id (trace_id, span_id),
                INDEX idx_trace_resource (resource_type, resource_id)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_metrics_snapshots (
                id INT AUTO_INCREMENT PRIMARY KEY,
                service_name VARCHAR(100) NOT NULL,
                metric_name VARCHAR(100) NOT NULL,
                metric_value DECIMAL(15,4) NOT NULL,
                metric_type ENUM('gauge','counter','histogram') DEFAULT 'gauge',
                labels_json LONGTEXT NULL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_metrics_service (service_name, metric_name, recorded_at)
            ) ENGINE=InnoDB
        """)

        # ---------------------------------------------------------
        # Group 10 — Saga
        # ---------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS saga_executions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                run_id INT NOT NULL,
                saga_type VARCHAR(100) NOT NULL,
                status ENUM('running','completed','compensating','compensated','failed') DEFAULT 'running',
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP NULL,
                organization_id INT NULL,
                INDEX idx_saga_run (run_id),
                INDEX idx_saga_org (organization_id, status, started_at),
                FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS saga_steps (
                id INT AUTO_INCREMENT PRIMARY KEY,
                saga_id INT NOT NULL,
                step_name VARCHAR(100) NOT NULL,
                step_order INT NOT NULL,
                status ENUM('pending','executing','completed','compensating','compensated','failed') DEFAULT 'pending',
                forward_action_json LONGTEXT NULL,
                compensation_json LONGTEXT NULL,
                result_json LONGTEXT NULL,
                error_message TEXT NULL,
                executed_at TIMESTAMP NULL,
                compensated_at TIMESTAMP NULL,
                INDEX idx_saga_step (saga_id, step_order),
                FOREIGN KEY (saga_id) REFERENCES saga_executions(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        # ---------------------------------------------------------
        # Group 11 — CQRS Read Models
        # ---------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rm_workflow_summaries (
                workflow_id INT NOT NULL PRIMARY KEY,
                organization_id INT NOT NULL,
                total_runs INT DEFAULT 0,
                successful_runs INT DEFAULT 0,
                failed_runs INT DEFAULT 0,
                avg_latency_ms INT DEFAULT 0,
                total_cost DECIMAL(12,6) DEFAULT 0,
                total_tokens BIGINT DEFAULT 0,
                last_run_at TIMESTAMP NULL,
                last_run_status VARCHAR(50) NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_rm_wf_org (organization_id, last_run_at)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rm_agent_summaries (
                agent_id INT NOT NULL PRIMARY KEY,
                organization_id INT NOT NULL,
                total_runs INT DEFAULT 0,
                success_rate DECIMAL(5,2) DEFAULT 0,
                avg_cost DECIMAL(10,6) DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_rm_ag_org (organization_id)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rm_org_dashboards (
                organization_id INT NOT NULL PRIMARY KEY,
                active_runs INT DEFAULT 0,
                queued_jobs INT DEFAULT 0,
                active_agents INT DEFAULT 0,
                dead_letter_count INT DEFAULT 0,
                monthly_cost DECIMAL(12,6) DEFAULT 0,
                monthly_tokens BIGINT DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB
        """)

        # ---------------------------------------------------------
        # Group 12 — Governance
        # ---------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS organization_policies (
                id INT AUTO_INCREMENT PRIMARY KEY,
                organization_id INT NOT NULL,
                policy_name VARCHAR(100) NOT NULL,
                policy_type ENUM('cost','rate','compliance','access','custom') DEFAULT 'custom',
                expression TEXT NOT NULL,
                enabled TINYINT(1) DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_org_policy (organization_id, policy_name),
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tenant_resource_quotas (
                id INT AUTO_INCREMENT PRIMARY KEY,
                organization_id INT NOT NULL UNIQUE,
                max_cpu_pct INT DEFAULT 100,
                max_memory_mb INT DEFAULT 2048,
                max_worker_slots INT DEFAULT 10,
                max_gpu_slots INT DEFAULT 1,
                max_storage_gb DECIMAL(10,2) DEFAULT 10,
                max_bandwidth_mbps INT DEFAULT 100,
                max_api_calls_per_hour INT DEFAULT 10000,
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tenant_resource_usage_realtime (
                organization_id INT NOT NULL PRIMARY KEY,
                current_cpu_pct DECIMAL(5,2) DEFAULT 0,
                current_memory_mb INT DEFAULT 0,
                active_worker_slots INT DEFAULT 0,
                active_gpu_slots INT DEFAULT 0,
                storage_used_gb DECIMAL(10,2) DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS external_effect_ledger (
                id INT AUTO_INCREMENT PRIMARY KEY,
                effect_hash CHAR(64) NOT NULL UNIQUE,
                effect_type ENUM('webhook','email','llm_call','deployment','billing','slack','sms','custom') NOT NULL,
                target VARCHAR(512) NOT NULL,
                payload_hash CHAR(64) NOT NULL,
                status ENUM('pending','delivered','failed','duplicate') DEFAULT 'pending',
                attempt_count INT DEFAULT 0,
                retry_token VARCHAR(128) NULL,
                delivered_at TIMESTAMP NULL,
                expires_at TIMESTAMP NOT NULL,
                run_id INT NULL,
                node_id VARCHAR(128) NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_effect_hash (effect_hash),
                INDEX idx_effect_run (run_id, node_id),
                INDEX idx_effect_expires (expires_at)
            ) ENGINE=InnoDB
        """)

        # ---------------------------------------------------------
        # Group 13 — Lineage & Cost
        # ---------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lineage_events (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                run_id INT NOT NULL,
                node_id VARCHAR(128) NULL,
                event_type ENUM('prompt_used','model_called','context_retrieved','tool_invoked','artifact_produced','output_generated','memory_read','memory_written','knowledge_queried','external_effect') NOT NULL,
                entity_type VARCHAR(50) NOT NULL,
                entity_id VARCHAR(255) NOT NULL,
                entity_version VARCHAR(50) NULL,
                metadata_json LONGTEXT NULL,
                parent_lineage_id BIGINT NULL,
                trace_id CHAR(32) NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_lineage_run (run_id, node_id),
                INDEX idx_lineage_type (run_id, event_type),
                INDEX idx_lineage_entity (entity_type, entity_id),
                FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cost_attribution (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                organization_id INT NOT NULL,
                department_tag VARCHAR(100) NULL,
                project_tag VARCHAR(100) NULL,
                workflow_id INT NULL,
                agent_id INT NULL,
                run_id INT NULL,
                node_id VARCHAR(128) NULL,
                tool_name VARCHAR(100) NULL,
                provider VARCHAR(50) NULL,
                tokens_used INT DEFAULT 0,
                cost_usd DECIMAL(10,6) DEFAULT 0,
                latency_ms INT DEFAULT 0,
                occurred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_cost_org (organization_id, occurred_at),
                INDEX idx_cost_dept (organization_id, department_tag, occurred_at),
                INDEX idx_cost_project (organization_id, project_tag, occurred_at),
                INDEX idx_cost_workflow (workflow_id, occurred_at),
                INDEX idx_cost_provider (provider, occurred_at)
            ) ENGINE=InnoDB
        """)

        # ---------------------------------------------------------
        # Group 14 — Provenance & Debugger
        # ---------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS provenance_nodes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                run_id INT NOT NULL,
                node_type ENUM('workflow','agent','node','provider','tool','artifact','output','prompt','knowledge') NOT NULL,
                node_ref_id VARCHAR(255) NOT NULL,
                label VARCHAR(255) NULL,
                metadata_json LONGTEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_prov_run (run_id, node_type)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS provenance_edges (
                id INT AUTO_INCREMENT PRIMARY KEY,
                run_id INT NOT NULL,
                source_id INT NOT NULL,
                target_id INT NOT NULL,
                relationship ENUM('produced','consumed','called','retrieved','stored','triggered') NOT NULL,
                INDEX idx_prov_edge_run (run_id),
                FOREIGN KEY (source_id) REFERENCES provenance_nodes(id) ON DELETE CASCADE,
                FOREIGN KEY (target_id) REFERENCES provenance_nodes(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS debug_sessions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                run_id INT NOT NULL,
                user_id INT NOT NULL,
                status ENUM('active','paused','stepping','closed') DEFAULT 'active',
                breakpoints_json LONGTEXT NULL,
                current_node_id VARCHAR(128) NULL,
                context_snapshot_json LONGTEXT NULL,
                variable_patches_json LONGTEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """)

        # ---------------------------------------------------------
        # Group 15 — Retention & Search
        # ---------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS retention_policies (
                id INT AUTO_INCREMENT PRIMARY KEY,
                organization_id INT NULL,
                resource_type ENUM('workflow_run','workflow_history','agent_run','audit_log','artifact') NOT NULL,
                hot_days INT DEFAULT 30,
                warm_days INT DEFAULT 90,
                cold_days INT DEFAULT 365,
                archive_after_days INT DEFAULT 180,
                purge_after_days INT DEFAULT 730,
                cold_storage_provider VARCHAR(50) DEFAULT 'local',
                cold_storage_path VARCHAR(512) NULL,
                UNIQUE KEY uq_org_resource (organization_id, resource_type)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS retention_archive_runs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                original_run_id INT NOT NULL,
                organization_id INT NOT NULL,
                compressed_blob LONGBLOB NULL,
                cold_storage_ref VARCHAR(512) NULL,
                archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_archive_org (organization_id, archived_at)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS search_index_queue (
                id INT AUTO_INCREMENT PRIMARY KEY,
                resource_type VARCHAR(50) NOT NULL,
                resource_id INT NOT NULL,
                operation ENUM('index','delete') DEFAULT 'index',
                indexed TINYINT(1) DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_search_pending (indexed, resource_type)
            ) ENGINE=InnoDB
        """)

        # ---------------------------------------------------------
        # Group 16 — Phase 8 Stubs
        # ---------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shard_assignments (
                shard_id INT NOT NULL,
                organization_id INT NOT NULL,
                worker_node_id VARCHAR(100) NOT NULL,
                region VARCHAR(50) NOT NULL DEFAULT 'local',
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (shard_id, organization_id)
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hlc_state (
                node_id VARCHAR(100) NOT NULL PRIMARY KEY,
                physical_time BIGINT NOT NULL,
                logical_counter INT NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB
        """)

        # ---------------------------------------------------------
        # Migration Checks
        # ---------------------------------------------------------
        
        # workflow_runs
        runs_cols = [
            ("snapshot_json", "LONGTEXT NULL"),
            ("snapshot_checksum", "CHAR(64) NULL"),
            ("node_schema_versions_json", "TEXT NULL"),
            ("compiled_plan_ref", "VARCHAR(512) NULL"),
            ("lineage_root_id", "INT NULL"),
            ("continued_from_run_id", "INT NULL"),
            ("history_compacted", "TINYINT(1) DEFAULT 0"),
            ("compaction_at", "TIMESTAMP NULL"),
            ("organization_id", "INT NULL"),
            ("priority", "INT DEFAULT 5"),
            ("trace_id", "CHAR(32) NULL"),
            ("span_id", "CHAR(16) NULL")
        ]
        for col_name, col_def in runs_cols:
            cursor.execute(f"SHOW COLUMNS FROM workflow_runs LIKE '{col_name}'")
            if not cursor.fetchone():
                logger.info(f"[Phase7 DB] Adding '{col_name}' to 'workflow_runs'...")
                cursor.execute(f"ALTER TABLE workflow_runs ADD COLUMN {col_name} {col_def}")
                if col_name == "organization_id":
                    try:
                        cursor.execute("ALTER TABLE workflow_runs ADD CONSTRAINT fk_wr_org FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL")
                    except Exception as e:
                        logger.warning(f"[Phase7 DB] Failed to add fk_wr_org constraint: {e}")

        # workflow_steps
        steps_cols = [
            ("input_hash", "CHAR(64) NULL"),
            ("execution_fingerprint", "CHAR(64) NULL"),
            ("local_activity", "TINYINT(1) DEFAULT 0"),
            ("trace_id", "CHAR(32) NULL"),
            ("span_id", "CHAR(16) NULL"),
            ("started_at", "TIMESTAMP NULL"),
            ("latency_ms", "INT DEFAULT 0")
        ]
        for col_name, col_def in steps_cols:
            cursor.execute(f"SHOW COLUMNS FROM workflow_steps LIKE '{col_name}'")
            if not cursor.fetchone():
                logger.info(f"[Phase7 DB] Adding '{col_name}' to 'workflow_steps'...")
                cursor.execute(f"ALTER TABLE workflow_steps ADD COLUMN {col_name} {col_def}")

        # agents
        agents_cols = [
            ("version_number", "INT DEFAULT 1"),
            ("is_multi_agent", "TINYINT(1) DEFAULT 0"),
            ("max_iterations", "INT DEFAULT 20"),
            ("sub_agents_json", "LONGTEXT NULL")
        ]
        for col_name, col_def in agents_cols:
            cursor.execute(f"SHOW COLUMNS FROM agents LIKE '{col_name}'")
            if not cursor.fetchone():
                logger.info(f"[Phase7 DB] Adding '{col_name}' to 'agents'...")
                cursor.execute(f"ALTER TABLE agents ADD COLUMN {col_name} {col_def}")

        # workflow_versions
        versions_cols = [
            ("commit_message", "TEXT NULL"),
            ("parent_version_id", "INT NULL"),
            ("checksum", "CHAR(64) NULL"),
            ("created_by", "INT NULL")
        ]
        for col_name, col_def in versions_cols:
            cursor.execute(f"SHOW COLUMNS FROM workflow_versions LIKE '{col_name}'")
            if not cursor.fetchone():
                logger.info(f"[Phase7 DB] Adding '{col_name}' to 'workflow_versions'...")
                cursor.execute(f"ALTER TABLE workflow_versions ADD COLUMN {col_name} {col_def}")


        conn.commit()
        print('[Phase7 DB] All Phase 7 tables initialized successfully.')
        return True

    except Exception as e:
        print(f'[Phase7 DB Error] {e}')
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()
