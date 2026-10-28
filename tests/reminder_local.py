#!/usr/bin/env python3
"""Compare the native reminder's answers and alarm files with the pinned source."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

REFERENCE_REVISION = "59152a727d93641eb63dcb099d1692023ddd0e31"
ZONES = ("UTC", "America/New_York", "Europe/Prague", "Australia/Lord_Howe", "Pacific/Apia")


def cases():
    valid = ["1970-01-01 00:00:00", "1969-12-31 23:59:59", "1800-06-15 12:34:56",
             "2000-02-29 00:00:00", "2024-02-29 23:59:59", "2038-01-19 03:14:08",
             "2026-03-08 01:59:59", "2026-03-08 02:00:00", "2026-03-08 02:30:00",
             "2026-03-08 03:00:00", "2026-11-01 01:00:00", "2026-11-01 01:30:00",
             "2026-03-29 02:30:00", "2026-10-25 02:30:00", "2026-04-05 01:45:00",
             "2026-10-04 02:15:00", "2011-12-30 12:00:00", "0001-01-01 00:00:00",
             "9999-12-31 23:59:59", "2026-2-3 4:5:6", "2026-02- 3 04:05:06",
             "2026-02-03\t04:05:06", "2026-02-03\u200304:05:06",
             "٢٠٢٦-02-03 04:05:06", "2026-02-1٣ 04:05:06", "2026-02-03 0٤:0٥:0٦"]
    invalid = ["", "not a date", "2026-02-29 00:00:00", "1900-02-29 00:00:00",
               "2026-13-01 00:00:00", "2026-00-01 00:00:00", "2026-04-31 00:00:00",
               "2026-10-28 24:00:00", "2026-10-28 00:60:00", "2026-10-28 00:00:60",
               "0000-01-01 00:00:00", "10000-01-01 00:00:00", "026-01-01 00:00:00",
               "2026-01-01 00:00:00 ", " 2026-01-01 00:00:00", "2026-01-  1 00:00:00",
               "2026-01- 01 00:00:00", "2026-٠١-01 00:00:00", "2026-01-3١ 00:00:00",
               "2026-02-0٣ 04:05:06", "2026-02-٣ 04:05:06"]
    invalid += ["2026-01-01 :00:00", "2026-01-01 x:00:00", "2026-01-01 ١٢:00:00",
                "2026-01-01 00::00", "2026-01-01 00:00:", "2026--01 00:00:00"]
    return valid + invalid


def files(root):
    directory = root / "memory/alarms"
    return None if not directory.exists() else {p.name: p.read_bytes().hex() for p in directory.iterdir()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    evidence = args.evidence.absolute()
    evidence.mkdir(parents=True, exist_ok=False)
    source = subprocess.check_output(["git", "show", f"{REFERENCE_REVISION}:tools/reminder.py"], cwd=root)
    oracle = evidence / "reference.py"
    oracle.write_bytes(source)
    spec = importlib.util.spec_from_file_location("reminder_reference", oracle)
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    workspace = evidence / "native"
    workspace.mkdir()
    source_hashes = {}
    for name in subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().strip("\0").split("\0"):
        destination = workspace / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, destination)
        source_hashes[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    (evidence / "sources.json").write_text(json.dumps(source_hashes, indent=2) + "\n")
    result = evidence / "metadata.json"
    request = workspace / "request.json"
    request.write_text(json.dumps({"module": "tools/reminder.metta", "function": "__tool_metadata__",
        "args": [], "kwargs": {}, "terminal": "", "terminal_error": "", "result": str(result)}))
    run = subprocess.run([str(args.binary.resolve()), "--lang", "petta", "--no-results",
                          "iter_worker.metta", str(request)], cwd=workspace,
                         env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TZ": "UTC"},
                         capture_output=True, text=True, timeout=10)
    metadata = json.loads(result.read_text()) if result.exists() else {"error": run.stderr}
    assert run.returncode == 0 and metadata.get("ok") is True, metadata
    assert metadata["result"]["required"] == ["when"]
    rows = []
    for zone in ZONES:
        os.environ["TZ"] = zone
        time.tzset()
        for number, when in enumerate(cases()):
            base = evidence / zone.replace("/", "_") / str(number)
            reference_root = base / "reference"
            reference_root.mkdir(parents=True)
            native_root = base / "native"
            native_root.mkdir()
            reference.__file__ = str(reference_root / "tools/reminder.py")
            kwargs = {"when": when}
            if number % 2:
                kwargs.update(channel="fixture\x00é", message="line 1\nline 2 😀")
            try:
                expected = {"ok": True, "result": reference.run(**kwargs)}
            except Exception as error:
                expected = {"ok": False, "error": type(error).__name__}
            result = base / "result.json"
            request = workspace / "request.json"
            request.write_text(json.dumps({"module": "tools/reminder.metta", "function": "run",
                "args": [], "kwargs": kwargs, "terminal": "", "terminal_error": "", "result": str(result)}))
            env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TZ": zone,
                   "ITER_ALARMS_DIR": str(native_root / "memory/alarms")}
            run = subprocess.run([str(args.binary.resolve()), "--lang", "petta", "--no-results",
                                  "iter_worker.metta", str(request)], cwd=workspace, env=env,
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
            actual = json.loads(result.read_text()) if result.exists() else {"ok": False, "error": run.stderr}
            ok = run.returncode == 0 and actual["ok"] == expected["ok"] and files(native_root) == files(reference_root)
            if ok and expected["ok"]:
                ok = actual["result"].replace(str(native_root), "<root>") == expected["result"].replace(str(reference_root), "<root>")
            rows.append({"zone": zone, "when": when, "ok": ok, "reference": expected, "native": actual,
                         "reference_files": files(reference_root), "native_files": files(native_root)})
    skipped = []
    os.environ["TZ"] = "UTC"
    time.tzset()
    for label, steps in [
        ("overwrite", [{"when": "1970-01-01 00:00:00", "message": "first"},
                       {"when": "1970-01-01 00:00:00", "channel": "next", "message": "replacement"}]),
        ("invalid-preserves-existing", [{"when": "2026-02-30 00:00:00"}]),
        ("write-refusal", [{"when": "1970-01-01 00:00:00"}]),
    ]:
        if label == "write-refusal" and os.geteuid() == 0:
            skipped.append({"case": label, "reason": "root bypasses the ordinary-user permission fixture"})
            continue
        base = evidence / label
        reference_root, native_root = base / "reference", base / "native"
        for directory in (reference_root, native_root):
            alarms = directory / "memory/alarms"
            alarms.mkdir(parents=True)
            (alarms / "untouched").write_bytes(b"preserved\x00bytes")
            if label == "write-refusal":
                alarms.chmod(0o555)
        reference.__file__ = str(reference_root / "tools/reminder.py")
        for number, kwargs in enumerate(steps):
            try:
                expected = {"ok": True, "result": reference.run(**kwargs)}
            except Exception as error:
                expected = {"ok": False, "error": type(error).__name__}
            result = base / f"result-{number}.json"
            request.write_text(json.dumps({"module": "tools/reminder.metta", "function": "run",
                "args": [], "kwargs": kwargs, "terminal": "", "terminal_error": "", "result": str(result)}))
            run = subprocess.run([str(args.binary.resolve()), "--lang", "petta", "--no-results",
                                  "iter_worker.metta", str(request)], cwd=workspace,
                                 env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TZ": "UTC",
                                      "ITER_ALARMS_DIR": str(native_root / "memory/alarms")},
                                 capture_output=True, text=True, timeout=10)
            actual = json.loads(result.read_text()) if result.exists() else {"ok": False, "error": run.stderr}
            ok = run.returncode == 0 and actual["ok"] == expected["ok"] and files(native_root) == files(reference_root)
            if ok and expected["ok"]:
                ok = actual["result"].replace(str(native_root), "<root>") == expected["result"].replace(str(reference_root), "<root>")
            if label == "write-refusal":
                ok = ok and not expected["ok"] and not actual["ok"]
            rows.append({"case": f"{label}/{number}", "ok": ok, "reference": expected, "native": actual,
                         "reference_files": files(reference_root), "native_files": files(native_root)})
        for directory in (reference_root, native_root):
            (directory / "memory/alarms").chmod(0o755)
    failures = [row for row in rows if not row["ok"]]
    (evidence / "qualification.json").write_text(json.dumps({"reference_sha256": hashlib.sha256(source).hexdigest(),
        "python_version": sys.version, "engine_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
        "cases": rows, "failures": failures, "skipped": skipped}, indent=2) + "\n")
    print(f"{len(rows) - len(failures)}/{len(rows)} reminder answer and file comparisons")
    for row in failures[:5]:
        print(json.dumps(row, ensure_ascii=False))
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
