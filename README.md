# Codex Agent

A self-hosted **AI coding agent** API that runs on your own VPS, uses your
**ChatGPT Plus** subscription via the OpenAI **Codex CLI**, and exposes a
clean REST + WebSocket interface. Pair it with the companion Android client
([`lesmanae/codex-mobile-app`](https://github.com/lesmanae/codex-mobile-app))
for a Devin-style mobile coding agent — or wire it into anything that
speaks HTTP / WS.

> **TL;DR:** clone → fill `.env` → `docker compose up -d` → install the
> mobile app → done. Detailed walkthrough below.

---

## Highlights

- **ChatGPT Plus auth (no API spend).** Uses Codex CLI's OAuth flow. Sign
  multiple Plus accounts in; auto-rotation on 5 h Plus rate limit.
- **Full host access.** The container drops into the host's namespaces via
  `nsenter`, so the agent operates on the **real host filesystem** as root.
  No bind-mount fiddling — Codex sees `/root/codex-workspace` directly.
- **Streaming events.** WebSocket relays Codex's tool-call events
  (shell, file edits, web search, plan updates, image gen) in real time.
- **240+ Claude-skills bundled.** Loaded as a small index in the system
  prompt; bodies fetched on demand by the agent — no token-bloat.
- **Workspace + Git API.** Browse the agent's working directory and view
  diffs from a client (mobile / web / curl) without SSHing in.
- **Interactive `ask_user`.** Agent can pause its turn and present
  multi-choice questions; client renders chips, agent resumes on answer.
- **Voice / image / video uploads** with on-device Whisper transcription.
- **Cloudflare Tunnel ready.** Quick start with `cloudflared`, or use a
  named tunnel for a stable URL on your own domain (no port-forwarding,
  no inbound firewall changes).

> ⚠️ **Threat model.** The agent has full root on the host by design.
> Treat `API_PIN`, `API_JWT_SECRET`, `ENCRYPTION_SECRET`, and `.env`
> like private keys. Don't expose the API publicly without HTTPS + PIN.

---

## Architecture in 30 seconds

```
┌────────────┐   HTTPS/WSS   ┌────────────────────────────────────────┐
│ Android    │ ────────────▶ │ Cloudflare Tunnel  →  VPS:8001         │
│ client     │               │                                        │
└────────────┘               │  Container codex-agent                 │
                             │  ──────────────────────                │
                             │  uvicorn → FastAPI (app/api.py)        │
                             │      │                                 │
                             │      ▼ for each turn                   │
                             │  app/codex_runner.py                   │
                             │      │ nsenter -t 1 -a --              │
                             │      │ codex exec --json …             │
                             │      ▼                                 │
                             │  HOST namespaces                       │
                             │   /root/.codex/auth.json   (ChatGPT)   │
                             │   /root/codex-workspace/   (workdir)   │
                             │   /root/.codex-accounts/   (rotation)  │
                             └────────────────────────────────────────┘
```

The container is `privileged: true`, `pid: host`, `network_mode: host` and
mounts `/:/host`. Each chat turn re-spawns `codex exec --json` inside the
host's mount/PID/net namespaces, so Codex truly runs **as host root** and
the API just transcribes Codex's JSONL output to a WebSocket protocol.

For a deep dive (event schema, rotation logic, PEP 563 trap, image-gen
synth events, …) see [`docs/event-schema.md`](docs/event-schema.md) and
the inline comments in [`app/codex_runner.py`](app/codex_runner.py).

---

## Requirements

- Linux VPS with **root access** (Ubuntu 22.04 / 24.04 tested).
- **2+ GB RAM** recommended.
- **Docker** + **Docker Compose v2** (`install.sh` will install both).
- A **ChatGPT Plus** subscription (Codex CLI uses your Plus quota).
- A **public hostname** if you want HTTPS (Cloudflare Tunnel works without
  a static IP or open inbound port).

---

## Quick start

### Option A — One-shot installer (recommended for fresh VPS)

```bash
curl -fsSL https://raw.githubusercontent.com/lesmanae/codex-agent/main/install.sh | bash
```

This installs system deps, Docker, Node, the Codex CLI, clones the repo
to `/opt/codex-agent`, generates random secrets in `.env`, and prints
the freshly-generated PIN. Then:

```bash
cd /opt/codex-agent
docker compose up -d
docker compose logs -f api
```

### Option B — Manual

```bash
# Clone wherever you want it
git clone https://github.com/lesmanae/codex-agent.git
cd codex-agent

# Copy template + fill in values
cp .env.example .env
$EDITOR .env

# Build + start
docker compose up -d --build
docker compose logs -f api
```

### Smoke test

```bash
curl -sf http://127.0.0.1:8001/api/health
# → {"ok":true,"version":"0.7.1"}
```

If you see `{"ok":true,...}` the API is up. Now expose it (see
[Exposing the API](#exposing-the-api-to-the-internet)) and connect a
client.

---

## ChatGPT Plus login

Codex CLI authenticates via OAuth. The agent does this on the host (not
inside the container), so the cookies persist across container restarts
and survive `docker compose up --build`.

```bash
PIN=<your-pin>
TOKEN=$(curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"pin\":\"$PIN\"}" http://127.0.0.1:8001/api/auth/login \
  | jq -r '.access_token')

# Begin OAuth — returns a chat.openai.com authorize URL
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8001/api/codex/login/start | jq

# Open the auth_url in your browser, finish the OAuth flow, and copy
# the http://localhost:1455/auth/callback?... URL the browser tries
# to redirect to (it'll fail in the browser; that's fine — copy the URL).

curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"callback_url":"http://localhost:1455/auth/callback?..."}' \
  http://127.0.0.1:8001/api/codex/login/complete

# Verify the account is registered
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8001/api/codex/accounts | jq
```

Sign in 2-3 Plus accounts for automatic rotation when you hit the 5-hour
Plus rate limit. The mobile app exposes this same flow under
**Settings → Akun ChatGPT Plus**.

---

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/health` | Liveness + version probe |
| `POST` | `/api/auth/login` | PIN → bearer token |
| `GET`  | `/api/auth/me` | Verify token |
| `GET`  | `/api/sessions` | List sessions (chat threads) |
| `POST` | `/api/sessions` | Create new session |
| `PATCH`| `/api/sessions/{id}` | Rename |
| `DELETE`| `/api/sessions/{id}` | Delete (cascades to messages) |
| `POST` | `/api/sessions/{id}/activate` | Set as active |
| `GET`  | `/api/sessions/{id}/messages` | List history |
| `POST` | `/api/sessions/{id}/messages/clear` | Wipe history |
| `POST` | `/api/sessions/{id}/uploads` | Upload files (multipart) |
| `GET`  | `/api/codex/accounts` | List Plus accounts + rotation state |
| `POST` | `/api/codex/login/start` | Begin Codex OAuth |
| `POST` | `/api/codex/login/complete` | Submit OAuth callback URL |
| `POST` | `/api/codex/login/cancel` | Abort pending login |
| `GET`  | `/api/skills` | List skills (240 by default) |
| `GET`  | `/api/workspace/tree?path=&depth=` | Browse the agent's filesystem |
| `GET`  | `/api/workspace/file?path=` | Read a file (256 KB cap) |
| `GET`  | `/api/workspace/git/status?path=` | Git status (porcelain) |
| `GET`  | `/api/workspace/git/diff?path=&staged=` | Unified diff |
| `POST` | `/api/ask-user/start` | Agent → register a question (X-Agent-Token) |
| `GET`  | `/api/ask-user/wait?id=` | Agent → long-poll for answer |
| `POST` | `/api/ask-user/answer` | Client → submit answer |
| `WS`   | `/api/ws/chat/{session_id}?token=<jwt>` | Streaming chat |

OpenAPI docs auto-generated at <http://127.0.0.1:8001/docs>.

---

## WebSocket protocol (compact)

Connect: `wss://your-host/api/ws/chat/<session_id>?token=<jwt>`.

**Send (client → server):**

```json
{ "type": "user_message", "text": "...", "attachment_ids": ["123:..."] }
```

**Receive (server → client), in order, repeated per turn:**

| Event | Payload |
|---|---|
| `turn_started` | `{turn_id, session_id}` |
| `assistant_text` | `{turn_id, delta, done}` (streaming chunks) |
| `reasoning` | `{id, status, text?}` |
| `tool_call` | `{id, kind, status, ...kind_specific}` |
| `plan` | `{steps: [{description, status}]}` |
| `ask_user` | `{question_id, question, options, allow_multiple, allow_freetext}` |
| `transcript` | `{name, transcript}` (after voice upload) |
| `session_renamed` | `{id, name}` |
| `turn_finished` | `{turn_id, usage?}` |
| `error` | `{message, code?}` |

`tool_call.kind` is one of: `command_execution`, `file_change`,
`web_search`, `mcp_tool_call`, `image_gen`, `ask_user`.

Full schema: [`docs/event-schema.md`](docs/event-schema.md).

---

## Exposing the API to the internet

The API binds to `0.0.0.0:8001` on the host. Three options to make it
reachable from your phone:

### 1. Cloudflare Tunnel — quick (no domain needed, URL changes on restart)

```bash
apt install -y cloudflared
cloudflared tunnel --url http://localhost:8001
# Copy the *.trycloudflare.com URL that appears.
# Paste it into the mobile app: Settings → Server URL.
```

Note: quick tunnels are slow and the URL rotates whenever cloudflared
restarts. Use only for testing.

### 2. Cloudflare Named Tunnel — production (your domain, stable URL, free)

Requires a domain with DNS managed by Cloudflare. You'll get a stable
URL like `https://codex.example.com` that survives restarts.

```bash
# On the VPS, with a Cloudflare API token (Account:Cloudflare Tunnel:Edit
# + Zone:DNS:Edit + Zone:Zone Settings:Edit):

export CF_TOKEN=<your-api-token>
export CF_ACCOUNT_ID=<your-account-id>
export CF_ZONE_ID=<zone-id-for-example.com>
export TUNNEL_NAME=codex-agent
export HOSTNAME=codex.example.com

# Create tunnel
TUNNEL=$(curl -s -X POST \
  -H "Authorization: Bearer $CF_TOKEN" \
  -H "Content-Type: application/json" \
  https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/cfd_tunnel \
  -d "{\"name\":\"$TUNNEL_NAME\",\"config_src\":\"cloudflare\"}" | jq -r '.result')
TUNNEL_ID=$(echo "$TUNNEL" | jq -r '.id')
TUNNEL_TOKEN=$(echo "$TUNNEL" | jq -r '.token')

# Configure ingress
curl -s -X PUT \
  -H "Authorization: Bearer $CF_TOKEN" \
  -H "Content-Type: application/json" \
  https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/configurations \
  -d "{\"config\":{\"ingress\":[{\"hostname\":\"$HOSTNAME\",\"service\":\"http://localhost:8001\"},{\"service\":\"http_status:404\"}]}}"

# DNS CNAME
curl -s -X POST \
  -H "Authorization: Bearer $CF_TOKEN" \
  -H "Content-Type: application/json" \
  https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records \
  -d "{\"type\":\"CNAME\",\"name\":\"codex\",\"content\":\"$TUNNEL_ID.cfargotunnel.com\",\"proxied\":true}"

# Disable Cloudflare's challenge layers (so mobile app gets JSON, not JS challenge)
for SETTING in security_level browser_check; do
  curl -s -X PATCH -H "Authorization: Bearer $CF_TOKEN" -H "Content-Type: application/json" \
    https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/settings/$SETTING \
    -d '{"value":"off"}'
done
# (security_level: "essentially_off" is the most permissive value)

# Install cloudflared as a systemd service
apt install -y cloudflared
cloudflared service install $TUNNEL_TOKEN
systemctl enable --now cloudflared
```

Then `https://codex.example.com/api/health` should return JSON with
your version.

### 3. Direct (no tunnel)

If your VPS already has a public IP + reverse proxy (Caddy / nginx +
Let's Encrypt), point a `proxy_pass http://127.0.0.1:8001;` at the
agent. The API needs no further config — it does its own auth via PIN
+ bearer token.

---

## Updating

```bash
cd /opt/codex-agent
git pull
docker compose up -d --build      # rebuild only when Dockerfile / deps change
# or
docker compose restart api        # for pure Python changes
```

---

## Repo layout

```
app/
├── main.py             ← uvicorn entry
├── api.py              ← all FastAPI routes (HTTP + WS), ~900 lines
├── codex_runner.py     ← spawns + parses Codex CLI subprocess
├── codex_auth.py       ← multi-account login + rotation
├── ask_user.py         ← in-memory question registry (Future-based)
├── skills.py           ← reads /app/skills/<name>/SKILL.md
├── jailbreak.py        ← system prompt builder + skill index
├── attachments.py      ← inbox + transcribe wiring
├── transcribe.py       ← faster-whisper lazy wrapper
├── db.py               ← aiosqlite helpers
└── config.py           ← pydantic-settings env vars
skills/                 ← 240 SKILL.md files
scripts/
└── ask_user.py         ← CLI agent invokes via shell (`codex-ask-user`)
docs/
└── event-schema.md     ← WS event protocol reference
docker-compose.yml      ← single service, host networking
Dockerfile              ← python:3.12-slim + git + ffmpeg + util-linux
install.sh              ← one-shot installer for fresh VPS
.env.example            ← copy to .env, fill in
```

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `curl /api/health` returns HTML with "Just a moment..." | Cloudflare Browser Integrity Check is on. Set Security Level → Essentially Off + Browser Integrity Check → OFF for the zone. |
| Mobile app shows HTTP 422 on a request that has a valid JSON body | Almost certainly the **PEP 563 trap** — see [CONTRIBUTING.md](CONTRIBUTING.md#pep-563-pydantic-trap). Use `body: dict` not a locally-scoped Pydantic class. |
| `/api/workspace/git/status` returns 500 with `FileNotFoundError` | The `git` binary isn't in the container. Pull `main` (≥ 0.7.1) — Dockerfile now installs `git`, and `_run_git()` catches the error gracefully. |
| `docker compose up` hangs on `apt-get install` | Pin `apt` to a fast mirror or use `--network=host` during build. |
| Codex login flow fails / OAuth never finishes | Open `chrome://settings/cookies/detail?site=chatgpt.com` in your browser, sign back into ChatGPT, retry. Some accounts need an extra browser session for the device-binding step. |
| `image_gen` PNG never appears in chat | Codex CLI emits no JSONL event for `image_gen` — the runner watches `generated_images/` instead. Make sure the host's `/root/codex-workspace/generated_images/` is writable. |
| WebSocket disconnects every 2-3 minutes | Cloudflare's WS idle timeout is 100 s. The mobile client auto-reconnects with backoff and replays via the event bus, so this should be invisible. If it isn't, check `cloudflared` logs. |
| Agent makes calls but never returns text | One of your Plus accounts hit the 5-hour rate limit. Check `/api/codex/accounts` and add another Plus account. |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, the
PEP 563 + Pydantic trap (please read before editing `api.py`), and
release checklist.

## License

MIT — see [LICENSE](LICENSE).

---

## See also

- **Mobile client:** [`lesmanae/codex-mobile-app`](https://github.com/lesmanae/codex-mobile-app)
- **Skill source:** [`alirezarezvani/claude-skills`](https://github.com/alirezarezvani/claude-skills) — 240 Claude skills bundled
- **Codex CLI:** [`openai/codex`](https://github.com/openai/codex)
