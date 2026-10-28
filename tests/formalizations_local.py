#!/usr/bin/env python3
"""Compare formalization extraction, pruning and file failures with erayindex."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

from native_module_support import NativeModules, value_equal


def message(text, role="tool"):
    return {"role": role, "content": text}


def trials():
    yield "absent", None, [message("Formalization: (a b)")]
    yield "empty", b"", []
    yield "default", b"", [message("Formalization: (a b)")]
    yield "stv", b"", [message("STV: (0.7, 0.8)\nFormalization: (a b)")]
    for n in (8, 9, 10):
        yield f"lookback-{n}", b"", [message("STV: (0.7, 0.8)\n" + "noise\n" * n + "Formalization: (a b)")]
    for index, stv in enumerate(("STV: (0.7,0.8)", "STV: \t ( a , b )", "STV: (a, )", "STV: (a,)",
                                 "STV: (,b)", "STV: garbage STV: (x,y)", "STV: (a) STV: (x,y)",
                                 "STV: (x,(y))", 'STV: ("a",b)', "STV: (a, b,c)",
                                 "STV:\u00a0(a,b)", "STV: invalid")):
        yield f"stv-{index}", b"", [message(stv + "\nFormalization: (a b)")]
    yield "nearest-invalid", b"", [message("STV: (0.8,0.9)\nSTV: bad\nFormalization: (a b)")]
    for index, form in enumerate(("(a b)", "(a)(b)", "((a) b)", "(a b) trailing", "prefix (a)", "",
                                  '(a "x")', "(a", ";; (a b)", "())(()", "(x (stv 0.2 0.3))",
                                  "(= (danger) (println! bad))", "(a\x00b)", "(a ;; x)", "(é ２)")):
        yield f"form-{index}", b"", [message("text Formalization: " + form)]
    yield "existing", b';; old header\n\n(a b)\n(a b)\n((x y) (stv 0.7 0.8))\n(a)(b)\n(a) tail\n(a "x")\n(a\n', [message("Formalization: (a b)")]
    yield "order", b"(old one)\n(old two)\n", [message("Formalization: (new one)\nFormalization: (old one)"), message("Formalization: (new two)")]
    yield "limit", ("\n".join(f"(old {i})" for i in range(505)) + "\n").encode(), [message("Formalization: (fresh)")]
    yield "universal-newlines", b"(a b)\r\n(c d)\r", []
    yield "state-utf8", b"\xff", []
    for index, content in enumerate((None, [], {}, 3, ["Formalization:"], {"Formalization:": "(x)"})):
        for role in ("user", "tool"):
            yield f"content-{index}-{role}", b"", [message(content, role)]


def snapshot(path):
    if not path.exists():
        return None
    return "directory" if path.is_dir() else path.read_bytes().hex()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    runner = NativeModules(root, args.binary, args.evidence)
    source = subprocess.check_output(["git", "show", "59152a727d93641eb63dcb099d1692023ddd0e31:transformations/atom_space_update.py"], cwd=root)
    oracle = runner.evidence / "reference.py"
    oracle.write_bytes(source)
    spec = importlib.util.spec_from_file_location("formalizations_reference", oracle)
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    loaded, _ = runner.call("transformations/atom_space_update.metta", "__description__")
    assert loaded["ok"], loaded
    rows, skipped = [], []
    cases = list(trials()) + [("directory", None, []), ("readonly", b"(old)\n", [])]
    for label, seed, messages in cases:
        if label == "readonly" and os.geteuid() == 0:
            skipped.append(label)
            continue
        base = runner.evidence / label
        home = base / "home"
        rfile = home / "iter/transformations/.runtime/space.metta"
        nfile = base / "native/space.metta"
        for path in (rfile, nfile):
            path.parent.mkdir(parents=True)
            if seed is not None:
                path.write_bytes(seed)
            if label == "directory":
                path.mkdir()
            if label == "readonly":
                path.chmod(0o444)
        tools = [{"preserve": [None, True, -0.0]}]
        try:
            with patch.object(Path, "home", return_value=home):
                expected = {"ok": True, "result": list(reference.transform(copy.deepcopy(messages), copy.deepcopy(tools)))}
        except Exception as error:
            expected = {"ok": False, "error": type(error).__name__}
        actual, raw = runner.call("transformations/atom_space_update.metta", "transform", [messages, tools],
                                  env={"ITER_SPACE_PATH": str(nfile)})
        ok = actual["ok"] == expected["ok"] and (not expected["ok"] or value_equal(actual["result"], expected["result"]))
        ok = ok and snapshot(rfile) == snapshot(nfile)
        rows.append({"case": label, "ok": ok, "reference": expected, "native": actual,
                     "reference_file": snapshot(rfile), "native_file": snapshot(nfile)})
        if label == "readonly":
            rfile.chmod(0o644)
            nfile.chmod(0o644)
    failures = [row for row in rows if not row["ok"]]
    (runner.evidence / "qualification.json").write_text(json.dumps({"reference_sha256": hashlib.sha256(source).hexdigest(),
        "cases": rows, "failures": failures, "skipped": skipped}, indent=2) + "\n")
    print(f"{len(rows) - len(failures)}/{len(rows)} formalization result and file-byte comparisons")
    for row in failures[:5]:
        print(json.dumps(row))
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
