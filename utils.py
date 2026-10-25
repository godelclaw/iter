import inspect
import json
import os
import sys
import openai
import importlib.util
import signal
import subprocess
import tempfile
import uuid
import shutil
from pathlib import Path

# --------------------------------------------------------------------
# 0. Configuration:
# --------------------------------------------------------------------
# Main loop configuration (MAX_MEMORY_CHARS, MAX_EXPERIENCE_SIZE, ...) lives in iter.metta.
LLM_TIMEOUT = 600
MAX_TOKENS = 2524
MAX_TOOLS = 30
MAX_TOOL_DESCRIPTION_CHARS = 500
DYNAMIC_TIMEOUT = 5
MODEL = os.getenv("LLM_MODEL", "mlx-community/gemma-4-26b-a4b-it-4bit")
BASE_URL = os.getenv("BASE_URL", "http://192.168.64.1:2277/v1")
API_KEY = os.getenv("AI_API_KEY", "dummy")

# When loaded by PeTTa via janus, sys.executable is swipl, not Python:
PYTHON = sys.executable if "python" in Path(sys.executable).name else (shutil.which("python3") or "python3")

# --------------------------------------------------------------------
# 1. Dynamic execution:
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
            description = str(module.DESCRIPTION)
            if len(description) > MAX_TOOL_DESCRIPTION_CHARS:
                description = description[:MAX_TOOL_DESCRIPTION_CHARS] + " [DESCRIPTION TRUNCATED]"
            result = {"description": description, "parameters": [parameter.name for parameter in parameters]}
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

def invoke_dynamic(path, function, *args, **kwargs):
    result_fd, result_file = tempfile.mkstemp(prefix="iter-result-", suffix=".json")
    payload_fd, payload_file = tempfile.mkstemp(prefix="iter-payload-", suffix=".json")
    try:
        os.close(result_fd)
        os.close(payload_fd)
        Path(payload_file).write_text(json.dumps({"args": args, "kwargs": kwargs}, ensure_ascii=False))
        inherit_stdin = Path(path).stem == "terminal" and function == "receive"
        process = subprocess.Popen(
            [PYTHON, str(Path(__file__).resolve()), "--invoke", str(Path(path).resolve()), function, result_file, payload_file],
            stdin=None if inherit_stdin else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=not inherit_stdin,
            close_fds=True
        )
        try:
            process.wait(timeout=DYNAMIC_TIMEOUT)
        except subprocess.TimeoutExpired:
            if inherit_stdin:
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
            return {"ok": False, "error": f"TIMEOUT after {DYNAMIC_TIMEOUT}s"}
        try:
            return json.loads(Path(result_file).read_text())
        except Exception:
            return {"ok": False, "error": f"Dynamic process exited with code {process.returncode} without a valid result"}
    finally:
        for file in (result_file, payload_file):
            try:
                os.unlink(file)
            except FileNotFoundError:
                pass

if len(sys.argv) > 1 and sys.argv[1] == "--invoke":
    dynamic_worker()
    sys.exit(0)

# --------------------------------------------------------------------
# 2. Library for iter.metta (called via py-call)
# --------------------------------------------------------------------
# Experience messages are MeTTa atoms:
#   (user "content") (system "content") (tool "call_id" "content") (assistant "<json>") (raw "<json>")
CLIENT = None

def to_message(atom):
    kind = str(atom[0])
    if kind in ("assistant", "raw"):
        return json.loads(str(atom[1]))
    if kind == "tool":
        return {"role": "tool", "tool_call_id": str(atom[1]), "content": str(atom[2])}
    return {"role": kind, "content": str(atom[1])}

def from_message(message):
    role = message.get("role")
    if role == "tool" and set(message) <= {"role", "tool_call_id", "content"}:
        return ["tool", str(message.get("tool_call_id", "")), str(message.get("content", ""))]
    if role in ("user", "system") and set(message) <= {"role", "content"} and isinstance(message.get("content"), str):
        return [role, message["content"]]
    return ["assistant" if role == "assistant" else "raw", json.dumps(message, ensure_ascii=False)]

def init_client():
    global CLIENT
    CLIENT = openai.OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=LLM_TIMEOUT, max_retries=0, default_headers={"X-APC-Tenant": "iter", "x-session-id": str(uuid.uuid4())})
    return True

def exception_text(error):
    return f"{type(error).__name__}: {error}"

def load_experience():
    try:
        with open("experience.json", "r", encoding="utf-8") as file:
            return [from_message(message) for message in json.load(file)]
    except FileNotFoundError:
        return []

def save_experience(experience):
    with open("experience.tmp", "w", encoding="utf-8") as file:
        json.dump([to_message(atom) for atom in experience], file, ensure_ascii=False, indent=2)
    os.replace("experience.tmp", "experience.json")
    return True

def read_prompt():
    return open("prompt.txt", encoding="utf-8", errors="replace").read().strip()

def memory_files():
    paths = [path for path in sorted(Path("memory").rglob("*")) if path.is_file() and not any(part.startswith("_") for part in path.relative_to("memory").parts)]
    return [[str(path), path.read_text(encoding="utf-8", errors="replace").strip()] for path in paths]

