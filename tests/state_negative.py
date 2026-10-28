#!/usr/bin/env python3
"""Separate reference behavior from plausible reliability/extraction mutations."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

from formalizations_local import message
from native_module_support import NativeModules
from reliability_local import pair


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    repeated = [pair()[0], pair()[0], pair()[1]]
    controls = [
        ("word-boundary", "reliability", "(not $left)", "true", b"{}", [pair("pre_error")]),
        ("revision-weight", "reliability", "(+ $weight 1.0)", "(+ $weight 2.0)", b"{}", [pair(), pair("failed")]),
        ("duplicate-assistants", "reliability", "(rel:previous $rest $id $content)", "true", b"{}", [repeated]),
        ("line-cap", "formalizations", "(first-n 500", "(first-n 499", "\n".join(f"(old {i})" for i in range(505)).encode(), [[]]),
        ("stv-lookback", "formalizations", "(forms:previous-stv $previous 9)", "(forms:previous-stv $previous 10)", b"", [[message("STV: (.2,.3)\n" + "noise\n" * 9 + "Formalization: (x)")]]),
        ("first-occurrence", "formalizations", "(list:unique", "(list:reverse", b"(a)\n", [[message("Formalization: (a)")]]),
    ]
    rows = []
    for label, kind, old, new, initial, steps in controls:
        module = "tool_reliability_tracker" if kind == "reliability" else "atom_space_update"
        library = "lib_reliability.metta" if kind == "reliability" else "lib_formalizations.metta"
        original = (root / library).read_text()
        assert old in original, label
        for variant in ("baseline", "mutant"):
            runner = NativeModules(root, args.binary, args.evidence / label / variant)
            if variant == "mutant":
                (runner.work / library).write_text(original.replace(old, new))
            source = subprocess.check_output(["git", "show", f"59152a727d93641eb63dcb099d1692023ddd0e31:transformations/{module}.py"], cwd=root)
            oracle = runner.evidence / "reference.py"
            oracle.write_bytes(source)
            spec = importlib.util.spec_from_file_location("reference", oracle)
            reference = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(reference)
            loaded, _ = runner.call(f"transformations/{module}.metta", "__description__")
            assert loaded["ok"], loaded
            home = runner.evidence / "home"
            rfile = home / "iter/transformations/.runtime/space.metta"
            nfile = runner.evidence / "native" / ("tool_reliability.json" if kind == "reliability" else "space.metta")
            for path in (rfile, nfile):
                path.parent.mkdir(parents=True)
                path.write_bytes(initial)
            reference.RUNTIME_DIR, reference.RELIABILITY_FILE = rfile.parent, rfile
            mismatch = False
            for messages in steps:
                with patch.object(Path, "home", return_value=home):
                    reference.transform(copy.deepcopy(messages), [])
                env = {"ITER_RELIABILITY_DIR": str(nfile.parent)} if kind == "reliability" else {"ITER_SPACE_PATH": str(nfile)}
                actual, _ = runner.call(f"transformations/{module}.metta", "transform", [messages, []], env=env)
                assert actual["ok"], actual
                mismatch |= rfile.read_bytes() != nfile.read_bytes()
            assert mismatch == (variant == "mutant"), (label, variant)
        rows.append({"mutation": label, "detected": True})
    (args.evidence / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
    print(f"PASS: {len(rows)} state mutations detected with passing baselines")


if __name__ == "__main__":
    main()
