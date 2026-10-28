#!/usr/bin/env python3
"""Compare the native alarm transformation with the pinned Python reference.

Every native call is a fresh worker process fed a JSON request file, as the
loop does. A case builds one fixture directory twice, runs the reference (with
only the alarm directory, the clock and the timestamp injected) on one copy and
the native worker on the other, compares the COMPLETE answers (value and JSON
spelling) and then every remaining file name, type, mode and byte. Python parity
is reported apart from the stronger declared checks, the known float-spelling
differences, the durable-delivery witnesses, the negative controls and, with
--measure, latency and memory.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import locale
import math
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import statistics
import subprocess
import sys
import time
import types

PINNED_SHA256 = "5ac8535929b32e4d429362b5b349a1ba2aa0abfe436e0c21b5b6a896be1abba6"
MODULE = "transformations/alarms.metta"
HELPER = "tests/fixtures/alarms_helper.metta"
WORKSPACE_FILES = ["utils.metta", "iter_worker.metta", "iter_llm.metta", "iter_python.py", "lib_text.metta", MODULE, HELPER]
ZONE = "America/New_York"

# A small process forks the worker and reports its own wait4 usage; forking from
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


def dumps(value):
    return json.dumps(value, ensure_ascii=True)


def text(s):
    return s.encode("utf-8")


def strict_equal(a, b):
    """Preserve number kinds and the sign of zero, as well as containers."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return list(a) == list(b) and all(strict_equal(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(strict_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and a == b == 0:
        return math.copysign(1, a) == math.copysign(1, b)
    return a == b


class ObjectPairs(tuple):
    """An object occurrence sequence, distinct from a JSON array."""


def wire_value(source):
    return json.loads(source, object_pairs_hook=ObjectPairs)


def result_parts(source):
    """Keep each raw argument of the two-element JSON result for the next call."""
    decoder = json.JSONDecoder()
    start = len(source) - len(source.lstrip())
    assert source[start] == "["
    start += 1
    while source[start].isspace():
        start += 1
    _, end = decoder.raw_decode(source, start)
    first = source[start:end]
    start = end
    while source[start].isspace():
        start += 1
    assert source[start] == ","
    start += 1
    while source[start].isspace():
        start += 1
    _, end = decoder.raw_decode(source, start)
    assert source[end:].strip() == "]"
    return first, source[start:end]


def comparator_controls():
    assert not strict_equal(-0.0, 0.0)
    assert not strict_equal(1, 1.0)
    assert not strict_equal(True, 1)
    assert not strict_equal(wire_value('{"a": 1, "a": 2}'), wire_value('{"a": 2}'))
    assert not strict_equal(wire_value('{"a": 1}'), wire_value('[["a", 1]]'))
    left, right = result_parts(' [ [{"a":-0.0,"a":2}], [{"t":3}] ] ')
    assert strict_equal(wire_value(left), wire_value('[{"a":-0.0,"a":2}]'))
    assert strict_equal(wire_value(right), wire_value('[{"t":3}]'))


class Harness:
    def __init__(self, args, evidence=None, module_text=None):
        self.binary = args.binary.resolve(strict=True)
        self.limit = args.address_space_kib
        self.worker_timeout = args.worker_timeout
        self.root = Path(__file__).resolve().parents[1]
        self.evidence = evidence or args.evidence.absolute()
        self.evidence.mkdir(parents=True, exist_ok=False)
        self.work = self.evidence / "workspace"
        for name in WORKSPACE_FILES:
            (self.work / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.root / name, self.work / name)
        if module_text is not None:
            (self.work / MODULE).write_text(module_text)
        self.cases = self.evidence / "cases"
        self.cases.mkdir()
        data = Path(args.oracle).read_bytes()
        assert hashlib.sha256(data).hexdigest() == PINNED_SHA256, "the oracle is not the pinned reference"
        spec = importlib.util.spec_from_file_location("reference", args.oracle)
        self.reference = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.reference)
        self.runs = 0
        self.count = 0

    def command(self, program):
        command = [str(self.binary), "--lang", "petta"] + program
        if self.limit:
            command = ["bash", "-c", f"ulimit -v {self.limit}; exec \"$@\"", "worker"] + command
        return ["nice", "-n", "19"] + command

    def environment(self, extra=None):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.work), "LANG": "C.UTF-8", "TZ": ZONE}
        env.update(extra or {})
        return env

    def launch(self, env=None):
        command = self.command(["--no-results", "iter_worker.metta", "request.json"])
        launch = [sys.executable, "-S", "-c", LAUNCHER, json.dumps(command), str(self.work), json.dumps(self.environment(env))]
        return subprocess.Popen(launch, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)

    def worker(self, function, arguments, module=HELPER, env=None, result=None, wait=True):
        """Run one fresh worker; return (answer, wall seconds, peak RSS KiB), or the process when not waiting.

        arguments is the JSON text of the argument array; answer is the worker's result file, parsed."""
        request = self.work / "request.json"
        result = Path(result or self.work / "result.json")
        result.unlink(missing_ok=True)
        request.write_text('{"module": %s, "function": %s, "args": %s, "kwargs": {}, "terminal": "", '
                           '"terminal_error": "", "result": %s}' % (
                               json.dumps(module), json.dumps(function), arguments, json.dumps(str(result))))
        process = self.launch(env)
        if not wait:
            return process
        try:
            stdout, stderr = process.communicate(timeout=self.worker_timeout)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, "launcher", stdout, stderr)
        usage = json.loads(stdout)
        self.runs += 1
        assert usage["status"] == 0, f"worker status {usage['status']}"
        return json.loads(result.read_text()), usage["wall"], usage["maxrss"]

    def native(self, messages_text, tools_text, directory, now, stamp, env=None):
        arguments = "[%s, %s, %s, %s, %s]" % (messages_text, tools_text, json.dumps(str(directory)),
                                               json.dumps(now), json.dumps(stamp))
        answer, wall, rss = self.worker("helper", arguments, env=env)
        if answer["ok"] is True:
            return ("ok", json.loads(answer["result"]), answer["result"]), wall, rss
        return ("error", answer["error"], None), wall, rss

    def expected(self, messages, tools, directory, now, stamp):
        ref = self.reference
        ref.ALARMS_DIR = Path(directory)
        ref.time = types.SimpleNamespace(time=lambda: now, strftime=lambda fmt: stamp)
        try:
            return ("ok", list(ref.transform(copy.deepcopy(messages), copy.deepcopy(tools))))
        except Exception as error:
            return ("error", type(error).__name__)


