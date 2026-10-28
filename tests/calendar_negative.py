#!/usr/bin/env python3
"""Separating controls for local-time, fold and Gregorian calendar mistakes."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.evidence.mkdir(parents=True, exist_ok=False)
    source = (root / "lib_calendar.metta").read_text()
    controls = [
        ("utc-instead-of-local", '$epoch local)', '$epoch utc)', "America/New_York", "2026-07-01 12:00:00"),
        ("later-fold", '($probe (- $first 86400))', '($probe (+ $first 86400))', "America/New_York", "2026-11-01 01:30:00"),
        ("earlier-gap", '(max $first $second)', '(min $first $second)', "America/New_York", "2026-03-08 02:30:00"),
        ("no-leap-day", '(if (calendar:leap $year) 29 28)', '(if (calendar:leap $year) 28 28)', "UTC", "2024-02-29 00:00:00"),
        ("epoch-shift", ') 719163)', ') 719162)', "UTC", "1970-01-01 00:00:00"),
    ]
    rows = []
    for name, old, new, zone, when in controls:
        assert source.count(old) == 1, name
        os.environ["TZ"] = zone
        time.tzset()
        expected = str(int(datetime.datetime.strptime(when, "%Y-%m-%d %H:%M:%S").timestamp()))
        query = ("(calendar:naive (CalendarParts 1970 1 1 0 0 0))" if name == "epoch-shift"
                 else f"(calendar:timestamp {json.dumps(when)})")
        result = {}
        for variant, contents in [("baseline", source), ("mutant", source.replace(old, new))]:
            directory = args.evidence / name / variant
            directory.mkdir(parents=True)
            (directory / "lib_calendar.metta").write_text(contents)
            shutil.copy2(root / "lib_text.metta", directory / "lib_text.metta")
            (directory / "check.metta").write_text('!(import! &self "lib_calendar.metta")\n'
                '!(system:write stdout "loaded\\n")\n'
                f'!(system:write stdout (str:number->string {query}))\n')
            run = subprocess.run([str(args.binary.resolve()), "--lang", "petta", "--no-results", "check.metta"],
                                 cwd=directory, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TZ": zone},
                                 capture_output=True, text=True, timeout=10)
            assert run.stdout.startswith("loaded\n"), (name, variant, run.stderr)
            result[variant] = {"returncode": run.returncode, "stdout": run.stdout, "stderr": run.stderr}
        assert result["baseline"]["returncode"] == 0 and result["baseline"]["stdout"] == "loaded\n" + expected
        detected = result["mutant"]["stdout"] != "loaded\n" + expected or result["mutant"]["returncode"] != 0
        rows.append({"name": name, "zone": zone, "when": when, "query": query,
                     "expected": expected, "detected": detected, **result})
        assert detected, name
    (args.evidence / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
    print(f"PASS: {len(rows)} calendar mutations rejected by their separating witnesses")


if __name__ == "__main__":
    main()
