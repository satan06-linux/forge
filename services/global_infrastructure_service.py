import logging
import json
import uuid
from datetime import datetime
from services.service_result import ServiceResult
from services.errors import ForgeError, StorageError, NotFoundError

logger = logging.getLogger(__name__)

class GlobalInfrastructureService:
    """
    Phase 8: Global Infrastructure Service.
    Handles multi-region support, worker federation, global load balancing, and active-active abstractions.
    """
    def __init__(self, container):
        self.container = container

    def _get_storage(self):
        storage = self.container.get('storage_provider')
        if not storage:
            raise StorageError("Storage provider not available")
        return storage

    def register_region(self, region_id: str, endpoint: str, is_active: bool = True) -> ServiceResult:
        """Register a new global region for active-active architecture."""
        try:
            storage = self._get_storage()
            data = {
                'region_id': region_id,
                'endpoint': endpoint,
                'is_active': 1 if is_active else 0,
                'last_heartbeat': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            try:
                updated = storage.update(
                    "UPDATE global_regions SET endpoint = %s, is_active = %s, last_heartbeat = %s WHERE region_id = %s", 
                    (endpoint, data['is_active'], data['last_heartbeat'], region_id)
                )
                if updated == 0:
                    storage.insert('global_regions', data)
            except Exception as e:
                logger.warning(f"Error upserting region: {e}")
                return ServiceResult.fail(ForgeError("Storage error during region registration", error_code="STORAGE_ERROR"))
                
            logger.info(f"Registered/updated region '{region_id}' at {endpoint}")
            return ServiceResult.ok(data=data)
            
        except Exception as e:
            logger.error(f"Error registering region: {e}")
            return ServiceResult.fail(ForgeError(str(e), error_code="REGION_REGISTER_ERROR"))

    def federate_worker(self, worker_id: str, region_id: str, capabilities: list) -> ServiceResult:
        """Federate a worker node to a specific region."""
        try:
            storage = self._get_storage()
            
            try:
                region = storage.execute_one("SELECT region_id FROM global_regions WHERE region_id = %s", (region_id,))
            except Exception as e:
                return ServiceResult.fail(ForgeError("Storage error looking up region", error_code="STORAGE_ERROR"))
                
            if not region:
                return ServiceResult.fail(NotFoundError(f"Region '{region_id}' not found"))
                
            data = {
                'worker_id': worker_id,
                'region_id': region_id,
                'capabilities': json.dumps(capabilities),
                'status': 'ONLINE',
                'joined_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            try:
                updated = storage.update(
                    "UPDATE federated_workers SET region_id = %s, capabilities = %s, status = %s WHERE worker_id = %s",
                    (region_id, data['capabilities'], data['status'], worker_id)
                )
                if updated == 0:
                    storage.insert('federated_workers', data)
            except Exception as e:
                return ServiceResult.fail(ForgeError("Storage error federating worker", error_code="STORAGE_ERROR"))
                
            logger.info(f"Federated worker '{worker_id}' to region '{region_id}'")
            return ServiceResult.ok(data=data)
            
        except ForgeError as e:
            return ServiceResult.fail(e)
        except Exception as e:
            logger.error(f"Error federating worker: {e}")
            return ServiceResult.fail(ForgeError(str(e), error_code="WORKER_FEDERATE_ERROR"))

    def route_request_globally(self, request_payload: dict, required_capabilities: list = None) -> ServiceResult:
        """Global load balancing to find the best region and worker for a request."""
        try:
            storage = self._get_storage()
            
            try:
                regions = storage.execute("SELECT region_id, endpoint FROM global_regions WHERE is_active = 1 ORDER BY last_heartbeat DESC")
            except Exception as e:
                return ServiceResult.fail(ForgeError("Storage error fetching regions", error_code="STORAGE_ERROR"))
                
            if not regions:
                return ServiceResult.fail(ForgeError("No active regions available", error_code="NO_ACTIVE_REGIONS", retryable=True))
                
            for region in regions:
                try:
                    workers = storage.execute("SELECT worker_id, capabilities FROM federated_workers WHERE region_id = %s AND status = 'ONLINE'", (region['region_id'],))
                except Exception as e:
                    logger.warning(f"Failed to fetch workers for region {region['region_id']}: {e}")
                    continue
                    
                for worker in workers:
                    worker_caps = json.loads(worker['capabilities'])
                    if required_capabilities:
                        if all(cap in worker_caps for cap in required_capabilities):
                            return ServiceResult.ok(data={
                                'routed_region': region['region_id'],
                                'routed_endpoint': region['endpoint'],
                                'routed_worker': worker['worker_id']
                            })
                    else:
                        return ServiceResult.ok(data={
                            'routed_region': region['region_id'],
                            'routed_endpoint': region['endpoint'],
                            'routed_worker': worker['worker_id']
                        })
                        
            return ServiceResult.fail(ForgeError("No workers available matching required capabilities", error_code="NO_CAPABLE_WORKERS", retryable=True))
            
        except ForgeError as e:
            return ServiceResult.fail(e)
        except Exception as e:
            logger.error(f"Error routing request globally: {e}")
            return ServiceResult.fail(ForgeError(str(e), error_code="GLOBAL_ROUTING_ERROR"))
            
    def synchronize_state(self, source_region_id: str, state_payload: dict) -> ServiceResult:
        """Abstractions for Active-Active architecture state synchronization."""
        try:
            storage = self._get_storage()
            try:
                regions = storage.execute("SELECT region_id FROM global_regions WHERE is_active = 1 AND region_id != %s", (source_region_id,))
            except Exception as e:
                return ServiceResult.fail(ForgeError("Storage error fetching regions for sync", error_code="STORAGE_ERROR"))
            
            sync_tasks = []
            for region in regions:
                task = {
                    'task_id': str(uuid.uuid4()),
                    'source_region': source_region_id,
                    'target_region': region['region_id'],
                    'payload': json.dumps(state_payload),
                    'status': 'PENDING',
                    'created_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                }
                try:
                    storage.insert('region_sync_tasks', task)
                    sync_tasks.append(task['task_id'])
                except Exception as e:
                    logger.warning(f"Failed to queue sync task to region {region['region_id']}: {e}")
                    
            logger.info(f"Queued state synchronization from '{source_region_id}' to {len(sync_tasks)} remote regions")
            return ServiceResult.ok(data={'sync_tasks': sync_tasks, 'target_regions_count': len(sync_tasks)})
            
        except ForgeError as e:
            return ServiceResult.fail(e)
        except Exception as e:
            logger.error(f"Error synchronizing state: {e}")
            return ServiceResult.fail(ForgeError(str(e), error_code="STATE_SYNC_ERROR"))