# ---------------------------------------------------------------- fixtures

def materialize(spec, root):
    """spec: name -> bytes | ("dir", spec, mode) | ("link", target) | ("file", bytes, mode)."""
    root = os.fsencode(root)
    for name, value in spec.items():
        path = os.path.join(root, os.fsencode(name))
        if isinstance(value, bytes):
            Path(os.fsdecode(path)).write_bytes(value)
        elif value[0] == "file":
            Path(os.fsdecode(path)).write_bytes(value[1])
            os.chmod(path, value[2])
        elif value[0] == "link":
            os.symlink(os.fsencode(value[1]), path)
        elif value[0] == "dir":
            os.mkdir(path)
            materialize(value[1], os.fsdecode(path))
            os.chmod(path, value[2])


def unlock(root):
    for dirpath, dirnames, filenames in os.walk(os.fsencode(root), topdown=True):
        os.chmod(dirpath, 0o700)
        for name in filenames:
            full = os.path.join(dirpath, name)
            if not os.path.islink(full):
                os.chmod(full, 0o600)


def snapshot(root):
    """Relative name (bytes) -> (type, mode, bytes or link target) for every entry, or None when absent."""
    result = {}
    base = os.fsencode(root)
    if not os.path.lexists(base):
        return None

    def walk(directory, prefix):
        mode = stat.S_IMODE(os.lstat(directory).st_mode)
        result[prefix] = ("dir", mode, None)
        os.chmod(directory, 0o700)
        try:
            for name in sorted(os.listdir(directory)):
                full = os.path.join(directory, name)
                key = prefix + b"/" + name
                info = os.lstat(full)
                if stat.S_ISDIR(info.st_mode):
                    walk(full, key)
                elif stat.S_ISLNK(info.st_mode):
                    result[key] = ("link", 0, os.readlink(full))
                else:
                    file_mode = stat.S_IMODE(info.st_mode)
                    os.chmod(full, 0o600)
                    result[key] = ("file", file_mode, Path(os.fsdecode(full)).read_bytes())
                    os.chmod(full, file_mode)
        finally:
            os.chmod(directory, mode)

    walk(base, b"")
    return result


def describe_difference(left, right):
    if left is None or right is None:
        return f"tree presence {left is not None} vs {right is not None}"
    names = sorted(set(left) | set(right))
    return [(os.fsdecode(n), "only native" if n not in left else "only reference" if n not in right else "content or mode")
            for n in names if left.get(n) != right.get(n)][:6]


BASE_MESSAGES = [{"role": "system", "content": "s", "extra": {"k": [1, 2.5, None, True], "n": 0.0}},
                 {"role": "user", "content": "u"}, {"role": "user", "content": "u"}]
BASE_TOOLS = [{"type": "function", "function": {"name": "a", "n": 1}}, {"type": "function", "function": {"name": "a", "n": 1}},
              {"b": [], "c": {}, "d": None, "e": 1.5, "f": "\u00e9"}]
STAMP = "2026-10-28 12:34:56"
WHITESPACE = [9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760] + list(range(8192, 8203)) + [8232, 8233, 8239, 8287, 12288]
NOT_WHITESPACE = [0, 8, 14, 27, 33, 127, 134, 159, 161, 5759, 5761, 6158, 8191, 8203, 8230, 8231, 8234, 8238, 8240,
                  8286, 8288, 12287, 12289, 65279, 65533]


