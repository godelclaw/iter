#!/usr/bin/env python3
"""Partial terminal lines survive idle workers and unrelated worker cancellation."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pty
import signal
import subprocess
import termios
import time
import tty


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence.absolute()
    evidence.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    program = evidence / "partial.metta"
    program.write_text('''!(import! &self utils)
!(println! (FIRST (invoke_dynamic "channels/terminal.metta" "receive" () (no-kwargs) 1)))
!(fs:write (str:concat (get-state &terminal-runtime) "/_incomplete.json") "{")
!(println! (CANCELLED (invoke_dynamic "tools/shell.metta" "run" ()
                                     (JsonObject ((JsonPair "cmd" "sleep 3"))) 0.1)))
!(sleep 1.5)
!(println! (SECOND (invoke_dynamic "channels/terminal.metta" "receive" () (no-kwargs) 1)))
!(println! (THIRD (invoke_dynamic "channels/terminal.metta" "receive" () (no-kwargs) 1)))
!(println! (PENDING (fs:is-file (str:concat (get-state &terminal-runtime) "/_incomplete.json"))))
!(fs:write (str:concat (get-state &terminal-runtime) "/error") "fixture-reader-fault")
!(println! (FAULT (invoke_dynamic "channels/terminal.metta" "receive" () (no-kwargs) 1)))
''')
    results = []
    for mode in ("pipe", "raw-pty"):
        master = slave = None
        if mode == "raw-pty":
            master, slave = pty.openpty()
            tty.setraw(slave)
        def acquire_terminal():
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        with (evidence / (mode + ".log")).open("wb") as log:
            child = subprocess.Popen([str(args.binary.resolve()), "--lang", "petta", str(program)],
                cwd=root, stdin=slave if slave is not None else subprocess.PIPE,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                preexec_fn=acquire_terminal if slave is not None else None)
            if slave is not None:
                os.close(slave)
            try:
                def feed(data):
                    if master is not None:
                        os.write(master, data)
                    else:
                        child.stdin.write(data)
                        child.stdin.flush()
                time.sleep(0.1)
                feed(b"par")
                time.sleep(1.0)
                data = "tial\né😀".encode()
                feed(data[:-2])
                time.sleep(0.1)
                feed(data[-2:] + b"\n")
                if master is None:
                    feed(b"last")
                    child.stdin.close()
                child.wait(timeout=10)
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                if master is not None:
                    os.close(master)
        output = (evidence / (mode + ".log")).read_text()
        assert child.returncode == 0, output
        assert '(FIRST (ok ""))' in output, output
        assert '(CANCELLED (error "TIMEOUT after 0.1s"))' in output, output
        expected = 'partial\né😀' + ('\nlast' if mode == "pipe" else '')
        assert '(SECOND (ok "' + expected + '"))' in output, output
        assert '(THIRD (ok ""))' in output, output
        assert '(PENDING true)' in output, output
        assert '(FAULT (error "terminal: fixture-reader-fault"))' in output, output
        results.append({"mode": mode, "passed": True, "exit": child.returncode,
            "split_utf8": True, "worker_cancellation": True, "eof_fragment": mode == "pipe"})
    (evidence / "outcome.json").write_text(json.dumps(results, indent=2) + "\n")
    print("PASS: pipe and raw PTY preserve partial lines, UTF-8 and EOF across workers")


if __name__ == "__main__":
    main()
