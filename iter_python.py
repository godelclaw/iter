"""Python modules for Iter on CeTTa: tools, channels and transformations written
as .py files, in the format of Patrick's Python Iter. iter_worker.metta runs
them in its own Python through py-call."""
import importlib.util
import inspect
import json
from pathlib import Path
import subprocess
import sys
import tempfile

_cetta = None
_root = Path(__file__).resolve().parent
_terminal_output = []

# Modules are loaded from their files on every call; bytecode caches would only
# litter the agent's module folders.
sys.dont_write_bytecode = True


def _load(path):
    path = Path(path)
    spec = importlib.util.spec_from_file_location("_dynamic_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def invoke(request_path, cetta=None):
    """One call, as Patrick's utils.py --invoke ran it: the request names the
    module, the function, its args and kwargs, and the file the result is
    written to."""
    global _cetta, _terminal_output
    _cetta = cetta
    _terminal_output = []
    request = json.loads(Path(request_path).read_text())
    function = request["function"]
    try:
        module = _load(request["module"])
        if function == "__description__":
            result = str(module.DESCRIPTION)
        elif function == "__tool_metadata__":
            parameters = inspect.signature(module.run).parameters.values()
            result = {"description": str(module.DESCRIPTION), "parameters": [parameter.name for parameter in parameters]}
        else:
            result = getattr(module, function)(*request.get("args", []), **request.get("kwargs", {}))
            if function != "transform" and result is not None:
                result = str(result)
        output = {"ok": True, "result": result}
    except BaseException as error:
        output = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    if _terminal_output:
        output["terminal_output"] = "".join(_terminal_output)
    try:
        text = json.dumps(output, ensure_ascii=False)
    except BaseException as error:
        text = json.dumps({"ok": False, "error": f"Result serialization failed: {type(error).__name__}: {error}"}, ensure_ascii=False)
    Path(request["result"]).write_text(text)
    return True


def native_call(module, function, args=(), kwargs=None):
    """Call a MeTTa module from a Python guest through the same worker protocol."""
    if not _cetta:
        raise RuntimeError("the current worker did not supply a CeTTa executable")
    runtime = _root / ".runtime"
    runtime.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="py-native-", dir=runtime) as directory:
        request_path = Path(directory).resolve() / "request.json"
        result_path = request_path.with_name("result.json")
        request_path.write_text(json.dumps({"module": str(Path(module).resolve()),
            "function": function, "args": args, "kwargs": kwargs or {},
            "result": str(result_path)}))
        child = subprocess.run([_cetta, "--lang", "petta", "--no-results",
            str(_root / "iter_worker.metta"), str(request_path)],
            cwd=_root, stdin=subprocess.DEVNULL, capture_output=True)
        if child.returncode or not result_path.is_file():
            raise RuntimeError(f"MeTTa worker exited with code {child.returncode}")
        output = json.loads(result_path.read_text())
        if function == "run" and (kwargs or {}).get("channel") == "terminal":
            _terminal_output.append(child.stdout.decode("utf-8", "replace"))
        if not output["ok"]:
            raise RuntimeError(output["error"])
        return output["result"]


def send(path, content):
    """A Python channel's send(content), for the send tool: "" when it sent,
    else the exception as Python's tool reported it."""
    try:
        _load(path).send(content)
        return ""
    except BaseException as error:
        return f"{type(error).__name__}: {error}"