def cases():
    out = []

    def add(name, files, now=1000, invocations=None, messages=BASE_MESSAGES, tools=BASE_TOOLS, stamp=STAMP,
            messages_text=None, tools_text=None, group="parity", subdirectory="alarms"):
        out.append(dict(name=name, files=files, invocations=invocations or [(now, stamp)], messages=messages,
                        tools=tools, messages_text=messages_text, tools_text=tools_text,
                        group=group, subdirectory=subdirectory))

    add("missing-directory", None)
    add("missing-nested-directory", None, subdirectory="a/b/c")
    add("empty-directory", {})
    add("directory-is-a-file", "FILE")
    add("only-nonnumeric", {n: b"x" for n in ["readme.txt", "alarm", ".hidden", "1.2.3", "0x10", "abc123", "--1", "e5",
                                              "...", "+", "-", "1e", "nan.txt", "1,5", "12abc", "a1", "1 2", "0b1"]})
    add("nonutf8-filename", {b"\xff".decode("utf-8", "surrogateescape"): b"x", "100": b"x"})
    add("past-equal-future", {"99": b"a\npast", "100": b"b\nequal", "101": b"c\nfuture", "100.5": b"d\nfuture",
                              "100.0": b"e\nequal float", "99.999999": b"f\npast", "1e2": b"g\nexp equal"}, now=100)
    add("float-now-equal", {"1000.5": b"equal", "1000.50": b"equal2", "1000.4999999999999": b"past",
                            "1000.5000000000001": b"future"}, now=1000.5)
    add("float-now-realistic", {"1793164055": b"a\npast", "1793164055.5874524": b"b\nequal", "1793164056": b"c\nfuture"},
        now=1793164055.5874524)
    add("integer-clock-mixed-boundary", {"9007199254740996.0": b"future"}, now=9007199254740995)
    add("integer-deadline-rounded", {"9007199254740993": b"due"}, now=9007199254740992)
    add("admitted-spellings", {n: text("c%d\nspelling %s" % (i, n)) for i, n in enumerate(
        ["+5", "5.", ".5", "-.5", "1e2", "1E2", "1e+2", "1.5e1", "005", "-0", "0", "0.0", "00.5e0", "1e-3", "-3",
         "3.14159", "+0", "-0.0", "1e0", "1e+0", "+.5e1", "9.9e1", "0.1e3", "1.e2", "1e-400"])}, now=100)
    add("hidden-name-is-numeric", {".5": b"hidden", "5": b"plain"}, now=100)
    add("inf-nan-overflow-never-due", {n: b"x" for n in ["inf", "+inf", "infinity", "Infinity", "nan", "-nan", "1e400", "+1e400"]},
        now=10 ** 12)
    add("huge-integer-names", {"1" + "0" * 250: b"x", "-1" + "0" * 250: b"y", "123456789012345678901234567890": b"z"}, now=10 ** 12)
    add("lexicographic-not-numeric", {n: text("c\n" + n) for n in
        ["9", "10", "100", "11", "2", "1e1", "05", "+3", "-1", "1.5", ".5", "0", "20", "3", "1000", "7.0", "07"]}, now=10 ** 6)
    add("duplicate-values", {"1": b"same\nbody", "2": b"same\nbody", "3": b"same\nbody"})
    add("first-and-second-invocation", {"50": b"a\nfirst", "150": b"b\nsecond", "250": b"c\nthird", "1": b"d\nearly"},
        invocations=[(100, "S1"), (200, "S2"), (200, "S3"), (300, "S4"), (300, "S5")])
    add("same-time-twice", {"5": b"x"}, invocations=[(10, "S1"), (10, "S2")])
    add("empty-messages", {"1": b"a\nb"}, messages=[])
    add("empty-messages-no-alarm", {}, messages=[], tools=[])
    add("empty-tools", {"1": b"a\nb"}, tools=[])
    add("zero-byte-file", {"1": b""})
    add("whitespace-only-file", {"1": b"  \n\t \r\n ", "2": text("\u3000\u2028"), "3": b"\n"})
    add("channel-and-message-shapes", {
        "01": b"ch\nl1\nl2\n\nl3", "02": b"no linefeed at all", "03": b"  ch with spaces  \nmsg",
        "04": b"ch\n\n\nmsg", "05": b"\nmsg", "06": b"msg\n", "07": b"\n\n  ch\nm  \n\n", "08": b"ch\n  indented msg  ",
        "09": b"a\nb\nc", "10": b"\t\nch\n\tm\t"})
    add("mixed-newlines", {"01": b"a\r\nb", "02": b"a\rb\rc", "03": b"a\r\n\r\nb", "04": b"ch\r\nm\rn\r\n",
                           "05": b"\r\rx", "06": b"x\r", "07": b"ch\rmsg", "08": b"ch\r\nmsg", "09": b"\r\n",
                           "10": b"a\n\rb", "11": b"a\r\r\nb\n\r\n"})
    add("nul", {"1": b"ch\x00x\nm\x00", "2": b"\x00", "3": b"\x00\n\x00", "4": b" \x00 ", "5": b"c\n\x00\x00msg"})
    add("non-ascii", {"1": text("caf\u00e9\n\u20ac \U0001f600 \u0430\u0431\u0432 \u4e2d\u6587"), "2": text("e\u0301\nx\u0301"),
                      "3": text("\u202eRTL\n\u200fmsg\u200b"), "4": text("\U0010ffff\n\ufffe\uffff\ud7ff\ue000"),
                      "5": text("\ufeffch\nmsg"), "6": text("\u180e\u200b\ufeff\u2060\nz\u180e\u200b\ufeff\u2060")})
    add("expression-shaped", {
        "1": text('(= (transform $m $t) boom)\n!(py-call (os.system "x"))'),
        "2": text('tg\n"unterminated \\ \\" \'q\n('), "3": text('c\n$var &self ; comment {"a": [1, 2]}'),
        "4": text("(shell)\n!(import! &self utils)"), "5": text("channel\n(((("), "6": text('c\n"'),
        "7": text("c\n\\n \\u0000 %s {} {0}")})
    add("unicode-whitespace-edges", {"%03d" % i: text(chr(c) + "ch\nmsg" + chr(c) + chr(c)) for i, c in enumerate(WHITESPACE)})
    add("unicode-whitespace-only-ends", {"%03d" % i: text(chr(c) + "x" + chr(c)) for i, c in enumerate(WHITESPACE)})
    add("not-whitespace-edges", {"%03d" % i: text(chr(c) + "x" + chr(c)) for i, c in enumerate(NOT_WHITESPACE)})
    add("unicode-whitespace-interior", {"%03d" % i: text("a" + chr(c) + "b\nc" + chr(c) + "d") for i, c in enumerate(WHITESPACE)})
    add("long-bodies", {"1": text("ch\n" + "x" * 1_000_000), "2": text("ch\n" + "\u00e9\n" * 200_000),
                        "3": text("  \u3000" * 150_000 + "k\nm" + "\u2003 " * 150_000)})
    add("expression-stamp", {"1": b"a\nb"}, stamp='S "q" (= a b) $x \\ \u00e9 \U0001f600 \x00')
    add("message-variants", {"1": b"a\nb"},
        messages=[{"role": "user", "content": {"k": [1.0, 1e22, 0.1, 1e-7, 5e-324, 1.7976931348623157e308,
                                                     123456789.123456789, 2 ** 100, -(2 ** 100), 10 ** 30]}},
                  [1, 2], "string", None, 7, True, {"role": "assistant", "tool_calls": []}] * 2,
        tools=[None, [], {}, "t", 1.5, {"a": {"b": {"c": [None]}}}])
    add("negative-zero-passthrough", {"1": b"a\nb"}, messages=[{"role": "user", "content": "z", "n": [-0.0, -0.5e-300]}],
        tools=[{"zero": -0.0, "integer": 0, "float": 0.0}])
    add("duplicate-tool-keys", {"1": b"a\nb"}, tools_text='[{"dup": 1, "dup": 2, "n": -0.0}]')
    add("duplicate-object-keys", {"1": b"a\nb"},
        messages_text='[{"role": "user", "content": "x", "k": 1}, {"content": "first", "k": 1, "content": "second"}]',
        group="duplicate-keys")
    bad = {"bad-byte": b"ok\n\x80\nok", "bad-last-byte": b"ok\n\xff", "bad-truncated": b"ok\n\xe2\x82", "bad-start-only": b"\xc3",
           "bad-overlong": b"a\n\xc0\x80", "bad-surrogate": b"a\n\xed\xa0\x80", "bad-too-large": b"a\n\xf4\x90\x80\x80",
           "bad-f5": b"a\n\xf5\x80\x80\x80", "bad-continuation": b"a\n\xe2\x28\xa1", "bad-bom-like": b"\xff\xfeA\x00",
           "bad-after-long-prefix": b"p" * 100000 + b"\xe2\x28", "bad-nul-then-byte": b"a\x00\xff"}
    for name, content in bad.items():
        add(name, {"1": b"c\nfirst", "2": content, "3": b"c\nlast"})
    add("bad-bytes-in-future-file", {"1": b"c\nfirst", "999999": b"\xff\xfe"})
    add("bad-bytes-in-nonnumeric-file", {"1": b"c\nfirst", "notes": b"\xff\xfe"})
    add("numeric-directory-alone", {"5": ("dir", {}, 0o755)})
    add("numeric-directory-among-alarms", {"1": b"a\nb", "5": ("dir", {"inner": b"x"}, 0o755), "9": b"c\nd"})
    add("numeric-directory-first", {"1": ("dir", {}, 0o755), "2": b"a\nb"})
    add("nonnumeric-directory", {"sub": ("dir", {"1": b"x"}, 0o755), "1": b"a\nb"})
    add("symlink-to-directory", {"1": b"a\nb", "2": ("link", "sub"), "sub": ("dir", {}, 0o755)})
    add("dangling-numeric-symlink", {"1": b"a\nb", "2": ("link", "nowhere"), "3": b"c\nd"})
    add("numeric-symlink-to-file", {"1": ("link", "target.txt"), "target.txt": b"viaLink\nbody"})
    add("failure-twice", {"1": b"a\nb", "2": ("dir", {}, 0o755)}, invocations=[(10, "S1"), (10, "S2")])
    if os.geteuid() != 0:
        add("read-refusal", {"1": b"a\nb", "2": ("file", b"secret\nx", 0o000), "3": b"c\nd"})
        add("read-refusal-first", {"1": ("file", b"x", 0o000)})
        add("remove-refusal", {"1": b"a\nb", "2": b"c\nd"}, group="remove-refusal")
        add("remove-refusal-single", {"1": b"a\nb"}, group="remove-refusal")
    big = {}
    for i in range(1, 3001):
        big[str(i * 7 % 100003)] = text("c%d\nbody %d \u00e9" % (i, i))
    for i in range(1500):
        big["z%d" % i] = b"nonnumeric"
        big["%d" % (10 ** 12 + i)] = b"future"
    add("many-due-alarms", big, now=10 ** 6, group="scale")
    return out


