# Live Smoke Tests

This page documents small live checks that validate the running OMADS GUI against the real local CLI integrations before release-facing documentation changes are treated as stable.

## Claude Builder WebSocket Smoke Test

Validated on **March 28, 2026** against the main GUI instance at `http://127.0.0.1:8080`.

### Purpose

- Confirm that the running OMADS instance is reachable.
- Confirm that `builder_agent` is set to `claude`.
- Confirm that two trivial WebSocket chat round-trips complete without timeouts, silent failures, or protocol errors.

### Demo

![Animated Claude builder smoke test demo](assets/omads-claude-builder-smoke-test.gif)

This animation is a compact illustration of the successful live run, based on the measured smoke-test transcript.

### Verified Outcome

Two independent runs succeeded on the same day:

| Runner | Message 1 | Duration | Message 2 | Duration | Errors |
| --- | --- | --- | --- | --- | --- |
| Codex | `Hallo.` | `3.9s` | `Das hat geklappt.` | `3.6s` | none |
| Claude Code | `Hallo.` | `3.6s` | `Das hat geklappt.` | `6.2s` | none |

### Reproduction Steps

1. Start OMADS and keep the main GUI reachable at `http://127.0.0.1:8080`.
2. Confirm CLI availability:

```bash
curl -sS http://127.0.0.1:8080/api/health
```

3. Force the primary builder to Claude Code:

```bash
curl -sS -X POST http://127.0.0.1:8080/api/settings \
  -H 'content-type: application/json' \
  -d '{"builder_agent":"claude"}'
```

4. Confirm the setting:

```bash
curl -sS http://127.0.0.1:8080/api/settings
```

5. Run the live WebSocket smoke test from the repository root:

```bash
./.venv/bin/python - <<'PY'
import asyncio
import json
import time
import websockets

URI = "ws://127.0.0.1:8080/ws"
ORIGIN = "http://localhost:8080"
MESSAGES = [
    "Antworte bitte nur mit: Hallo.",
    "Antworte jetzt bitte nur mit: Das hat geklappt.",
]

async def run_message(ws, text):
    await ws.send(json.dumps({"type": "chat", "text": text}))
    start = time.time()
    events = []
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=120)
        msg = json.loads(raw)
        events.append(msg)
        if msg.get("type") == "unlock":
            break
    texts = [
        msg.get("text", "")
        for msg in events
        if msg.get("type") in {"stream_text", "chat_response"} and msg.get("text")
    ]
    errors = [
        msg.get("text", "")
        for msg in events
        if msg.get("type") in {"task_error", "error"} and msg.get("text")
    ]
    return {
        "elapsed_s": round(time.time() - start, 1),
        "texts": texts,
        "errors": errors,
        "statuses": [msg.get("status", "") for msg in events if msg.get("type") == "agent_status"],
    }

async def main():
    results = []
    async with websockets.connect(URI, origin=ORIGIN, max_size=2**20) as ws:
        for text in MESSAGES:
            results.append((text, await run_message(ws, text)))
            await asyncio.sleep(1.2)
    print(json.dumps(results, ensure_ascii=False, indent=2))

asyncio.run(main())
PY
```

### Pass Criteria

- `builder_agent` resolves to `claude`.
- Message 1 returns `Hallo.`.
- Message 2 returns `Das hat geklappt.`.
- Each run emits `unlock`.
- No `task_error`, `error`, or timeout occurs.
