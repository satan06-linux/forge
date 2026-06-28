import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
import uuid

from services.service_result import ServiceResult
from services.errors import ForgeError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

@dataclass
class DatasetMetadata:
    dataset_id: str
    name: str
    description: str
    samples: int
    tokens: int
    language: str
    quality_score: float
    status: str
    version: str
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class DatasetRegistryService:
    """
    Searchable index of datasets, tracking samples, tokens, language, quality score, status, and version.
    """
    def __init__(self):
        # In-memory store for dataset metadata
        self._datasets: Dict[str, DatasetMetadata] = {}

    def register_dataset(
        self,
        name: str,
        description: str,
        language: str,
        samples: int = 0,
        tokens: int = 0,
        quality_score: float = 0.0,
        status: str = "active",
        version: str = "v1",
        tags: Optional[List[str]] = None
    ) -> ServiceResult:
        try:
            if not name:
                raise ValidationError("Dataset name is required.")
            if not language:
                raise ValidationError("Language is required.")
            
            dataset_id = str(uuid.uuid4())
            dataset = DatasetMetadata(
                dataset_id=dataset_id,
                name=name,
                description=description,
                samples=samples,
                tokens=tokens,
                language=language,
                quality_score=quality_score,
                status=status,
                version=version,
                tags=tags or []
            )
            
            self._datasets[dataset_id] = dataset
            logger.info(f"Registered dataset '{name}' with ID {dataset_id}")
            return ServiceResult.ok(data=dataset.to_dict())
        except ForgeError as e:
            logger.error(f"Validation error registering dataset: {e}")
            return ServiceResult.fail(e)
        except Exception as e:
            logger.exception("Unexpected error registering dataset")
            return ServiceResult.fail(ForgeError(str(e)))

    def get_dataset(self, dataset_id: str) -> ServiceResult:
        try:
            dataset = self._datasets.get(dataset_id)
            if not dataset:
                raise NotFoundError(f"Dataset with ID {dataset_id} not found.")
            return ServiceResult.ok(data=dataset.to_dict())
        except ForgeError as e:
            logger.error(f"Error fetching dataset {dataset_id}: {e}")
            return ServiceResult.fail(e)
        except Exception as e:
            logger.exception(f"Unexpected error fetching dataset {dataset_id}")
            return ServiceResult.fail(ForgeError(str(e)))

    def update_dataset(self, dataset_id: str, updates: Dict[str, Any]) -> ServiceResult:
        try:
            dataset = self._datasets.get(dataset_id)
            if not dataset:
                raise NotFoundError(f"Dataset with ID {dataset_id} not found.")

            valid_keys = {
                "name", "description", "samples", "tokens", "language",
                "quality_score", "status", "version", "tags"
            }

            for key, value in updates.items():
                if key not in valid_keys:
                    raise ValidationError(f"Invalid field '{key}' provided for update.")
                setattr(dataset, key, value)
            
            self._datasets[dataset_id] = dataset
            logger.info(f"Updated dataset {dataset_id}")
            return ServiceResult.ok(data=dataset.to_dict())
        except ForgeError as e:
            logger.error(f"Error updating dataset {dataset_id}: {e}")
            return ServiceResult.fail(e)
        except Exception as e:
            logger.exception(f"Unexpected error updating dataset {dataset_id}")
            return ServiceResult.fail(ForgeError(str(e)))
            
    def delete_dataset(self, dataset_id: str) -> ServiceResult:
        try:
            if dataset_id not in self._datasets:
                raise NotFoundError(f"Dataset with ID {dataset_id} not found.")
            del self._datasets[dataset_id]
            logger.info(f"Deleted dataset {dataset_id}")
            return ServiceResult.ok(data={"deleted": True, "dataset_id": dataset_id})
        except ForgeError as e:
            logger.error(f"Error deleting dataset {dataset_id}: {e}")
            return ServiceResult.fail(e)
        except Exception as e:
            logger.exception(f"Unexpected error deleting dataset {dataset_id}")
            return ServiceResult.fail(ForgeError(str(e)))

    def search_datasets(
        self, 
        query: Optional[str] = None, 
        language: Optional[str] = None, 
        min_quality: Optional[float] = None, 
        status: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> ServiceResult:
        try:
            results = []
            for dataset in self._datasets.values():
                if query:
                    q_lower = query.lower()
                    if q_lower not in dataset.name.lower() and q_lower not in dataset.description.lower():
                        continue
                if language and dataset.language != language:
                    continue
                if min_quality is not None and dataset.quality_score < min_quality:
                    continue
                if status and dataset.status != status:
                    continue
                if tags:
                    if not all(tag in dataset.tags for tag in tags):
                        continue
                        
                results.append(dataset.to_dict())
                
            logger.info(f"Search returned {len(results)} results")
            return ServiceResult.ok(data=results)
        except Exception as e:
            logger.exception("Unexpected error searching datasets")
            return ServiceResult.fail(ForgeError(str(e)))
