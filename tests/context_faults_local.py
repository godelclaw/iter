#!/usr/bin/env python3
"""Compare the source's explicit or swallowed I/O failures and file effects."""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import types

from native_module_support import NativeModules, value_equal


def snapshot(path):
    if not path.exists():
        return None
    mode = path.stat().st_mode & 0o777
    return ["directory", mode] if path.is_dir() else ["file", mode, path.read_bytes().hex()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    runner = NativeModules(root, args.binary, args.evidence)
    rows, skipped = [], []
    for module in ("transcript", "unbounded_log", "history"):
        source = subprocess.check_output(["git", "show", f"59152a727d93641eb63dcb099d1692023ddd0e31:transformations/{module}.py"], cwd=root)
        path = runner.evidence / f"reference-{module}.py"
        path.write_bytes(source)
        spec = importlib.util.spec_from_file_location("ref_" + module, path)
        reference = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reference)
        for kind in ("bad-state-utf8", "log-directory", "log-readonly", "state-unwritable"):
            if os.geteuid() == 0 and kind in ("log-readonly", "state-unwritable"):
                skipped.append([module, kind])
                continue
            base = runner.evidence / module / kind
            paths = []
            for variant in ("reference", "native"):
                directory = base / variant
                directory.mkdir(parents=True)
                log, state = directory / "log", directory / "state"
                if kind == "bad-state-utf8":
                    state.write_bytes(b"\xff")
                elif kind == "log-directory":
                    log.mkdir()
                elif kind == "log-readonly":
                    log.write_bytes(b"old\n")
                    log.chmod(0o444)
                else:
                    parent = directory / "readonly"
                    parent.mkdir(mode=0o555)
                    state = parent / "state"
                paths.append((log, state))
            (rlog, rstate), (nlog, nstate) = paths
            reference.LOG_PATH = str(rlog)
            reference.HISTORY = rlog
            reference.STATE_PATH = str(rstate)
            reference.time = types.SimpleNamespace(strftime=lambda fmt: "STAMP")
            messages, tools = [{"role": "user", "content": "hello"}], []
            try:
                want = {"ok": True, "result": list(reference.transform(copy.deepcopy(messages), tools.copy()))}
            except Exception as error:
                want = {"ok": False, "error": type(error).__name__}
            if module == "history":
                got, raw = runner.call("tests/fixtures/history_helper.metta", "helper", [messages, tools, str(nlog), str(nstate), "STAMP"])
                if got["ok"]:
                    got["result"] = json.loads(got["result"])
            else:
                env = ({"ITER_TRANSCRIPT_PATH": str(nlog), "ITER_TRANSCRIPT_STATE": str(nstate)} if module == "transcript"
                       else {"ITER_LOG_PATH": str(nlog), "ITER_LOG_STATE": str(nstate)})
                got, raw = runner.call(f"transformations/{module}.metta", "transform", [messages, tools], env=env)
            ok = got["ok"] == want["ok"] and (not want["ok"] or value_equal(got["result"], want["result"]))
            ok = ok and snapshot(rlog) == snapshot(nlog) and snapshot(rstate) == snapshot(nstate)
            rows.append({"module": module, "case": kind, "ok": ok, "reference": want, "native": got,
                         "reference_log": snapshot(rlog), "native_log": snapshot(nlog),
                         "reference_state": snapshot(rstate), "native_state": snapshot(nstate)})
            for log, state in paths:
                if log.is_file():
                    log.chmod(0o644)
                if kind == "state-unwritable":
                    state.parent.chmod(0o755)
    failures = [r for r in rows if not r["ok"]]
    (runner.evidence / "qualification.json").write_text(json.dumps({"cases": rows, "failures": failures, "skipped": skipped}, indent=2) + "\n")
    print(f"{len(rows) - len(failures)}/{len(rows)} context I/O failure comparisons")
    for row in failures:
        print(json.dumps(row))
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
