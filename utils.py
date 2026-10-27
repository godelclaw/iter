import inspect
import json
import sys
import importlib.util
from pathlib import Path

# --------------------------------------------------------------------
# 1. Dynamic execution (worker side, started by invoke_dynamic of utils.metta):
# --------------------------------------------------------------------
def dynamic_worker():
    path = Path(sys.argv[2])
    function = sys.argv[3]
    result_path = Path(sys.argv[4])
    payload_path = Path(sys.argv[5])
    try:
        payload = json.loads(payload_path.read_text())
        spec = importlib.util.spec_from_file_location("_dynamic_" + path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if function == "__description__":
            result = str(module.DESCRIPTION)
        elif function == "__tool_metadata__":
            parameters = inspect.signature(module.run).parameters.values()
            result = {"description": str(module.DESCRIPTION), "parameters": [parameter.name for parameter in parameters]}
        else:
            result = getattr(module, function)(*payload.get("args", []), **payload.get("kwargs", {}))
            if function != "transform" and result is not None:
                result = str(result)
        output = {"ok": True, "result": result}
    except BaseException as error:
        output = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    try:
        result_path.write_text(json.dumps(output, ensure_ascii=False))
    except BaseException as error:
        result_path.write_text(json.dumps({"ok": False, "error": f"Result serialization failed: {type(error).__name__}: {error}"}, ensure_ascii=False))

if len(sys.argv) > 1 and sys.argv[1] == "--invoke":
    dynamic_worker()
    sys.exit(0)
