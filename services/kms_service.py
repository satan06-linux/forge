# ForgePrompt Phase 7 — KmsService
import os
import base64
import logging
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from typing import Optional

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class KmsService:
    """
    DEK/KEK envelope encryption provider.
    Rule 9: DEK encryption at app level; KEK never in DB.
    """
    def __init__(self, container):
        self.container = container
        # KEK should be managed externally. For this environment, we use ENV var or a static fallback.
        env_kek = os.environ.get("FORGE_MASTER_KEK", "0000000000000000000000000000000000000000000000000000000000000000")
        if len(env_kek) == 64:
            self._kek = bytes.fromhex(env_kek)
        else:
            self._kek = env_kek.encode('utf-8')[:32].ljust(32, b'\0')

    def generate_data_key(self, context: Optional[bytes] = None) -> ServiceResult:
        """
        Generates a new Data Encryption Key (DEK).
        Returns a tuple of (plaintext_dek, encrypted_dek)
        """
        try:
            plaintext_dek = AESGCM.generate_key(bit_length=256)
            aesgcm = AESGCM(self._kek)
            nonce = os.urandom(12)
            encrypted_dek_bytes = aesgcm.encrypt(nonce, plaintext_dek, context)
            
            # Pack nonce and ciphertext together
            encrypted_dek = base64.b64encode(nonce + encrypted_dek_bytes).decode('utf-8')
            return ServiceResult.ok(data=(plaintext_dek, encrypted_dek))
        except Exception as e:
            logger.error(f"[KmsService Error] Failed to generate data key: {e}")
            return ServiceResult.fail(ForgeError(f"Failed to generate data key: {e}", error_code="KMS_ERROR"))

    def decrypt_data_key(self, encrypted_dek_str: str, context: Optional[bytes] = None) -> ServiceResult:
        """
        Decrypts an encrypted DEK using the master KEK.
        """
        try:
            encrypted_dek_bytes = base64.b64decode(encrypted_dek_str)
            nonce = encrypted_dek_bytes[:12]
            ciphertext = encrypted_dek_bytes[12:]
            
            aesgcm = AESGCM(self._kek)
            plaintext_dek = aesgcm.decrypt(nonce, ciphertext, context)
            return ServiceResult.ok(data=plaintext_dek)
        except Exception as e:
            logger.error(f"[KmsService Error] Failed to decrypt data key: {e}")
            return ServiceResult.fail(ForgeError(f"Failed to decrypt data key: {e}", error_code="KMS_ERROR"))

    def encrypt_payload(self, plaintext: bytes, dek: bytes, aad: Optional[bytes] = None) -> ServiceResult:
        """
        Encrypts a payload using a given DEK (AES-GCM).
        Returns (ciphertext_b64, iv_b64, auth_tag_b64)
        """
        try:
            aesgcm = AESGCM(dek)
            nonce = os.urandom(12)
            ct_with_tag = aesgcm.encrypt(nonce, plaintext, aad)
            
            # AESGCM appends 16-byte tag at the end
            ciphertext = ct_with_tag[:-16]
            auth_tag = ct_with_tag[-16:]
            
            return ServiceResult.ok(data=(
                base64.b64encode(ciphertext).decode('utf-8'),
                base64.b64encode(nonce).decode('utf-8'),
                base64.b64encode(auth_tag).decode('utf-8')
            ))
        except Exception as e:
            logger.error(f"[KmsService Error] Payload encryption failed: {e}")
            return ServiceResult.fail(ForgeError(f"Payload encryption failed: {e}", error_code="KMS_ERROR"))

    def decrypt_payload(self, ciphertext_b64: str, iv_b64: str, auth_tag_b64: str, dek: bytes, aad: Optional[bytes] = None) -> ServiceResult:
        """
        Decrypts a payload using a given DEK (AES-GCM).
        """
        try:
            ciphertext = base64.b64decode(ciphertext_b64)
            nonce = base64.b64decode(iv_b64)
            auth_tag = base64.b64decode(auth_tag_b64)
            
            aesgcm = AESGCM(dek)
            ct_with_tag = ciphertext + auth_tag
            plaintext = aesgcm.decrypt(nonce, ct_with_tag, aad)
            return ServiceResult.ok(data=plaintext)
        except Exception as e:
            logger.error(f"[KmsService Error] Payload decryption failed: {e}")
            return ServiceResult.fail(ForgeError(f"Payload decryption failed: {e}", error_code="KMS_ERROR"))