def run_case(harness, case, failures, log):
    harness.count += 1
    base = harness.cases / f"{harness.count:04d}-{case['name']}"
    roots = [base / "ref", base / "native"]
    for root in roots:
        root.mkdir(parents=True)
        files = case["files"]
        target = root / "alarms"
        if files == "FILE":
            target.write_bytes(b"i am a file")
        elif files is not None:
            target.mkdir()
            materialize(files, target)
            if case["group"] == "remove-refusal":
                os.chmod(target, 0o555)
    directories = [root / case["subdirectory"] for root in roots]
    messages_text = case["messages_text"] or json.dumps(case["messages"])
    tools_text = case["tools_text"] or json.dumps(case["tools"])
    expected_messages, expected_tools = json.loads(messages_text), json.loads(tools_text)
    problems = []
    for index, (now, stamp) in enumerate(case["invocations"]):
        want = harness.expected(expected_messages, expected_tools, directories[0], now, stamp)
        got, wall, rss = harness.native(messages_text, tools_text, directories[1], now, stamp)
        label = f"{case['name']}#{index}"
        left, right = snapshot(roots[0]), snapshot(roots[1])
        if left != right:
            problems.append({"case": label, "remaining_files": describe_difference(left, right)})
        if want[0] != got[0]:
            problems.append({"case": label, "reference": want[0], "native": got[0],
                             "detail": str(want[1])[:200] if want[0] == "error" else got[1][:300]})
            break
        if want[0] == "error":
            log["error_pairs"].append({"case": label, "python": want[1], "native": got[1][:240]})
            if not got[1].startswith("alarms:"):
                problems.append({"case": label, "error_scope": got[1]})
            continue
        if not strict_equal(got[1], want[1]):
            problems.append({"case": label, "value": "differs", "got": dumps(got[1])[:300], "want": dumps(want[1])[:300]})
        # The reference appends new messages but never rewrites the existing
        # messages or tools. Preserve their raw object occurrence sequences,
        # which a normal dict comparison would silently collapse.
        expected_wire = [wire_value(messages_text) +
                         wire_value(dumps(want[1][0][len(expected_messages):])),
                         wire_value(tools_text)]
        if not strict_equal(wire_value(got[2]), expected_wire):
            problems.append({"case": label, "wire_values": "number kind/sign, order or field occurrences differ"})
        expected_messages, expected_tools = want[1]
        messages_text, tools_text = result_parts(got[2])
    left, right = snapshot(roots[0]), snapshot(roots[1])
    if left != right:
        problems.append({"case": case["name"], "remaining_files": describe_difference(left, right)})
    for root in roots:
        unlock(root)
    remaining = sorted(os.fsdecode(k) for k in right if k.startswith(b"/alarms/") and k.count(b"/") == 2)
    log["cases"].append({"name": case["name"], "group": case["group"], "invocations": len(case["invocations"]),
                         "ok": not problems, "remaining_count": len(remaining), "remaining_first": remaining[:8]})
    failures.extend(problems)
    return not problems


# ------------------------------------------- known Python float differences

DIFFERENCES = [
    ("1_0", "underscore separator, 10.0"), ("1_000", "underscores"), (" 5", "leading space"), ("5 ", "trailing space"),
    ("\t5\n", "surrounding whitespace"), ("-inf", "-infinity, always due"), ("-infinity", "-infinity"),
    ("-1e400", "overflows to -infinity, always due"), ("\u0663", "Arabic-Indic digit three"),
    ("\uff11\uff12", "fullwidth digits"), ("5\u00a0", "NBSP edge"), ("+_1", "invalid in both"), ("1__0", "invalid in both"),
]


