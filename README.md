# Iter on CeTTa

This branch runs Patrick Hammer's MeTTa version of Iter (`iter.metta` and
`utils.metta` from the `metta` branch) on
[CeTTa](https://github.com/godelclaw/CeTTa) alone, with no Python and no
SWI-Prolog: it uses CeTTa's native json, str, fs, system, proc and io
libraries. The loop is Patrick's.

- **The model call.** `iter_llm.metta` uses CeTTa's native HTTP client. It
  sends the request that `utils.py` sent through the OpenAI SDK. A failed call
  answers an `(Error what why)` value, which ends the step as an exception did.
- **Tools, channels and transformations** are `.metta` modules.
  `iter_worker.metta` runs each call in a CeTTa process of its own, which a
  timeout kills.
- **Tools:** send, nop, shell and metta. The python tool is not ported.
- **Channels:** the terminal, and `_example.metta`, a channel that runs its own
  daemon.

The module formats, which the agent uses to extend itself, are in
`reprogramming.txt`.

## Running

Use a Linux build of CeTTa with HTTP (`make ENABLE_HTTP=1`), which places
`cetta.proc-scope` beside the binary. It needs `system:executable`,
`system:read-line` with a timeout, the process library's `inherit` and `null`
standard streams and its `run` and `detached` lifetimes, and `fs:lock`'s
`exclusive-nowait`. Configure the model as upstream does:

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
