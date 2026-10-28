#!/usr/bin/env python3
"""Compare ordered log bytes and window-state sets with the frozen Python logs."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

from native_module_support import NativeModules, value_equal


def sequences():
    send = {"role": "assistant", "tool_calls": [{"function": {"name": "send",
            "arguments": json.dumps({"channel": "terminal", "content": "reply é\x00"})}}]}
    return [
        ("empty", [[], []]),
        ("window", [[{"role": "user", "content": "hello"}] * 2,
                    [{"role": "user", "content": "hello"}], [],
                    [{"role": "user", "content": "hello"}]]),
        ("roles", [[{"role": role, "content": "same"} for role in ("system", "user", "assistant", "tool", "unknown")]]),
        ("control", [[{"role": "user", "content": "Step now: [ALARM] test"},
                       {"role": "user", "content": "mention [NO ADDITIONAL input"},
                       {"role": "assistant", "content": "[ALARM] retained here"}]]),
        ("whitespace", [[{"role": "user", "content": text} for text in ("", " \t\u3000", "\n", " keep \r\n", "a\x00b")]]),
        ("send", [[send, send], [send], []]),
        ("malformed-send", [[{"role": "user", "content": "first"},
                             {"role": "assistant", "tool_calls": [{"function": {"name": "send", "arguments": "{"}}]}]]),
        ("nonobject-arguments", [[{"role": "user", "content": "first"},
                                  {"role": "assistant", "tool_calls": [{"function": {"name": "send", "arguments": "[]"}}]}]]),
        ("bad-function", [[{"role": "user", "content": "first"},
                           {"role": "assistant", "tool_calls": [{"function": None}]}]]),
        ("nonobjects", [[None, 3, "text", [], {"content": "no role"}]]),
        ("nonstring-content", [[{"role": "user", "content": []}, {"role": "user", "content": None}]]),
        ("scalar-roles", [[{"role": role, "content": "body " + str(index)}
                           for index, role in enumerate((None, True, False, 0, 1, 1.5, [], {}))]]),
        ("repr-fields", [[{"role": {"a": "é\u202e", "b": [None, True, -0.0]}, "content": "line"},
                          {"role": "assistant", "tool_calls": [{"function": {"name": "send", "arguments":
                              json.dumps({"channel": 7, "content": {"a": "x'y", "b": [False, None, "\x08"]}})}}]}]]),
        ("abort-unicode", [[{"role": "user", "content": "first"},
                            {"role": "assistant", "tool_calls": [{"function": {"name": "send", "arguments":
                                '{"content":"\\ud800"}'}}]}]]),
    ]


def read_log(path):
    return path.read_bytes().hex() if path.exists() else None


def state_keys(path):
    return set(path.read_text().splitlines()) if path.exists() else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    harness = NativeModules(root, args.binary, args.evidence)
    rows, identities = [], {}
    for module in ("transcript", "unbounded_log"):
        source = subprocess.check_output(["git", "show", f"59152a727d93641eb63dcb099d1692023ddd0e31:transformations/{module}.py"], cwd=root)
        path = harness.evidence / f"reference-{module}.py"
        path.write_bytes(source)
        identities[module] = hashlib.sha256(source).hexdigest()
        spec = importlib.util.spec_from_file_location("reference_" + module, path)
        reference = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reference)
        for label, inputs in sequences():
            directory = harness.evidence / module / label
            directory.mkdir(parents=True)
            rlog, nlog = directory / "ref.log", directory / "native.log"
            rstate, nstate = directory / "ref.state", directory / "native.state"
            reference.LOG_PATH, reference.STATE_PATH = str(rlog), str(rstate)
            env = ({"ITER_TRANSCRIPT_PATH": str(nlog), "ITER_TRANSCRIPT_STATE": str(nstate)} if module == "transcript"
                   else {"ITER_LOG_PATH": str(nlog), "ITER_LOG_STATE": str(nstate)})
            for index, messages in enumerate(inputs):
                tools = [{"unknown": [1, None, True, -0.0]}]
                expected = list(reference.transform(copy.deepcopy(messages), copy.deepcopy(tools)))
                actual, raw = harness.call(f"transformations/{module}.metta", "transform", [messages, tools], env=env)
                same = (actual.get("ok") is True and value_equal(actual["result"], expected) and
                        read_log(rlog) == read_log(nlog) and state_keys(rstate) == state_keys(nstate))
                rows.append({"module": module, "case": label, "step": index, "ok": same,
                             "reference_log": read_log(rlog), "native_log": read_log(nlog),
                             "reference_state": sorted(state_keys(rstate) or []),
                             "native_state": sorted(state_keys(nstate) or []), "native": actual})
    failures = [row for row in rows if not row["ok"]]
    (harness.evidence / "qualification.json").write_text(json.dumps({"references": identities, "cases": rows, "failures": failures}, indent=2) + "\n")
    print(f"{len(rows) - len(failures)}/{len(rows)} ordered-log and window-state comparisons")
    for row in failures[:4]:
        print(json.dumps(row, ensure_ascii=True))
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
