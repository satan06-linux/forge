import logging
import json
import hashlib
from typing import List, Dict, Any, Union

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class DatasetCleaner:
    """
    Service responsible for cleaning raw datasets.
    Features:
    - Deduplication
    - Invalid JSON detection
    - Toxic content filtering
    - Document chunking
    """

    def __init__(self, chunk_size: int = 500, toxic_keywords: List[str] = None):
        self.chunk_size = chunk_size
        # A basic mock list of toxic keywords for filtering
        self.toxic_keywords = toxic_keywords or ["toxic", "hate", "slur", "profanity"]

    def clean_dataset(self, raw_documents: List[Union[str, Dict[str, Any]]]) -> ServiceResult:
        """
        Cleans a list of raw documents (strings or dicts) and returns chunks.
        """
        logger.info(f"Starting dataset cleaning for {len(raw_documents)} documents.")
        try:
            cleaned_docs = []
            seen_hashes = set()
            
            for index, doc in enumerate(raw_documents):
                # 1. Invalid JSON detection
                parsed_doc = self._parse_json(doc)
                if not parsed_doc:
                    logger.warning(f"Skipping document at index {index} due to invalid JSON.")
                    continue
                
                # Assume document has a 'text' or 'content' field
                text_content = parsed_doc.get("text", parsed_doc.get("content", ""))
                if not text_content:
                    logger.warning(f"Skipping document at index {index} due to missing text/content field.")
                    continue
                
                # 2. Toxic content filtering
                if self._is_toxic(text_content):
                    logger.warning(f"Skipping document at index {index} due to toxic content.")
                    continue
                
                # 3. Deduplication
                doc_hash = self._compute_hash(text_content)
                if doc_hash in seen_hashes:
                    logger.info(f"Skipping document at index {index} due to deduplication.")
                    continue
                seen_hashes.add(doc_hash)
                
                # 4. Document chunking
                chunks = self._chunk_document(text_content)
                for i, chunk in enumerate(chunks):
                    new_doc = parsed_doc.copy()
                    # Standardize output format
                    if "content" in new_doc:
                        del new_doc["content"]
                    new_doc["text"] = chunk
                    new_doc["chunk_index"] = i
                    new_doc["source_index"] = index
                    cleaned_docs.append(new_doc)
                    
            logger.info(f"Dataset cleaning completed. {len(cleaned_docs)} chunks generated.")
            return ServiceResult.ok(
                data={
                    "cleaned_documents": cleaned_docs,
                    "total_processed": len(raw_documents),
                    "total_chunks": len(cleaned_docs),
                    "unique_documents": len(seen_hashes)
                }
            )
            
        except Exception as e:
            logger.error(f"Error cleaning dataset: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(f"Failed to clean dataset: {str(e)}"))

    def _parse_json(self, doc: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Attempt to parse the document as JSON. Returns empty dict if invalid."""
        if isinstance(doc, dict):
            return doc
        try:
            parsed = json.loads(doc)
            if isinstance(parsed, dict):
                return parsed
            return {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def _is_toxic(self, text: str) -> bool:
        """Check if the text contains any of the predefined toxic keywords."""
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in self.toxic_keywords)

    def _compute_hash(self, text: str) -> str:
        """Compute SHA-256 hash of the text for deduplication."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _chunk_document(self, text: str) -> List[str]:
        """Split text into chunks of `chunk_size` words."""
        words = text.split()
        chunks = []
        for i in range(0, len(words), self.chunk_size):
            chunk = " ".join(words[i:i + self.chunk_size])
            chunks.append(chunk)
        return chunks if chunks else [""]
