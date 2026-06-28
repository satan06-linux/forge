# ForgePrompt Phase 7 — CircuitBreakerService
import time
from typing import Optional

from services.service_result import ServiceResult
from services.errors import ForgeError, CircuitOpenError, StorageError

class CircuitBreakerService:
    """
    Per-provider state machine (closed, open, half-open) for remote failures.
    """
    
    def __init__(self, container):
        self.storage = container.get('storage_provider')
        self._ensure_table()
        
    def _ensure_table(self):
        with self.storage.get_session() as session:
            session.begin()
            session.execute("""
                CREATE TABLE IF NOT EXISTS circuit_breakers (
                    provider_id VARCHAR(255) PRIMARY KEY,
                    state VARCHAR(50) NOT NULL DEFAULT 'closed',
                    failure_count INT NOT NULL DEFAULT 0,
                    last_failure_at DOUBLE NULL,
                    updated_at DOUBLE NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            session.commit()
            
    def record_success(self, provider_id: str, conn=None) -> ServiceResult:
        """
        Record a success for the provider. Resets circuit to closed.
        """
        session = conn if conn else self.storage.get_session()
        owns_session = conn is None
        try:
            if owns_session:
                session.begin()
            now = time.time()
            session.execute("""
                INSERT INTO circuit_breakers (provider_id, state, failure_count, updated_at)
                VALUES (%s, 'closed', 0, %s)
                ON DUPLICATE KEY UPDATE 
                    state = 'closed',
                    failure_count = 0,
                    updated_at = %s
            """, (provider_id, now, now))
            if owns_session:
                session.commit()
            return ServiceResult.ok()
        except Exception as e:
            if owns_session:
                session.rollback()
            return ServiceResult.fail(StorageError(f"[CircuitBreakerService Error] {str(e)}"))
        finally:
            if owns_session:
                session.close()

    def record_failure(self, provider_id: str, threshold: int = 5, conn=None) -> ServiceResult:
        """
        Record a failure for the provider. If failure count >= threshold, transition to open.
        """
        session = conn if conn else self.storage.get_session()
        owns_session = conn is None
        try:
            if owns_session:
                session.begin()
            now = time.time()
            session.execute("SELECT state, failure_count FROM circuit_breakers WHERE provider_id = %s FOR UPDATE", (provider_id,))
            row = session.fetchone()
            
            if not row:
                failures = 1
                state = 'open' if failures >= threshold else 'closed'
                session.execute("""
                    INSERT INTO circuit_breakers (provider_id, state, failure_count, last_failure_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s)
                """, (provider_id, state, failures, now, now))
            else:
                failures = row['failure_count'] + 1
                state = 'open' if failures >= threshold else row['state']
                session.execute("""
                    UPDATE circuit_breakers 
                    SET failure_count = %s, state = %s, last_failure_at = %s, updated_at = %s
                    WHERE provider_id = %s
                """, (failures, state, now, now, provider_id))
            
            if owns_session:
                session.commit()
            return ServiceResult.ok(data={'state': state, 'failures': failures})
        except Exception as e:
            if owns_session:
                session.rollback()
            return ServiceResult.fail(StorageError(f"[CircuitBreakerService Error] {str(e)}"))
        finally:
            if owns_session:
                session.close()

    def check_circuit(self, provider_id: str, open_timeout_sec: float = 60.0, conn=None) -> ServiceResult:
        """
        Check if the circuit is open. If open and timeout has passed, transition to half-open and allow.
        """
        session = conn if conn else self.storage.get_session()
        owns_session = conn is None
        try:
            if owns_session:
                session.begin()
            
            session.execute("SELECT state, last_failure_at FROM circuit_breakers WHERE provider_id = %s", (provider_id,))
            row = session.fetchone()
            if not row:
                if owns_session:
                    session.commit()
                return ServiceResult.ok(data={'allowed': True})
            
            state = row['state']
            if state == 'closed':
                if owns_session:
                    session.commit()
                return ServiceResult.ok(data={'allowed': True})
                
            if state == 'open':
                last_failure = row['last_failure_at'] or 0
                now = time.time()
                if now - last_failure > open_timeout_sec:
                    # Transition to half-open
                    session.execute("""
                        UPDATE circuit_breakers SET state = 'half-open', updated_at = %s WHERE provider_id = %s
                    """, (now, provider_id))
                    if owns_session:
                        session.commit()
                    return ServiceResult.ok(data={'allowed': True})
                else:
                    if owns_session:
                        session.commit()
                    return ServiceResult.fail(CircuitOpenError(f"Circuit for {provider_id} is OPEN"))
            
            if state == 'half-open':
                # Allow one through (caller must record success/failure which resolves the state)
                if owns_session:
                    session.commit()
                return ServiceResult.ok(data={'allowed': True})

            if owns_session:
                session.commit()
            return ServiceResult.ok(data={'allowed': True})
        except CircuitOpenError as e:
            if owns_session:
                session.rollback()
            return ServiceResult.fail(e)
        except Exception as e:
            if owns_session:
                session.rollback()
            return ServiceResult.fail(StorageError(f"[CircuitBreakerService Error] {str(e)}"))
        finally:
            if owns_session:
                session.close()
