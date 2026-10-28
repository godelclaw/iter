#!/usr/bin/env python3
"""Compare the native transcript transformation with the Python reference.

Every call is a fresh worker process fed a JSON request file, as the loop
does. Fixtures are generated into the evidence directory, each answer is
compared whole with the reference's, and every input file is checked to be
unchanged. With --measure it also records latency and peak memory for
transcripts with ever longer old prefixes and the same six-line suffix.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys

MODULE = "transformations/append_last_transcript_lines.metta"
HELPER = "tests/fixtures/transcript_helper.metta"
CHUNK = 65536
WORKSPACE_FILES = ["utils.metta", "iter_worker.metta", "iter_llm.metta", "iter_python.py", MODULE, HELPER]


# A small process forks the worker and reports its own wait4 usage. Forking from
# this large harness would charge the harness's memory to the worker's peak.
LAUNCHER = """
import json, os, sys, time
command, directory, env = json.loads(sys.argv[1]), sys.argv[2], json.loads(sys.argv[3])
start = time.monotonic()
pid = os.fork()
if pid == 0:
    os.chdir(directory)
    null = os.open(os.devnull, os.O_RDWR)
    for descriptor in (0, 1, 2):
        os.dup2(null, descriptor)
    os.execvpe(command[0], command, env)
