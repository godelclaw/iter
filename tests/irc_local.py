#!/usr/bin/env python3
"""Isolated numeric-loopback IRC qualification; Python is fixture/harness only.

Run from the repository root. Supply an immutable bundle, its expected hashes,
an external NEW evidence directory and a child address-space limit in KiB.
Every evaluator uses an isolated source snapshot and allowlisted environment.
"""
import argparse
import concurrent.futures
import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import resource
import signal
import socket
import ssl
import subprocess
import threading
import time
import traceback


def wait_for(predicate, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        answer = predicate()
        if answer:
            return answer
        time.sleep(0.02)
    raise AssertionError("bounded wait expired")


class Peer:
    """One owned listener; exact application bytes and line-completion times."""
    def __init__(self, evidence, name, tls=None, delay=0):
        self.e, self.name, self.tls, self.delay = evidence, name, tls, delay
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(4)
        self.sock.settimeout(0.05)
        self.stop = threading.Event()
        self.commands = queue.Queue()
        self.lines, self.events, self.errors = [], [], []
        self.connections = 0
        self.close_on_message = False
        self.thread = threading.Thread(target=self.serve)
        self.thread.start()

    def write(self, conn, generation, data):
        conn.sendall(data)
        self.events.append([time.monotonic(), generation, "server-write", data.hex()])
        with (self.e / f"{self.name}-server-wire-{generation}.bin").open("ab") as f:
            f.write(data)

    def serve(self):
        end = time.monotonic() + 180
        try:
            while not self.stop.is_set() and time.monotonic() < end:
                try:
                    conn, address = self.sock.accept()
                except socket.timeout:
                    continue
                assert address[0] == "127.0.0.1"
                self.connections += 1
                generation = self.connections
                self.events.append([time.monotonic(), generation, "accepted", ""])
                conn.settimeout(3)
                if self.delay:
                    self.stop.wait(self.delay)
                if self.tls:
                    conn = self.tls.wrap_socket(conn, server_side=True)
                with conn:
                    conn.settimeout(0.03)
                    buf, retried, user, welcomed = b"", False, False, False
                    while not self.stop.is_set() and time.monotonic() < end:
                        try:
                            command = self.commands.get_nowait()
                        except queue.Empty:
                            command = None
                        if command == "close":
                            break
                        if isinstance(command, bytes):
                            self.write(conn, generation, command)
                        try:
                            data = conn.recv(8192)
                        except socket.timeout:
                            continue
                        if not data:
                            break
                        self.events.append([time.monotonic(), generation, "client-read", data.hex()])
                        with (self.e / f"{self.name}-wire-{generation}.bin").open("ab") as f:
                            f.write(data)
                        buf += data
                        closing = False
                        while b"\r\n" in buf:
                            line, buf = buf.split(b"\r\n", 1)
                            self.lines.append([time.monotonic(), generation, line + b"\r\n"])
                            if line == b"NICK TestIter":
                                self.write(conn, generation, b":fixture 433 * TestIter :in use\r\n")
                            if line == b"NICK TestIter_1":
                                retried = True
                            if line == b"USER TestIter 0 * :Iter Agent":
                                user = True
                            if retried and user and not welcomed:
                                self.write(conn, generation, b":fixture 001 TestIter_1 :welcome\r\n")
                                welcomed = True
                            if line == b"JOIN ##test":
                                self.write(conn, generation, b"PING :fixture-token\r\n:alice!u@local PRIVMSG ##test :!(system:exit 99) inert\r\n:bob!u@local PRIVMSG TestIter_1 :[IRC channel] not channel traffic\r\n")
                            if line.startswith(b"PRIVMSG ") and self.close_on_message:
                                self.close_on_message = False
                                closing = True
                        if closing:
                            break
                self.events.append([time.monotonic(), generation, "closed", ""])
        except (BrokenPipeError, ConnectionResetError, ssl.SSLEOFError):
            self.events.append([time.monotonic(), self.connections, "peer-ended", ""])
        except Exception:
            self.errors.append(traceback.format_exc())
        finally:
            self.sock.close()

    def messages(self):
        return [x for x in self.lines if x[2].startswith(b"PRIVMSG ")]

    def finish(self):
        self.stop.set()
        self.thread.join(4)
        (self.e / f"{self.name}-peer.json").write_text(json.dumps(dict(
            connections=self.connections, events=self.events, errors=self.errors,
            lines=[[t, g, b.hex()] for t, g, b in self.lines], alive=self.thread.is_alive()), indent=2)+"\n")
        assert not self.thread.is_alive() and not self.errors


class Run:
    def __init__(self, args):
        self.args = args
        self.root = Path.cwd().resolve()
        self.e = Path(args.evidence).absolute()
        assert not self.e.is_relative_to(self.root)
        self.e.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.bundle = Path(args.bundle).absolute()
        self.exe = self.bundle / "cetta"
        self.verify_bundle("start")
        self.source = self.e / "source"
        self.source.mkdir()
        names = subprocess.check_output(["git", "ls-files", "-z"]).decode().strip("\0").split("\0")
        names += [str(p.relative_to(self.root)) for p in self.root.glob("tests/irc*") if p.is_file()]
        if (self.root / "channels/irc.metta").exists():
            names.append("channels/irc.metta")
        self.sources = {}
        for name in sorted(set(names)):
            data = (self.root / name).read_bytes()
            dest = self.source / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            self.sources[name] = hashlib.sha256(data).hexdigest()
        (self.e / "sources.json").write_text(json.dumps(self.sources, indent=2) + "\n")
        self.env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        for key, name in (("HOME", "home"), ("TMPDIR", "tmp"), ("XDG_CONFIG_HOME", "config"),
                          ("XDG_CACHE_HOME", "cache"), ("XDG_DATA_HOME", "data"),
                          ("XDG_STATE_HOME", "state"), ("XDG_RUNTIME_DIR", "xdg-runtime")):
            path = self.e / name
            path.mkdir(mode=0o700)
            self.env[key] = str(path)
        self.env.update(ITER_IRC_HOST="127.0.0.1", ITER_IRC_NICK="TestIter",
                        ITER_IRC_CHANNEL="##test", ITER_IRC_RUNTIME=str(self.e / "irc"))
        self.counter = 0
        self.requests = 0
        self.guard = threading.Lock()
        self.direct = set()
        self.processes = {}
        self.reaped = {}
        self.monitor_errors = []
        self.stop = threading.Event()
        self.libc = ctypes.CDLL(None, use_errno=True)
        assert self.libc.prctl(36, 1, 0, 0, 0) == 0  # subreap OUR descendants
        self.monitor = threading.Thread(target=self.observe)
        self.monitor.start()

    def verify_bundle(self, label):
        root = self.bundle
        for parent in (root, *root.parents):
            assert not parent.is_symlink()
        raw = (root / "SHA256SUMS").read_bytes()
        assert hashlib.sha256(raw).hexdigest() == self.args.manifest_sha
        expected = {}
        for line in raw.decode("ascii").splitlines():
            digest, name = line.split("  ", 1)
            assert name.startswith("./") and ".." not in Path(name).parts
            name = name[2:]
            assert name not in expected and len(digest) == 64
            expected[name] = digest
        actual = {}
        for p in sorted(root.rglob("*")):
            assert not p.is_symlink() and p.resolve().is_relative_to(root)
            assert p.is_dir() or p.is_file()
            if p.is_file():
                st = p.stat()
                actual[str(p.relative_to(root))] = [hashlib.sha256(p.read_bytes()).hexdigest(),
                    st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns,
                    st.st_mode, st.st_uid, st.st_gid, st.st_nlink]
        assert set(actual) == set(expected) | {"SHA256SUMS"}
        for name, digest in expected.items():
            assert actual[name][0] == digest
        assert expected["cetta"] == self.args.engine_sha
        if label == "start":
            self.bundle_identity = actual
        else:
            assert actual == self.bundle_identity
        (self.e / f"bundle-{label}.json").write_text(json.dumps(actual, indent=2) + "\n")
        (self.e / f"SHA256SUMS.{label}").write_bytes(raw)

    def observe(self):
        try:
            while not self.stop.is_set():
                pending, seen = [os.getpid()], set()
                while pending:
                    pid = pending.pop()
                    if pid in seen:
                        continue
                    seen.add(pid)
                    try:
                        for task in Path(f"/proc/{pid}/task").glob("*/children"):
                            pending.extend(map(int, task.read_text().split()))
                        if pid != os.getpid():
                            with self.guard:
                                if pid not in self.processes:
                                    st = Path(f"/proc/{pid}/stat").read_text()
                                    limits = Path(f"/proc/{pid}/limits").read_text()
                                    cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
                                    exe = os.readlink(f"/proc/{pid}/exe")
                                    self.processes[pid] = dict(stat=st, limits=limits,
                                        cmdline=cmd.hex(), exe=exe, observed=time.monotonic())
                                    if exe in (str(self.exe), str(self.bundle / "cetta.proc-scope")):
                                        line = next(x for x in limits.splitlines() if x.startswith("Max address space"))
                                        assert line.split()[-3:-1] == [str(self.args.as_kib * 1024)] * 2
                                # Only an adopted child, never an active Popen's child.
                                fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                                if int(fields[1]) == os.getpid() and pid not in self.direct:
                                    got, status = os.waitpid(pid, os.WNOHANG)
                                    if got:
                                        self.reaped[got] = os.waitstatus_to_exitcode(status)
                    except (FileNotFoundError, ProcessLookupError, ChildProcessError):
                        pass
                self.stop.wait(0.005)
        except Exception:
            self.monitor_errors.append(traceback.format_exc())

    def child(self, label, files, extra=None, timeout=12, program=None):
        with self.guard:
            self.counter += 1
            ident = f"{self.counter:03}-{label}"
            argv = program or [str(self.exe), "--lang", "petta", *map(str, files)]
            env = self.env | (extra or {})
            def setup():
                limit = self.args.as_kib * 1024
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            start = time.monotonic()
            p = subprocess.Popen(argv, cwd=self.source, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, preexec_fn=setup)
            self.direct.add(p.pid)
            limits = Path(f"/proc/{p.pid}/limits").read_text()
        timed_out = False
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(p.pid, signal.SIGKILL)  # positively owned, unreaped direct child
            out, err = p.communicate()
        for suffix, data in (("stdout", out), ("stderr", err), ("limits", limits.encode())):
            (self.e / f"{ident}.{suffix}").write_bytes(data)
        (self.e / f"{ident}.json").write_text(json.dumps(dict(argv=argv, env=env,
            cwd=str(self.source), pid=p.pid, exit=p.returncode, timed_out=timed_out,
            elapsed=time.monotonic()-start, rlimit_as=[self.args.as_kib*1024]*2), indent=2) + "\n")
        assert not timed_out and p.returncode == 0, (ident, p.returncode, out, err)
        if program is None:
            assert not err, (ident, err)
        return out.decode()

    def certificates(self):
        self.child("certificate", [], program=["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(self.e / "key.pem"), "-out", str(self.e / "ca.pem"), "-days", "1",
            "-subj", "/CN=Isolated IRC Fixture", "-addext", "subjectAltName=IP:127.0.0.1"])
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.e / "ca.pem", self.e / "key.pem")
        return ctx

    def primitives(self):
        self.verify_bundle("pre-primitives")
        ctx = self.certificates()
        (self.e / "invalid-ca.pem").write_text("not a certificate\n")
        for label, mode in (("untrusted", "default"), ("trusted", "explicit"), ("invalid-ca", "explicit")):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            sock.settimeout(4)
            port = sock.getsockname()[1]
            observations = []
            def serve():
                try:
                    conn, peer = sock.accept()
                    assert peer[0] == "127.0.0.1"
                    conn.settimeout(3)
                    with ctx.wrap_socket(conn, server_side=True) as secure:
                        time.sleep(0.25)
                        wire = b"PING :primitive\r\n"
                        secure.sendall(wire)
                        observations.append([time.monotonic(), "write", wire.hex()])
                        data = secure.recv(4096)
                        observations.append([time.monotonic(), "read", data.hex()])
                        if data:
                            observations.append([time.monotonic(), "client-close", secure.recv(4096).hex()])
                except Exception:
                    observations.append([time.monotonic(), "error", traceback.format_exc()])
                finally:
                    sock.close()
            thread = threading.Thread(target=serve)
            thread.start()
            try:
                out = self.child(label, ["tests/irc_primitives.metta"],
                    {"IRC_TEST_URL": f"tls://127.0.0.1:{port}", "IRC_TEST_MODE": mode,
                     "IRC_TEST_CA": str(self.e / ("invalid-ca.pem" if label == "invalid-ca" else "ca.pem"))}, timeout=18)
            finally:
                thread.join(5)
                (self.e / f"{label}-server.json").write_text(json.dumps(observations, indent=2)+"\n")
                assert not thread.is_alive()
            (self.e / f"{label}-reductions.txt").write_text(out)
            if mode == "default":
                assert out.count("(completed ") == 1 and "(tcp:failed 1 60 " in out and "tcp:connected" not in out, out
            elif label == "invalid-ca":
                assert out.count("(completed ") == 1 and "(tcp:failed 1 77 " in out and "tcp:connected" not in out, out
            else:
                expected = ["(completed (io:event 1 (tcp:connected 1)))", "(read-idle (io:idle))",
                    "(sent (io:event 3 (tcp:sent 1)))", "(read-ready (io:ready (2)))",
                    '(received (io:event 2 (tcp:received 1 "PING :primitive")))',
                    "(consumed (io:unknown 2))", "(pending-idle (io:idle))",
                    "(cancelled ())", "(cancel-consumed (io:unknown 4))",
                    '(closed (io:event 5 (tcp:closed 1 "")))']
                assert out.splitlines() == expected + ["true", "true", "true"], out
                assert any(x[1:] == ["read", b"PONG :primitive\r\n".hex()] for x in observations), observations
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            out = self.child("malformed-options", ["tests/irc_primitives.metta"],
                {"IRC_TEST_MODE": "malformed", "IRC_TEST_URL": f"tls://127.0.0.1:{reserved.getsockname()[1]}"})
            assert out.count("(refused ((Error ") == 1 and "io:pending" not in out, out
        self.verify_bundle("post-primitives")

    def regression(self):
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            out = self.child("worker-regression", ["tests/worker.metta"],
                {"ITER_TEST_REFUSED_URL": f"http://127.0.0.1:{reserved.getsockname()[1]}/v1"}, timeout=40)
        assert out.count("✅") == 53 and "❌" not in out, out

    def smoke(self):
        out = self.child("units", ["tests/irc_units.metta"],
                         {"ITER_IRC_RUNTIME": str(self.e / "unit-runtime")})
        assert out.count("✅") == 26 and "❌" not in out, out
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            out = self.child("refused-connect-state", ["tests/irc_failure.metta"],
                             {"ITER_IRC_PORT": str(reserved.getsockname()[1])})
            assert out.count("✅") == 5 and "❌" not in out, out

    def worker(self, function, args=(), extra=None, module="channels/irc.metta"):
        with self.guard:
            self.requests += 1
            ident = self.requests
        request, result = self.e / f"request-{ident}.json", self.e / f"result-{ident}.json"
        request.write_text(json.dumps(dict(module=module, function=function,
            args=args, kwargs={}, result=str(result)), ensure_ascii=False)+"\n")
        self.child("worker-"+function, ["iter_worker.metta", request], extra)
        answer = json.loads(result.read_bytes())
        assert set(answer) in ({"ok", "result"}, {"ok", "error"}), answer
        return answer

    def daemon(self, runtime):
        # Metadata is only diagnostic; prove process ownership and executable.
        def candidate():
            if not (runtime / "pid").exists():
                return None
            raw = (runtime / "pid").read_text()
            if not raw.isdigit():
                return None
            pid = int(raw)
            rec = self.processes.get(pid)
            return pid if rec and b"--irc-daemon" in bytes.fromhex(rec["cmdline"]) else None
        pid = wait_for(candidate)
        rec = self.processes[pid]
        assert rec["exe"] == str(self.exe) and b"--irc-daemon" in bytes.fromhex(rec["cmdline"])
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        assert int(fields[1]) == os.getpid(), "daemon must be adopted by THIS harness"
        return pid

    def exited(self, pid, seconds=5):
        wait_for(lambda: pid in self.reaped, seconds)
        assert self.reaped[pid] == 0 and not Path(f"/proc/{pid}").exists(), (pid, self.reaped)

    def nul_witness(self, env, nul):
        observation = self.e / "nul-channel-argument.json"
        inner_request, inner_result = self.e / "nul-inner-request.json", self.e / "nul-inner-result.json"
        witnessed = self.worker("irc-test:send", ["bad\0QUIT :injected"],
            env | {"IRC_TEST_OBSERVATION":str(observation), "IRC_TEST_REQUEST":str(inner_request),
                   "IRC_TEST_RESULT":str(inner_result)}, module="tests/irc_worker.metta")
        assert witnessed == {"ok":False,"error":"IRC-boundary: "+nul["error"]}, witnessed
        original = "bad\0QUIT :injected"
        assert json.loads(inner_request.read_bytes())["args"] == [original]
        assert json.loads(inner_result.read_bytes()) == nul
        observed = json.loads(observation.read_bytes())
        assert observed["content"] == original and observed["bytes"] == len(original.encode()) == 18
        assert observed["codepoints"] == list(map(ord, original))
        assert observed["sha256"] == hashlib.sha256(original.encode()).hexdigest()
        limits = next(x for x in observed["limits"].splitlines() if x.startswith("Max address space"))
        assert limits.split()[-3:-1] == [str(self.args.as_kib*1024)]*2

    def nul_only(self):
        runtime = self.e / "nul-only-runtime"
        with socket.socket() as refused:
            refused.bind(("127.0.0.1", 0))
            env = {"ITER_IRC_RUNTIME":str(runtime), "ITER_IRC_PORT":str(refused.getsockname()[1])}
            nul = self.worker("send", ["bad\0QUIT :injected"], env)
            assert nul == {"ok":False,"error":"IRC: Content must be valid UTF-8, no NUL, at most 65536 bytes"}
            self.nul_witness(env, nul)
            assert not runtime.exists()  # rejection precedes even directory creation

    @staticmethod
    def ready(runtime, directory):
        return sorted(p for p in (runtime / directory).glob("*") if len(p.name) == 53 and p.name[20] == "-")

    def ordering(self):
        runtime = self.e / "ordering"
        runtime.mkdir()
        # Helper-seeded retry delay prevents network activation in this spool test.
        (runtime / "retry.json").write_text(json.dumps([1, time.monotonic_ns()+60000000000, 2]))
        env = {"ITER_IRC_RUNTIME": str(runtime), "ITER_IRC_PORT": "1"}
        ids = []
        for n in range(4):
            answer = self.worker("send", [f"sequence-{n}"], env)
            assert answer["ok"], answer
            ids.append(answer["result"].split(": ", 1)[1].split(";", 1)[0])
        assert ids == sorted(ids) and len(set(ids)) == 4
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            answers = list(pool.map(lambda n: self.worker("send", [f"parallel-{n}"], env), range(2)))
        assert all(a["ok"] for a in answers), answers
        payloads = [json.loads(p.read_text())[0] for p in self.ready(runtime, "outbox")]
        assert payloads[:4] == [f"sequence-{n}" for n in range(4)]
        assert set(payloads[4:]) == {"parallel-0", "parallel-1"}
        assert len({p.name for p in self.ready(runtime, "outbox")}) == 6
        for n in range(4):
            assert self.worker("irc:note", [f"inbox-{n}"], env)["ok"]
        assert self.worker("receive", extra=env) == {"ok": True, "result": "\n".join(f"inbox-{n}" for n in range(4))}
        (self.e / "ordering.json").write_text(json.dumps(dict(ids=ids, sorted_payloads=payloads,
            concurrent_call_order="unspecified; observed publication lock order only"), indent=2)+"\n")
        assert not self.worker("send", ["x"*65537], env)["ok"]
        assert len(self.ready(runtime, "outbox")) == 6
        broken = self.e / "publication-failure"
        broken.mkdir()
        (broken / "order.ns").write_text("not-a-number")
        failed = self.worker("send", ["must-not-claim-queued"], {"ITER_IRC_RUNTIME": str(broken)})
        assert not failed["ok"] and "Spool publication failed" in failed["error"], failed
        assert not self.ready(broken, "outbox") and not (broken / "attempt.ns").exists()
        # No network attempt has occurred: these are pending originals, never
        # uncertain effects. Release the helper-seeded delay in this owned fixture.
        assert not (runtime / "attempt.ns").exists() and not self.ready(runtime, "uncertain")
        (runtime / "retry.json").write_text("[0,0,0]")
        peer = Peer(self.e, "fifo")
        try:
            assert self.worker("receive", extra=env | {"ITER_IRC_PORT":str(peer.port)})["ok"]
            pid = self.daemon(runtime)
            wait_for(lambda: len(peer.messages()) == len(payloads), 7)
            assert [x[2] for x in peer.messages()] == [b"PRIVMSG ##test :"+p.encode()+b"\r\n" for p in payloads]
            peer.commands.put("close")
            self.exited(pid)
        finally:
            peer.finish()

    def lifecycle(self):
        peer = Peer(self.e, "tcp")
        runtime = self.e / "irc"
        for name in ("inbox", "outbox", "uncertain", "written"):
            (runtime / name).mkdir(parents=True, exist_ok=True)
        (runtime / "pid").write_text(str(os.getpid()))  # must never be signalled/trusted
        (runtime / "inbox/_partial").write_text("must-not-be-received")
        (runtime / "outbox/_partial").write_text("QUIT :must-not-be-sent")
        env = {"ITER_IRC_PORT": str(peer.port)}
        prefix = b"PRIVMSG ##test :"
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                received = list(pool.map(lambda _: self.worker("receive", extra=env), range(2)))
            assert all(x["ok"] for x in received), received
            pid = self.daemon(runtime)
            wait_for(lambda: any(x[2] == b"PONG :fixture-token\r\n" for x in peer.lines))
            received.append(self.worker("receive", extra=env))
            text = "\n".join(x.get("result", "") for x in received)
            assert '[IRC channel target="##test" from="alice"] !(system:exit 99) inert' in text, text
            assert '[IRC private target="TestIter_1" from="bob"] [IRC channel] not channel traffic' in text, text
            assert "001 received; JOIN written locally" in text and "must-not-be-received" not in text
            assert peer.connections == 1
            time.sleep(0.25)
            assert pid not in self.reaped  # an idle owned read is not EOF
            assert self.worker("send", ["\n".join(f"paced-{n}" for n in range(7))], env)["ok"]
            wait_for(lambda: len(peer.messages()) >= 4)
            ping_time = time.monotonic()
            peer.commands.put(b"PING :during-pacing\r\n")
            pong = wait_for(lambda: next((x for x in peer.lines if x[2] == b"PONG :during-pacing\r\n"), None))
            assert pong[0] - ping_time < 0.6
            wait_for(lambda: len(peer.messages()) == 7, 9)
            frames = peer.messages()
            assert [x[2] for x in frames] == [prefix+f"paced-{n}".encode()+b"\r\n" for n in range(7)]
            times = [x[0] for x in frames]
            assert times[3]-times[0] < 1.3 and times[4]-times[0] >= 1.9, times
            assert all(times[n]-times[n-1] >= 1.85 for n in (5, 6)), times
            (self.e / "pacing.json").write_text(json.dumps(dict(wire_times=times,
                relative=[t-times[0] for t in times], ping_to_pong=pong[0]-ping_time), indent=2)+"\n")
            content = "A"*(510-len(prefix)) + "\r\n" + "😀"*123+"éx" + "\rQUIT :not-command\nlast"
            expected = []
            for line in content.replace("\r", "\n").split("\n"):
                chunk = ""
                for c in line:
                    if len((chunk+c).encode()) > 510-len(prefix):
                        expected.append(prefix+chunk.encode()+b"\r\n")
                        chunk = ""
                    chunk += c
                if chunk:
                    expected.append(prefix+chunk.encode()+b"\r\n")
            assert self.worker("send", [content], env)["ok"]
            wait_for(lambda: len(peer.messages()) == 7+len(expected), 12)
            assert [x[2] for x in peer.messages()[7:]] == expected
            assert max(map(len, expected)) == 512
            for wire in expected:
                wire.decode("utf-8", errors="strict")
                assert len(wire) <= 512 and wire.count(b"\r\n") == 1 and b"\0" not in wire
            wait_for(lambda: not self.ready(runtime, "outbox") and not self.ready(runtime, "uncertain"))
            before = {str(p):p.read_bytes() for d in ("outbox", "uncertain", "written") for p in (runtime/d).iterdir()}
            nul = self.worker("send", ["bad\0QUIT :injected"], env)
            assert nul == {"ok": False, "error": "IRC: Content must be valid UTF-8, no NUL, at most 65536 bytes"}, nul
            self.nul_witness(env, nul)
            after = {str(p):p.read_bytes() for d in ("outbox", "uncertain", "written") for p in (runtime/d).iterdir()}
            assert before == after
            (self.e / "nul-spool-before.json").write_text(json.dumps({k:v.hex() for k,v in before.items()}, indent=2)+"\n")
            (self.e / "nul-spool-after.json").write_text(json.dumps({k:v.hex() for k,v in after.items()}, indent=2)+"\n")
            count = len(peer.messages())
            for key, value in (("ITER_IRC_HOST", "127.0.0.1\r\nNICK evil"), ("ITER_IRC_NICK", "x\nUSER evil"),
                               ("ITER_IRC_CHANNEL", "##test\r\nJOIN #evil"), ("ITER_IRC_PORT", "0"),
                               ("ITER_IRC_PORT", "65536"), ("ITER_IRC_PORT", "no"),
                               ("ITER_IRC_CA_FILE", ""), ("ITER_IRC_IDLE_MINUTES", "0")):
                assert not self.worker("receive", extra=env | {key:value})["ok"], (key,value)
            assert len(peer.messages()) == count
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.worker, "receive", (), env), pool.submit(self.worker, "send", ["concurrent"], env)]
                assert all(f.result()["ok"] for f in futures)
            wait_for(lambda: any(x[2] == prefix+b"concurrent\r\n" for x in peer.messages()), 4)
            wait_for(lambda: not self.ready(runtime, "outbox") and not self.ready(runtime, "uncertain"))
            assert peer.connections == 1 and (runtime / "outbox/_partial").exists()
            assert all(b"must-not-be-sent" not in x[2] and b"injected" not in x[2] and b"bad\r\n" not in x[2] for x in peer.lines)
            seeded = runtime / "uncertain" / ("0"*20+"-"+"e"*32)
            seeded.write_text(json.dumps(["NEVER-REPLAY"]))
            peer.close_on_message = True
            original = [f"uncertain-{n}" for n in range(20)]
            assert self.worker("send", ["\n".join(original)], env)["ok"]
            self.exited(pid, 5)
            outstanding = [p for p in self.ready(runtime, "uncertain") if p != seeded]
            assert len(outstanding) == 1 and json.loads(outstanding[0].read_text()) == original
            sent_count = sum(b"uncertain-" in x[2] for x in peer.messages())
            assert 0 < sent_count < 20
            assert self.worker("send", ["pending-after-restart"], env)["ok"]
            before_connections = peer.connections
            assert self.worker("receive", extra=env)["ok"]
            assert peer.connections == before_connections
            until = json.loads((runtime / "retry.json").read_text())[1]
            time.sleep(max(0, (until-time.monotonic_ns())/1e9)+0.05)
            assert self.worker("receive", extra=env)["ok"]
            pid2 = self.daemon(runtime)
            wait_for(lambda: any(x[2] == prefix+b"pending-after-restart\r\n" for x in peer.messages()))
            time.sleep(0.25)
            assert sum(b"uncertain-" in x[2] for x in peer.messages()) == sent_count
            assert outstanding[0].exists() and seeded.exists()
            assert not any(b"NEVER-REPLAY" in x[2] for x in peer.messages())
            assert json.loads((runtime / "retry.json").read_text()) == [0, 0, 0]  # 001 reset
            wait_for(lambda: not self.ready(runtime, "outbox"))
            bad = runtime / "outbox" / ("9"*20+"-"+"f"*32)
            bad.write_text("not JSON")
            self.exited(pid2)
            assert (runtime / "uncertain" / bad.name).read_text() == "not JSON"
            assert not bad.exists()
        finally:
            peer.finish()

    def backoff(self):
        runtime = self.e / "backoff"
        env = {"ITER_IRC_RUNTIME": str(runtime)}
        attempts, delays = [], []
        with socket.socket() as refused:
            refused.bind(("127.0.0.1", 0))
            env["ITER_IRC_PORT"] = str(refused.getsockname()[1])
            for generation in range(2):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    answers = list(pool.map(lambda _: self.worker("receive", extra=env), range(2)))
                assert all(a["ok"] for a in answers), answers
                wait_for(lambda: (runtime / "ended.txt").exists())
                wait_for(lambda: json.loads((runtime / "retry.json").read_text())[0] == generation+1)
                retry = json.loads((runtime / "retry.json").read_text())
                attempt = int((runtime / "attempt.ns").read_text())
                attempts.append(attempt)
                delays.append(retry)
                for _ in range(3):
                    assert self.worker("receive", extra=env)["ok"]
                assert int((runtime / "attempt.ns").read_text()) == attempt
                if generation == 0:
                    time.sleep(max(0, (retry[1]-time.monotonic_ns())/1e9)+0.05)
                    (runtime / "ended.txt").unlink()
            assert delays[0][2] == 2 and delays[1][2] == 4
            assert attempts[1]-attempts[0] >= 2000000000
        (self.e / "backoff-timed.json").write_text(json.dumps(dict(attempt_ns=attempts, retry_states=delays,
            distinction="real 2-second elapsed integration; growth to 180 cap separately uses helper clock"), indent=2)+"\n")

    def idle(self):
        peer = Peer(self.e, "idle")
        runtime = self.e / "idle-runtime"
        env = {"ITER_IRC_RUNTIME": str(runtime), "ITER_IRC_PORT": str(peer.port), "ITER_IRC_IDLE_MINUTES": "0.02"}
        try:
            assert self.worker("receive", extra=env)["ok"]
            pid = self.daemon(runtime)
            start = time.monotonic()
            while time.monotonic()-start < 2:
                peer.commands.put(b"PING :active\r\n")
                assert self.worker("receive", extra=env)["ok"]
                time.sleep(0.3)
                assert pid not in self.reaped
            last = (runtime / "receive.ns").read_bytes()
            last_ns = int(last)
            while pid not in self.reaped and time.monotonic_ns()-last_ns < 3000000000:
                peer.commands.put(b"PING :idle-wire\r\n:alice!u@local PRIVMSG ##test :wire does not refresh receive\r\n")
                assert self.worker("send", ["send-does-not-refresh"], env)["ok"]
                time.sleep(0.2)
                assert (runtime / "receive.ns").read_bytes() == last
            self.exited(pid)
            elapsed = time.monotonic()-last_ns/1e9
            assert 1.1 <= elapsed < 2.2 and peer.connections == 1, elapsed
            assert (runtime / "ended.txt").read_text() == "(idle-exit)"
            (self.e / "idle-timed.json").write_text(json.dumps(dict(active_receive_seconds=time.monotonic()-start-elapsed,
                no_receive_until_observed_exit=elapsed, configured_minutes=0.02,
                incoming_and_send_do_not_refresh=True), indent=2)+"\n")
        finally:
            peer.finish()

    def tls_channel(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.e / "ca.pem", self.e / "key.pem")
        peer = Peer(self.e, "tls-channel", tls=ctx, delay=1.0)
        runtime = self.e / "tls-runtime"
        env = {"ITER_IRC_RUNTIME": str(runtime), "ITER_IRC_HOST": "tls://127.0.0.1",
               "ITER_IRC_PORT": str(peer.port), "ITER_IRC_CA_FILE": str(self.e / "ca.pem")}
        try:
            start = time.monotonic()
            assert self.worker("receive", extra=env)["ok"]
            bounded = time.monotonic()-start
            assert bounded < 0.8, bounded
            pid = self.daemon(runtime)
            start = time.monotonic()
            assert self.worker("receive", extra=env)["ok"]
            assert time.monotonic()-start < 0.5
            wait_for(lambda: any(x[2] == b"PONG :fixture-token\r\n" for x in peer.lines))
            accepted = next(x[0] for x in peer.events if x[2] == "accepted")
            assert peer.lines[0][0]-accepted >= 0.9  # real delayed TLS handshake
            assert self.worker("send", ["tls-message"], env)["ok"]
            wait_for(lambda: any(x[2] == b"PRIVMSG ##test :tls-message\r\n" for x in peer.messages()))
            assert self.worker("irc:port", extra={"ITER_IRC_HOST":"tls://127.0.0.1"}) == {"ok":True,"result":"6697"}
            assert self.worker("irc:port", extra={"ITER_IRC_SCHEME":"tls"}) == {"ok":True,"result":"6697"}
            count = peer.connections
            assert not self.worker("receive", extra=env | {"ITER_IRC_CA_FILE":str(self.e/"missing-ca")})["ok"]
            assert peer.connections == count
            (self.e / "connect-bounded.json").write_text(json.dumps(dict(worker_elapsed=bounded,
                fixture_delayed_handshake=peer.lines[0][0]-accepted, connect_parameter_ms=15000,
                distinction="actual 1-second local handshake delay, NOT an actual 15-second timeout"), indent=2)+"\n")
            peer.commands.put("close")
            self.exited(pid)
        finally:
            peer.finish()

    def integration(self):
        self.verify_bundle("pre-integration")
        self.ordering()
        self.lifecycle()
        self.backoff()
        self.idle()
        self.tls_channel()
        self.verify_bundle("post-integration")

    def finish(self):
        # Test daemons normally leave by EOF/idle. Emergency cleanup only uses
        # positively identified adopted children and pidfds, never PID metadata.
        time.sleep(0.1)
        self.stop.set()
        self.monitor.join(2)
        cleanup = []
        for pid, rec in self.processes.items():
            if pid in self.direct or pid in self.reaped:
                continue
            try:
                fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                old = rec["stat"].rsplit(")", 1)[1].split()
                if fields[19] != old[19] or int(fields[1]) != os.getpid():
                    continue
                fd = self.libc.pidfd_open(pid, 0)
                assert fd >= 0
                try:
                    got, status = os.waitpid(pid, os.WNOHANG)
                    if not got:
                        assert self.libc.pidfd_send_signal(fd, signal.SIGTERM, None, 0) == 0
                        cleanup.append(pid)
                        def reap():
                            got, status = os.waitpid(pid, os.WNOHANG)
                            if got:
                                self.reaped[pid] = os.waitstatus_to_exitcode(status)
                                return True
                        wait_for(reap, 3)
                    else:
                        self.reaped[got] = os.waitstatus_to_exitcode(status)
                finally:
                    os.close(fd)
            except (FileNotFoundError, ProcessLookupError, ChildProcessError):
                pass
        (self.e / "processes.json").write_text(json.dumps(self.processes, indent=2)+"\n")
        (self.e / "reaped.json").write_text(json.dumps(self.reaped, indent=2)+"\n")
        (self.e / "emergency-cleanup.json").write_text(json.dumps(cleanup)+"\n")
        (self.e / "monitor-errors.json").write_text(json.dumps(self.monitor_errors)+"\n")
        assert not self.monitor_errors
        for task in Path(f"/proc/{os.getpid()}/task").glob("*/children"):
            assert not task.read_text().strip(), "owned child remains after cleanup"
        for pid in self.processes:
            assert not Path(f"/proc/{pid}").exists(), (pid, "owned descendant remains")
        for name, digest in self.sources.items():
            assert hashlib.sha256((self.root / name).read_bytes()).hexdigest() == digest, name
        self.verify_bundle("end")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "engine-sha", "manifest-sha", "evidence"):
        parser.add_argument("--"+name, required=True)
    parser.add_argument("--as-kib", type=int, required=True)
    parser.add_argument("--prerequisites", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--nul-only", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    run = Run(args)
    passed = False
    try:
        if args.nul_only:
            run.nul_only()
        elif args.smoke:
            run.smoke()
        else:
            run.primitives()
            run.regression()
            if not args.prerequisites:
                run.smoke()
                run.integration()
        passed = True
    except Exception:
        (run.e / "failure.txt").write_text(traceback.format_exc())
        raise
    finally:
        run.finish()
        (run.e / "outcome.json").write_text(json.dumps({"passed": passed})+"\n")
    print("PASS: native NUL boundary" if args.nul_only else "PASS: native smoke" if args.smoke else "PASS: native prerequisites" if args.prerequisites else "PASS: native IRC local qualification")


if __name__ == "__main__":
    main()