def differences(harness, log, failures):
    """float() spellings outside the admitted decimal form: the native leaves the file byte-identical."""
    for number, (name, why) in enumerate(DIFFERENCES):
        files = {name: b"x\nbody", "7": b"c\nplain"}
        base = harness.cases / f"diff-{number:02d}"
        roots = [base / "ref" / "alarms", base / "native" / "alarms"]
        for root in roots:
            root.mkdir(parents=True)
            materialize(files, root)
        want = harness.expected(BASE_MESSAGES, BASE_TOOLS, roots[0], 10 ** 6, STAMP)
        got, _, _ = harness.native(json.dumps(BASE_MESSAGES), json.dumps(BASE_TOOLS), roots[1], 10 ** 6, STAMP)
        left, right = snapshot(roots[0].parent), snapshot(roots[1].parent)
        entry = (right or {}).get(b"/alarms/" + os.fsencode(name))
        native_untouched = got[0] == "ok" and len(got[1][0]) == len(BASE_MESSAGES) + 1 and entry is not None and entry[2] == b"x\nbody"
        python_consumed = want[0] == "ok" and len(want[1][0]) == len(BASE_MESSAGES) + 2
        parity = want[0] == got[0] and left == right and (want[0] != "ok" or strict_equal(got[1], want[1]))
        log["python_float_spelling_differences"].append({
            "name": ascii(name), "why": why, "python_consumed_it": python_consumed,
            "native_left_untouched": native_untouched, "same_as_python": parity})
        if not native_untouched:
            failures.append({"case": f"difference/{ascii(name)}", "declared": "an unsupported spelling is skipped and left byte-identical"})


def listing_refusal(harness, log, failures):
    """An alarm directory that cannot be listed. Python's glob treats it as empty; the native call fails explicitly."""
    rows = []
    if os.geteuid() == 0:
        log["listing_refusal"] = "skipped: running as root"
        return
    for mode in (0o000, 0o111):
        base = harness.cases / f"listing-refusal-{mode:03o}"
        roots = [base / "ref" / "alarms", base / "native" / "alarms"]
        for root in roots:
            root.mkdir(parents=True)
            materialize({"1": b"a\nb"}, root)
            os.chmod(root, mode)
        want = harness.expected(BASE_MESSAGES, BASE_TOOLS, roots[0], 10, STAMP)
        got, _, _ = harness.native(json.dumps(BASE_MESSAGES), json.dumps(BASE_TOOLS), roots[1], 10, STAMP)
        left, right = snapshot(roots[0].parent), snapshot(roots[1].parent)
        for root in roots:
            unlock(root.parent)
        rows.append({"mode": oct(mode), "python": want[0], "python_messages_added": len(want[1][0]) - len(BASE_MESSAGES) if want[0] == "ok" else None,
                     "native": got[0], "native_error": (got[1] or "")[:160] if got[0] == "error" else None, "files_equal": left == right})
        if got[0] != "error" or left != right:
            failures.append({"case": f"listing-refusal/{mode:03o}", "declared": "an unlistable directory is an explicit failure and changes nothing"})
    log["listing_refusal"] = rows


def large_integer_rounding(harness, log, failures):
    """Both clock representations and deadline spellings must match the oracle."""
    rows = []
    boundaries = []
    for power in (52, 53, 54, 63, 64):
        for sign in (1, -1):
            for offset in (-1, 1, 3):
                clock = sign * (2 ** power + offset)
                for now in (clock, float(clock)):
                    boundaries.append((str(clock), now))
                    boundaries.append((str(float(clock + 1)), now))
    for name, now in boundaries:
        base = harness.cases / f"round-{len(rows)}"
        roots = [base / "ref" / "alarms", base / "native" / "alarms"]
        for root in roots:
            root.mkdir(parents=True)
            materialize({name: b"a\nb"}, root)
        want = harness.expected(BASE_MESSAGES, BASE_TOOLS, roots[0], now, STAMP)
        got, _, _ = harness.native(json.dumps(BASE_MESSAGES), json.dumps(BASE_TOOLS), roots[1], now, STAMP)
        ok = (want[0] == got[0] == "ok" and strict_equal(want[1], got[1])
              and snapshot(roots[0]) == snapshot(roots[1]))
        row = {"name": name, "now": now, "clock_kind": type(now).__name__, "ok": ok,
               "python_due": len(want[1][0]) > len(BASE_MESSAGES) if want[0] == "ok" else None,
               "native_due": len(got[1][0]) > len(BASE_MESSAGES) if got[0] == "ok" else None}
        rows.append(row)
        if not ok:
            failures.append({"case": "deadline-boundary", **row})
    log["large_integer_rounding"] = rows


# ---------------------------------------- public transform and loop checks

STAMP_RE = re.compile(r"Step (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d): ")


