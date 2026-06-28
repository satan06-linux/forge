# ForgePrompt Phase 7 — AuditService
import json
import hashlib
import logging
from typing import Optional, Dict, Any
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.exceptions import InvalidSignature
from contextlib import nullcontext

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class AuditService:
    """
    Hash-chain + ECDSA signatures writing to audit_logs.
    """
    def __init__(self, container):
        self.container = container
        # For demonstration in Phase 7, generate an in-memory ECDSA key.
        # In production, this key should be loaded securely via KMS or SecretsService.
        self._private_key = ec.generate_private_key(ec.SECP256R1())
        self._signer_key_id = "forge-audit-key-v1"

    def _sign_payload(self, payload: bytes) -> bytes:
        return self._private_key.sign(
            payload,
            ec.ECDSA(hashes.SHA256())
        )

    def log_event(
        self,
        action: str,
        resource_type: str,
        organization_id: Optional[int] = None,
        actor_id: Optional[int] = None,
        resource_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        conn=None
    ) -> ServiceResult:
        """
        Write a new hash-chained and signed audit log entry.
        """
        try:
            sp = self.container.get('storage_provider')
            
            ctx = nullcontext(conn) if conn else sp.transaction()
            
            with ctx as session:
                active_session = conn if conn else session

                # 1. Fetch previous hash
                # Lock the table conceptually or just rely on global order if using transaction isolation.
                # In strict systems, sequence numbers or table locks might be used to avoid race conditions on previous_hash.
                prev_sql = "SELECT entry_hash FROM audit_logs ORDER BY id DESC LIMIT 1 FOR UPDATE"
                
                if hasattr(active_session, 'execute_one'):
                    prev_row = active_session.execute_one(prev_sql)
                else:
                    active_session.execute(prev_sql)
                    prev_row = active_session.fetchone()
                    
                prev_hash = prev_row['entry_hash'] if prev_row and prev_row['entry_hash'] else "0" * 64

                # 2. Build payload string to hash
                meta_json = json.dumps(metadata) if metadata else None
                payload_str = f"{prev_hash}|{organization_id}|{actor_id}|{action}|{resource_type}|{resource_id}|{meta_json}|{ip_address}"
                payload_bytes = payload_str.encode('utf-8')
                
                entry_hash = hashlib.sha256(payload_bytes).hexdigest()
                
                # 3. Sign the entry hash
                signature = self._sign_payload(entry_hash.encode('utf-8'))

                # 4. Insert into DB
                insert_sql = """
                    INSERT INTO audit_logs (
                        organization_id, actor_id, action, resource_type, resource_id, 
                        metadata_json, ip_address, entry_hash, previous_hash, 
                        signature, signer_key_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                params = (
                    organization_id, actor_id, action, resource_type, resource_id,
                    meta_json, ip_address, entry_hash, prev_hash,
                    signature, self._signer_key_id
                )
                
                active_session.execute(insert_sql, params)

            return ServiceResult.ok(data={"entry_hash": entry_hash})

        except Exception as e:
            logger.error(f"[AuditService Error] Failed to log audit event: {e}")
            return ServiceResult.fail(ForgeError(f"Failed to log audit event: {str(e)}", error_code="AUDIT_LOG_FAILED"))

    def verify_chain(self) -> ServiceResult:
        """
        Verify the integrity of the audit log hash chain and signatures.
        """
        try:
            sp = self.container.get('storage_provider')
            rows = sp.execute("SELECT * FROM audit_logs ORDER BY id ASC")
            
            expected_prev = "0" * 64
            public_key = self._private_key.public_key()
            
            for row in rows:
                if row['previous_hash'] != expected_prev:
                    return ServiceResult.fail(ForgeError(f"Hash chain broken at ID {row['id']}", error_code="AUDIT_VERIFY_FAILED"))
                    
                meta_json = row['metadata_json']
                payload_str = f"{row['previous_hash']}|{row['organization_id']}|{row['actor_id']}|{row['action']}|{row['resource_type']}|{row['resource_id']}|{meta_json}|{row['ip_address']}"
                expected_hash = hashlib.sha256(payload_str.encode('utf-8')).hexdigest()
                
                if row['entry_hash'] != expected_hash:
                    return ServiceResult.fail(ForgeError(f"Hash mismatch at ID {row['id']}", error_code="AUDIT_VERIFY_FAILED"))
                    
                try:
                    public_key.verify(
                        row['signature'],
                        expected_hash.encode('utf-8'),
                        ec.ECDSA(hashes.SHA256())
                    )
                except InvalidSignature:
                    return ServiceResult.fail(ForgeError(f"Signature validation failed at ID {row['id']}", error_code="AUDIT_VERIFY_FAILED"))
                    
                expected_prev = row['entry_hash']
                
            return ServiceResult.ok(data={"valid": True, "count": len(rows)})
            
        except Exception as e:
            logger.error(f"[AuditService Error] Failed to verify audit chain: {e}")
            return ServiceResult.fail(ForgeError(f"Failed to verify audit chain: {str(e)}", error_code="AUDIT_VERIFY_FAILED"))
