"""Runtime configuration loaded from env vars."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- API auth -----------------------------------------------------------
    api_pin: str = "0000"
    """Shared PIN/password the Android app submits to /api/auth/login.
    Change this to a long random string in production."""

    api_jwt_secret: str = "change-me-to-a-long-random-jwt-secret"
    """HMAC secret used to sign bearer tokens issued by the API."""

    api_token_ttl_seconds: int = 60 * 60 * 24 * 30
    """How long an access token stays valid (default 30 days)."""

    api_host: str = "0.0.0.0"
    api_port: int = 8001

    cors_origins: str = "*"
    """Comma-separated CORS origins. `*` allows any origin (fine for the
    Android app since it uses bearer auth, not cookies)."""

    # ---- Ownership / data scoping ------------------------------------------
    owner_user_id: int = 1
    """All threads + messages are scoped to this user_id in the SQLite DB.
    Single-user app, so this is normally just `1`."""

    encryption_secret: str = "change-me-to-a-long-random-string"
    """Master secret used to derive Fernet keys for at-rest encryption of any
    sensitive values stored in SQLite."""

    history_max_messages: int = 40
    """Sliding-window cap on conversation history kept per thread."""

    db_path: Path = Path("/data/bot.sqlite")
    skills_dir: Path = Path("/app/skills")
    """Path INSIDE the API container where SKILL.md files are mounted.
    Used by the API to load + serve skills."""

    skills_host_path: Path = Path("/opt/codex-agent/skills")
    """Path on the HOST filesystem where SKILL.md files live. The Codex
    CLI subprocess runs in the host's mount namespace via nsenter, so it
    cannot see the container path; we tell the agent to `cat` from this
    path instead. Override with SKILLS_HOST_PATH env var if you cloned
    the repo somewhere other than /opt/codex-agent."""

    log_level: str = "INFO"

    # ---- Codex CLI integration ---------------------------------------------
    codex_sandbox: str = "danger-full-access"
    """One of: read-only, workspace-write, danger-full-access."""

    codex_workdir: Path = Path("/root/codex-workspace")
    """Working directory the Codex agent operates in (path on the HOST,
    since the API calls codex via nsenter into the host's mount namespace)."""

    codex_home: str = "/root/.codex"
    """Path used as $CODEX_HOME on the HOST (where auth.json lives)."""

    codex_timeout_seconds: int = 600

    codex_model: str = "gpt-5.5"
    """Codex CLI model to request via `-m`. Must match a slug in
    `~/.codex/models_cache.json`."""

    codex_reasoning_effort: str = "high"
    """Reasoning effort level: low | medium | high | xhigh."""

    # ---- Multi-modal attachment inbox --------------------------------------
    # The API container writes uploads via `inbox_container`, but
    # codex (running on the host via nsenter) sees them at `inbox_host`.
    # Default values match the docker-compose setup that mounts host `/` at
    # `/host` and host `/root/codex-workspace` exists.
    inbox_host: Path = Path("/root/codex-workspace/inbox")
    inbox_container: Path = Path("/host/root/codex-workspace/inbox")

    # ---- Whisper / faster-whisper transcription ----------------------------
    whisper_model: str = "base"
    whisper_cache: Path = Path("/data/whisper-cache")

    # ---- Public URL discovery (cloudflared quick tunnel watcher) -----------
    # External daemon (codex-tunnel-watch) writes the current public URL to
    # this file on the host. The container reads it via the bind-mount
    # `/:/host` defined in docker-compose. Surfaced via `GET /api/info`.
    public_url_file: Path = Path("/host/var/lib/codex-agent/tunnel.url")

    # ---- Agent-side back-channel (ask_user, etc.) --------------------------
    # The agent CLI scripts running inside Codex's sandbox call back into
    # this process for interactive features (e.g. ``ask_user``). They
    # authenticate with an in-process token derived per-startup; the same
    # value is exported as ``CODEX_AGENT_TOKEN`` to the codex subprocess.
    agent_loopback_url: str = "http://127.0.0.1:8001"
    """URL the agent scripts hit to talk back to the backend. The container
    listens on this address inside the host's network namespace (via
    nsenter) so 127.0.0.1 works."""

    def cors_origin_list(self) -> list[str]:
        raw = (self.cors_origins or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
