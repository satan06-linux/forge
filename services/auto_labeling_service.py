import logging
import random
from typing import Dict, Any, List

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class AutoLabelingService:
    """
    Service responsible for automatically labeling documents.
    Generates:
    - categories
    - topics
    - difficulty
    - quality scores
    """

    def __init__(self):
        self.categories = [
            "Science", "Technology", "Engineering", 
            "Mathematics", "Humanities", "Arts", "Business"
        ]
        self.topics = [
            "Programming", "Artificial Intelligence", "History", 
            "Physics", "Literature", "Design", "Economics", "Data Science"
        ]

    def label_document(self, document: Dict[str, Any]) -> ServiceResult:
        """
        Labels a single document with category, topic, difficulty, and quality score.
        """
        try:
            text = document.get("text", "")
            if not text:
                return ServiceResult.fail(ForgeError("Document must have a 'text' field to be labeled."))

            # In a production environment, this would call an LLM or specific classifier models.
            # Here we implement a deterministic mock based on the text content to simulate consistent labeling.
            
            # Simple heuristic for difficulty based on text length and unique words
            words = text.split()
            unique_words = set(words)
            if len(words) > 1000 or len(unique_words) > 500:
                difficulty = "Advanced"
            elif len(words) > 200 or len(unique_words) > 100:
                difficulty = "Intermediate"
            else:
                difficulty = "Beginner"

            # Use a deterministic seed based on the text content to ensure consistent "predictions"
            seed = sum(ord(c) for c in text[:200])
            rng = random.Random(seed)
            
            category = rng.choice(self.categories)
            topic = rng.choice(self.topics)
            # Generate a quality score biased towards higher values, between 0.6 and 1.0
            quality_score = round(rng.uniform(0.6, 1.0), 2)

            labeled_doc = document.copy()
            labeled_doc["labels"] = {
                "category": category,
                "topic": topic,
                "difficulty": difficulty,
                "quality_score": quality_score
            }

            return ServiceResult.ok(data=labeled_doc)
            
        except Exception as e:
            logger.error(f"Error labeling document: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(f"Auto-labeling failed: {str(e)}"))

    def label_dataset(self, dataset: List[Dict[str, Any]]) -> ServiceResult:
        """
        Labels an entire dataset of documents.
        """
        logger.info(f"Starting auto-labeling for {len(dataset)} documents.")
        try:
            labeled_dataset = []
            failed_count = 0

            for doc in dataset:
                result = self.label_document(doc)
                if result.success:
                    labeled_dataset.append(result.data)
                else:
                    failed_count += 1
                    logger.warning(f"Failed to label document: {result.error}")

            logger.info(f"Auto-labeling completed. Success: {len(labeled_dataset)}, Failed: {failed_count}.")
            return ServiceResult.ok(
                data={
                    "labeled_documents": labeled_dataset,
                    "total_processed": len(dataset),
                    "successful_labels": len(labeled_dataset),
                    "failed_labels": failed_count
                }
            )

        except Exception as e:
            logger.error(f"Error labeling dataset: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(f"Dataset auto-labeling failed: {str(e)}"))
