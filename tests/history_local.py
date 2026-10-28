#!/usr/bin/env python3
"""History text, within-call deduplication and persisted window-state parity."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import types

from context_logs_local import sequences, read_log
from native_module_support import NativeModules, value_equal


def state(path):
    if not path.exists():
        return None
    return set(path.read_text().split("\n")) - {""}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    harness = NativeModules(root, args.binary, args.evidence)
    raw = subprocess.check_output(["git", "show", "59152a727d93641eb63dcb099d1692023ddd0e31:transformations/history.py"], cwd=root)
    oracle = harness.evidence / "reference.py"
    oracle.write_bytes(raw)
    spec = importlib.util.spec_from_file_location("history_reference", oracle)
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    rows = []
    trials = sequences() + [("multiline-state", [[{"role": "user", "content": "a\nb\rc\u2028d\\\""}],
                                                [{"role": "user", "content": "a\nb\rc\u2028d\\\""}]])]
    for label, inputs in trials:
        directory = harness.evidence / label
        directory.mkdir()
        rlog, nlog = directory / "ref.log", directory / "native.log"
        rstate, nstate = directory / "ref.state", directory / "native.state"
        reference.HISTORY, reference.STATE_PATH = rlog, rstate
        for index, messages in enumerate(inputs):
            stamp = f"2026-10-28 10:00:{index:02d}"
            reference.time = types.SimpleNamespace(strftime=lambda fmt: stamp)
            tools = [{"preserved": [None, True, -0.0]}]
            try:
                expected = {"ok": True, "result": list(reference.transform(copy.deepcopy(messages), copy.deepcopy(tools)))}
            except Exception as error:
                expected = {"ok": False, "error": type(error).__name__}
            actual, wire = harness.call("tests/fixtures/history_helper.metta", "helper",
                                       [messages, tools, str(nlog), str(nstate), stamp])
            ok = actual["ok"] == expected["ok"]
            if ok and expected["ok"]:
                ok = value_equal(json.loads(actual["result"]), expected["result"])
            ok = ok and read_log(rlog) == read_log(nlog) and state(rstate) == state(nstate)
            rows.append({"case": label, "step": index, "ok": ok, "reference": expected, "native": actual,
                         "reference_log": read_log(rlog), "native_log": read_log(nlog),
                         "reference_state": sorted(state(rstate) or []), "native_state": sorted(state(nstate) or [])})
    failures = [row for row in rows if not row["ok"]]
    (harness.evidence / "qualification.json").write_text(json.dumps({"reference_sha256": hashlib.sha256(raw).hexdigest(),
        "cases": rows, "failures": failures}, indent=2) + "\n")
    print(f"{len(rows) - len(failures)}/{len(rows)} history output and state comparisons")
    for row in failures[:4]:
        print(json.dumps(row))
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
