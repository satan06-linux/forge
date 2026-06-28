import mysql.connector
from mysql.connector import errorcode
import config
import json

def get_db_connection(include_db=True):
    """
    Establishes and returns a direct connection to the MySQL server.
    If include_db is True, it connects directly to the 'forge_db' database.
    """
    try:
        if include_db:
            return mysql.connector.connect(
                host=config.MYSQL_HOST,
                user=config.MYSQL_USER,
                password=config.MYSQL_PASSWORD,
                database=config.MYSQL_DATABASE
            )
        else:
            return mysql.connector.connect(
                host=config.MYSQL_HOST,
                user=config.MYSQL_USER,
                password=config.MYSQL_PASSWORD
            )
    except mysql.connector.Error as err:
        print(f"[MySQL Connection Error] {err}")
        raise err

def init_db():
    """
    Verifies the existence of 'forge_db' and creates it if it doesn't exist.
    Initializes the required database schemas: 'users' and 'prompts_history'.
    """
    # 1. Connect to MySQL Server (Without DB) to create the schema if missing
    conn = None
    cursor = None
    try:
        conn = get_db_connection(include_db=False)
        cursor = conn.cursor()
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {config.MYSQL_DATABASE} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        print(f"[MySQL] Database '{config.MYSQL_DATABASE}' checked/created successfully.")
    except Exception as e:
        print(f"[MySQL] Failed to verify/create database: {str(e)}")
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    # 2. Connect to the database and initialize tables
    conn = None
    cursor = None
    try:
        conn = get_db_connection(include_db=True)
        cursor = conn.cursor()

        # Create Users Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                prompts_used INT DEFAULT 0,
                role VARCHAR(20) DEFAULT 'user',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB
            """
        )

        # Migration Safety Check: Add 'role' column if it does not exist
        cursor.execute("SHOW COLUMNS FROM users LIKE 'role'")
        if not cursor.fetchone():
            print("[MySQL] Migrating database: Adding 'role' column to 'users' table...")
            cursor.execute("ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT 'user'")

        # Migration Safety Check: Add 'username' column if it does not exist
        cursor.execute("SHOW COLUMNS FROM users LIKE 'username'")
        if not cursor.fetchone():
            print("[MySQL] Migrating database: Adding 'username' column to 'users' table...")
            cursor.execute("ALTER TABLE users ADD COLUMN username VARCHAR(50) UNIQUE NULL")

        # Migration Safety Check: Add 'is_banned' column if it does not exist
        cursor.execute("SHOW COLUMNS FROM users LIKE 'is_banned'")
        if not cursor.fetchone():
            print("[MySQL] Migrating database: Adding 'is_banned' column to 'users' table...")
            cursor.execute("ALTER TABLE users ADD COLUMN is_banned TINYINT(1) DEFAULT 0")

        # Create Prompts Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS prompts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NULL,
                input_text TEXT NOT NULL,
                category VARCHAR(50) NOT NULL,
                mcq_questions JSON NOT NULL,
                mcq_answers JSON NOT NULL,
                generated_prompt LONGTEXT NOT NULL,
                is_favorite TINYINT(1) DEFAULT 0,
                version_number INT DEFAULT 1,
                parent_prompt_id INT DEFAULT NULL,
                quality_score INT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY (parent_prompt_id) REFERENCES prompts(id) ON DELETE SET NULL
            ) ENGINE=InnoDB
            """
        )

        # Migration Safety Check: Add 'is_favorite' column to prompts table if it doesn't exist
        cursor.execute("SHOW COLUMNS FROM prompts LIKE 'is_favorite'")
        if not cursor.fetchone():
            print("[MySQL] Migrating database: Adding 'is_favorite' column to 'prompts' table...")
            cursor.execute("ALTER TABLE prompts ADD COLUMN is_favorite TINYINT(1) DEFAULT 0")

        # Migration Safety Check: Add 'version_number' column if it doesn't exist
        cursor.execute("SHOW COLUMNS FROM prompts LIKE 'version_number'")
        if not cursor.fetchone():
            print("[MySQL] Migrating database: Adding 'version_number' column to 'prompts' table...")
            cursor.execute("ALTER TABLE prompts ADD COLUMN version_number INT DEFAULT 1")

        # Migration Safety Check: Add 'parent_prompt_id' column if it doesn't exist
        cursor.execute("SHOW COLUMNS FROM prompts LIKE 'parent_prompt_id'")
        if not cursor.fetchone():
            print("[MySQL] Migrating database: Adding 'parent_prompt_id' column to 'prompts' table...")
            cursor.execute("ALTER TABLE prompts ADD COLUMN parent_prompt_id INT DEFAULT NULL")
            try:
                cursor.execute("ALTER TABLE prompts ADD CONSTRAINT fk_parent_prompt FOREIGN KEY (parent_prompt_id) REFERENCES prompts(id) ON DELETE SET NULL")
            except Exception as fk_err:
                print(f"[MySQL Migration Warning] Failed to add foreign key constraint: {fk_err}")

        # Migration Safety Check: Add 'quality_score' column if it doesn't exist
        cursor.execute("SHOW COLUMNS FROM prompts LIKE 'quality_score'")
        if not cursor.fetchone():
            print("[MySQL] Migrating database: Adding 'quality_score' column to 'prompts' table...")
            cursor.execute("ALTER TABLE prompts ADD COLUMN quality_score INT DEFAULT NULL")

        # Migration Safety Check: Phase 4 prompt columns
        columns_to_add = [
            ("is_public", "TINYINT(1) DEFAULT 0"),
            ("visibility", "ENUM('private', 'unlisted', 'public') DEFAULT 'private'"),
            ("published_at", "TIMESTAMP NULL"),
            ("share_uuid", "CHAR(12) UNIQUE NULL"),
            ("views", "INT DEFAULT 0"),
            ("like_count", "INT DEFAULT 0"),
            ("fork_count", "INT DEFAULT 0"),
            ("target_model", "VARCHAR(50) DEFAULT NULL"),
            ("optimization_style", "VARCHAR(50) DEFAULT NULL"),
            ("forked_from_prompt_id", "INT DEFAULT NULL"),
            ("moderation_status", "ENUM('pending', 'approved', 'flagged', 'hidden') DEFAULT 'approved'"),
            ("is_featured", "TINYINT(1) DEFAULT 0"),
            ("deleted_at", "TIMESTAMP NULL")
        ]

        for col_name, col_def in columns_to_add:
            cursor.execute(f"SHOW COLUMNS FROM prompts LIKE '{col_name}'")
            if not cursor.fetchone():
                print(f"[MySQL] Migrating database: Adding '{col_name}' column to 'prompts' table...")
                cursor.execute(f"ALTER TABLE prompts ADD COLUMN {col_name} {col_def}")
                if col_name == "forked_from_prompt_id":
                    try:
                        cursor.execute("ALTER TABLE prompts ADD CONSTRAINT fk_forked_from FOREIGN KEY (forked_from_prompt_id) REFERENCES prompts(id) ON DELETE SET NULL")
                    except Exception as fk_err:
                        print(f"[MySQL Migration Warning] Failed to add fk_forked_from constraint: {fk_err}")

        # Create prompt_views Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS prompt_views (
                visitor_id VARCHAR(64) NOT NULL,
                prompt_id INT NOT NULL,
                viewed_date DATE NOT NULL,
                PRIMARY KEY (visitor_id, prompt_id, viewed_date),
                FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create collections Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS collections (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                title VARCHAR(255) NOT NULL,
                description TEXT NULL,
                cover_color VARCHAR(7) DEFAULT '#6c63ff',
                emoji VARCHAR(10) DEFAULT '📁',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create collection_prompts Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS collection_prompts (
                collection_id INT NOT NULL,
                prompt_id INT NOT NULL,
                PRIMARY KEY (collection_id, prompt_id),
                FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
                FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create prompt_likes Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS prompt_likes (
                user_id INT NOT NULL,
                prompt_id INT NOT NULL,
                PRIMARY KEY (user_id, prompt_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create profiles Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INT PRIMARY KEY,
                display_name VARCHAR(100) NULL,
                bio TEXT NULL,
                avatar VARCHAR(255) NULL,
                github VARCHAR(255) NULL,
                website VARCHAR(255) NULL,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create follows Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS follows (
                follower_id INT NOT NULL,
                following_id INT NOT NULL,
                PRIMARY KEY (follower_id, following_id),
                FOREIGN KEY (follower_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (following_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create reports Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id INT AUTO_INCREMENT PRIMARY KEY,
                reporter_id INT NOT NULL,
                prompt_id INT NOT NULL,
                reason ENUM('Spam', 'Abuse', 'Duplicate', 'Copyright', 'Other') NOT NULL,
                comment TEXT NULL,
                status ENUM('pending', 'reviewed', 'dismissed') DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_report (reporter_id, prompt_id),
                FOREIGN KEY (reporter_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create notifications Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                sender_id INT NULL,
                type ENUM('like', 'follow', 'fork', 'system') NOT NULL,
                prompt_id INT NULL,
                is_read TINYINT(1) DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE SET NULL
            ) ENGINE=InnoDB
            """
        )

        # Create Database Search Indexes safely
        indexes_to_create = [
            ("prompts", "idx_prompts_sharing", "share_uuid"),
            ("prompts", "idx_prompts_gallery", "visibility, moderation_status, deleted_at"),
            ("prompts", "idx_prompts_category_model", "category, target_model, optimization_style"),
            ("prompts", "idx_prompts_ranking", "quality_score, published_at"),
            ("users", "idx_users_username", "username")
        ]

        for table_name, index_name, columns in indexes_to_create:
            cursor.execute(
                """
                SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS 
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND INDEX_NAME = %s
                """,
                (config.MYSQL_DATABASE, table_name, index_name)
            )
            if cursor.fetchone()[0] == 0:
                print(f"[MySQL] Migrating database: Adding index '{index_name}' to '{table_name}'...")
                cursor.execute(f"ALTER TABLE {table_name} ADD INDEX {index_name} ({columns})")

        # Create Analytics Events Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics_events (
                id INT AUTO_INCREMENT PRIMARY KEY,
                event_type VARCHAR(50) NOT NULL,
                user_id INT NULL,
                visitor_id VARCHAR(64) NOT NULL,
                category_used VARCHAR(50) NULL,
                template_name VARCHAR(50) NULL,
                event_metadata TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB
            """
        )

        # Migration Safety Check: Add 'event_metadata' column if it does not exist, or modify to TEXT if JSON
        cursor.execute("SHOW COLUMNS FROM analytics_events LIKE 'event_metadata'")
        meta_col = cursor.fetchone()
        if not meta_col:
            print("[MySQL] Migrating database: Adding 'event_metadata' column to 'analytics_events' table...")
            cursor.execute("ALTER TABLE analytics_events ADD COLUMN event_metadata TEXT NULL")
        else:
            col_type = meta_col[1].decode('utf-8') if isinstance(meta_col[1], bytes) else meta_col[1]
            if 'json' in col_type.lower():
                print("[MySQL] Migrating database: Modifying 'event_metadata' column type from JSON to TEXT in 'analytics_events' table...")
                cursor.execute("ALTER TABLE analytics_events MODIFY COLUMN event_metadata TEXT NULL")

        # Create Feedback Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NULL,
                feedback_type VARCHAR(20) NOT NULL,
                rating INT NULL,
                comment TEXT NOT NULL,
                prompt_id INT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE SET NULL
            ) ENGINE=InnoDB
            """
        )

        # Create organizations table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS organizations (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                slug VARCHAR(100) UNIQUE NOT NULL,
                owner_id INT NOT NULL,
                max_monthly_cost DECIMAL(10,2) DEFAULT 100.00,
                max_monthly_tokens INT DEFAULT 5000000,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create organization_members table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS organization_members (
                organization_id INT NOT NULL,
                user_id INT NOT NULL,
                role ENUM('owner', 'admin', 'editor', 'viewer') DEFAULT 'viewer',
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (organization_id, user_id),
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create organization_secrets table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS organization_secrets (
                id INT AUTO_INCREMENT PRIMARY KEY,
                organization_id INT NOT NULL,
                name VARCHAR(100) NOT NULL,
                encrypted_value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_org_secret (organization_id, name),
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create workflows table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS workflows (
                id INT AUTO_INCREMENT PRIMARY KEY,
                organization_id INT NULL,
                user_id INT NOT NULL,
                title VARCHAR(255) NOT NULL,
                description TEXT NULL,
                status ENUM('draft', 'published', 'archived') DEFAULT 'draft',
                sharing ENUM('private', 'organization', 'unlisted', 'public', 'invite') DEFAULT 'private',
                category VARCHAR(50) DEFAULT 'Automation',
                clone_count INT DEFAULT 0,
                max_cost_limit DECIMAL(10,4) DEFAULT 1.0000,
                max_token_limit INT DEFAULT 100000,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                deleted_at TIMESTAMP NULL,
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create workflow_versions table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_versions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                workflow_id INT NOT NULL,
                version_number INT NOT NULL,
                nodes_json LONGTEXT NOT NULL,
                edges_json LONGTEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create workflow_variables table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_variables (
                id INT AUTO_INCREMENT PRIMARY KEY,
                workflow_id INT NOT NULL,
                name VARCHAR(100) NOT NULL,
                default_value TEXT NULL,
                required TINYINT(1) DEFAULT 0,
                description TEXT NULL,
                type ENUM('string', 'number', 'boolean', 'file', 'secret') DEFAULT 'string',
                FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create workflow_schedules table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_schedules (
                id INT AUTO_INCREMENT PRIMARY KEY,
                workflow_id INT NOT NULL,
                user_id INT NOT NULL,
                status ENUM('active', 'inactive') DEFAULT 'inactive',
                cron_expression VARCHAR(100) NOT NULL,
                last_run TIMESTAMP NULL,
                next_run TIMESTAMP NULL,
                FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create workflow_nodes table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_nodes (
                id VARCHAR(128) PRIMARY KEY,
                workflow_id INT NOT NULL,
                title VARCHAR(255) NOT NULL,
                type VARCHAR(50) NOT NULL,
                prompt_template TEXT NULL,
                agent_id INT NULL,
                config_json TEXT NULL,
                x_pos FLOAT DEFAULT 0,
                y_pos FLOAT DEFAULT 0,
                FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create workflow_edges table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_edges (
                id INT AUTO_INCREMENT PRIMARY KEY,
                workflow_id INT NOT NULL,
                source_node_id VARCHAR(128) NOT NULL,
                target_node_id VARCHAR(128) NOT NULL,
                source_handle VARCHAR(50) NULL,
                target_handle VARCHAR(50) NULL,
                FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE,
                FOREIGN KEY (source_node_id) REFERENCES workflow_nodes(id) ON DELETE CASCADE,
                FOREIGN KEY (target_node_id) REFERENCES workflow_nodes(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create agents table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agents (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                organization_id INT NULL,
                name VARCHAR(100) NOT NULL,
                role VARCHAR(100) NOT NULL,
                goals TEXT NULL,
                instructions TEXT NULL,
                preferred_model VARCHAR(50) DEFAULT 'llama3-8b-8192',
                default_style VARCHAR(50) DEFAULT 'detailed',
                tools_json TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                deleted_at TIMESTAMP NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL
            ) ENGINE=InnoDB
            """
        )

        # Create agent_sessions table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_sessions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                agent_id INT NOT NULL,
                user_id INT NOT NULL,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP NULL,
                FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create agent_messages table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_messages (
                id INT AUTO_INCREMENT PRIMARY KEY,
                session_id INT NOT NULL,
                role ENUM('user', 'assistant', 'system') NOT NULL,
                message TEXT NOT NULL,
                tokens INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create agent_memory table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_memory (
                id INT AUTO_INCREMENT PRIMARY KEY,
                agent_id INT NULL,
                organization_id INT NULL,
                user_id INT NULL,
                memory_key VARCHAR(100) NOT NULL,
                memory_value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE,
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create workflow_runs table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_runs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                workflow_id INT NOT NULL,
                user_id INT NOT NULL,
                status ENUM('queued', 'running', 'paused', 'completed', 'failed', 'cancelled') DEFAULT 'queued',
                inputs LONGTEXT NOT NULL,
                outputs LONGTEXT NULL,
                duration_ms INT DEFAULT 0,
                total_tokens INT DEFAULT 0,
                total_cost DECIMAL(10,6) DEFAULT 0.000000,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create workflow_steps table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_steps (
                id INT AUTO_INCREMENT PRIMARY KEY,
                run_id INT NOT NULL,
                node_id VARCHAR(128) NOT NULL,
                status ENUM('pending', 'running', 'completed', 'failed') DEFAULT 'pending',
                input_used LONGTEXT NULL,
                output_generated LONGTEXT NULL,
                tokens_used INT DEFAULT 0,
                cost DECIMAL(10,6) DEFAULT 0.000000,
                error_message TEXT NULL,
                completed_at TIMESTAMP NULL,
                FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create ratings table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ratings (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                workflow_id INT NULL,
                agent_id INT NULL,
                rating_value INT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_user_wf_rating (user_id, workflow_id),
                UNIQUE KEY unique_user_ag_rating (user_id, agent_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE,
                FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Create reviews table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS reviews (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                workflow_id INT NULL,
                agent_id INT NULL,
                comment TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE,
                FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Phase 6: Normalized providers and models tables
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS providers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(50) NOT NULL,
                api_key_secret VARCHAR(255) NULL,
                endpoint VARCHAR(255) NULL,
                enabled TINYINT(1) DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS models (
                id INT AUTO_INCREMENT PRIMARY KEY,
                provider_id INT NOT NULL,
                model_name VARCHAR(100) NOT NULL,
                model_type ENUM('llm', 'embedding') DEFAULT 'llm',
                context_window INT DEFAULT 8192,
                has_vision TINYINT(1) DEFAULT 0,
                has_audio TINYINT(1) DEFAULT 0,
                has_reasoning TINYINT(1) DEFAULT 0,
                enabled TINYINT(1) DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (provider_id) REFERENCES providers(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS model_routers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                organization_id INT NULL,
                task_type VARCHAR(50) NOT NULL,
                preferred_model_id INT NOT NULL,
                fallback_model_id INT NULL,
                max_cost_limit DECIMAL(10,6) DEFAULT 0.050000,
                max_latency_ms INT DEFAULT 10000,
                enabled TINYINT(1) DEFAULT 1,
                FOREIGN KEY (preferred_model_id) REFERENCES models(id) ON DELETE CASCADE,
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL
            ) ENGINE=InnoDB
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS organization_features (
                organization_id INT NOT NULL,
                feature_name VARCHAR(100) NOT NULL,
                enabled TINYINT(1) DEFAULT 1,
                PRIMARY KEY (organization_id, feature_name),
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Centralized Prompt Registry
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS prompt_registry (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                organization_id INT NULL,
                title VARCHAR(255) NOT NULL,
                description TEXT NULL,
                category VARCHAR(50) DEFAULT 'general',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                deleted_at TIMESTAMP NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL
            ) ENGINE=InnoDB
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS prompt_registry_versions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                prompt_id INT NOT NULL,
                version_number INT NOT NULL,
                system_prompt TEXT NULL,
                prompt_template TEXT NOT NULL,
                variables_json TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (prompt_id) REFERENCES prompt_registry(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Knowledge Bases & RAG
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_bases (
                id INT AUTO_INCREMENT PRIMARY KEY,
                organization_id INT NULL,
                user_id INT NOT NULL,
                title VARCHAR(255) NOT NULL,
                description TEXT NULL,
                embedding_model_id INT NULL,
                chunk_size INT DEFAULT 500,
                chunk_overlap INT DEFAULT 50,
                visibility ENUM('private', 'organization', 'public') DEFAULT 'private',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (embedding_model_id) REFERENCES models(id) ON DELETE SET NULL
            ) ENGINE=InnoDB
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_documents (
                id INT AUTO_INCREMENT PRIMARY KEY,
                kb_id INT NOT NULL,
                filename VARCHAR(255) NOT NULL,
                filetype VARCHAR(100) NOT NULL,
                filesize INT NOT NULL,
                checksum VARCHAR(64) NULL,
                upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_jobs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                kb_id INT NOT NULL,
                doc_id INT NULL,
                status ENUM('queued', 'processing', 'completed', 'failed') DEFAULT 'queued',
                progress_pct INT DEFAULT 0,
                error_message TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id INT AUTO_INCREMENT PRIMARY KEY,
                doc_id INT NOT NULL,
                chunk_index INT NOT NULL,
                chunk_text TEXT NOT NULL,
                page_number INT DEFAULT 1,
                token_count INT DEFAULT 0,
                embedding_vector LONGBLOB NOT NULL,
                FOREIGN KEY (doc_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Connectors
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS connectors (
                id INT AUTO_INCREMENT PRIMARY KEY,
                organization_id INT NULL,
                user_id INT NOT NULL,
                type VARCHAR(50) NOT NULL,
                name VARCHAR(100) NOT NULL,
                config_json TEXT NULL,
                status ENUM('active', 'inactive', 'error') DEFAULT 'inactive',
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS connector_jobs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                connector_id INT NOT NULL,
                status ENUM('queued', 'running', 'completed', 'failed') DEFAULT 'queued',
                progress_pct INT DEFAULT 0,
                last_sync TIMESTAMP NULL,
                error_message TEXT NULL,
                FOREIGN KEY (connector_id) REFERENCES connectors(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS uploaded_assets (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                filename VARCHAR(255) NOT NULL,
                filetype VARCHAR(100) NOT NULL,
                filesize INT NOT NULL,
                purpose ENUM('avatar', 'knowledge', 'workflow_temp', 'generated') DEFAULT 'workflow_temp',
                storage_path VARCHAR(512) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # MCP Servers & Tool Registry
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS mcp_servers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) UNIQUE NOT NULL,
                transport ENUM('stdio', 'sse') DEFAULT 'stdio',
                command VARCHAR(255) NULL,
                args_json TEXT NULL,
                url VARCHAR(255) NULL,
                env_json TEXT NULL,
                enabled TINYINT(1) DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_registry (
                id INT AUTO_INCREMENT PRIMARY KEY,
                mcp_server_id INT NULL,
                name VARCHAR(100) UNIQUE NOT NULL,
                description TEXT NULL,
                input_schema TEXT NULL,
                is_active TINYINT(1) DEFAULT 1,
                FOREIGN KEY (mcp_server_id) REFERENCES mcp_servers(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Workflow Run Contexts & Artifacts
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_contexts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                run_id INT NOT NULL,
                variables_json LONGTEXT NULL,
                memory_json LONGTEXT NULL,
                current_node_id VARCHAR(128) NULL,
                shared_state_json LONGTEXT NULL,
                FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS generated_artifacts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                run_id INT NOT NULL,
                node_id VARCHAR(128) NOT NULL,
                filename VARCHAR(255) NOT NULL,
                mime_type VARCHAR(100) NOT NULL,
                file_size INT NOT NULL,
                storage_path VARCHAR(512) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # AI Evaluation & Observability Tracing
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_run_evaluations (
                id INT AUTO_INCREMENT PRIMARY KEY,
                run_id INT NOT NULL,
                node_id VARCHAR(128) NOT NULL,
                evaluator_type VARCHAR(50) NOT NULL,
                latency_ms INT DEFAULT 0,
                token_count INT DEFAULT 0,
                cost DECIMAL(10,6) DEFAULT 0.000000,
                accuracy_score DECIMAL(5,2) NULL,
                hallucination_score DECIMAL(5,2) NULL,
                similarity_score DECIMAL(5,4) NULL,
                status ENUM('pass', 'fail', 'warning') DEFAULT 'pass',
                feedback_comment TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS observability_traces (
                id INT AUTO_INCREMENT PRIMARY KEY,
                run_id INT NOT NULL,
                node_id VARCHAR(128) NOT NULL,
                parent_node_id VARCHAR(128) NULL,
                status VARCHAR(50) NOT NULL,
                input_data LONGTEXT NULL,
                output_data LONGTEXT NULL,
                trace_logs LONGTEXT NULL,
                latency_ms INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )

        # Memory Manager Scopes
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_scopes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NULL,
                organization_id INT NULL,
                scope_type ENUM('short_term', 'conversation', 'workflow', 'knowledge', 'long_term') NOT NULL,
                scope_key VARCHAR(128) NOT NULL,
                memory_key VARCHAR(100) NOT NULL,
                memory_value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL
            ) ENGINE=InnoDB
            """
        )

        # Migration Safety Check: Add 'prompt_id' and 'prompt_version' columns to workflow_nodes table
        cursor.execute("SHOW COLUMNS FROM workflow_nodes LIKE 'prompt_id'")
        if not cursor.fetchone():
            print("[MySQL] Migrating database: Adding 'prompt_id' column to 'workflow_nodes' table...")
            cursor.execute("ALTER TABLE workflow_nodes ADD COLUMN prompt_id INT NULL")
            try:
                cursor.execute("ALTER TABLE workflow_nodes ADD CONSTRAINT fk_nodes_prompt FOREIGN KEY (prompt_id) REFERENCES prompt_registry(id) ON DELETE SET NULL")
            except Exception as fk_err:
                print(f"[MySQL Migration Warning] Failed to add fk_nodes_prompt constraint: {fk_err}")

        cursor.execute("SHOW COLUMNS FROM workflow_nodes LIKE 'prompt_version'")
        if not cursor.fetchone():
            print("[MySQL] Migrating database: Adding 'prompt_version' column to 'workflow_nodes' table...")
            cursor.execute("ALTER TABLE workflow_nodes ADD COLUMN prompt_version INT DEFAULT -1")

        # Retroactive Backfill: Set unique usernames for any users where username is NULL
        cursor.execute("SELECT id, name FROM users WHERE username IS NULL")
        null_users = cursor.fetchall()
        for u_id, u_name in null_users:
            base_username = "".join(c for c in u_name if c.isalnum()).lower()
            if not base_username:
                base_username = f"creator_{u_id}"
            
            username = base_username
            counter = 1
            while True:
                cursor.execute("SELECT COUNT(*) FROM users WHERE username = %s AND id != %s", (username, u_id))
                if cursor.fetchone()[0] == 0:
                    break
                username = f"{base_username}{counter}"
                counter += 1
            
            cursor.execute("UPDATE users SET username = %s WHERE id = %s", (username, u_id))

        # Retroactive Backfill: Profiles for all existing users
        cursor.execute("SELECT id, name FROM users")
        all_users = cursor.fetchall()
        for u_id, u_name in all_users:
            cursor.execute("SELECT COUNT(*) FROM profiles WHERE user_id = %s", (u_id,))
            if cursor.fetchone()[0] == 0:
                cursor.execute("INSERT INTO profiles (user_id, display_name) VALUES (%s, %s)", (u_id, u_name))

        # Retroactive Backfill: Add default display names to profiles where NULL
        cursor.execute("UPDATE profiles p JOIN users u ON p.user_id = u.id SET p.display_name = u.name WHERE p.display_name IS NULL")

        conn.commit()
        
        # Phase 7 Initialization
        import models_phase7
        models_phase7.init_phase7_db()
        
        print("[MySQL] Database tables and migrations verified/created successfully.")
        return True
    except mysql.connector.Error as err:
        print(f"[MySQL Initialization Error] {err}")
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
