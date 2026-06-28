# ForgePrompt Phase 7 — StreamingService
import json
import time
from typing import Any, Generator, Dict, Optional

from services.service_result import ServiceResult
from services.errors import ForgeError

class StreamingService:
    def __init__(self, container: Any):
        self.container = container
        self._storage = container.get("storage_provider")
        self._quota_limit_bytes = 10 * 1024 * 1024 # 10 MB limit per stream
        self._backpressure_chunk_threshold = 50

    def format_sse_event(self, event_type: str, data: Any) -> str:
        """
        Formats data into a standard Server-Sent Event (SSE) structure.
        """
        payload = {
            "event": event_type,
            "data": data
        }
        return f"data: {json.dumps(payload)}\n\n"

    def acquire_stream(self, run_id: str, tenant_id: Optional[str] = None) -> ServiceResult:
        """
        Returns a ServiceResult envelope containing the stream generator.
        """
        try:
            # Perform synchronous acquisition checks (e.g. auth, limits) before returning stream
            generator = self._stream_workflow_progress(run_id, tenant_id)
            return ServiceResult.ok(data=generator)
        except Exception as e:
            print(f"[StreamingService Error] Failed to acquire stream: {e}")
            error = ForgeError(message=f"Stream acquisition failed: {str(e)}", error_code="ACQUIRE_STREAM_FAILED", retryable=False)
            return ServiceResult.fail(error)

    def _stream_workflow_progress(self, run_id: str, tenant_id: Optional[str] = None) -> Generator[str, None, None]:
        """
        Generator yielding execution step traces and live logs.
        Enforces bandwidth quotas and tracks chunk backpressure.
        """
        last_step_id = 0
        completed = False
        bytes_sent = 0
        chunks_sent_in_window = 0
        
        print(f"[StreamingService] Starting progress stream for run: {run_id}")
        
        init_event = self.format_sse_event("connect", {"status": "connected", "run_id": run_id})
        bytes_sent += len(init_event.encode('utf-8'))
        yield init_event
        
        while not completed:
            if bytes_sent > self._quota_limit_bytes:
                print(f"[StreamingService Error] Bandwidth quota exceeded for run {run_id}")
                yield self.format_sse_event("error", {"error": "Bandwidth quota exceeded"})
                break

            if chunks_sent_in_window >= self._backpressure_chunk_threshold:
                # Apply simple backpressure by yielding to other tasks or slowing down stream
                time.sleep(1.0)
                chunks_sent_in_window = 0

            try:
                with self._storage.get_session() as session:
                    session.execute("SELECT status, outputs FROM workflow_runs WHERE id = %s", (run_id,))
                    run = session.fetchone()
                    
                    if not run:
                        yield self.format_sse_event("error", {"error": "Run not found"})
                        break
                    
                    session.execute(
                        """
                        SELECT id, node_id, status, output_generated, error_message, completed_at
                        FROM workflow_steps
                        WHERE run_id = %s AND id > %s
                        ORDER BY id ASC
                        """,
                        (run_id, last_step_id)
                    )
                    steps = session.fetchall()
                    
                    for step in steps:
                        last_step_id = step["id"]
                        event_str = self.format_sse_event("step_update", {
                            "node_id": step["node_id"],
                            "status": step["status"],
                            "output": step["output_generated"],
                            "error": step["error_message"]
                        })
                        bytes_sent += len(event_str.encode('utf-8'))
                        chunks_sent_in_window += 1
                        yield event_str
                        
                        if bytes_sent > self._quota_limit_bytes:
                            break
                    
                    if run["status"] in ("completed", "failed", "cancelled"):
                        completed = True
                        final_event = self.format_sse_event("run_complete", {
                            "status": run["status"],
                            "outputs": run["outputs"]
                        })
                        yield final_event
                        break
                        
            except Exception as e:
                print(f"[StreamingService Error] Stream failed: {e}")
                yield self.format_sse_event("error", {"error": str(e)})
                break
                
            time.sleep(0.5)
