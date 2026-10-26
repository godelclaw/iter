import inspect
import json
import sys
import openai
import importlib.util
import uuid
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

# --------------------------------------------------------------------
# 2. Python API access for utils.metta (imported via pyimport)
# --------------------------------------------------------------------
CLIENT = None

def init_client(base_url, api_key, timeout):
    global CLIENT
    CLIENT = openai.OpenAI(api_key=str(api_key), base_url=str(base_url), timeout=timeout, max_retries=0, default_headers={"X-APC-Tenant": "iter", "x-session-id": str(uuid.uuid4())})
    return True

def exception_text(error):
    return f"{type(error).__name__}: {error}"

def chat_completion(messages, tools, model, max_tokens): #messages and tools as JSON text, returns message_json finish_reason response usage
    response = CLIENT.chat.completions.create(model=str(model), messages=json.loads(str(messages)), tools=json.loads(str(tools)), tool_choice="required", max_tokens=int(max_tokens), extra_body={ "enable_thinking": True})
    choice = response.choices[0]
    return [json.dumps(choice.message.model_dump(exclude_none=True), ensure_ascii=False), str(choice.finish_reason), str(response), str(response.usage)]
