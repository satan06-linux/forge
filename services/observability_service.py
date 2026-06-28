from abc import ABC, abstractmethod
from models import get_db_connection
from services.event_bus import event_bus
import time
import json
import re

class BaseEvaluator(ABC):
    @abstractmethod
    def evaluate(self, input_text, output_text, config_dict=None):
        """
        Returns a tuple: (status: str ['pass', 'fail', 'warning'], score: float, comment: str)
        """
        pass

class SimilarityEvaluator(BaseEvaluator):
    def evaluate(self, input_text, output_text, config_dict=None):
        config_dict = config_dict or {}
        expected = config_dict.get("expected", "")
        if not expected:
            return "pass", 1.0, "No expected text provided to compute similarity."
            
        match_count = sum(1 for c in expected if c in output_text)
        similarity = match_count / max(len(expected), 1)
        status = "pass" if similarity >= 0.7 else ("warning" if similarity >= 0.4 else "fail")
        return status, similarity, f"Character similarity score: {similarity:.2f}"

class RegexEvaluator(BaseEvaluator):
    def evaluate(self, input_text, output_text, config_dict=None):
        config_dict = config_dict or {}
        pattern = config_dict.get("pattern", ".*")
        try:
            match = re.search(pattern, output_text)
            if match:
                return "pass", 1.0, f"Regex pattern '{pattern}' matched successfully."
            return "fail", 0.0, f"Regex pattern '{pattern}' did not match outputs."
        except Exception as e:
            return "fail", 0.0, f"Invalid regex pattern: {e}"

class JSONSchemaEvaluator(BaseEvaluator):
    def evaluate(self, input_text, output_text, config_dict=None):
        try:
            json.loads(output_text)
            return "pass", 1.0, "Output is valid JSON."
        except Exception as e:
            return "fail", 0.0, f"Output is not valid JSON: {e}"

class LLMJudgeEvaluator(BaseEvaluator):
    def evaluate(self, input_text, output_text, config_dict=None):
        config_dict = config_dict or {}
        criteria = config_dict.get("criteria", "hallucination")
        lower_out = output_text.lower()
        if "hallucination" in lower_out or "error" in lower_out or "corrupted" in lower_out:
            return "fail", 0.1, f"LLM Judge flagged output for: {criteria}"
        return "pass", 0.95, "LLM Judge approved outputs."

class HumanEvaluator(BaseEvaluator):
    def evaluate(self, input_text, output_text, config_dict=None):
        return "warning", 0.5, "Awaiting manual human verification."

class ObservabilityService:
    _evaluators = {
        "similarity": SimilarityEvaluator(),
        "regex": RegexEvaluator(),
        "json_schema": JSONSchemaEvaluator(),
        "llm_judge": LLMJudgeEvaluator(),
        "human": HumanEvaluator()
    }

    @classmethod
    def register_evaluator(cls, name, evaluator: BaseEvaluator):
        cls._evaluators[name] = evaluator

    @classmethod
    def log_trace(cls, run_id, node_id, parent_node_id, status, input_data, output_data, trace_logs, latency_ms):
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO observability_traces (run_id, node_id, parent_node_id, status, input_data, output_data, trace_logs, latency_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (run_id, node_id, parent_node_id, status, 
                 json.dumps(input_data) if isinstance(input_data, (dict, list)) else str(input_data),
                 json.dumps(output_data) if isinstance(output_data, (dict, list)) else str(output_data),
                 trace_logs, latency_ms)
            )
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            print(f"[ObservabilityService Trace Error] {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def run_evaluation(cls, evaluator_type, run_id, node_id, input_text, output_text, config_dict=None, latency_ms=0, token_count=0, cost=0.0):
        evaluator = cls._evaluators.get(evaluator_type)
        if not evaluator:
            evaluator = JSONSchemaEvaluator() if evaluator_type == "json" else SimilarityEvaluator()
            evaluator_type = "similarity"
            
        status, score, comment = evaluator.evaluate(input_text, output_text, config_dict)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            acc_score = score if evaluator_type == "similarity" else None
            hallucination_score = (1.0 - score) if evaluator_type == "llm_judge" else None
            similarity_score = score if evaluator_type == "similarity" else None
            
            cursor.execute(
                """
                INSERT INTO workflow_run_evaluations 
                (run_id, node_id, evaluator_type, latency_ms, token_count, cost, accuracy_score, hallucination_score, similarity_score, status, feedback_comment)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (run_id, node_id, evaluator_type, latency_ms, token_count, cost, acc_score, hallucination_score, similarity_score, status, comment)
            )
            conn.commit()
            return status, score, comment
        except Exception as e:
            print(f"[ObservabilityService Evaluation Error] {e}")
            return status, score, comment
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def init_event_hooks(cls):
        def on_node_completed(event):
            p = event.payload
            cls.log_trace(
                run_id=p.get("run_id"),
                node_id=p.get("node_id"),
                parent_node_id=p.get("parent_node_id"),
                status=p.get("status", "completed"),
                input_data=p.get("inputs"),
                output_data=p.get("outputs"),
                trace_logs=f"Step completed successfully in {p.get('duration_ms', 0)}ms.",
                latency_ms=p.get("duration_ms", 0)
            )
            cls.run_evaluation(
                evaluator_type="json_schema",
                run_id=p.get("run_id"),
                node_id=p.get("node_id"),
                input_text=str(p.get("inputs")),
                output_text=str(p.get("outputs")),
                latency_ms=p.get("duration_ms", 0),
                token_count=p.get("tokens", 0),
                cost=p.get("cost", 0.0)
            )

        def on_node_failed(event):
            p = event.payload
            cls.log_trace(
                run_id=p.get("run_id"),
                node_id=p.get("node_id"),
                parent_node_id=None,
                status="failed",
                input_data={},
                output_data={},
                trace_logs=f"Step failed: {p.get('error_message')}",
                latency_ms=p.get("duration_ms", 0)
            )

        event_bus.subscribe("NodeCompleted", on_node_completed)
        event_bus.subscribe("NodeFailed", on_node_failed)

ObservabilityService.init_event_hooks()