_, status, usage = os.wait4(pid, 0)
print(json.dumps({"status": status, "wall": time.monotonic() - start, "maxrss": usage.ru_maxrss}))
"""


class Harness:
    def __init__(self, args):
        self.binary = args.binary.resolve(strict=True)
        self.limit = args.address_space_kib
        self.root = Path(__file__).resolve().parents[1]
        self.evidence = args.evidence.absolute()
        self.evidence.mkdir(parents=True, exist_ok=False)
        self.work = self.evidence / "workspace"
        for name in WORKSPACE_FILES:
            (self.work / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.root / name, self.work / name)
        self.files = self.evidence / "fixtures"
        self.files.mkdir()
        spec = importlib.util.spec_from_file_location("reference", args.oracle)
        self.reference = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.reference)
        self.runs = 0

    def worker(self, function, arguments, path=None, module=MODULE):
        """Run one fresh worker; return (answer, wall seconds, peak RSS KiB).

        arguments is the JSON text of the argument array."""
        request = self.work / "request.json"
        result = self.work / "result.json"
        result.unlink(missing_ok=True)
        request.write_text('{"module": %s, "function": %s, "args": %s, "kwargs": {}, "terminal": "", '
                           '"terminal_error": "", "result": %s}' % (
                               json.dumps(module), json.dumps(function), arguments, json.dumps(str(result))))
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.work), "LANG": "C.UTF-8"}
        if path is not None:
            env["ITER_TRANSCRIPT_PATH"] = str(path)
        command = [str(self.binary), "--lang", "petta", "--no-results", "iter_worker.metta", str(request)]
        if self.limit:
            command = ["bash", "-c", f"ulimit -v {self.limit}; exec \"$@\"", "worker"] + command
        done = subprocess.run([sys.executable, "-S", "-c", LAUNCHER, json.dumps(["nice", "-n", "19"] + command),
                               str(self.work), json.dumps(env)], capture_output=True, text=True, check=True)
        usage = json.loads(done.stdout)
        self.runs += 1
        assert usage["status"] == 0, f"worker status {usage['status']}"
        answer = json.loads(result.read_text())
        assert answer["ok"] is True, answer
        value = answer["result"]
        return (json.loads(value) if module == HELPER else value), usage["wall"], usage["maxrss"]

    def expected(self, messages, tools, path):
        self.reference.TRANSCRIPT_PATH = str(path)
        return list(self.reference.transform(copy.deepcopy(messages), copy.deepcopy(tools)))


def identity(path):
    info = os.stat(path, follow_symlinks=False)
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if os.access(path, os.R_OK) and path.is_file() else None
    return (info.st_size, info.st_mtime_ns, info.st_mode, digest)


def dumps(value):
    return json.dumps(value, ensure_ascii=True)


def build_fixtures(directory):
    """Write the transcript files; return their names."""
    boundary = CHUNK - 1
    text = lambda s: s.encode("utf-8")
    files = {
        "empty": b"", "lf": b"\n", "blank-lines": b"\n\n\n\n", "spaces-only": b"   \n\t\n \n",
        "one": b"one", "one-lf": b"one\n", "two": b"a\nb\n",
        "five": b"1\n2\n3\n4\n5\n", "six": b"1\n2\n3\n4\n5\n6\n", "six-unterminated": b"1\n2\n3\n4\n5\n6",
        "seven": b"1\n2\n3\n4\n5\n6\n7\n", "seven-unterminated": b"1\n2\n3\n4\n5\n6\n7",
        "interior-blank": b"1\n2\n\n\n5\n6\n7\n\n", "trailing-blank-lines": b"1\n2\n3\n4\n\n\n\n\n",
        "cr": b"a\rb\rc\rd\re\rf\rg\rh", "crlf": b"a\r\nb\r\nc\r\nd\r\ne\r\nf\r\ng\r\n",
        "mixed-newlines": b"a\rb\r\nc\n\r\nd\r\r\ne\n\nf\r\n\rg",
        "ends-cr": b"a\nb\r", "ends-crlf": b"a\nb\r\n", "ends-lfcr": b"a\nb\n\r", "only-cr": b"\r",
        "only-crlf": b"\r\n", "cr-then-lf-lines": b"x\r\n\r\n\r\ny\r\n",
        "expression": text('(= (transform $m $t) boom)\n!(py-call (os.system "x"))\n"unterminated \\ \\" \'q\n(')
                      + b"\n$var &self ; comment\n{\"a\": [1, 2]}\n",
        "nul": b"a\x00b\n\x00\nc\x00",
        "unicode": text("caf\u00e9 \u20ac \U0001f600\n\u0430\u0431\u0432 \u4e2d\u6587\n\u2028 mid \x0b \x0c \x1c \x85 \x1d split?\nend\u2029\n"),
        "unicode-space": text("a\u00a0\n\u2003 lead\n\u3000trail\u3000 \u2028\u2029\n\u00a0\u1680\u2000\u200a\u202f\u205f\u3000\n"),
        "unicode-space-end": text("keep\n \u3000\u00a0\u2003\u2028\x85\x1f\n"),
        "scalar-edges": text("\x7f\u0080\u07ff\u0800\ud7ff\ue000\ufffd\ufffe\uffff\U00010000\U0010ffff\n\u0085x\u2028"),
        "all-scalars": text("".join(chr(c) for c in range(0x110000) if not 0xD800 <= c < 0xE000)) + b"\nlast\n",
        "long-whitespace": text("x" + " \u3000\t\n" * 300000),
        "long-whitespace-fragment": text("x\n" + " \u2003" * 400000),
        "not-space": text("x\u180e\u200b\ufeff\u2060\n"), "bom": b"\xef\xbb\xbfhead\nbody\n",
        "long-line": text("w" * 200000) + b"\nafter\n", "only-long-line": text("\u00e9" * 100000) + b"\n",
        "long-fragments": b"a\n" + text("x" * 150000) + b"\n" + text("\u00e9" * 70000),
        # line ends and scalars on the default read boundary
        "boundary-cr-lf": b"p" * boundary + b"\r\nq\nr\n",
        "boundary-cr-cr": b"p" * boundary + b"\r\rq\n",
        "boundary-cr-eof": b"p\n" + b"p" * (boundary - 2) + b"\r",
        "boundary-lf": b"p" * boundary + b"\nq\nr",
        "boundary-2byte": b"\n" + text("p" * (boundary - 1) + "\u00e9") + b"\nq\n",
        "boundary-3byte-1": b"\n" + text("p" * (boundary - 1) + "\u20ac") + b"\nq\n",
        "boundary-3byte-2": b"\n" + text("p" * (boundary - 2) + "\u20ac") + b"\nq\n",
        "boundary-4byte-1": b"\n" + text("p" * (boundary - 1) + "\U0001f600") + b"\nq\n",
        "boundary-4byte-2": b"\n" + text("p" * (boundary - 2) + "\U0001f600") + b"\nq\n",
        "boundary-4byte-3": b"\n" + text("p" * (boundary - 3) + "\U0001f600") + b"\nq\n",
        "boundary-space": text("keep" + " " * 3) + b"\n" + text("p" * (boundary - 8) + "\u3000\u2028 \n"),
        "exact-chunk": b"p" * (CHUNK - 1) + b"\n", "exact-chunks": (b"p" * (CHUNK - 1) + b"\n") * 2,
        "chunk-plus-one": b"p" * CHUNK + b"\n",
        "many-lines": b"".join(b"line %d\n" % n for n in range(70000)),
        "many-lines-unterminated": b"".join(b"line %d\r\n" % n for n in range(70000)) + b"tail",
        # malformed UTF-8
        "bad-byte": b"ok\n\x80\nok\n", "bad-last": b"1\n2\n3\n4\n5\n6\n\xff", "bad-truncated-end": b"ok\n\xe2\x82",
        "bad-truncated-start": b"\xc3", "bad-overlong": b"a\n\xc0\x80\nb\n", "bad-surrogate": b"a\n\xed\xa0\x80\nb\n",
        "bad-too-large": b"a\n\xf4\x90\x80\x80\nb\n", "bad-f5": b"a\n\xf5\x80\x80\x80\n",
        "bad-continuation": b"a\n\xe2\x28\xa1\nb\n", "bad-bom-like": b"\xff\xfeA\x00\n",
        "bad-prefix-valid-suffix": b"\xff" + b"old prefix line\n" * 20000 + b"1\n2\n3\n4\n5\n6\n",
        "bad-before-suffix": b"old\n" * 20000 + b"\xc3" + b"1\n2\n3\n4\n5\n6\n",
        "bad-boundary-split": b"p" * (boundary - 1) + b"\xe2\x82\x28\n" + b"x\n",
        "bad-after-boundary": b"p" * boundary + b"\n\xe2\x28\xa1\n",
    }
    for name, content in files.items():
        (directory / name).write_bytes(content)
    (directory / "directory").mkdir()
    (directory / "dangling").symlink_to(directory / "nowhere")
    unreadable = directory / "unreadable"
    unreadable.write_bytes(b"1\n2\n")
    unreadable.chmod(0)
    return sorted(list(files) + ["missing", "directory", "dangling", "unreadable"])


def system(content):
    return [{"role": "system", "content": content, "name": "head", "extra": {"k": [1, 2.5, None, True]}},
            {"role": "user", "content": "u"}, {"role": "user", "content": "u"}]


TOOLS = [{"type": "function", "function": {"name": "a", "n": 1}}, {"type": "function", "function": {"name": "a", "n": 1}},
         {"b": [], "c": {}, "d": None, "e": 1.5, "f": "\u00e9"}]
EXPRESSION = '(= (transform $m $t) boom) !(shell "x") "q" \\ \'x\' $v &self'
VARIANTS = {
    "string": system("You are here."),
    "string-trailing-space": system("  \u00a0lead kept; end stripped \t\n\u3000\u2028 "),
    "string-long-space": system("a" + " \u3000\n" * 300000),
    "string-empty": system(""), "string-blank": system(" \n\t\u3000"),
    "string-expression": system(EXPRESSION + " \n"), "string-nul-unicode": system("a\x00\u00e9\u20ac\U0001f600 "),
    "parts": system([{"type": "text", "text": "keep \n"}, {"type": "image", "url": "u"}]),
    "parts-empty": system([]), "null": system(None), "number": system(7), "float": system(2.5),
    "true": system(True), "false": system(False), "object": system({"type": "text"}),
    "missing": [{"role": "system", "name": "no content"}], "missing-only": [{}],
    "first-is-user": [{"role": "user", "content": "u "}, {"role": "system", "content": "s"}],
    "empty-messages": [], "array-first": [[1]], "string-first": ["system"], "null-first": [None], "number-first": [3],
    "repeated": [{"role": "system", "content": "s "}] * 3,
}
DUPLICATE_KEYS = '[{"role": "system", "content": "first ", "k": 1, "content": "second \\n", "k": 2}, {"content": "x"}]'


def qualify(harness):
    directory = harness.files
    names = build_fixtures(directory)
    before = {name: identity(directory / name) for name in names
              if (directory / name).exists() or (directory / name).is_symlink()}
    failures, counts = [], {"compared": 0, "changed": 0}

    def check(label, function, messages_text, tools, extra, env_path, path):
        """messages_text is the JSON text of the messages; extra the arguments after the tools."""
        messages = json.loads(messages_text)
        want = harness.expected(messages, tools, path)
        arguments = "[%s, %s%s]" % (messages_text, json.dumps(tools), "".join(", " + json.dumps(x) for x in extra))
        got, wall, rss = harness.worker(function, arguments, env_path, HELPER if function.startswith("helper") else MODULE)
        counts["compared"] += 1
        if got != want or dumps(got) != dumps(want):
            failures.append({"case": label, "got": dumps(got)[:300], "want": dumps(want)[:300]})
        elif want[0] != messages:
            counts["changed"] += 1

    small = [name for name in names if (directory / name).is_file() and (directory / name).stat().st_size < 600]
    for name in names:
        path = directory / name
        for variant in ("string", "parts", "missing"):
            text = json.dumps(VARIANTS[variant])
            # the transformation selects its path from ITER_TRANSCRIPT_PATH
            check(f"env/{name}/{variant}", "transform", text, TOOLS, [], path, path)
        # the helper takes the path as an argument and needs no environment
        check(f"path/{name}", "helper-default", json.dumps(VARIANTS["string-trailing-space"]),
              TOOLS, [str(path)], None, path)
    for name in small + ["missing", "directory", "dangling", "unicode", "unicode-space", "expression", "bad-byte"]:
        path = directory / name
        for chunk in (1, 2, 3, 4, 5, 7, 16, 4096):
            check(f"chunk{chunk}/{name}", "helper", json.dumps(VARIANTS["string"]),
                  TOOLS, [str(path), chunk], None, path)
    for variant, messages in VARIANTS.items():
        for name in ("six", "unicode-space", "empty", "missing"):
            path = directory / name
            check(f"variant/{variant}/{name}", "transform", json.dumps(messages), TOOLS, [], path, path)
    # duplicate keys in the request text: json.loads keeps the last value at the first place
    path = directory / "mixed-newlines"
    check("duplicate-keys", "transform", DUPLICATE_KEYS, [], [], path, path)
    loop_check(harness, directory / "mixed-newlines", failures, counts)
    after = {name: identity(directory / name) for name in before}
    modified = [name for name in before if before[name] != after[name]]
    summary = {"fixtures": len(names), "comparisons": counts["compared"], "answers_changed": counts["changed"],
               "worker_runs": harness.runs, "unreadable_enforced": not os.access(directory / "unreadable", os.R_OK),
               "input_files_modified": modified, "failures": failures}
    (harness.evidence / "qualification.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def loop_check(harness, path, failures, counts):
    """The loop's own apply_transformation runs the module in a worker, with the environment inherited."""
    program = harness.work / "loop.metta"
    program.write_text('!(import! &self utils)\n'
                       '!(apply_transformation ((system "S ") (user "u")) (JsonArray ()) 5)\n')
    env = {"PATH": "/usr/bin:/bin", "HOME": str(harness.work), "LANG": "C.UTF-8", "ITER_TRANSCRIPT_PATH": str(path)}
    command = [str(harness.binary), "--lang", "petta", str(program)]
    if harness.limit:
        command = ["bash", "-c", f"ulimit -v {harness.limit}; exec \"$@\"", "worker"] + command
    done = subprocess.run(["nice", "-n", "19"] + command, cwd=harness.work, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=60)
    # plain ASCII text prints with its own newlines
    block = harness.expected([{"content": "S "}], [], path)[0][0]["content"]
    want = 'true\n(((system "%s") (user "u")) (JsonArray ()) "")\n' % block
    counts["compared"] += 1
    if done.stdout != want:
        failures.append({"case": "loop/apply_transformation", "got": done.stdout[:300], "want": want[:300]})