def public_transform(harness, failures, log):
    """transform with the environment's directory and the real clock; stamps are checked against Python's strftime."""
    os.environ["TZ"] = ZONE
    time.tzset()
    files = {"0": b"first\nnow past", "1": b"second", "100": b"third\nfine", "99999999999": b"future\nstays", "text": b"x"}
    results = []
    plans = (("default-directory-absent", harness.work / "memory" / "alarms", {}, None),
             ("default-directory", harness.work / "memory" / "alarms", {}, files),
             ("ITER_ALARMS_DIR", harness.evidence / "custom-alarms", {"ITER_ALARMS_DIR": str(harness.evidence / "custom-alarms")}, files),
             ("ITER_ALARMS_DIR-relative", harness.work / "rel" / "dir", {"ITER_ALARMS_DIR": "rel/dir/"}, files))
    for label, directory, env, spec in plans:
        reference_dir = harness.evidence / f"public-ref-{label}"
        reference_dir.mkdir()
        materialize(spec or {}, reference_dir)
        if spec is None:
            assert not (harness.work / "memory").exists()
            assert "ITER_ALARMS_DIR" not in harness.environment(env)
        else:
            directory.mkdir(parents=True, exist_ok=True)
            materialize(spec, directory)
        before = int(time.time())
        answer, wall, rss = harness.worker("transform", json.dumps([BASE_MESSAGES, BASE_TOOLS]), module=MODULE, env=env)
        after = int(time.time())
        window = {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) for t in range(before, after + 1)}
        want = harness.expected(BASE_MESSAGES, BASE_TOOLS, reference_dir, time.time(), "@@")
        ok, stamps = answer["ok"] is True, []
        if ok:
            normalized = copy.deepcopy(answer["result"])
            for message in normalized[0][len(BASE_MESSAGES):]:
                found = STAMP_RE.match(message["content"])
                stamps.append(found.group(1) if found else None)
                message["content"] = STAMP_RE.sub("Step @@: ", message["content"], count=1)
            ok = strict_equal(normalized, want[1]) and all(s in window for s in stamps)
        left, right = snapshot(reference_dir), snapshot(directory)
        ok = ok and left == right and directory.is_dir()
        results.append({"case": label, "ok": ok, "stamps": stamps, "zone": ZONE, "latency_s": round(wall, 3)})
        if not ok:
            failures.append({"case": "public/" + label, "answer": str(answer)[:400]})
    answer, _, _ = harness.worker("transform", json.dumps([BASE_MESSAGES, BASE_TOOLS]), module=MODULE, env={"ITER_ALARMS_DIR": ""})
    results.append({"case": "empty ITER_ALARMS_DIR fails explicitly", "ok": answer["ok"] is False, "answer": str(answer)[:200]})
    if answer["ok"] is not False:
        failures.append({"case": "public/empty-directory-setting"})
    answer, _, _ = harness.worker("__description__", "[]", module=MODULE)
    results.append({"case": "description", "ok": answer.get("result") == harness.reference.DESCRIPTION})
    if answer.get("result") != harness.reference.DESCRIPTION:
        failures.append({"case": "description", "got": str(answer)})
    log["public"] = results


def loop_check(harness, failures, log):
    """The loop's own apply_transformation runs the module in a worker, with the environment inherited."""
    directory = harness.evidence / "loop-alarms"
    directory.mkdir()
    (directory / "0").write_bytes(b"irc\nwake up")
    program = harness.work / "loop.metta"
    program.write_text('!(import! &self utils)\n'
                       '!(apply_transformation ((system "S ") (user "u")) (JsonArray ()) 20)\n')
    done = subprocess.run(harness.command([str(program)]), cwd=harness.work,
                          env=harness.environment({"ITER_ALARMS_DIR": str(directory)}), stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=120)
    pattern = re.compile(r'^true\n\(\(\(system "S "\) \(user "u"\) \(user "Step \d{4}-\d\d-\d\d \d\d:\d\d:\d\d: \[irc\] \[ALARM\] wake up"\)\) \(JsonArray \(\)\) ""\)\n$')
    ok = bool(pattern.match(done.stdout)) and list(directory.iterdir()) == []
    log["loop"] = {"ok": ok, "stdout": done.stdout[:300]}
    if not ok:
        failures.append({"case": "loop/apply_transformation", "stdout": done.stdout[:300], "stderr": done.stderr[:300]})
    # Exercise both parent request serialization and child result serialization.
    # A helper-only run would not notice the parent erasing signed zero first.
    messages = '[{"n": -0.0, "n": 1.0, "raw": [0, -0.0]}]'
    tools = '[{"x": -0.0, "x": 2}]'
    program.write_text('!(import! &self utils)\n'
        f'!(let (ok $wire) (invoke_dynamic "{HELPER}" "helper" '
        f'((json:parse {json.dumps(messages)}) (json:parse {json.dumps(tools)}) '
        f'{json.dumps(str(directory))} 10 "S") (no-kwargs) 20) (system:write stdout $wire))\n')
    done = subprocess.run(harness.command(["--no-results", str(program)]), cwd=harness.work,
                          env=harness.environment(), stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=30)
    try:
        same = strict_equal(wire_value(done.stdout), [wire_value(messages), wire_value(tools)])
    except (ValueError, TypeError):
        same = False
    ok = done.returncode == 0 and not done.stderr and same
    log["parent_worker_wire"] = {"ok": ok, "returncode": done.returncode,
                                 "stdout": done.stdout, "stderr": done.stderr}
    if not ok:
        failures.append({"case": "loop/parent-worker-wire", "stdout": done.stdout, "stderr": done.stderr})


# -------------------------------------------- durability and interruption

def _pgrp(pid):
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[2])
    except (OSError, ValueError, IndexError):
        return None


