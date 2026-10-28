#!/usr/bin/env python3
"""Replay reliability updates against the pinned Python source, offline."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

from native_module_support import NativeModules, value_equal


def pair(text="SUCCESS", name="shell", identity="c1"):
    return [{"role": "assistant", "tool_calls": [{"id": identity, "function": {"name": name}}]},
            {"role": "tool", "tool_call_id": identity, "content": text}]


def trials():
    texts = ["SUCCESS", "error", "ERROR", "errors", "failures", "failed", "pre_error", "error_post",
             "erroré", "éerror", "error²", "²error", "error\u0301", "\u0301error", "(Error)",
             "traceBACK", "EXCEPTION", "not found", "not  found", "not\tfound", "invalid", "İnvalıd",
             "timed out", "TIMED OUT", "TIMEOUT", "timeout", "errorerror", "éerror error", "error_error",
             "\x00error\x00", "a\nerror\nb", "faİled", "x failedé failed", "not foundnot found",
             "exceptional", "tracebacks", "Timed Outage", "TIMEOUTS", "errors TIMEOUT"]
    for index, text in enumerate(texts):
        yield f"words-{index}", None, [pair(text)]
    yield "empty", None, [[], [], [{"role": "tool", "tool_call_id": "c1", "content": "ERROR"}]]
    yield "replay", None, [pair(), pair(), pair("failed"), [], pair()]
    yield "two-tools", None, [pair() + pair("error", "send", "c2"), pair("ok", "send", "c3")]
    assistant = pair()[0]
    yield "duplicate-assistants", None, [[assistant, assistant, pair()[1]]]
    assistant_twice = copy.deepcopy(assistant)
    assistant_twice["tool_calls"] *= 2
    yield "duplicate-calls", None, [[assistant_twice, pair()[1]]]
    yield "future-assistant", None, [[pair()[1], pair()[0]]]
    for index, content in enumerate(("", None, False, 0, [], {}, 5, [1], {"a": 1})):
        yield f"content-{index}", None, [pair(content)]
    for index, state in enumerate(("{", "[]", "null", '{"shell":null}', '{"shell":{}}',
                                  '{"shell":{"calls":"1"}}', '{"shell":{"calls":1}}',
                                  '{"shell":{"calls":1,"f":0.25,"c":0.3333}}',
                                  '{"shell":{"calls":1,"f":0.25,"c":1.0}}',
                                  '{"shell":{"calls":1,"f":0.25,"c":2.0}}',
                                  '{"shell":{"calls":true,"f":true,"c":false}}',
                                  '{"shell":{"calls":1,"f":0.25,"c":-1.0}}',
                                  '{"shell":{"calls":1,"f":0.25,"c":0.5,"successes":"bad"}}',
                                  '{"shell":{"calls":0,"f":0.1,"c":0.1,"extra":"keep"}}',
                                  '{"shell":{"calls":0,"calls":3,"f":0.25,"c":0.75}}')):
        yield f"state-{index}", state.encode(), [pair()]
    yield "utf8", b"\xff", [pair()]
    for kind in ("NaN", "Infinity", "-Infinity"):
        yield "nonfinite-" + kind, ('{"shell":{"calls":1,"f":' + kind + ',"c":0.5}}').encode(), [pair()]
    yield "long-revision", None, [pair("ok" if i % 3 else "error") for i in range(30)]


def snapshot(path):
    if not path.exists():
        return None
    if path.is_dir():
        return ["directory"]
    return path.read_bytes().hex()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    runner = NativeModules(root, args.binary, args.evidence)
    source = subprocess.check_output(["git", "show", "59152a727d93641eb63dcb099d1692023ddd0e31:transformations/tool_reliability_tracker.py"], cwd=root)
    oracle = runner.evidence / "reference.py"
    oracle.write_bytes(source)
    spec = importlib.util.spec_from_file_location("reliability_reference", oracle)
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    tool_source = subprocess.check_output(["git", "show", "59152a727d93641eb63dcb099d1692023ddd0e31:tools/tool_reliability.py"], cwd=root)
    tool_path = runner.evidence / "reference-tool.py"
    tool_path.write_bytes(tool_source)
    tool_spec = importlib.util.spec_from_file_location("reliability_tool_reference", tool_path)
    tool_reference = importlib.util.module_from_spec(tool_spec)
    tool_spec.loader.exec_module(tool_reference)
    for module in ("transformations/tool_reliability_tracker.metta", "tools/tool_reliability.metta"):
        loaded, _ = runner.call(module, "__description__")
        assert loaded["ok"], loaded
    rows = []

    def run_case(label, seed, inputs, fault=None):
        base = runner.evidence / label
        rdir, ndir = base / "reference", base / "native"
        rfile, nfile = rdir / "tool_reliability.json", ndir / "tool_reliability.json"
        for directory, path in ((rdir, rfile), (ndir, nfile)):
            directory.mkdir(parents=True)
            if seed is not None:
                path.write_bytes(seed)
            if fault == "directory":
                path.mkdir()
            elif fault == "readonly":
                path.write_text("{}")
                path.chmod(0o444)
            elif fault == "mkdir":
                directory.rmdir()
                directory.write_text("not a directory")
        reference.RUNTIME_DIR, reference.RELIABILITY_FILE = rdir, rfile
        tool_reference.RELIABILITY_FILE = str(rfile)
        for step, messages in enumerate(inputs):
            tools = [{"preserved": [None, True, -0.0]}]
            try:
                expected = {"ok": True, "result": list(reference.transform(copy.deepcopy(messages), copy.deepcopy(tools)))}
            except Exception as error:
                expected = {"ok": False, "error": type(error).__name__}
            actual, raw = runner.call("transformations/tool_reliability_tracker.metta", "transform", [messages, tools],
                                      env={"ITER_RELIABILITY_DIR": str(ndir)})
            ok = actual["ok"] == expected["ok"] and (not expected["ok"] or value_equal(actual["result"], expected["result"]))
            ok = ok and snapshot(rfile) == snapshot(nfile)
            rows.append({"case": label, "step": step, "ok": ok, "reference": expected, "native": actual,
                         "reference_state": snapshot(rfile), "native_state": snapshot(nfile)})
            try:
                report_want = {"ok": True, "result": tool_reference.run()}
            except Exception as error:
                report_want = {"ok": False, "error": type(error).__name__}
            report_got, _ = runner.call("tools/tool_reliability.metta", "run", env={"ITER_RELIABILITY_DIR": str(ndir)})
            same_report = report_got["ok"] == report_want["ok"] and (not report_want["ok"] or report_got["result"] == report_want["result"])
            rows.append({"case": label + "-report", "step": step, "ok": same_report, "reference": report_want, "native": report_got})
        if fault == "readonly":
            rfile.chmod(0o644)
            nfile.chmod(0o644)

    for label, seed, inputs in trials():
        run_case(label, seed, inputs)
    run_case("file-directory", None, [pair()], "directory")
    run_case("mkdir-failure", None, [pair()], "mkdir")
    skipped = []
    if os.geteuid() != 0:
        run_case("write-denied", None, [pair()], "readonly")
    else:
        skipped.append("write-denied")
    failures = [row for row in rows if not row["ok"]]
    (runner.evidence / "qualification.json").write_text(json.dumps({"reference_sha256": hashlib.sha256(source).hexdigest(),
        "tool_reference_sha256": hashlib.sha256(tool_source).hexdigest(),
        "cases": rows, "failures": failures, "skipped": skipped}, indent=2) + "\n")
    print(f"{len(rows) - len(failures)}/{len(rows)} reliability result and file-byte comparisons")
    for row in failures[:6]:
        print(json.dumps(row))
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
