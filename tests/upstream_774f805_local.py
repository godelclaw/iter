#!/usr/bin/env python3
"""End-to-end and unit regression suite for upstream Iter updates (f4064d9..774f805)
ported to metta-native:
  1. Configurable env parameters (MAX_TOKENS=5000 default, INIT_WAIT, TOOL_CHOICE,
     MAX_SKILLS, MAX_CONTEXT_TRANSFORMATIONS, etc.).
  2. Markdown skills indexing (DESCRIPTION first-line grammar, underscore exclusion,
     MAX_SKILLS cap and warning).
  3. MAX_CONTEXT_TRANSFORMATIONS cap (warning and non-execution of omitted modules).
  4. LLM_BACKUP_MODEL refusal & error fallback (including primary-error preservation
     on double failure).
  5. Native structured logging (iter_log.metta) and optional pinned-panel tool
     (tools/_ui.metta -> tools/ui.metta).
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time


def copy_repo(root: Path, workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    files = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=root
    ).decode().strip("\0").split("\0")
    for name in files:
        dest = workspace / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((root / name).read_bytes())


def run_unit_checks(binary: Path, root: Path, evidence: Path) -> None:
    pin_file = evidence / "unit-pin.json"
    script = f"""
!(import! &self "{(root / 'utils.metta').resolve()}")

