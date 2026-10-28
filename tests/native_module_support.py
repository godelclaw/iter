"""Isolated native module calls for application-level oracle comparisons."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys


def value_equal(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return list(left) == list(right) and all(value_equal(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(value_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, float) and left == right == 0:
        return math.copysign(1, left) == math.copysign(1, right)
    return left == right


class NativeModules:
    def __init__(self, root, binary, evidence):
        self.root = Path(root)
        self.binary = Path(binary).resolve(strict=True)
        self.evidence = Path(evidence).absolute()
        self.evidence.mkdir(parents=True, exist_ok=False)
        self.work = self.evidence / "workspace"
        self.work.mkdir()
        self.requests = self.evidence / "requests"
        self.requests.mkdir()
        self.count = 0
        sources = {}
        # Snapshot tracked/staged source only, never workspace data or previous
        # evidence trees. New candidate source files should be staged first.
        for name in subprocess.check_output(["git", "ls-files", "-z"], cwd=self.root).decode().strip("\0").split("\0"):
            target = self.work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.root / name, target)
            sources[name] = hashlib.sha256(target.read_bytes()).hexdigest()
        (self.evidence / "sources.json").write_text(json.dumps(sources, indent=2) + "\n")
        companion = self.binary.with_name(self.binary.name + ".proc-scope")
        (self.evidence / "engine.json").write_text(json.dumps({
            "path": str(self.binary), "sha256": hashlib.sha256(self.binary.read_bytes()).hexdigest(),
            "companion_sha256": hashlib.sha256(companion.read_bytes()).hexdigest() if companion.exists() else None,
            "python_oracle_version": sys.version,
        }, indent=2) + "\n")

    def call(self, module, function, args=None, kwargs=None, env=None, args_json=None, timeout=30):
        self.count += 1
        request = self.requests / f"{self.count:05d}.json"
        result = self.requests / f"{self.count:05d}.result.json"
        arguments = args_json if args_json is not None else json.dumps(args or [])
        json.loads(arguments)
        request.write_text('{"module":%s,"function":%s,"args":%s,"kwargs":%s,'
                           '"terminal":"","terminal_error":"","result":%s}' %
                           (json.dumps(module), json.dumps(function), arguments,
                            json.dumps(kwargs or {}), json.dumps(str(result))))
        environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
                       "HOME": str(self.work), "ITER_IRC_HOST": ""}
        environment.update(env or {})
        command = [str(self.binary), "--lang", "petta", "--no-results", "iter_worker.metta", str(request)]
        process = subprocess.Popen(command, cwd=self.work, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
        raw = result.read_text() if result.exists() else ""
        observation = {"command": command, "returncode": process.returncode,
                       "stdout": stdout, "stderr": stderr, "result": raw}
        request.with_suffix(".observation.json").write_text(json.dumps(observation, indent=2) + "\n")
        assert process.returncode == 0 and raw, observation
        return json.loads(raw), raw
