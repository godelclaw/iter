#!/usr/bin/env python3
"""Run the unchanged Iter loop against a scripted loopback model endpoint."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--python-tools", action="store_true")
    parser.add_argument("--history-size", type=int, default=0,
                        help="seed N earlier messages; 16 or more also checks the startup cleanup, 100 or more the trim")
    parser.add_argument("--transcript", type=Path,
                        help="valid UTF-8 fixture copied into the loop workspace")
    parser.add_argument("--alarms", action="store_true",
                        help="seed due and future alarms and check the complete model turn")
    parser.add_argument("--enable-thinking", choices=("true", "false"),
                        help="exercise the explicit provider extension")
    parser.add_argument("--reminder", action="store_true",
                        help="schedule a reminder and observe it on the next model turn")
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    root = Path(__file__).resolve().parents[1]
    evidence = args.evidence.absolute()
    evidence.mkdir(parents=True, exist_ok=False)
    source = evidence / "source"
    source.mkdir()
    files = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().strip("\0").split("\0")
    identities = {}
    for name in files:
        content = (root / name).read_bytes()
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        identities[name] = hashlib.sha256(content).hexdigest()
    (evidence / "sources.json").write_text(json.dumps(identities, indent=2) + "\n")
    if args.python_tools:
        (source / "tools/_python.py").rename(source / "tools/python.py")
    if args.history_size:
        history = [{"role": "user", "content": f"old fixture {number}"}
                   for number in range(args.history_size)]
        if args.history_size >= 16:
            for offset, call_id, body_text in ((15, "seed-old-tool", "O" * 400),
                                               (5, "seed-recent-tool", "R" * 400)):
                pos = args.history_size - offset
                history[pos - 1] = {"role": "assistant", "content": None, "tool_calls": [{
                    "id": call_id, "type": "function", "function": {"name": "shell", "arguments": "{}"}}]}
                history[pos] = {"role": "tool", "tool_call_id": call_id, "content": body_text}
        (source / "experience.json").write_text(json.dumps(history))
    if args.alarms:
        alarms = source / "memory/alarms"
        alarms.mkdir(parents=True)
        (alarms / "0").write_text("terminal\nfixture alarm")
        (alarms / "99999999999").write_text("terminal\nfuture alarm")
    transcript = None
    transcript_identity = None
    if args.transcript:
        data = args.transcript.read_bytes()
        (source / "transcript.txt").write_bytes(data)
        transcript_identity = hashlib.sha256(data).hexdigest()
        with args.transcript.open(encoding="utf-8") as handle:
            transcript = "transcript:\n" + "".join(handle.readlines()[-6:]).rstrip()
    next_transcript = [transcript]
    transcript_checks = []
    (evidence / "source-adaptations.json").write_text(json.dumps(
        {"enable_python_tool": args.python_tools, "seed_history_size": args.history_size,
         "seed_alarms": args.alarms,
         "schedule_reminder": args.reminder,
         "transcript_sha256": transcript_identity}, indent=2) + "\n")
    script = [("send", {"channel": "terminal", "content": "fixture delivered"}),
              ("shell", {"cmd": "printf shell-proof"}),
              ("metta", {"code": "!(+ 1 2)"}),
              ("missing-tool", {})]
    if args.reminder:
        script.insert(0, ("reminder", {"when": "1970-01-01 00:00:00", "message": "fixture scheduled"}))
    if args.python_tools:
        script.append(("python", {"code": "print(send('terminal', 'nested delivered')); print(shell('printf nested-proof'))"}))
    script.append(("nop", {}))
    requests, peer_errors = [], []

    class Peer(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                number = len(requests)
                requests.append({"path": self.path, "body": body,
                    "session": self.headers.get("x-session-id"),
                    "tenant": self.headers.get("X-APC-Tenant")})
                assert self.path == "/v1/chat/completions"
                assert self.headers.get("Authorization") == "Bearer fixture-only"
                if args.enable_thinking is None:
                    assert "enable_thinking" not in body
                else:
                    assert body.get("enable_thinking") is (args.enable_thinking == "true")
                if transcript is not None:
                    first = body["messages"][0]
                    assert first["role"] == "system" and first["content"].endswith(next_transcript[0]), first
                    assert first["content"].count("transcript:\n") == 1, first
                    transcript_checks.append(next_transcript[0])
                    # The writer transformation runs after the tail reader. Its
                    # completed file is therefore the next request's input.
                    with (source / "transcript.txt").open(encoding="utf-8") as handle:
                        next_transcript[0] = "transcript:\n" + "".join(handle.readlines()[-6:]).rstrip()
                if number == 0:
                    message = {"role": "assistant", "content": "retry me"}
                    finish = "length"
                else:
                    name, values = script[min(number - 1, len(script) - 1)]
                    message = {"role": "assistant", "content": None, "tool_calls": [{
                        "id": f"fixture-{number}", "type": "function", "function": {
                            "name": name, "arguments": json.dumps(values)}}]}
                    finish = "tool_calls"
                reply = json.dumps({"choices": [{"message": message, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 2}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)
            except Exception as error:
                peer_errors.append(repr(error))
                self.close_connection = True

    peer = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
    thread = threading.Thread(target=peer.serve_forever, daemon=True)
    thread.start()
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
           "LLM_MODEL": "fixture-model", "AI_API_KEY": "fixture-only",
           "BASE_URL": f"http://127.0.0.1:{peer.server_port}/v1", "ITER_IRC_HOST": ""}
    if args.enable_thinking is not None:
        env["ITER_ENABLE_THINKING"] = args.enable_thinking
    for key, name in (("HOME", "home"), ("TMPDIR", "tmp"), ("XDG_CONFIG_HOME", "config")):
        directory = evidence / name
        directory.mkdir(mode=0o700)
        env[key] = str(directory)
    passed = False
    child = None
    started = time.monotonic()
    try:
        with (evidence / "loop.log").open("wb") as log:
            child = subprocess.Popen([str(binary), "--lang", "petta", "iter.metta"],
                cwd=source, env=env, stdin=subprocess.PIPE, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True)
            child.stdin.write(b"hello fixture\n")
            child.stdin.close()
            experience = None
            while time.monotonic() - started < 75:
                assert child.poll() is None, "loop exited before its tool receipts were saved"
                saved = source / "experience.json"
                if saved.exists():
                    try:
                        experience = json.loads(saved.read_text())
                    except json.JSONDecodeError:
                        experience = None
                    if experience and any(m.get("tool_call_id") == f"fixture-{len(script)}" for m in experience):
                        break
                time.sleep(0.05)
            assert experience is not None, "no saved history"
            assert not peer_errors, peer_errors
            assert len(requests) == len(script) + 1, requests
            assert len({r["session"] for r in requests}) == 1 and requests[0]["session"]
            assert all(r["tenant"] == "iter" and r["body"]["model"] == "fixture-model" for r in requests)
            if args.alarms:
                alarm_messages = [[m for m in request["body"]["messages"]
                                   if isinstance(m.get("content"), str) and "[ALARM] fixture alarm" in m["content"]]
                                  for request in requests]
                assert len(alarm_messages[0]) == 1
                assert sum(map(len, alarm_messages)) == 1, alarm_messages
                assert not (alarms / "0").exists()
                assert (alarms / "99999999999").read_text() == "terminal\nfuture alarm"
            if args.reminder:
                observed = [m for request in requests for m in request["body"]["messages"]
                            if isinstance(m.get("content"), str) and "[ALARM] fixture scheduled" in m["content"]]
                assert len(observed) == 1, observed
                assert "[terminal]" in observed[0]["content"]
            if transcript is not None:
                assert len(transcript_checks) == len(requests)
                if (source / "transformations/transcript.metta").exists():
                    assert "hello fixture" in (source / "transcript.txt").read_text()
                else:
                    assert hashlib.sha256((source / "transcript.txt").read_bytes()).hexdigest() == transcript_identity
            assert any("OUTPUT TOKEN LIMIT REACHED" in str(message.get("content", ""))
                       for message in requests[1]["body"]["messages"])
            assert not any("[RUNTIME ERROR in " in str(message.get("content", ""))
                           for request in requests for message in request["body"]["messages"])
            assert any("hello fixture" in str(m.get("content", "")) for m in experience if m["role"] == "user")
            seeded_tool_ids = {"seed-old-tool", "seed-recent-tool"} if args.history_size >= 16 else set()
            receipts = [m for m in experience
                        if m["role"] == "tool" and m.get("tool_call_id") not in seeded_tool_ids]
            assert len(receipts) == len(script)
            assert len({m["tool_call_id"] for m in receipts}) == len(script)
            offset = int(args.reminder)
            if args.reminder:
                assert "SUCCESS, RETURN: Reminder set for 1970-01-01 00:00:00" in receipts[0]["content"]
            assert receipts[offset]["content"].endswith("SUCCESS")
            assert receipts[offset + 1]["content"].endswith("SUCCESS, RETURN: shell-proof")
            assert receipts[offset + 2]["content"].endswith("true\ntrue\n3\n")
            assert receipts[offset + 3]["content"].endswith("Unknown tool: missing-tool")
            if args.python_tools:
                assert "SUCCESS, RETURN: nested-proof" in receipts[offset + 4]["content"]
            assert receipts[-1]["content"].endswith("SUCCESS")
            assert not (source / "channels/.runtime/irc").exists()
            if args.history_size >= 16:
                first_messages = requests[0]["body"]["messages"]
                old_seed = [m for m in first_messages if m.get("tool_call_id") == "seed-old-tool"]
                recent_seed = [m for m in first_messages if m.get("tool_call_id") == "seed-recent-tool"]
                assert (len(old_seed) == 1
                        and old_seed[0]["content"].endswith(" [TRUNCATED]")
                        and len(old_seed[0]["content"]) < 400), old_seed
                assert len(recent_seed) == 1 and recent_seed[0]["content"] == "R" * 400, recent_seed
            if args.history_size >= 100:
                assert len(experience) <= 100
                assert not any(m.get("content") == "old fixture 0" for m in experience)
            log.flush()
            output = (evidence / "loop.log").read_text()
            assert "fixture delivered" in output
            if args.python_tools:
                assert "nested delivered" in output
            passed = True
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
        (evidence / "requests.json").write_text(json.dumps(requests, indent=2) + "\n")
        (evidence / "outcome.json").write_text(json.dumps({"passed": passed,
            "elapsed_seconds": time.monotonic() - started,
            "engine_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "loop_exit_after_owned_test_stop": child.returncode if child else None,
            "peer_errors": peer_errors}, indent=2) + "\n")
    print("PASS: real loop, native HTTP, retry, terminal, tools and saved history")


if __name__ == "__main__":
    main()
