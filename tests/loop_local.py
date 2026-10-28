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
    (evidence / "source-adaptations.json").write_text(json.dumps(
        {"enable_python_tool": args.python_tools}, indent=2) + "\n")
    script = [("send", {"channel": "terminal", "content": "fixture delivered"}),
              ("shell", {"cmd": "printf shell-proof"}),
              ("metta", {"code": "!(+ 1 2)"}),
              ("missing-tool", {})]
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
            assert "OUTPUT TOKEN LIMIT REACHED" in requests[1]["body"]["messages"][-1]["content"]
            assert any("hello fixture" in str(m.get("content", "")) for m in experience if m["role"] == "user")
            receipts = [m for m in experience if m["role"] == "tool"]
            assert len(receipts) == len(script)
            assert len({m["tool_call_id"] for m in receipts}) == len(script)
            assert receipts[0]["content"].endswith("SUCCESS")
            assert receipts[1]["content"].endswith("SUCCESS, RETURN: shell-proof")
            assert receipts[2]["content"].endswith("true\ntrue\n3\n")
            assert receipts[3]["content"].endswith("Unknown tool: missing-tool")
            if args.python_tools:
                assert "SUCCESS, RETURN: nested-proof" in receipts[4]["content"]
            assert receipts[-1]["content"].endswith("SUCCESS")
            assert not (source / "channels/.runtime/irc").exists()
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
