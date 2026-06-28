import queue
import threading

class ExecutionQueue:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ExecutionQueue, cls).__new__(cls)
                cls._instance._queue = queue.Queue()
                cls._instance._executor_callback = None
                cls._instance._worker_thread = None
                cls._instance._started = False
        return cls._instance

    def register_executor(self, callback):
        """
        Registers the function/callback that will perform the actual workflow execution.
        """
        self._executor_callback = callback
        if not self._started:
            self._start_worker()

    def enqueue(self, run_id):
        """
        Enqueues a run ID for execution.
        """
        self._queue.put(run_id)

    def _start_worker(self):
        self._started = True
        self._worker_thread = threading.Thread(target=self._worker_loop, name="ExecutionQueueWorker", daemon=True)
        self._worker_thread.start()

    def _worker_loop(self):
        while True:
            try:
                run_id = self._queue.get()
                if self._executor_callback:
                    try:
                        self._executor_callback(run_id)
                    except Exception as e:
                        print(f"[ExecutionQueue Worker Error] Failed to execute run {run_id}: {e}")
                self._queue.task_done()
            except Exception as e:
                print(f"[ExecutionQueue Worker Loop Error] {e}")

execution_queue = ExecutionQueue()