def measure(harness, prefix_mib, repeats):
    directory = harness.files / "measure"
    directory.mkdir()
    suffix = "s1\ns2\r\ns3\nsuffix four \u00e9\n\ns6".encode()
    line = "old prefix line with text \u00e9\u20ac 0123456789\n".encode()
    messages = VARIANTS["string"]
    rows = []
    for mib in prefix_mib:
        path = directory / f"prefix-{mib}.txt"
        with path.open("wb") as handle:
            for _ in range(mib * 1024 * 1024 // len(line)):
                handle.write(line)
            handle.write(suffix)
        walls, peaks = [], []
        want = harness.expected(messages, TOOLS, path)
        for _ in range(repeats):
            got, wall, peak = harness.worker("transform", json.dumps([messages, TOOLS]), path)
            assert got == want, "measured answer differs"
            walls.append(wall)
            peaks.append(peak)
        rows.append({"prefix_mib": mib, "file_bytes": path.stat().st_size, "first_run_s": round(walls[0], 3),
                     "warm_median_s": round(statistics.median(walls[1:] or walls), 3),
                     "peak_rss_mib_max": round(max(peaks) / 1024, 1)})
        path.unlink()
    (harness.evidence / "measurements.json").write_text(json.dumps(rows, indent=2) + "\n")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True, help="a copy of the reference transformation")
    parser.add_argument("--evidence", type=Path, required=True, help="a new directory")
    parser.add_argument("--address-space-kib", type=int, default=0, help="optional ulimit -v for each worker")
    parser.add_argument("--measure", type=int, nargs="*", metavar="PREFIX_MIB", help="also measure these old-prefix sizes")
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    harness = Harness(args)
    summary = qualify(harness)
    print(json.dumps({key: value for key, value in summary.items() if key != "failures"}, indent=2))
    for failure in summary["failures"][:5]:
        print("FAILED", json.dumps(failure)[:700])
    if args.measure:
        print(json.dumps(measure(harness, args.measure, args.repeats), indent=2))
    ok = not summary["failures"] and not summary["input_files_modified"]
    print("PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
