# ForgePrompt Phase 7 — ServiceContainer
import logging

logger = logging.getLogger(__name__)

class ServiceContainer:
    """
    Dependency Injection Container for ForgePrompt services.
    Instantiates and holds singleton instances of all services.
    """
    def __init__(self):
        self._services = {}
        self._bootstrapped = False

    def register(self, name: str, service_instance):
        self._services[name] = service_instance

    def get(self, name: str):
        if name not in self._services:
            logger.warning(f"[ServiceContainer] Service '{name}' not found.")
            return None
        return self._services[name]

    def bootstrap_phase7_foundation(self):
        """Initializes Milestone 7A Foundation services."""
        if self._bootstrapped:
            return

        from services.lock_service import LockService
        from services.cache_service import CacheService
        from services.feature_flag_service import FeatureFlagService
        from services.maintenance_coordinator import MaintenanceCoordinator
        from services.storage_provider import get_storage_provider
        from services.config_service import ConfigService

        # Core Providers & Utils
        self.register('storage_provider', get_storage_provider('mysql'))
        self.register('config_service', ConfigService()) # Static class, but registering for consistency

        # Infrastructure Services
        lock_service = LockService(self)
        self.register('lock_service', lock_service)

        cache_service = CacheService(self)
        self.register('cache_service', cache_service)

        feature_flag_service = FeatureFlagService(self)
        self.register('feature_flag_service', feature_flag_service)

        maint_coord = MaintenanceCoordinator(self)
        self.register('maintenance_coordinator', maint_coord)

        # Start background coordinator
        maint_coord.start()
        
        self._bootstrapped = True
        logger.info("[ServiceContainer] Foundation Phase 7 services bootstrapped.")

    def shutdown(self):
        """Cleanup resources on shutdown."""
        maint_coord = self.get('maintenance_coordinator')
        if maint_coord:
            maint_coord.stop()

# Global singleton
container = ServiceContainer()
