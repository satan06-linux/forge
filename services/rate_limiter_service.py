# ForgePrompt Phase 7 — RateLimiterService
import time
from typing import Optional
from services.service_result import ServiceResult
from services.errors import ForgeError, RateLimitedError, StorageError

class RateLimiterService:
    """
    Distributed token bucket logic using rate_limit_buckets table and adaptive rate limiting.
    """
    def __init__(self, container):
        self.storage = container.get('storage_provider')
        self._ensure_table()
        
    def _ensure_table(self):
        with self.storage.get_session() as session:
            session.begin()
            session.execute("""
                CREATE TABLE IF NOT EXISTS rate_limit_buckets (
                    bucket_key VARCHAR(255) PRIMARY KEY,
                    tokens DOUBLE NOT NULL,
                    capacity DOUBLE NOT NULL,
                    refill_rate DOUBLE NOT NULL,
                    last_refill_at DOUBLE NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            session.commit()
            
    def configure_bucket(self, bucket_key: str, capacity: float, refill_rate: float, conn=None) -> ServiceResult:
        session = conn if conn else self.storage.get_session()
        owns_session = conn is None
        try:
            if owns_session: session.begin()
            now = time.time()
            session.execute("""
                INSERT INTO rate_limit_buckets (bucket_key, tokens, capacity, refill_rate, last_refill_at)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    capacity = %s,
                    refill_rate = %s
            """, (bucket_key, capacity, capacity, refill_rate, now, capacity, refill_rate))
            if owns_session: session.commit()
            return ServiceResult.ok()
        except Exception as e:
            if owns_session: session.rollback()
            return ServiceResult.fail(StorageError(f"[RateLimiterService Error] {str(e)}"))
        finally:
            if owns_session: session.close()
            
    def consume(self, bucket_key: str, tokens: float = 1.0, conn=None) -> ServiceResult:
        session = conn if conn else self.storage.get_session()
        owns_session = conn is None
        try:
            if owns_session: session.begin()
            
            # Select FOR UPDATE to lock the row for atomic token update
            session.execute("SELECT tokens, capacity, refill_rate, last_refill_at FROM rate_limit_buckets WHERE bucket_key = %s FOR UPDATE", (bucket_key,))
            row = session.fetchone()
            
            if not row:
                if owns_session: session.rollback()
                return ServiceResult.fail(ForgeError(f"Bucket {bucket_key} not found", error_code="NOT_FOUND"))
                
            now = time.time()
            elapsed = now - row['last_refill_at']
            refill_amount = elapsed * row['refill_rate']
            
            current_tokens = min(row['capacity'], row['tokens'] + refill_amount)
            
            if current_tokens >= tokens:
                new_tokens = current_tokens - tokens
                session.execute("""
                    UPDATE rate_limit_buckets 
                    SET tokens = %s, last_refill_at = %s 
                    WHERE bucket_key = %s
                """, (new_tokens, now, bucket_key))
                if owns_session: session.commit()
                return ServiceResult.ok(data={"remaining": new_tokens})
            else:
                if owns_session: session.commit() # just commit the lock release, no tokens taken
                return ServiceResult.fail(RateLimitedError(f"Rate limit exceeded for {bucket_key}"))
                
        except RateLimitedError as e:
            if owns_session: session.rollback()
            return ServiceResult.fail(e)
        except Exception as e:
            if owns_session: session.rollback()
            return ServiceResult.fail(StorageError(f"[RateLimiterService Error] {str(e)}"))
        finally:
            if owns_session: session.close()
            
    def adaptive_adjust(self, bucket_key: str, backoff_factor: float = 0.5, conn=None) -> ServiceResult:
        """
        Reduce the refill_rate temporarily due to downstream pressure.
        """
        session = conn if conn else self.storage.get_session()
        owns_session = conn is None
        try:
            if owns_session: session.begin()
            session.execute("""
                UPDATE rate_limit_buckets 
                SET refill_rate = refill_rate * %s 
                WHERE bucket_key = %s
            """, (backoff_factor, bucket_key))
            if owns_session: session.commit()
            return ServiceResult.ok()
        except Exception as e:
            if owns_session: session.rollback()
            return ServiceResult.fail(StorageError(f"[RateLimiterService Error] {str(e)}"))
        finally:
            if owns_session: session.close()
