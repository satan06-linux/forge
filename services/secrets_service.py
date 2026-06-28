# ForgePrompt Phase 7 — SecretsService
import logging
from typing import Optional
from contextlib import nullcontext

from services.service_result import ServiceResult
from services.errors import ForgeError, SecretError

logger = logging.getLogger(__name__)

class SecretsService:
    """
    Versioned + auto-rotation using secrets and secret_rotation_policies tables.
    Rule 9: DEK encryption at app level; KEK never in DB.
    """
    def __init__(self, container):
        self.container = container

    def _get_kms(self):
        return self.container.get('kms_service')

    def store_secret(self, organization_id: int, name: str, value: str, conn=None) -> ServiceResult:
        """
        Encrypts and stores a new version of a secret.
        """
        try:
            kms = self._get_kms()
            if not kms:
                return ServiceResult.fail(SecretError("KMS service not available"))

            aad = f"org_{organization_id}_secret_{name}".encode('utf-8')
            
            # Generate DEK
            dek_res = kms.generate_data_key(context=aad)
            if not dek_res.success:
                return dek_res
            plaintext_dek, encrypted_dek = dek_res.data

            # Encrypt payload
            enc_res = kms.encrypt_payload(value.encode('utf-8'), plaintext_dek, aad=aad)
            if not enc_res.success:
                return enc_res
            ciphertext, iv, auth_tag = enc_res.data

            sp = self.container.get('storage_provider')
            
            ctx = nullcontext(conn) if conn else sp.transaction()
            with ctx as session:
                # Use standard 'conn' interface if provided, otherwise the 'session' context manager
                active_session = conn if conn else session
                
                # Get current version
                curr_row = active_session.execute_one(
                    "SELECT MAX(version) as max_v FROM secrets WHERE organization_id = %s AND name = %s",
                    (organization_id, name)
                ) if not hasattr(active_session, 'execute_one') else active_session.execute_one(
                    "SELECT MAX(version) as max_v FROM secrets WHERE organization_id = %s AND name = %s",
                    (organization_id, name)
                )
                
                if hasattr(active_session, 'execute_one'):
                    curr_row = active_session.execute_one("SELECT MAX(version) as max_v FROM secrets WHERE organization_id = %s AND name = %s", (organization_id, name))
                else:
                    active_session.execute("SELECT MAX(version) as max_v FROM secrets WHERE organization_id = %s AND name = %s", (organization_id, name))
                    curr_row = active_session.fetchone()
                
                version = (curr_row['max_v'] + 1) if curr_row and curr_row['max_v'] else 1

                # Deactivate old versions
                active_session.execute(
                    "UPDATE secrets SET is_active = 0 WHERE organization_id = %s AND name = %s",
                    (organization_id, name)
                )

                # Insert new version
                sql = """
                    INSERT INTO secrets (organization_id, name, encrypted_dek, ciphertext, iv, auth_tag, kms_provider, key_version, version, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                active_session.execute(sql, (
                    organization_id, name, encrypted_dek, ciphertext, iv, auth_tag, 'local', 1, version, 1
                ))
                
                # Update rotation policy last_rotated_at if exists
                active_session.execute("""
                    UPDATE secret_rotation_policies 
                    SET last_rotated_at = CURRENT_TIMESTAMP 
                    WHERE organization_id = %s AND secret_name = %s
                """, (organization_id, name))

                # Check policy for rotation_due_at
                active_session.execute(
                    "SELECT rotation_interval_days FROM secret_rotation_policies WHERE organization_id = %s AND secret_name = %s",
                    (organization_id, name)
                )
                pol = active_session.fetchone() if not hasattr(active_session, 'execute_one') else active_session.execute_one(
                    "SELECT rotation_interval_days FROM secret_rotation_policies WHERE organization_id = %s AND secret_name = %s",
                    (organization_id, name)
                )

                if pol and pol['rotation_interval_days']:
                    active_session.execute("""
                        UPDATE secrets 
                        SET rotation_due_at = DATE_ADD(CURRENT_TIMESTAMP, INTERVAL %s DAY)
                        WHERE organization_id = %s AND name = %s AND version = %s
                    """, (pol['rotation_interval_days'], organization_id, name, version))

            return ServiceResult.ok(data={"version": version})
        except Exception as e:
            logger.error(f"[SecretsService Error] Failed to store secret: {e}")
            return ServiceResult.fail(SecretError(f"Failed to store secret: {str(e)}"))

    def get_secret(self, organization_id: int, name: str, version: Optional[int] = None, conn=None) -> ServiceResult:
        """
        Retrieves and decrypts a secret. If version is None, gets the active version.
        """
        try:
            sp = self.container.get('storage_provider')
            
            if version is None:
                sql = "SELECT * FROM secrets WHERE organization_id = %s AND name = %s AND is_active = 1 ORDER BY version DESC LIMIT 1"
                params = (organization_id, name)
            else:
                sql = "SELECT * FROM secrets WHERE organization_id = %s AND name = %s AND version = %s"
                params = (organization_id, name, version)
            
            if conn:
                conn.execute(sql, params)
                row = conn.fetchone()
            else:
                row = sp.execute_one(sql, params)

            if not row:
                return ServiceResult.fail(ForgeError("Secret not found", error_code="NOT_FOUND"))

            kms = self._get_kms()
            aad = f"org_{organization_id}_secret_{name}".encode('utf-8')
            
            dek_res = kms.decrypt_data_key(row['encrypted_dek'], context=aad)
            if not dek_res.success:
                return dek_res
            dek = dek_res.data
            
            dec_res = kms.decrypt_payload(row['ciphertext'], row['iv'], row['auth_tag'], dek, aad=aad)
            if not dec_res.success:
                return dec_res
                
            return ServiceResult.ok(data=dec_res.data.decode('utf-8'))
        except Exception as e:
            logger.error(f"[SecretsService Error] Failed to get secret: {e}")
            return ServiceResult.fail(SecretError(f"Failed to get secret: {str(e)}"))

    def rotate_secrets(self) -> ServiceResult:
        """
        Scheduled job to auto-rotate or flag secrets past their rotation_due_at.
        """
        try:
            sp = self.container.get('storage_provider')
            with sp.transaction() as session:
                session.execute("""
                    SELECT id, organization_id, name, version 
                    FROM secrets 
                    WHERE is_active = 1 AND rotation_due_at <= CURRENT_TIMESTAMP
                """)
                rows = session.fetchall()
            return ServiceResult.ok(data={"secrets_to_rotate": len(rows), "details": rows})
        except Exception as e:
            logger.error(f"[SecretsService Error] Auto-rotation failed: {e}")
            return ServiceResult.fail(SecretError(f"Auto-rotation failed: {str(e)}"))
