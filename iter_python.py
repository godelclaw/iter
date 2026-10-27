"""Python modules for Iter on CeTTa: tools, channels and transformations written
as .py files, in the format of Patrick's Python Iter. iter_worker.metta runs
them in its own Python through py-call."""
import importlib.util
import inspect
import json
from pathlib import Path
import sys

# Modules are loaded from their files on every call; bytecode caches would only
# litter the agent's module folders.
sys.dont_write_bytecode = True


def _load(path):
    path = Path(path)
    spec = importlib.util.spec_from_file_location("_dynamic_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def invoke(request_path):
    """One call, as Patrick's utils.py --invoke ran it: the request names the
    module, the function, its args and kwargs, and the file the result is
    written to."""
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
    try:
        text = json.dumps(output, ensure_ascii=False)
    except BaseException as error:
        text = json.dumps({"ok": False, "error": f"Result serialization failed: {type(error).__name__}: {error}"}, ensure_ascii=False)
    Path(request["result"]).write_text(text)
    return True


def send(path, content):
    """A Python channel's send(content), for the send tool: "" when it sent,
    else the exception as Python's tool reported it."""
    try:
        _load(path).send(content)
        return ""
    except BaseException as error:
        return f"{type(error).__name__}: {error}"