def witnesses(harness, log):
    """Executable witnesses of the durable-delivery gap. They report; nothing here is atomic or exactly-once."""
    rows = {}
    directory = harness.evidence / "witness-fail" / "alarms"
    directory.mkdir(parents=True)
    materialize({"1": b"a\nconsumed before the failure", "2": ("dir", {}, 0o755)}, directory)
    got, _, _ = harness.native(json.dumps(BASE_MESSAGES), json.dumps(BASE_TOOLS), directory, 10, STAMP)
    rows["failure-after-removal"] = {"answer": got[0], "error": got[1][:200], "files_after": sorted(p.name for p in directory.iterdir()),
                                     "alarm_message_delivered": False}
    assert got[0] == "error" and got[1].startswith("alarms:")
    assert rows["failure-after-removal"]["files_after"] == ["2"]
    directory = harness.evidence / "witness-result" / "alarms"
    directory.mkdir(parents=True)
    materialize({"1": b"a\nconsumed, result unwritable"}, directory)
    lost = harness.evidence / "no-such-directory" / "result.json"
    arguments = "[%s, %s, %s, 10, %s]" % (json.dumps(BASE_MESSAGES), json.dumps(BASE_TOOLS), json.dumps(str(directory)), json.dumps(STAMP))
    process = harness.worker("helper", arguments, result=lost, wait=False)
    stdout, stderr = process.communicate(timeout=harness.worker_timeout)
    rows["result-not-accepted"] = {"launcher_output": stdout[:120], "result_file_exists": lost.exists(),
                                   "files_after": sorted(p.name for p in directory.iterdir())}
    assert json.loads(stdout)["status"] == 256, "result publication failure must exit 1"
    assert not lost.exists() and rows["result-not-accepted"]["files_after"] == []
    attempts = []
    for count in (4000, 12000, 30000):
        directory = harness.evidence / f"witness-kill-{count}" / "alarms"
        directory.mkdir(parents=True)
        materialize({str(i): text("c\nbody %d" % i) for i in range(1, count + 1)}, directory)
        result = harness.evidence / f"witness-kill-{count}" / "result.json"
        arguments = "[%s, %s, %s, 10000000, %s]" % (json.dumps(BASE_MESSAGES), json.dumps(BASE_TOOLS), json.dumps(str(directory)), json.dumps(STAMP))
        process = harness.worker("helper", arguments, result=result, wait=False)
        killed = False
        deadline = time.monotonic() + harness.worker_timeout
        while process.poll() is None and time.monotonic() < deadline:
            if len(os.listdir(directory)) < count - 20:
                os.killpg(process.pid, signal.SIGKILL)
                killed = True
                break
            time.sleep(0.002)
        timed_out = process.poll() is None and not killed
        if timed_out:
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)
        names = os.listdir(directory)
        whole = all((directory / n).read_bytes() == text("c\nbody %s" % n) for n in names)
        leftover = [p for p in os.listdir("/proc") if p.isdigit() and _pgrp(int(p)) == process.pid]
        attempts.append({"alarms": count, "killed_while_running": killed, "timed_out": timed_out,
                         "remaining": len(names), "removed": count - len(names),
                         "result_file_exists": result.exists(), "remaining_files_whole_and_unchanged": whole,
                         "processes_left_in_group": leftover})
        if killed:
            break
    assert any(row["killed_while_running"] and row["removed"] > 0 and not row["result_file_exists"] for row in attempts)
    assert all(row["remaining_files_whole_and_unchanged"] and not row["processes_left_in_group"] for row in attempts)
    rows["sigkill-during-handling"] = attempts
    log["delivery_witnesses"] = rows


# ----------------------------------------------------- the metta test files