;; Skill description grammar: DESCRIPTION="(?:[^"\\\\]|\\\\.)*"
!(test (valid-skill-description "DESCRIPTION=\\"Build and test\\"") true)
!(test (valid-skill-description "DESCRIPTION=\\"\\"") true)
!(test (valid-skill-description "DESCRIPTION=\\"Quote \\\\\\"ok\\\\\\" and \\\\\\\\slash\\"") true)
!(test (valid-skill-description "DESCRIPTION=\\"ok\\" trailing") false)
!(test (valid-skill-description "DESCRIPTION=\\"unterminated") false)
!(test (valid-skill-description "DESCRIPTION=\\"ends-escape\\\\\\"") false)
!(test (valid-skill-description "# Heading") false)

;; Refusal detection matching upstream utils.py:is_refusal
!(test (iter-llm:is-refusal (JsonObject ((JsonPair "role" "assistant") (JsonPair "content" "hi"))) "stop") false)
!(test (iter-llm:is-refusal (JsonObject ((JsonPair "role" "assistant") (JsonPair "content" "hi"))) "refusal") true)
!(test (iter-llm:is-refusal (JsonObject ((JsonPair "role" "assistant") (JsonPair "content" "hi"))) "CONTENT_FILTER") true)
!(test (iter-llm:is-refusal (JsonObject ((JsonPair "role" "assistant") (JsonPair "refusal" "policy") (JsonPair "content" "hi"))) "stop") true)
!(test (iter-llm:is-refusal (JsonObject ((JsonPair "role" "assistant"))) "stop") true)
!(test (iter-llm:is-refusal (JsonObject ((JsonPair "role" "assistant") (JsonPair "content" ""))) "stop") true)
!(test (iter-llm:is-refusal (JsonObject ((JsonPair "role" "assistant") (JsonPair "tool_calls" (JsonArray ((JsonObject ((JsonPair "id" "1")))))))) "tool_calls") false)

;; Request tool_choice option
!(test (json:get (json:parse (iter-llm:request-with-options (JsonArray ()) (JsonArray ()) "m" "auto" 5000 "")) "tool_choice") "auto")
!(test (json:get (json:parse (iter-llm:request-with-options (JsonArray ()) (JsonArray ()) "m" "auto" 5000 "")) "max_tokens") 5000)

;; Env helpers
!(test (env-int "ITER_TEST_UNSET_INT" 42) 42)
!(test (env-int "ITER_TEST_SET_INT" 42) 77)
!(test (env-num "ITER_TEST_SET_NUM" 1) 2.5)
!(test (env-bool "ITER_TEST_SET_BOOL_T" "False") true)
!(test (env-bool "ITER_TEST_SET_BOOL_F" "True") false)

;; Atomic pinned panel persistence
!(test (iter-log:pin "Line 1" "My Panel") true)
!(test (json:get (iter-log:read-pin) "title") "My Panel")
!(test (json:get (iter-log:read-pin) "text") "Line 1")
!(test (iter-log:clear-pin) true)
!(test (json:get (iter-log:read-pin) "text") "")
"""
    unit_file = evidence / "unit_checks.metta"
    unit_file.write_text(script)
    env = dict(os.environ)
    env.update({
        "ITER_TEST_SET_INT": "77",
        "ITER_TEST_SET_NUM": "2.5",
        "ITER_TEST_SET_BOOL_T": "True",
        "ITER_TEST_SET_BOOL_F": "False",
        "ITER_PIN_FILE": str(pin_file),
    })
    proc = subprocess.run(
        [str(binary), "--lang", "petta", str(unit_file)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    (evidence / "unit.log").write_text(proc.stdout + proc.stderr)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "❌" not in proc.stdout and "❌" not in proc.stderr, proc.stdout + proc.stderr
    assert proc.stdout.count("✅") == 26, proc.stdout


def run_loop_scenario(
    binary: Path,
    workspace: Path,
    evidence: Path,
    handler_fn,
    extra_env: dict,
    inputs: list,
    target_requests: int,
    deadline: float = 45.0,
):
    requests = []
    peer_errors = []

    class Peer(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                idx = len(requests)
                requests.append(body)
                status, payload = handler_fn(idx, body)
                raw = json.dumps(payload).encode() if isinstance(payload, dict) else str(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except Exception as exc:
                peer_errors.append(repr(exc))
                self.close_connection = True

    peer = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
    thread = threading.Thread(target=peer.serve_forever, daemon=True)
    thread.start()

    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LLM_MODEL": "primary-model",
        "AI_API_KEY": "fixture-only",
        "BASE_URL": f"http://127.0.0.1:{peer.server_port}/v1",
        "ITER_IRC_HOST": "",
        "INIT_WAIT": "0",
        "SLOW_STEP_DELAY": "1",
        "ERROR_RECOVERY_TIME": "0.1",
    }
    for key, name in (("HOME", "home"), ("TMPDIR", "tmp"), ("XDG_CONFIG_HOME", "config")):
        d = evidence / name
        d.mkdir(parents=True, exist_ok=True)
        env[key] = str(d)
    env.update(extra_env)

    child = None
    started = time.monotonic()
    log_path = evidence / "loop.log"
    try:
        with log_path.open("wb") as log:
            child = subprocess.Popen(
                [str(binary), "--lang", "petta", "iter.metta"],
                cwd=workspace,
                env=env,
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            for line in inputs:
                child.stdin.write((line + "\n").encode())
            child.stdin.flush()
            child.stdin.close()
            while len(requests) < target_requests and time.monotonic() - started < deadline:
                assert child.poll() is None, f"iter exited early: {log_path.read_text()}"
                time.sleep(0.05)
            # Brief settle so final tool call / failure log flushes
            time.sleep(0.4)
    finally:
        if child and child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        peer.shutdown()
        peer.server_close()
        thread.join(timeout=5)

    assert not peer_errors, peer_errors
    assert len(requests) >= target_requests, (
        f"expected >= {target_requests} requests, got {len(requests)}\n{log_path.read_text()}"
    )
    return requests, log_path.read_text()


def scenario_skills_limits_config_ui(binary: Path, root: Path, evidence: Path) -> None:
    ws = evidence / "workspace"
    copy_repo(root, ws)

    # Clear existing transformations and install two deterministic probe transforms:
    # a_keep.metta (runs) and b_omit.metta (omitted by MAX_CONTEXT_TRANSFORMATIONS=1).
    xdir = ws / "transformations"
    for p in list(xdir.iterdir()):
        if p.is_file():
            p.unlink()
    (xdir / "a_keep.metta").write_text(
        '(= (description) "Active probe transform.")\n'
        '(= (transform $messages $tools)\n'
        '   (progn (fs:write "xform_a.ran" "yes") ($messages $tools)))\n'
    )
    (xdir / "b_omit.metta").write_text(
        '(= (description) "Omitted probe transform.")\n'
        '(= (transform $messages $tools)\n'
        '   (progn (fs:write "xform_b.ran" "yes") ($messages $tools)))\n'
    )

    # Install skills: 3 eligible (with MAX_SKILLS=2 -> 1 omitted) plus 2 underscore-ignored
    sdir = ws / "skills"
    sdir.mkdir(exist_ok=True)
    (sdir / "01_valid.md").write_text('DESCRIPTION="Use \\"quotes\\" cleanly"\nBody never indexed.\n')
    (sdir / "02_missing.md").write_text("# Missing description header\nBody.\n")
    (sdir / "03_omitted.md").write_text('DESCRIPTION="Should be omitted by cap"\n')
    (sdir / "_ignored.md").write_text('DESCRIPTION="Must be ignored"\n')
    (sdir / "_hidden").mkdir()
    (sdir / "_hidden" / "sub.md").write_text('DESCRIPTION="Hidden subdir ignored"\n')

    # Enable optional tools/_ui.metta as tools/ui.metta
    (ws / "tools" / "ui.metta").write_bytes((ws / "tools" / "_ui.metta").read_bytes())

    def handler(idx, body):
        if idx == 0:
            msg = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "ui-1", "type": "function",
                "function": {"name": "ui", "arguments": json.dumps({"title": "Board", "content": "Pinned OK"})},
            }]}
        elif idx == 1:
            msg = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "ui-2", "type": "function",
                "function": {"name": "ui", "arguments": json.dumps({"title": "Board", "content": ""})},
            }]}
        else:
            msg = {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"nop-{idx}", "type": "function",
                "function": {"name": "nop", "arguments": "{}"},
            }]}
        return 200, {"choices": [{"message": msg, "finish_reason": "tool_calls"}],
                     "usage": {"prompt_tokens": 12, "completion_tokens": 4}}

    requests, log_text = run_loop_scenario(
        binary, ws, evidence, handler,
        extra_env={
            "MAX_SKILLS": "2",
            "MAX_CONTEXT_TRANSFORMATIONS": "1",
            "TOOL_CHOICE": "auto",
        },
        inputs=["hello skills"],
        target_requests=3,
    )

    req0 = requests[0]
    assert req0["max_tokens"] == 5000, req0["max_tokens"]
    assert req0["tool_choice"] == "auto", req0["tool_choice"]

    sys_content = req0["messages"][0]["content"]
    assert "./skills/:\n" in sys_content, sys_content
    assert './skills/01_valid.md: DESCRIPTION="Use \\"quotes\\" cleanly"' in sys_content, sys_content
    assert "./skills/02_missing.md: [DESCRIPTION FIRST LINE MISSING]" in sys_content, sys_content
    assert "03_omitted.md" not in sys_content, sys_content
    assert "_ignored.md" not in sys_content and "_hidden" not in sys_content, sys_content
    assert "Body never indexed." not in sys_content, sys_content
    assert "a_keep: Active probe transform." in sys_content, sys_content
    assert "b_omit" not in sys_content, sys_content

    user_texts = [m["content"] for m in req0["messages"] if m["role"] == "user"]
    assert any("[SKILL LIMIT REACHED: 1 Markdown skills are omitted." in t for t in user_texts), user_texts
    assert any("[CONTEXT TRANSFORMATION LIMIT REACHED: 1 transformations are omitted and will not execute." in t for t in user_texts), user_texts

    assert (ws / "xform_a.ran").exists(), "active transformation did not run"
    assert not (ws / "xform_b.ran").exists(), "omitted transformation must NOT execute"

    # Turn 0 pinned panel, Turn 1 cleared panel -> check tool results in Turn 2 request
    tool_msgs = [m for m in requests[2]["messages"] if m["role"] == "tool"]
    assert any("Terminal pinned panel updated." in m["content"] for m in tool_msgs), tool_msgs
    assert any("Terminal pinned panel cleared." in m["content"] for m in tool_msgs), tool_msgs
    pin_data = json.loads((ws / ".iter-ui-pin.json").read_text())
    assert pin_data["text"] == "" and pin_data["title"] == "PINNED", pin_data
    assert "ITER" in log_text and "LLM" in log_text, log_text


def scenario_backup_model_fallback(binary: Path, root: Path, evidence: Path) -> None:
    ws = evidence / "workspace"
    copy_repo(root, ws)
    # Remove transformations for speed/clarity
    for p in list((ws / "transformations").iterdir()):
        if p.is_file():
            p.unlink()

    def nop_reply(call_id: str):
        return 200, {
            "choices": [{
                "message": {"role": "assistant", "content": None, "tool_calls": [{
                    "id": call_id, "type": "function",
                    "function": {"name": "nop", "arguments": "{}"},
                }]},
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }

    def handler(idx, body):
        # 0: primary refuses (content_filter) -> 1: backup succeeds
        if idx == 0:
            return 200, {"choices": [{"message": {"role": "assistant", "content": "nope"}, "finish_reason": "content_filter"}]}
        if idx == 1:
            return nop_reply("from-backup-refusal")
        # 2: primary HTTP 503 error -> 3: backup succeeds
        if idx == 2:
            return 503, "primary-transient-503"
        if idx == 3:
            return nop_reply("from-backup-error")
        # 4: primary HTTP 500 error -> 5: backup HTTP 502 error -> must surface PRIMARY error
        if idx == 4:
            return 500, "primary-boom-error"
        if idx == 5:
            return 502, "backup-boom-error"
        return nop_reply(f"after-{idx}")

    requests, log_text = run_loop_scenario(
        binary, ws, evidence, handler,
        extra_env={
            "LLM_MODEL": "primary-model",
            "LLM_BACKUP_MODEL": "backup-model",
            "MAX_TOKENS": "1234",
        },
        inputs=["turn 1"],
        target_requests=7,
    )

    models = [r["model"] for r in requests[:7]]
    assert models == [
        "primary-model", "backup-model",
        "primary-model", "backup-model",
        "primary-model", "backup-model",
        "primary-model",
    ], models
    assert all(r["max_tokens"] == 1234 for r in requests[:7])
    assert "BACKUP !" in log_text and "used LLM_BACKUP_MODEL backup-model" in log_text, log_text
    assert "primary-boom-error" in log_text, log_text
    assert "backup-boom-error" not in log_text, f"double failure must preserve primary error:\n{log_text}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    root = Path(__file__).resolve().parents[1]
    evidence = args.evidence.absolute()
    evidence.mkdir(parents=True, exist_ok=False)

    run_unit_checks(binary, root, evidence / "unit" if (evidence / "unit").mkdir() or True else evidence)
    scenario_skills_limits_config_ui(binary, root, evidence / "skills-config-ui" if (evidence / "skills-config-ui").mkdir() or True else evidence)
    scenario_backup_model_fallback(binary, root, evidence / "backup-model" if (evidence / "backup-model").mkdir() or True else evidence)
    print("PASS: upstream 774f805 port (units, skills, limits, config, backup model fallback, native log & ui tool)")


if __name__ == "__main__":
    main()
