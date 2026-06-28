import logging
from typing import Dict, Any, List

from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class InstructionDatasetBuilder:
    """
    AI Dataset Generator service.
    Converts raw/cleaned/labeled documents into instruction pairs (Q&A) 
    ready for LoRA or instruction fine-tuning.
    """

    def __init__(self, llm_service=None):
        # In a real implementation, llm_service would be an instance of an LLM client
        # to generate high-quality instructions and responses.
        self.llm_service = llm_service

    def build_instruction_pair(self, document: Dict[str, Any]) -> ServiceResult:
        """
        Converts a single document into an instruction/response pair (Q&A).
        """
        try:
            text = document.get("text", "")
            if not text:
                return ServiceResult.fail(ForgeError("Document must have a 'text' field to generate instructions."))

            # If an actual LLM service is integrated, we would call it here.
            # E.g., prompt = f"Given this text, generate a relevant question and answer. Text: {text}"
            # Since this is a production skeleton that can work without external dependencies, 
            # we provide a fallback mock generation based on the text structure.
            
            words = text.split()
            if len(words) < 5:
                return ServiceResult.fail(ForgeError("Text is too short to generate a meaningful instruction pair."))

            # Mock logic to generate a question and answer based on the text content
            subject_phrase = " ".join(words[:min(5, len(words))]).strip('.,!?')
            question = f"Can you explain or summarize the concept of '{subject_phrase}' as discussed in the text?"
            
            # For the response, we use the text itself as the ground truth answer.
            answer = text

            instruction_pair = {
                "instruction": question,
                "input": "",  # Input is empty as the context is inherently part of the answer in this mock
                "output": answer,
                "source_metadata": document.get("labels", {})
            }

            # If there's an original chunk index or source index, preserve it
            if "chunk_index" in document:
                instruction_pair["chunk_index"] = document["chunk_index"]
            if "source_index" in document:
                instruction_pair["source_index"] = document["source_index"]

            return ServiceResult.ok(data=instruction_pair)

        except Exception as e:
            logger.error(f"Error building instruction pair: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(f"Failed to build instruction pair: {str(e)}"))

    def build_dataset(self, labeled_dataset: List[Dict[str, Any]], quality_threshold: float = 0.7) -> ServiceResult:
        """
        Processes a dataset of labeled documents and generates an instruction dataset for LoRA fine-tuning.
        Filters out documents that do not meet the minimum quality threshold.
        """
        logger.info(f"Starting instruction dataset generation for {len(labeled_dataset)} documents.")
        try:
            instruction_dataset = []
            failed_count = 0
            filtered_count = 0

            for index, doc in enumerate(labeled_dataset):
                labels = doc.get("labels", {})
                quality_score = labels.get("quality_score", 1.0)
                
                if quality_score < quality_threshold:
                    logger.debug(f"Skipping document at index {index} due to low quality score ({quality_score} < {quality_threshold}).")
                    filtered_count += 1
                    continue

                result = self.build_instruction_pair(doc)
                if result.success:
                    instruction_dataset.append(result.data)
                else:
                    failed_count += 1
                    logger.warning(f"Failed to generate instruction pair for doc index {index}: {result.error}")

            logger.info(
                f"Instruction dataset building completed. "
                f"Generated: {len(instruction_dataset)}, Filtered out: {filtered_count}, Failed: {failed_count}."
            )
            
            return ServiceResult.ok(
                data={
                    "instruction_dataset": instruction_dataset,
                    "total_processed": len(labeled_dataset),
                    "generated_pairs": len(instruction_dataset),
                    "filtered_pairs": filtered_count,
                    "failed_pairs": failed_count
                }
            )

        except Exception as e:
            logger.error(f"Error building instruction dataset: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(f"Instruction dataset building failed: {str(e)}"))
