import sys
import io
import traceback
import multiprocessing
import queue

def _run_in_process(code, inputs, result_queue):
    whitelist = ["math", "json", "datetime", "re"]
    original_import = __import__
    
    def safe_import(name, globals=None, locals=None, fromlist=None, level=0):
        if name in whitelist:
            return original_import(name, globals, locals, fromlist, level)
        raise ImportError(f"Import of module '{name}' is blocked in sandbox.")

    def safe_open(*args, **kwargs):
        raise PermissionError("File operations are not allowed in sandbox.")

    sandbox_globals = {
        "__builtins__": {
            "abs": abs, "all": all, "any": any, "bin": bin, "bool": bool,
            "chr": chr, "dict": dict, "dir": dir, "divmod": divmod, "enumerate": enumerate,
            "filter": filter, "float": float, "format": format, "hash": hash, "hex": hex,
            "id": id, "int": int, "isinstance": isinstance, "issubclass": issubclass,
            "len": len, "list": list, "map": map, "max": max, "min": min,
            "next": next, "oct": oct, "ord": ord, "pow": pow, "print": print,
            "range": range, "repr": repr, "reversed": reversed, "round": round,
            "set": set, "slice": slice, "sorted": sorted, "str": str, "sum": sum,
            "tuple": tuple, "type": type, "zip": zip,
            "__import__": safe_import,
            "open": safe_open
        },
        "inputs": inputs
    }

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    try:
        compiled = compile(code, "<sandbox>", "exec")
        exec(compiled, sandbox_globals)
        
        stdout_val = sys.stdout.getvalue()
        stderr_val = sys.stderr.getvalue()
        
        if len(stdout_val) > 10000:
            stdout_val = stdout_val[:10000] + "\n... [Output truncated due to sandbox limit]"

        outputs = {}
        for k, v in sandbox_globals.items():
            if k not in ("__builtins__", "inputs"):
                try:
                    import json
                    json.dumps(v)
                    outputs[k] = v
                except Exception:
                    pass

        result_queue.put({
            "success": True,
            "stdout": stdout_val,
            "stderr": stderr_val,
            "outputs": outputs
        })
    except Exception as e:
        stderr_val = sys.stderr.getvalue()
        result_queue.put({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "stderr": stderr_val
        })
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

class SandboxService:
    @staticmethod
    def execute_code(code, inputs=None, timeout_sec=3.0):
        if inputs is None:
            inputs = {}
        
        result_queue = multiprocessing.Queue()
        p = multiprocessing.Process(
            target=_run_in_process,
            args=(code, inputs, result_queue)
        )
        p.start()
        
        try:
            res = result_queue.get(timeout=timeout_sec)
            p.join()
            return res
        except queue.Empty:
            p.terminate()
            p.join()
            return {
                "success": False,
                "error": f"Execution timed out after {timeout_sec} seconds.",
                "stdout": "",
                "stderr": ""
            }
        except Exception as e:
            try:
                p.terminate()
            except Exception:
                pass
            p.join()
            return {
                "success": False,
                "error": f"Sandbox execution manager error: {str(e)}",
                "stdout": "",
                "stderr": ""
            }