def metta_suites(harness, log, failures):
    rows = []
    for suite in ("tests/alarms.metta", "tests/text_helper.metta", "tests/worker_wire.metta"):
        for name in (suite, "tests/fixtures/transcript/malformed.bin", "transformations/append_last_transcript_lines.metta"):
            (harness.work / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(harness.root / name, harness.work / name)
        done = subprocess.run(harness.command([suite]), cwd=harness.work,
                              env=harness.environment({"ALARMS_TEST_DIR": str(harness.evidence / "metta-fixtures")}),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace", timeout=300)
        failed, passed = done.stdout.count("\u274c"), done.stdout.count("\u2705")
        declared = (harness.root / suite).read_text().count("\n!(test")
        rows.append({"suite": suite, "returncode": done.returncode, "passed": passed, "failed": failed, "declared_tests": declared})
        if done.returncode or failed or passed != declared:
            failures.append({"case": suite, "returncode": done.returncode, "failed": failed, "passed": passed, "declared": declared})
    log["metta_suites"] = rows


# -------------------------------------------------------- negative controls

MUTATIONS = {
    "equal-time-not-due": ("(>= $now $target)", "(> $now $target)"),
    "numeric-order": ("(<= (str:compare $lower $upper) 0)", "(<= (str:string->number $lower) (str:string->number $upper))"),
    "no-removal": ("(case (fs:remove $path)", "(case (let $kept $path ())"),
    "no-strip": ("(text:strip (text:universal-newlines $text))", "(text:universal-newlines $text)"),
    "no-newline-normalization": ("(text:strip (text:universal-newlines $text))", "(text:strip $text)"),
    "malformed-accepted": ("(str:utf8-new strict)", "(str:utf8-new ignore)"),
    "unsorted": ("(alarms:handle-all (alarms:sort $names)", "(alarms:handle-all $names"),
    "no-directory-creation": ("(case (fs:make-directories $directory (alarms:mode))", "(case (let $skipped $directory ())"),
    "arbitrary-limit": ("(alarms:handle-all (alarms:sort $names)", "(alarms:handle-all (list:take 100 (alarms:sort $names))"),
    "split-at-blank-line": ("(str:find $content \"\\n\")", "(str:find $content \"\\n\\n\")"),
    "rounded-integer-clock": ("(> $now-floor $target-floor)", "(>= $now $target)"),
    "exact-integer-deadline": ("(alarms:at-or-after $now (* 1.0 $target))", "(alarms:at-or-after $now $target)"),
}


def negative_controls(args, root, log):
    source = (root / MODULE).read_text()
    rows = []
    for name, (old, new) in MUTATIONS.items():
        assert source.count(old) == 1, f"mutation {name} does not apply exactly once"
        harness = Harness(args, evidence=args.evidence.absolute() / f"negative-{name}", module_text=source.replace(old, new))
        failures, scratch = [], {"cases": [], "spelling_differences": [], "error_pairs": []}
        detected = None
        for case in cases():
            if not run_case(harness, case, failures, scratch):
                detected = case["name"]
                break
        rows.append({"mutation": name, "detected": detected is not None, "first_failing_case": detected,
                     "failure": failures[0] if failures else None})
    harness = Harness(args, evidence=args.evidence.absolute() / "negative-signed-zero-wire")
    path = harness.work / "utils.metta"
    source = path.read_text()
    assert source.count("(json:stringify-python $value)") == 1
    path.write_text(source.replace("(json:stringify-python $value)", "(json:stringify $value)"))
    failures, scratch = [], {"cases": [], "spelling_differences": [], "error_pairs": []}
    case = next(case for case in cases() if case["name"] == "negative-zero-passthrough")
    detected = not run_case(harness, case, failures, scratch)
    rows.append({"mutation": "signed-zero-wire", "detected": detected,
                 "first_failing_case": case["name"] if detected else None,
                 "failure": failures[0] if failures else None})
    log["negative_controls"] = rows
    return rows


# ------------------------------------------------------------ measurements

def measure(harness, args, log):
    rows = []
    scenarios = [("no-alarms", 0, 0, 0), ("one-alarm", 1, 0, 0), ("100-alarms", 100, 0, 0), ("1000-alarms", 1000, 0, 0),
                 ("5000-alarms", 5000, 0, 0), ("20000-alarms", 20000, 0, 0), ("50000-alarms", 50000, 0, 0), ("1000-alarms-and-20000-nonnumeric", 1000, 20000, 0),
                 ("20000-future-only", 0, 0, 20000), ("one-4MiB-alarm", 1, 0, "big")]
    for label, due, nonnumeric, future in scenarios:
        walls, peaks, python_wall = [], [], None
        for run in range(args.repeats):
            directory = harness.evidence / "measure" / f"{label}-{run}"
            reference_dir = harness.evidence / "measure" / f"{label}-{run}-ref"
            files = {str(i): text("channel\nalarm body %d with some text \u00e9" % i) for i in range(1, due + 1)}
            if future == "big":
                files = {"1": text("chan\n" + "0123456789abcdef" * 262144)}
            elif future:
                files.update({str(10 ** 12 + i): b"future" for i in range(future)})
            files.update({"x%d" % i: b"nonnumeric" for i in range(nonnumeric)})
            for root in (directory, reference_dir):
                root.mkdir(parents=True)
                materialize(files, root)
            started = time.monotonic()
            want = harness.expected(BASE_MESSAGES, BASE_TOOLS, reference_dir, 10 ** 9, STAMP)
            python_wall = time.monotonic() - started
            got, wall, rss = harness.native(json.dumps(BASE_MESSAGES), json.dumps(BASE_TOOLS), directory, 10 ** 9, STAMP)
            assert got[0] == "ok" and strict_equal(got[1], want[1]) and snapshot(directory) == snapshot(reference_dir), \
                f"measured answer differs in {label}"
            walls.append(wall)
            peaks.append(rss)
            shutil.rmtree(directory)
            shutil.rmtree(reference_dir)
        rows.append({"scenario": label, "first_run_s": round(walls[0], 3),
                     "warm_median_s": round(statistics.median(walls[1:] or walls), 3), "all_runs_s": [round(w, 3) for w in walls],
                     "peak_rss_mib_max": round(max(peaks) / 1024, 1), "python_in_process_s": round(python_wall, 4)})
    log["measurements"] = rows
    resource = []
    shutil.copyfile(harness.root / "tests/fixtures/alarms_resource.metta", harness.work / "tests/fixtures/alarms_resource.metta") \
        if (harness.work / "tests/fixtures").exists() else None
    for label, rounds, count, fault in (("success-300-rounds", 300, 10, "0"), ("success-4500-rounds", 4500, 10, "0"),
                                        ("failing-directory-each-round", 300, 10, "1"), ("larger-rounds", 20, 1000, "0")):
        directory = harness.evidence / "measure" / f"resource-{label}"
        (harness.work / "tests/fixtures").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(harness.root / "tests/fixtures/alarms_resource.metta", harness.work / "tests/fixtures/alarms_resource.metta")
        started = time.monotonic()
        done = subprocess.run(harness.command(["tests/fixtures/alarms_resource.metta"]), cwd=harness.work,
                              env=harness.environment({"ALARMS_RESOURCE_DIR": str(directory), "ALARMS_RESOURCE_ROUNDS": str(rounds),
                                                       "ALARMS_RESOURCE_COUNT": str(count), "ALARMS_RESOURCE_FAULT": fault}),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=1800)
        match = re.search(r"resident-kb (\d+) (\d+) descriptors (\d+) (\d+)", done.stdout)
        resource.append({"scenario": label, "rounds": rounds, "alarms_per_round": count, "returncode": done.returncode,
                         "seconds": round(time.monotonic() - started, 2),
                         "rss_kib_before_after": list(map(int, match.groups()[:2])) if match else None,
                         "descriptors_before_after": list(map(int, match.groups()[2:])) if match else None})
    log["retained_resources"] = resource


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True, help="a copy of the pinned reference transformation")
    parser.add_argument("--evidence", type=Path, required=True, help="a new directory")
    parser.add_argument("--address-space-kib", type=int, default=0, help="optional ulimit -v for each worker")
    parser.add_argument("--worker-timeout", type=float, default=120, help="seconds before a test worker is stopped")
    parser.add_argument("--negative-controls", action="store_true", help="also check that mutated modules are caught")
    parser.add_argument("--measure", action="store_true", help="also measure latency and memory")
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    assert locale.getencoding().lower().replace("-", "") == "utf8" or sys.flags.utf8_mode, \
        "the reference reads text in the locale encoding: run with PYTHONUTF8=1"
    os.environ["TZ"] = ZONE
    time.tzset()
    comparator_controls()
    harness = Harness(args)
    failures = []
    log = {"cases": [], "spelling_differences": [], "error_pairs": [], "python_float_spelling_differences": [],
           "pinned_reference_sha256": PINNED_SHA256}
    for case in cases():
        run_case(harness, case, failures, log)
    differences(harness, log, failures)
    listing_refusal(harness, log, failures)
    large_integer_rounding(harness, log, failures)
    public_transform(harness, failures, log)
    loop_check(harness, failures, log)
    metta_suites(harness, log, failures)
    witnesses(harness, log)
    summary = {"cases": len(log["cases"]), "cases_ok": sum(c["ok"] for c in log["cases"]), "worker_runs": harness.runs,
               "failures": failures, "spelling_differences_in_spelling_group": sum(d["group"] == "spelling" for d in log["spelling_differences"]),
               "spelling_differences_elsewhere": [d["case"] for d in log["spelling_differences"] if d["group"] != "spelling"]}
    log["summary"] = summary
    if args.negative_controls:
        rows = negative_controls(args, harness.root, log)
        summary["negative_controls_undetected"] = [r["mutation"] for r in rows if not r["detected"]]
    if args.measure:
        measure(harness, args, log)
    (harness.evidence / "qualification.json").write_text(json.dumps(log, indent=2, default=str) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "failures"}, indent=2))
    for failure in failures[:8]:
        print("FAILED", json.dumps(failure, default=str)[:700])
    ok = not failures and not summary.get("negative_controls_undetected")
    print("PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
