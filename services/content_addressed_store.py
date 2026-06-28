# ForgePrompt Phase 7 — ContentAddressedStore
import hashlib
import os
import shutil
import time
import json
import logging
from typing import BinaryIO, Optional, Dict, Any

from services.service_result import ServiceResult
from services.errors import ForgeError, NotFoundError

logger = logging.getLogger(__name__)

class ContentAddressedStore:
    def __init__(self, container, storage_dir: str = "cas_storage"):
        self.container = container
        self.storage_dir = os.path.abspath(storage_dir)
        os.makedirs(self.storage_dir, exist_ok=True)

    def _get_blob_path(self, sha256_hash: str) -> str:
        d1 = sha256_hash[0:2]
        d2 = sha256_hash[2:4]
        target_dir = os.path.join(self.storage_dir, d1, d2)
        os.makedirs(target_dir, exist_ok=True)
        return os.path.join(target_dir, sha256_hash)

    def put_stream(self, stream: BinaryIO, organization_id: int, conn=None) -> ServiceResult:
        """
        Reads from stream, computes SHA256, stores to temporary file,
        then moves to CAS storage. Dedups if already exists.
        """
        start_time = time.time()
        session = conn or self.container.storage_provider.get_session()
        owns_tx = False
        if conn is None:
            session.begin()
            owns_tx = True

        temp_path = None
        try:
            hasher = hashlib.sha256()
            temp_path = os.path.join(self.storage_dir, f"tmp_{time.time()}_{os.getpid()}")
            size = 0
            
            with open(temp_path, 'wb') as temp_file:
                while True:
                    chunk = stream.read(8192)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    temp_file.write(chunk)
                    size += len(chunk)
            
            sha256_hash = hasher.hexdigest()
            blob_path = self._get_blob_path(sha256_hash)
            
            existing = session.execute_one(
                "SELECT hash FROM cas_blobs WHERE hash=%s AND organization_id=%s FOR UPDATE",
                (sha256_hash, organization_id)
            )

            is_new = False
            if not existing:
                if not os.path.exists(blob_path):
                    shutil.move(temp_path, blob_path)
                else:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    
                session.execute(
                    "INSERT INTO cas_blobs (organization_id, hash, size_bytes, created_at) "
                    "VALUES (%s, %s, %s, %s)",
                    (organization_id, sha256_hash, size, time.time())
                )
                
                session.execute(
                    "INSERT INTO lineage_events (entity_type, entity_id, event_type, details_json, created_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    ('cas_blob', 0, 'CREATED', json.dumps({"hash": sha256_hash, "size": size}), time.time())
                )

                event_payload = json.dumps({"hash": sha256_hash, "organization_id": organization_id, "size": size})
                session.execute(
                    "INSERT INTO event_outbox (event_type, payload_json, created_at) VALUES (%s, %s, %s)",
                    ('BlobStored', event_payload, time.time())
                )
                is_new = True
            else:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            if owns_tx:
                session.commit()

            return ServiceResult.ok({"hash": sha256_hash, "size": size, "is_new": is_new}, duration_ms=int((time.time() - start_time) * 1000))
        except Exception as e:
            if owns_tx:
                session.rollback()
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass
            logger.error(f"[ContentAddressedStore Error] {e}")
            return ServiceResult.fail(ForgeError(str(e)))

    def get_stream(self, sha256_hash: str, organization_id: int, conn=None) -> ServiceResult:
        """
        Returns an open file handle to the requested blob if it exists.
        Caller is responsible for closing the stream.
        """
        start_time = time.time()
        session = conn or self.container.storage_provider.get_session()
        
        try:
            existing = session.execute_one(
                "SELECT size_bytes FROM cas_blobs WHERE hash=%s AND organization_id=%s",
                (sha256_hash, organization_id)
            )
            if not existing:
                raise NotFoundError(f"Blob {sha256_hash} not found")

            blob_path = self._get_blob_path(sha256_hash)
            if not os.path.exists(blob_path):
                raise ForgeError(f"Blob file missing for hash {sha256_hash}")

            f = open(blob_path, 'rb')
            return ServiceResult.ok({"stream": f, "size": existing['size_bytes']}, duration_ms=int((time.time() - start_time) * 1000))
        except Exception as e:
            logger.error(f"[ContentAddressedStore Error] {e}")
            return ServiceResult.fail(ForgeError(str(e)))
