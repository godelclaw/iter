# Iter on CeTTa without Python

This branch runs Patrick Hammer's MeTTa version of Iter (`iter.metta` and
`utils.metta` from the `metta` branch) on
[CeTTa](https://github.com/godelclaw/CeTTa), with no Python process. The loop
is unchanged. What was Python is now MeTTa:

- **The model call.** `iter_llm.metta` uses CeTTa's native HTTP client. It
  sends the same request that `utils.py` sent through the OpenAI SDK.
- **Tools, channels and transformations** are `.metta` modules.
  `iter_worker.metta` runs each call in a CeTTa process of its own. On one
  machine, a call took about 40 ms, against about 260 ms for the Python worker,
  which imports the SDK on every call.
- **Ported tools:** send, nop, shell and metta. The python tool is not ported.
- **Channels:** the terminal, and `_example.metta`, a channel that runs its own
  daemon.

The module formats, which the agent uses to extend itself, are in
`reprogramming.txt`.

## Running

Use a CeTTa build with HTTP (`make ENABLE_HTTP=1`) and the SWI-Prolog library,
which is linked when `pkg-config` finds `swipl`. Configure the model as
upstream does:

```sh
export LLM_MODEL=... BASE_URL=... AI_API_KEY=...
cetta --lang petta iter.metta
```

The other settings are at the top of `iter.metta`.

## Tests

From the repository root:

```sh
cetta --lang petta tests/worker.metta < /dev/null
```
