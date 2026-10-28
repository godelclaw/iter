#!/usr/bin/env python3
"""Ensure log qualification distinguishes multiplicity, order and state pruning."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import types

from context_logs_local import sequences, read_log, state_keys
from history_local import state as history_state
from native_module_support import NativeModules


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = (root / "lib_context.metta").read_text()
    controls = [
        ("current-window-dedup", "transcript", "window", "(filter-atom $events $event", "(filter-atom (list:unique $events) $event"),
        ("stale-window-state", "transcript", "window", "(fs:write $state-path", "(fs:append-text $state-path"),
        ("control-text-logged", "transcript", "control", "(ctx:control $content)", "false"),
        ("reversed-log", "unbounded_log", "roles", "map-atom $fresh $event", "map-atom (list:reverse $fresh) $event"),
        ("history-duplicate", "history", "window", "(let $added (add-atom $seen (seen $key))", "(let $added true"),
    ]
    rows = []
    for name, module, label, old, new in controls:
        assert old in source, name
        detected = False
        for variant in ("baseline", "mutant"):
            runner = NativeModules(root, args.binary, args.evidence / name / variant)
            if variant == "mutant":
                (runner.work / "lib_context.metta").write_text(source.replace(old, new))
            raw = subprocess.check_output(["git", "show", f"59152a727d93641eb63dcb099d1692023ddd0e31:transformations/{module}.py"], cwd=root)
            oracle = runner.evidence / "reference.py"
            oracle.write_bytes(raw)
            spec = importlib.util.spec_from_file_location("reference", oracle)
            reference = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(reference)
            rlog, nlog = runner.evidence / "ref.log", runner.evidence / "native.log"
            rstate, nstate = runner.evidence / "ref.state", runner.evidence / "native.state"
            reference.LOG_PATH, reference.HISTORY, reference.STATE_PATH = str(rlog), rlog, str(rstate)
            reference.time = types.SimpleNamespace(strftime=lambda fmt: "STAMP")
            metadata, _ = runner.call(f"transformations/{module}.metta", "__description__")
            assert metadata["ok"], metadata
            mismatch = False
            for messages in dict(sequences())[label]:
                reference.transform(copy.deepcopy(messages), [])
                if module == "history":
                    answer, _ = runner.call("tests/fixtures/history_helper.metta", "helper", [messages, [], str(nlog), str(nstate), "STAMP"])
                    same_state = history_state(rstate) == history_state(nstate)
                else:
                    env = ({"ITER_TRANSCRIPT_PATH": str(nlog), "ITER_TRANSCRIPT_STATE": str(nstate)} if module == "transcript"
                           else {"ITER_LOG_PATH": str(nlog), "ITER_LOG_STATE": str(nstate)})
                    answer, _ = runner.call(f"transformations/{module}.metta", "transform", [messages, []], env=env)
                    same_state = state_keys(rstate) == state_keys(nstate)
                assert answer["ok"], answer
                mismatch = mismatch or read_log(rlog) != read_log(nlog) or not same_state
            if variant == "baseline":
                assert not mismatch, name
            else:
                detected = mismatch
        rows.append({"mutation": name, "detected": detected})
        assert detected, name
    (args.evidence / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
    print(f"PASS: {len(rows)} context-log mutations detected; every unmodified control passed")


if __name__ == "__main__":
    main()
