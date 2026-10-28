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

## Transcript context

`transformations/append_last_transcript_lines.metta` is the native port of
Iter's `append_last_transcript_lines.py`: it appends `transcript:` and the last
six lines of the transcript to the first message, right-stripped as Python's
`str.rstrip` does. It reads `ITER_TRANSCRIPT_PATH`, by default `transcript.txt`
in the Iter workspace (the Python version's fixed home path is the one
deliberate difference). `transcript-tail:transform-path $messages $tools $path`
takes the path directly.

- Messages and tools stay unchanged when there are no messages, the first is
  not an object, or the file is missing, unreadable, a directory or not
  well-formed UTF-8. A String `content` (or a missing one) gains
  `"\n\n" + block`; a parts array gains one text part; other content stays.
- CR, LF and CRLF end lines, as in Python text files; a final unterminated line
  counts, a trailing LF adds none. Other Unicode line boundaries do not split.
- The file is read in chunks through a strict streaming UTF-8 decoder. Only the
  last six lines and the line being read are kept; discarded lines do not
  accumulate. A very long current line can still be large. The whole file is
  decoded, so malformed bytes anywhere
  leave the messages unchanged, as with Python's strict read. The read size
  (`transcript-tail:chunk-bytes`) is an allocation choice, not a limit.
- Transcript text is data and is never parsed or evaluated.

`cetta --lang petta tests/transcript_tail.metta < /dev/null` runs the unit and
descriptor-cleanup checks. `tests/transcript_tail_local.py --help` describes the
comparison with the Python reference through fresh workers, which needs a copy
of that reference and optionally measures long prefixes.

## Alarms

`transformations/alarms.metta` is the native port of Iter's `alarms.py`: each
file in the alarm directory whose name is a decimal epoch time that has come
becomes a user message `Step STAMP: [CHANNEL] [ALARM] MESSAGE`, and the file is
removed. The directory is `ITER_ALARMS_DIR`, by default `memory/alarms` in the
Iter workspace (the Python version's fixed module-relative path is the one
deliberate difference); the time and the `Step` timestamp are native wall time
and local time. `alarms:transform-at $messages $tools $directory $now $stamp`
takes the directory, the time and the timestamp text directly.

- The directory is created when absent. Entries are handled in file-name (byte)
  order, not numeric order, and the time is read once: an alarm is due when
  `now >= target`, so an equal time is due. There is no limit on the number of
  entries or the size of a message.
- Admitted names are whole finite decimal numbers: an optional sign, digits with
  an optional point, and an optional exponent. Any other name, and any file not
  yet due, is left untouched. Python's `float` also takes underscores, outer
  whitespace, `inf`/`nan` and non-ASCII digits; those names are left alone here,
  where Python would use some of them.
- A file is decoded as strict UTF-8 with CR, LF and CRLF read as LF and
  stripped at both ends as Python's `str.strip` does (`lib_text.metta`). With a
  LF in the stripped text the first line is the channel and the rest the
  message; otherwise the channel is `terminal`. The text is data and is never
  parsed or evaluated.
- Existing messages and tools, unknown fields and duplicates pass through. The
  messages must be a JSON array, which is checked before any file is touched.
- A fault making or reading the directory, or reading, decoding or removing a
  file, is an explicit `(Error ...)` failure of the call, as the Python
  exception was. Alarms handled before the fault stay removed and the failing
  file stays. An unreadable directory is a failure here; Python's `glob` would
  see it as empty.
- **Not durable delivery.** A file is removed before the caller has accepted the
  messages, as in the reference, so a failure or an interruption after the
  removal loses that alarm's message. Nothing here is atomic or exactly-once;
  `tests/alarms_local.py` keeps executable witnesses.

`cetta --lang petta tests/alarms.metta < /dev/null` and `... tests/text_helper.metta`
run the unit and descriptor-cleanup checks (the output must hold no failed
check). `tests/alarms_local.py --help` describes the comparison with the pinned
Python reference through fresh workers, the negative controls and the
measurements.

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
