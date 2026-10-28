# Iter on CeTTa

This branch runs Patrick Hammer's MeTTa version of Iter (`iter.metta` and
`utils.metta` from the `metta` branch) on
[CeTTa](https://github.com/godelclaw/CeTTa) without SWI-Prolog. The loop,
model client, worker protocol and native modules use CeTTa's json, str, fs,
system, proc and io libraries. Python guest modules are optional. The loop
is Patrick's, with its helpers ported to native MeTTa.

- **The model call.** `iter_llm.metta` uses CeTTa's native HTTP client. It
  sends the request that `utils.py` sent through the OpenAI SDK. A failed call
  answers an `(Error what why)` value, which ends the step as an exception did.
- **Tools, channels and transformations** are `.metta` modules.
  `iter_worker.metta` runs each call in a CeTTa process of its own, which a
  timeout kills.
- **Tools:** send, nop, shell and metta. The optional Python tool is disabled
  as `tools/_python.py`; rename it to `tools/python.py` to enable it.
- **Channels:** the terminal, IRC, and `_example.metta`, a channel that runs its own
  daemon.
  One MeTTa reader owns terminal input for the loop's lifetime. Channel workers
  receive complete lines from it, so a worker timeout cannot lose a partial line.
- **Python modules.** A tool, channel or transformation may also be a `.py`
  file in Python Iter's format, so Python tools from the Python version work
  unchanged. The worker runs them in its own embedded Python through `py-call`
  and `iter_python.py`, which needs a CeTTa build with Python (`BUILD=python`).

The module formats, which the agent uses to extend itself, are in
`reprogramming.txt`.

## Running

Build [CeTTa](https://github.com/godelclaw/CeTTa/tree/cetta-ours) from its
checkout with the native HTTP features:

```sh
make BUILD=core ENABLE_HTTP=1 ENABLE_LIB_PROLOG=0 ENABLE_SQLITE=0 \
     ENABLE_DURABLE=0 ENABLE_JSON_GSLT=1 ENABLE_MANAGED_SYMBOLS=1
```

This places
`cetta.proc-scope` beside the binary. It needs `system:executable`,
`system:read-line` with a timeout, the process library's `inherit` and `null`
standard streams and its `run` and `detached` lifetimes, and `fs:lock`'s
`exclusive-nowait`. Python modules also need a Python build (`BUILD=python`)
whose `py-call` returns Python text as strings. From the Iter checkout,
configure the model as upstream does:

```sh
export LLM_MODEL=... BASE_URL=... AI_API_KEY=...
cetta --lang petta iter.metta
```

The other settings are at the top of `iter.metta`. Module calls exchange their
requests and results through `.runtime/` in the working directory.

## Tests

From the repository root:

```sh
cetta --lang petta tests/worker.metta < /dev/null
```

That full worker suite includes optional Python interoperability. The loop
can also be checked with a core HTTP build, without Python or Prolog:

```sh
python3 tests/loop_local.py --binary /path/to/cetta --evidence /new/evidence/directory
```

The fixture is a local HTTP server; no model credentials or external service
are needed. Add `--python-tools` to also test the optional Python tool.

The worker regression accepts `ITER_TEST_REFUSED_URL` for a harness-owned HTTP
refusal target. `tests/irc_local.py --help` describes the separate isolated IRC
fixture harness; it uses only numeric loopback TCP/TLS, not a public server.

## IRC

`channels/irc.metta` uses native fs/proc/json/str/system/io through `utils.metta`.
Fresh `receive`/`send` workers exchange files with one detached daemon, which
holds `daemon.lock` while connecting and serving. `control.lock` coordinates
startup, retry state and receive activity. PID metadata is diagnostic only:
neither liveness nor signalling uses a stale PID. Workers wait only for daemon
startup (bounded short lock/startup retries), not its connect completion.

Settings are environment variables, inherited by the daemon:

| Setting | Default |
| --- | --- |
| `ITER_IRC_HOST` | unset: the channel stays idle; set a host, optionally prefixed by `tcp://` or `tls://` |
| `ITER_IRC_SCHEME` | `tcp`, when the host has no scheme prefix |
| `ITER_IRC_PORT` | `6667`, or **6697 for TLS**, unless explicitly supplied |
| `ITER_IRC_NICK` / `ITER_IRC_CHANNEL` | `Iter` / `##metta` |
| `ITER_IRC_RUNTIME` | `channels/.runtime/irc`; must be private and trusted |
| `ITER_IRC_IDLE_MINUTES` | **5**, positive numeric minutes |
| `ITER_IRC_CA_FILE` | **absent**: native default trust store |

TLS requires the native TCP provider's optional `((ca-file PATH))` argument.
An explicit CA file replaces the trust store for that connection; peer and
hostname verification remain enabled. Empty, missing or unreadable explicit
paths are configuration errors, not default-store fallbacks. Invalid certificate
contents fail the native connect. No SSL/CURL environment trust workaround is
used. The local tests use an isolated fixture CA and matching numeric IP.

Connect timeout is **15000 ms**, with a daemon-only owned wait of 16000 ms.
Owned pending receives survive 100 ms idle waits. PRIVMSG has a token bucket
of **four frames**, refilling one token every **two seconds**, capacity four.
PING/PONG and registration/control writes do not consume these tokens.

Reconnect state persists in `retry.json`, under locks: reserve the attempt
before connecting; failure delays are **2, 4, 8, 16, 32, 64, 128, 180 seconds**,
then capped at 180. Failure measures its delay from session end; successful
`001` resets the count/deadline to zero. A failed daemon exits; a later worker
may start another only after the deadline. There is no automatic uncertain-item
replay. Retry/activity clocks are monotonic and intended for the same host boot;
after a reboot, archive/reconcile the private runtime deliberately rather than
editing deadlines as resend authority.

Only `receive` atomically refreshes `receive.ns`. Incoming wire traffic and
`send` do not. A runtime's first call sets a fixed initial grace origin if no
receive has occurred; sends cannot extend it. The daemon exits/releases its
connection after the configured no-receive interval; sends still queue locally
but do not restart an expired session. A subsequent receive reactivates it,
subject to retry delay. Isolated tests shorten this to **0.02 minutes**; normal
timing is not otherwise shortened. Receive can race an already-decided idle
exit; a subsequent receive starts the replacement, not a duplicate connector.

Spool names have a **20-digit zero-padded nanosecond prefix plus UUID**. A
publication lock and persisted last stamp clamp clock ties/regressions to
`last+1`; the prefix is thus a logical nanosecond stamp derived from the native
clock. Sequential publications cannot reverse. Concurrent calls are ordered by
publication-lock acquisition, **not call arrival**, and UUID breaks any residual
name tie. There is no distributed/global request-arrival order. Hidden temporary
files become ready by same-filesystem atomic rename; inbox consumers claim by
rename. This is visibility atomicity, **not fsync/crash durability**.

Outbound originals move from `outbox` (pending) to `uncertain` **before any
write**, then to `written` only after all local socket writes complete. Retained
uncertain originals may have any prefix written and are **never automatically
replayed**, including after restart. Queued/written status is **not server
delivery**. The `001` note reports a local JOIN write, not a JOIN acknowledgement.

NICK/USER, `001`/JOIN, PING/PONG and up to eight suffixed nick retries are
supported. Inbound messages begin with explicit `channel` or `private`, plus
JSON-quoted target/sender metadata; body text is inert, never evaluated. Outgoing
CR/LF splits into messages; every complete frame, prefix and CRLF included, is
at most **512 UTF-8 bytes**, splitting only at scalar boundaries. Original
NUL-bearing fresh-worker requests are rejected before publication. Content has
a 65536-byte ceiling; configuration is ASCII host/nick/channel only. Empty lines
are omitted. The inherited native receive bound is 8192 bytes. Hostile spool
access/symlinks, automatic hidden-claim recovery, spool retention, full IRC
semantics, public-server/production qualification and exactly-once delivery are
not supplied by these finite local tests.