def channel_paths():
    return [str(path) for path in sorted(Path("channels").glob("*.py")) if not path.name.startswith("_")]

def receive_channel(path):
    path = Path(str(path))
    try:
        result = invoke_dynamic(path, "receive")
        if not result["ok"]:
            raise RuntimeError(result["error"])
        event = result["result"]
        return "[" + path.stem + "] " + str(event) if event else ""
    except Exception as error:
        return f"[CHANNEL ERROR in {path}: {type(error).__name__}: {error}. Repair {path} if needed.]"

def load_tools(): #returns ((name path description (parameters)) ...) omitted_tools errors
    inops = []
    errors = []
    paths = [path for path in sorted(Path("tools").glob("*.py")) if not path.name.startswith("_")]
    for path in paths[:MAX_TOOLS]:
        try:
            result = invoke_dynamic(path, "__tool_metadata__")
            if not result["ok"]:
                raise RuntimeError(result["error"])
            metadata = result["result"]
            inops.append([path.stem, str(path), metadata["description"], metadata["parameters"]])
        except Exception as error:
            errors.append(f"[TOOL ERROR in {path}: {type(error).__name__}: {error}. Repair {path} if needed.]")
    return [inops, len(paths) - MAX_TOOLS, "\n".join(errors)]

def native_tools(inops): #returns the OpenAI tool schemas as JSON text
    tools = []
    for name, path, description, parameters in inops:
        parameters = [str(parameter) for parameter in parameters]
        tools.append({"type": "function", "function": {"name": str(name), "description": str(description), "parameters": {"type": "object", "properties": { parameter: { "type": "string" } for parameter in parameters }, "required": parameters, "additionalProperties": False}}})
    return json.dumps(tools, ensure_ascii=False)

def load_transformation_descriptions():
    entries = []
    for path in sorted(Path("transformations").glob("*.py")):
        if path.name.startswith("_"):
            continue
        result = invoke_dynamic(path, "__description__")
        if result["ok"]:
            entries.append(f"{path.stem}: {result['result']}")
        else:
            entries.append(f"{path.stem}: [DESCRIPTION MISSING]")
    return "\n".join(entries)

def apply_transformation(messages, tools): #messages as atoms, tools as JSON text
    messages = [to_message(atom) for atom in messages]
    tools = json.loads(str(tools))
    errors = []
    paths = sorted(path for path in Path("transformations").glob("*.py") if not path.name.startswith("_"))
    for path in paths:
        try:
            result = invoke_dynamic(path, "transform", messages, tools)
            if not result["ok"]:
                raise RuntimeError(result["error"])
            messages, tools = result["result"]
        except Exception as error:
            errors.append(f"[RUNTIME ERROR in {path}: {type(error).__name__}: {error}. Repair {path} if needed.]")
    return [[from_message(message) for message in messages], json.dumps(tools, ensure_ascii=False), "\n".join(errors)]

def llm(messages, tools): #returns message_json finish_reason response usage
    response = CLIENT.chat.completions.create(model=MODEL, messages=[to_message(atom) for atom in messages], tools=json.loads(str(tools)), tool_choice="required", max_tokens=MAX_TOKENS, extra_body={ "enable_thinking": True})
    choice = response.choices[0]
    return [json.dumps(choice.message.model_dump(exclude_none=True), ensure_ascii=False), str(choice.finish_reason), str(response), str(response.usage)]

def message_content(message):
    return json.loads(str(message)).get("content") or ""

def message_content_repr(message):
    return repr(json.loads(str(message)).get("content"))

def set_message_content(message, content):
    message = json.loads(str(message))
    message["content"] = str(content)
    return json.dumps(message, ensure_ascii=False)

def message_tool_calls(message): #returns ((id name arguments) ...)
    calls = json.loads(str(message)).get("tool_calls") or []
    return [[call["id"], call["function"]["name"], call["function"]["arguments"]] for call in calls]

def limit_tool_calls(message, limit):
    message = json.loads(str(message))
    if message.get("tool_calls"):
        message["tool_calls"] = message["tool_calls"][:int(limit)]
    return json.dumps(message, ensure_ascii=False)

def strip_reasoning(message):
    message = json.loads(str(message))
    for key in ("reasoning", "reasoning_details", "reasoning_content"):
        message.pop(key, None)
    return json.dumps(message, ensure_ascii=False)

def parse_tool_arguments(arguments): #returns status(ok/notdict/invalid) printable_arguments error
    try:
        parsed = json.loads(str(arguments))
    except json.JSONDecodeError as error:
        return ["invalid", str(arguments), f"Invalid tool arguments from model: {error}"]
    return ["ok" if isinstance(parsed, dict) else "notdict", str(parsed), ""]

def invoke_tool(path, arguments):
    try:
        result = invoke_dynamic(Path(str(path)), "run", **json.loads(str(arguments)))
        ret = result["result"] if result["ok"] else f"Tool execution failed: {result['error']}"
    except Exception as error:
        ret = f"Tool execution failed: {type(error).__name__}: {error}"
    return str(ret)
