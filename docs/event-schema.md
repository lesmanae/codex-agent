# WebSocket event schema

The agent streams events over `WS /api/ws/chat/{session_id}` while a turn
is running. There are **two schema versions** running in parallel:

- **v1 (legacy)** — flat `{"type":"progress","text":"..."}` strings. Always
  emitted. Old mobile builds rely on these.
- **v2 (rich)** — structured tool-call / plan / reasoning events with full
  payloads (stdout, stderr, exit code, file diff, plan steps, etc.).
  Announced by the server via `GET /api/info` →
  `"stream_schema_version": 2`.

A v2-aware client SHOULD subscribe to v2 events and ignore the v1
`progress` strings (they carry the same information, formatted as a
one-liner). A v1-only client just keeps reading `progress` events as
before.

---

## Lifecycle envelope

Every turn produces this sequence (terminology unchanged from v1):

```jsonc
{"type": "turn_started"}
{"type": "user_message_persisted", "content": "..."}
... (zero or more progress / tool_call / plan / reasoning events)
{"type": "agent_message", "text": "..."}
{"type": "turn_done", "ok": true, "rate_limited": false, "error": null}
```

Reconnects within the same turn re-emit `turn_resumed` after replaying
the events the client missed.

---

## v1 events (legacy, kept stable)

```jsonc
{"type": "progress", "text": "🛠 `ls -la`"}
{"type": "progress", "text": "✓ `ls -la`"}
{"type": "progress", "text": "📝 edit `/etc/nginx/nginx.conf`"}
{"type": "progress", "text": "🌐 search: how to do X"}
{"type": "progress", "text": "🔌 mcp: example_tool"}
{"type": "progress", "text": "📋 Decompile the APK"}
{"type": "transcript", "name": "voice.ogg", "transcript": "halo agent"}
{"type": "warning", "text": "..."}
{"type": "error", "text": "..."}
{"type": "session_renamed", "id": 12, "name": "Audit codex-agent"}
{"type": "turn_started"}
{"type": "turn_resumed"}
{"type": "user_message_persisted", "content": "..."}
{"type": "agent_message", "text": "..."}
{"type": "turn_done", "ok": true, "rate_limited": false, "error": null}
```

The `progress.text` is rendered with these glyphs:

| Glyph | Meaning |
|---|---|
| `🛠` | command starting |
| `✓` | command / mcp tool success |
| `✗` | command / mcp tool failure |
| `📝` | file edit |
| `🌐` | web search |
| `🔌` | MCP tool call |
| `📋` | plan step in progress |
| `🎤` | voice transcription started |
| `⏭` | rotated to next account on rate-limit |

---

## v2 events (rich, structured)

All v2 events share these envelope fields:

| Field | Type | Notes |
|---|---|---|
| `type` | `"tool_call"` / `"plan"` / `"reasoning"` | discriminator |
| `id` | string | stable across started / completed for the same item |
| `status` | `"running"` / `"ok"` / `"failed"` | |
| `ts` | float | epoch seconds |
| `item` | object | raw Codex CLI item (forward-compat escape hatch) |

### `tool_call` events

`type=tool_call` carries a `kind` field that determines payload shape.

#### `kind=command_execution`

Emitted twice per shell command — once on `started`, once on `completed`.

```jsonc
// started
{
  "type": "tool_call", "kind": "command_execution",
  "id": "ce_01HX...", "status": "running", "ts": 1714389123.456,
  "command": "ls -la /etc/nginx",
  "item": { /* raw Codex item */ }
}
// completed
{
  "type": "tool_call", "kind": "command_execution",
  "id": "ce_01HX...", "status": "ok", "ts": 1714389123.789,
  "command": "ls -la /etc/nginx",
  "exit_code": 0,
  "stdout": "total 48\ndrwxr-xr-x ...",
  "stderr": null,
  "aggregated_output": "total 48\ndrwxr-xr-x ...",
  "item": { /* raw Codex item */ }
}
```

`stdout` / `stderr` / `aggregated_output` may be `null` if Codex did not
include them in the item. `aggregated_output` is the merged stream and
is generally what to display in the Shell tab.

#### `kind=file_change`

```jsonc
{
  "type": "tool_call", "kind": "file_change",
  "id": "fc_01HX...", "status": "ok", "ts": 1714389124.0,
  "path": "/opt/proj/app/config.py",
  "change_kind": "edit",
  "diff": "@@ -10,3 +10,4 @@\n ...",
  "contents": null,
  "item": { /* raw Codex item */ }
}
```

`diff` is a unified diff if Codex provides one; otherwise `null`. Render
in a Diff tab using a markdown / monospace diff view.

#### `kind=web_search`

```jsonc
{
  "type": "tool_call", "kind": "web_search",
  "id": "ws_01HX...", "status": "ok", "ts": 1714389125.0,
  "query": "best way to deploy uvicorn behind nginx",
  "results": [{"title": "...", "url": "...", "snippet": "..."}],
  "item": { /* raw Codex item */ }
}
```

#### `kind=mcp_tool_call`

```jsonc
{
  "type": "tool_call", "kind": "mcp_tool_call",
  "id": "mc_01HX...", "status": "ok", "ts": 1714389126.0,
  "name": "example_tool",
  "arguments": {"foo": "bar"},
  "result": "...",
  "item": { /* raw Codex item */ }
}
```

### `plan` events

Emitted on `plan_update.completed` only.

```jsonc
{
  "type": "plan", "id": "pl_01HX...", "status": "ok", "ts": 1714389127.0,
  "steps": [
    {"title": "Decompile APK", "status": "completed"},
    {"title": "Audit endpoints", "status": "in_progress"},
    {"title": "Write report", "status": "pending"}
  ],
  "item": { /* raw Codex item */ }
}
```

Render as a checklist in a Plan tab, mirroring the Devin worklog style.

### `reasoning` events

Off by default in the legacy v1 stream (filtered out as too noisy). v2
emits them so a UI can put them behind a "show thinking" toggle.

```jsonc
{
  "type": "reasoning", "id": "rs_01HX...", "status": "ok",
  "ts": 1714389128.0, "text": "I should first read the config file before...",
  "item": { /* raw Codex item */ }
}
```

---

## Client integration tips

- **Discovery:** call `GET /api/info` once on app launch; check
  `stream_schema_version >= 2` before enabling the rich UI.
- **Correlation:** group events by `id`. A `running` event always
  precedes its `ok`/`failed` counterpart (same `id`).
- **Backpressure:** stdout / aggregated_output for a single command can
  be tens of kilobytes. Truncate at render time, not at receive time.
- **Backward compat:** if the client also handles v1 events, the same
  tool call will appear *twice* (once as a `progress` text, once as a
  rich `tool_call`). Pick one stream and ignore the other to avoid
  double-rendering. Recommended: ignore `progress` whenever you've seen
  any v2 event in the same turn.
