from abc import ABC, abstractmethod

class BaseConnector(ABC):
    def __init__(self, connector_id, config_dict):
        self.connector_id = connector_id
        self.config = config_dict

    @abstractmethod
    def sync(self):
        """
        Executes sync operations. Returns a dictionary with:
        {
          "success": bool,
          "synced_count": int,
          "error": str (optional)
        }
        """
        pass

    @abstractmethod
    def fetch_metadata(self):
        """
        Retrieves connection metadata or structures (e.g. list of files or repos).
        """
        pass
