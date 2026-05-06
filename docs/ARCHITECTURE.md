# Architecture

A reference document for humans and AI agents working on this repo.

## 1. Why "container-as-control-plane" + `nsenter`?

Codex CLI needs:

- The user's real ChatGPT cookies (`/root/.codex/auth.json`),
- A persistent workspace where `git`, `node`, `python` etc. are
  installed and configured the user's way (`/root/codex-workspace`),
- Network egress on the host's interfaces.

Putting Codex inside the API container would mean re-installing the
toolchain inside the image, mounting volumes, and dealing with UID
mismatches. Instead, the API container does *one* thing: it shells out
to the host (`nsenter -t 1 -a -- codex exec --json …`) so Codex runs
**as host root with the host's everything**.

The trade-off is that a compromised API container = host root. So the
threat model assumes the API itself is trusted code, gated only by
`API_PIN` + bearer token. v0.7.0 will introduce per-session sandboxes
to mitigate this.

## 2. Request lifecycle (chat turn)

```
mobile (WS)
   │  {"type":"user_message","text":"...","attachment_ids":[...]}
   ▼
api.py: ws_chat()
   │  inserts user msg into DB
   │  emits "turn_started"
   │  calls codex_runner.run_session(...)
   ▼
codex_runner.py
   │  builds system prompt (jailbreak.py + skill index)
   │  spawns: nsenter -t 1 -a -- codex exec --json --sandbox danger-full-access -
   │  pipes prompt into codex stdin
   │  parses JSONL stdout line-by-line
   │  for each event:
   │    - reasoning  → emit ws "reasoning"
   │    - command_execution / file_change / web_search / mcp_tool_call
   │      → emit ws "tool_call"
   │    - assistant text delta → emit ws "assistant_text" (streamed)
   │    - plan update → emit ws "plan"
   │  detects 5h-rate-limit error → rotates account via codex_auth.py, retries
   │  watches generated_images/ folder → synthesizes "tool_call kind=image_gen"
   ▼
api.py: emits "turn_finished" + persists assistant message
```

## 3. SessionBus + replay

`app/ws.py` keeps an in-memory ring buffer of the last N events per
session. On WebSocket reconnect the client passes `?since=<seq>` and
the server replays anything newer. Combined with the mobile client's
outbox, this makes turn_started loss invisible.

## 4. ChatGPT account rotation

`app/codex_auth.py` manages multiple Plus accounts via Codex CLI's
`--profile` flag (each profile has its own `auth.json` under
`/root/.codex-accounts/<name>/`). When a turn fails with the 5-hour
Plus rate-limit error string, the runner:

1. Marks the current profile as "in cooldown" (5 h timer).
2. Picks the next profile not in cooldown.
3. Re-spawns the codex subprocess with `--profile <next>`.
4. If all profiles are in cooldown, returns a "rate-limited" error to
   the client (which the mobile renders as a red banner).

## 5. ask_user

Three actors:

- **Agent shell** runs `codex-ask-user --question "..." --option A --option B`.
- **Backend** registers the question in `app/ask_user.py` (a dict from
  question_id → asyncio.Future), emits a WS `ask_user` event so the
  client can render chips.
- **Client** taps a chip, POSTs `/api/ask-user/answer`, which sets the
  Future's result. The CLI script returns, agent reads stdout, agent
  resumes its turn.

The CLI script auths with `X-Agent-Token` (a per-startup random token
injected into Codex's env). The `/answer` endpoint is unauthenticated
(any client with a valid session token can answer).

## 6. Workspace + Git endpoints

- All paths resolve to `/root/codex-workspace` on the host (via the
  container's `/host/root/codex-workspace`).
- `Path.resolve().relative_to(WORKSPACE)` enforces no traversal.
- `git` runs inside the container (the binary is in the Dockerfile);
  the cwd passed to `git` is on the bind mount, so it sees the same
  worktree the agent does.
- `git status --porcelain=v1 -z` for stable, NUL-separated parsing.
- Diff capped at 200 KB by default (configurable via query param).

## 7. Image-gen synth events

Codex CLI's built-in `image_gen` tool writes PNG files to
`generated_images/` but emits **no JSONL event** for them. The runner:

1. Snapshots `os.listdir(generated_images/)` before each turn.
2. After the turn, diffs the snapshot.
3. For each new file, synthesizes a `tool_call` event with
   `kind=image_gen, url_path=/api/generated/<thread>/<filename>`.

The endpoint `GET /api/generated/{thread_id}/{filename}` serves the
file with the right MIME type.

## 8. Skill index

`jailbreak.py` reads frontmatter (`name:` + `description:`) from each
`skills/*/SKILL.md` and injects a compact list:

```
You have access to these skills. To use one, read its body via:
  cat /app/skills/<name>/SKILL.md

- agent-workflow: Break tasks down + use ask_user.
- code-quality: Linting + review checklists.
- ...
```

Total ~62 KB. Skills can have a `references/` subdirectory with
additional files Codex can `cat` on demand.

## 9. Database schema

SQLite at `/data/bot.sqlite` (volume-mounted). Tables:

```sql
sessions(id, name, created_at, updated_at, active)
messages(id, session_id, role, content, attachments_json, created_at)
attachments(id, session_id, kind, original_name, host_path, mime, size, transcript)
codex_accounts(id, name, file_path, last_used_at, cooldown_until)
kv(key, value)  -- generic settings
```

v0.7.0 will add `projects(id, name, workspace_path)` + `session.project_id`.

## 10. Dependencies

- **fastapi** 0.115+ — HTTP/WS framework
- **uvicorn[standard]** — ASGI server with WebSocket support
- **pydantic** 2.8+ + **pydantic-settings** — config & validation
- **websockets** 13 — bundled by uvicorn
- **aiosqlite** — async SQLite driver
- **faster-whisper** — voice transcription (CPU-only, downloads model
  to `data/whisper-cache/` on first use)
- **python-multipart** — multipart upload parsing

## 11. Common operations

```bash
# Logs
docker compose logs -f api --tail=200

# Shell into container (note: doesn't enter host namespaces)
docker compose exec api bash

# Shell into HOST namespaces from inside the container
docker compose exec api nsenter -t 1 -a -- bash

# Wipe DB (start fresh)
docker compose down
rm -f data/bot.sqlite
docker compose up -d

# Tail Codex CLI raw JSONL output for one turn
docker compose logs api | grep '"type":' | head -200
```
