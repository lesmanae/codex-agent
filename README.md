# Codex Agent

A private **OpenAI Codex CLI** agent exposed as an HTTP/WebSocket API on your
own VPS, designed to be driven by a custom Android (or any other) chat client
you control. Runs as a self-hosted backend, uses your **ChatGPT Plus**
subscription via Codex's OAuth flow (no OpenAI API spend), and has full root
access to the host filesystem.

> Mobile client lives in a separate repo:
> **[lesmanae/codex-mobile-app](https://github.com/lesmanae/codex-mobile-app)**.

## Highlights

- **ChatGPT Plus auth** — uses your existing Plus subscription via Codex CLI's
  OAuth flow. No OpenAI API key, no token billing.
- **Multi-account login + auto-rotation** — sign multiple Plus accounts in;
  when one hits the ~5 h Plus rate limit, the API automatically rotates to
  the next.
- **PIN + bearer-token auth** — the client logs in with a PIN configured in
  `.env`, gets back a 30-day JWT-style HMAC token, and uses it for every
  subsequent request and WebSocket connection.
- **Sessions / threads** — ChatGPT-style parallel conversations per session.
  Untitled sessions auto-rename from the first user message.
- **Multi-modal input** — upload photos, voice notes, audio files, videos,
  and documents through `POST /api/sessions/{id}/uploads`. Photos are
  forwarded to Codex's vision input via `codex exec -i`. Voice / audio are
  transcribed locally with `faster-whisper` (CPU, no API). Videos and
  documents land in a host inbox dir Codex can read.
- **Skill bundles** — 12 starter skill packs are inlined into Codex's system
  prompt every turn (devops-vps, git-workflow, python-dev, node-dev,
  database-ops, linux-troubleshoot, web-scraping, defensive-security,
  crypto-analysis, media-processing, ml-ops, android-reverse-engineering).
- **Live progress streaming** — the WebSocket emits Codex's tool-call events
  (shell commands, file edits, web searches, plan updates) to the client in
  real time, ChatGPT-style.
- **Full host access** — runs in a `privileged: true`, `pid: host`,
  `network_mode: host` container that drops into the host namespaces via
  `nsenter`, so Codex operates on the real host filesystem with full root.

> **Threat model:** by design the agent has full root on the host. Treat
> `API_PIN`, `API_JWT_SECRET`, and `.env` like private keys.

---

## Architecture

```
┌──────────────────┐  HTTPS  ┌──────────────────────────────────────┐
│ Android client   │ ───────▶│ docker container  codex-agent       │
│ (or curl, etc.)  │  WSS    │                                      │
└──────────────────┘         │  uvicorn ── FastAPI                  │
                             │       │                              │
                             │       ▼                              │
                             │  app/api.py  + lifespan              │
                             │       │                              │
                             │       ▼                              │
                             │  app/codex_runner.py                 │
                             │       │ nsenter -t 1 -a --           │
                             │       │ codex exec --json            │
                             │       │   --sandbox danger-full-     │
                             │       │   access -                   │
                             │       ▼                              │
                             │  HOST namespaces                     │
                             │   /root/.codex/auth.json             │
                             │   /root/codex-workspace/             │
                             │   /root/.codex-accounts/             │
                             └──────────────────────────────────────┘
```

The container shares the host's PID, mount, and network namespaces
(`pid: host`, `network_mode: host`), and mounts `/:/host`. Each chat turn
spawns `codex exec --json` via `nsenter` so the agent runs **as root on the
host**, not inside the container. The API binds host port `API_PORT` (default
`8001`) directly thanks to host networking.

## Requirements

- A VPS with **root access** (Ubuntu 22.04 / 24.04 tested).
- **2+ GB RAM** recommended (Codex CLI is heavyweight; the API itself is light).
- **Docker** + **Docker Compose v2** (the `install.sh` script installs both
  if missing).
- A **ChatGPT Plus** subscription for the OAuth login.

## One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/lesmanae/codex-agent/main/install.sh | bash
```

This installs system deps, Docker, Node, the Codex CLI, clones the repo to
`/opt/codex-agent`, generates a random `API_PIN`, `API_JWT_SECRET`, and
`ENCRYPTION_SECRET` in `.env`, and prints the next-step instructions
(including the freshly-generated PIN).

To start:

```bash
cd /opt/codex-agent
docker compose up -d
docker compose logs -f api
```

The API now listens on `http://0.0.0.0:8001`. Smoke-test:

```bash
curl -s http://127.0.0.1:8001/api/health
# → {"ok":true,"version":"0.4.0"}
```

## Login flow (curl)

```bash
PIN=<your-pin>
TOKEN=$(curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"pin\":\"$PIN\"}" http://127.0.0.1:8001/api/auth/login \
  | jq -r '.access_token')

# Start the OAuth login for a Plus account
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8001/api/codex/login/start
# Open the auth_url in a browser, complete OAuth, copy the
# http://localhost:1455/auth/callback?... URL it tries to redirect to.

curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"callback_url":"http://localhost:1455/auth/callback?..."}' \
  http://127.0.0.1:8001/api/codex/login/complete

# List accounts:
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8001/api/codex/accounts | jq
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/health` | Liveness probe |
| `POST` | `/api/auth/login` | PIN → bearer token |
| `GET`  | `/api/auth/me` | Verify token |
| `GET`  | `/api/sessions` | List threads |
| `POST` | `/api/sessions` | Create + activate new thread |
| `GET`  | `/api/sessions/active` | Get / autocreate active thread |
| `PATCH`| `/api/sessions/{id}` | Rename |
| `DELETE`| `/api/sessions/{id}` | Delete (also wipes its messages) |
| `POST` | `/api/sessions/{id}/activate` | Set as active |
| `GET`  | `/api/sessions/{id}/messages` | List thread history |
| `POST` | `/api/sessions/{id}/messages/clear` | Wipe history |
| `POST` | `/api/sessions/{id}/uploads` | Upload one or more files (multipart) |
| `GET`  | `/api/codex/accounts` | List Plus accounts + rotation state |
| `POST` | `/api/codex/login/start` | Begin OAuth, returns auth URL |
| `POST` | `/api/codex/login/complete` | Submit callback URL |
| `POST` | `/api/codex/login/cancel` | Abort pending login |
| `GET`  | `/api/skills` | List skill bundles |
| `WS`   | `/api/ws/chat/{session_id}?token=<jwt>` | Streaming chat |

OpenAPI docs are auto-generated at <http://127.0.0.1:8001/docs>.

## WebSocket protocol

Connect: `wss://your-host/api/ws/chat/<session_id>?token=<jwt>`.

**Send:**
```json
{ "type": "user_message", "text": "...", "attachment_ids": ["123:..."] }
```

**Receive (in order, repeated):**
```json
{ "type": "turn_started" }
{ "type": "progress", "text": "🛠 `git status`" }
{ "type": "transcript", "name": "voice.ogg", "transcript": "..." }
{ "type": "session_renamed", "id": 1, "name": "..." }
{ "type": "agent_message", "text": "final reply markdown" }
{ "type": "turn_done", "ok": true, "rate_limited": false, "usage": {...} }
```

`attachment_ids` come from the response of `POST /api/sessions/{id}/uploads`
made earlier in the same session.

## Exposing the API to the internet

The API binds to `0.0.0.0:8001` on the host. To reach it from your phone over
the public internet, the recommended path is **Cloudflare Tunnel** (free, no
domain required, hides your VPS IP):

```bash
apt install -y cloudflared
cloudflared tunnel --url http://localhost:8001
# copy the *.trycloudflare.com URL into your Android app settings
```

## Updating

```bash
cd /opt/codex-agent
git pull
docker compose build && docker compose up -d
```

## Repo layout

```
app/
  api.py            ← FastAPI routes + lifespan
  auth.py           ← PIN + JWT-style token helpers
  codex_runner.py   ← codex exec --json subprocess driver
  codex_auth.py     ← multi-account login + rotation on /root/.codex
  attachments.py    ← inbox storage helpers (framework-agnostic)
  transcribe.py     ← faster-whisper wrapper (lazy-loaded)
  db.py             ← SQLite (threads, messages, settings)
  skills.py         ← skill bundle loader
  jailbreak.py      ← system persona prompt
  config.py         ← pydantic-settings env config
  main.py           ← uvicorn entrypoint
skills/             ← 12 skill bundles (SKILL.md + references/)
docker-compose.yml
Dockerfile
install.sh
```

## License

MIT
