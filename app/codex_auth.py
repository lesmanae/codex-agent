"""Codex CLI multi-account auth manager.

Behavior summary
----------------
- The API container is privileged with `pid: host`, so all commands here
  run on the host via `nsenter -t 1 -a --`. The authoritative auth file is
  `/root/.codex/auth.json` on the host. Per-account snapshots live in
  `/root/.codex-accounts/<name>.json`.
- Rotation state (which account is active, which are temporarily exhausted)
  lives in `/root/.codex-accounts/_state.json`.

Endpoints surfaced via FastAPI:
- `POST /api/codex/login/start`    → begin OAuth, returns the OpenAI auth URL.
- `POST /api/codex/login/complete` → submit the localhost callback URL the
  browser was redirected to; auto-derive an account name from the email and
  save it as a backup.
- `POST /api/codex/login/cancel`   → cancel a pending login.
- `GET  /api/codex/accounts`       → list accounts with active/exhausted status.

Rotation: when `codex exec` reports a rate-limit error, the runner calls
`rotate_to_next()`, which marks the current account exhausted for 5 hours
and copies the next non-exhausted backup over `auth.json`.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

CALLBACK_URL_RE = re.compile(
    r"http://localhost:1455/auth/callback\?[^\s]+",
    re.IGNORECASE,
)

AUTH_PATH = "/root/.codex/auth.json"
ACCOUNTS_DIR = "/root/.codex-accounts"
STATE_PATH = f"{ACCOUNTS_DIR}/_state.json"
LOGIN_LOG = "/tmp/codex_login.log"

# Default plus-tier rate limit cooldown after a hit. Codex Plus uses a rolling
# 5h window, so 5h is a safe upper bound to wait before retrying.
EXHAUSTED_COOLDOWN_SECONDS = 5 * 60 * 60

_global_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Subprocess plumbing (everything runs in the host's namespaces)
# ---------------------------------------------------------------------------


def _nsenter(*cmd: str) -> list[str]:
    return [
        "nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--",
        "env",
        "HOME=/root",
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        *cmd,
    ]


async def _run(*cmd: str, input_bytes: bytes | None = None, timeout: int = 30) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if input_bytes is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(input=input_bytes), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "", f"timed out after {timeout}s"
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# Auth file inspection
# ---------------------------------------------------------------------------


@dataclass
class AuthInfo:
    email: str | None
    plan: str | None
    chatgpt_user_id: str | None
    raw_size: int


_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def derive_name_from_email(email: str | None) -> str:
    """Stable, filesystem-safe account name from an email address."""
    if not email:
        return f"acc_{int(time.time())}"
    sanitized = re.sub(r"[^A-Za-z0-9]+", "_", email).strip("_").lower()
    return sanitized or f"acc_{int(time.time())}"


async def read_auth_info(path: str = AUTH_PATH) -> AuthInfo | None:
    """Decode the JWT inside auth.json (no signature check) to surface email + plan."""
    rc, out, _ = await _run(*_nsenter("cat", path), timeout=5)
    if rc != 0 or not out:
        return None
    try:
        blob = json.loads(out)
    except json.JSONDecodeError:
        return None
    info = AuthInfo(email=None, plan=None, chatgpt_user_id=None, raw_size=len(out))
    tokens = blob.get("tokens") or {}
    id_token = tokens.get("id_token") or blob.get("id_token")
    if id_token and id_token.count(".") == 2:
        try:
            payload_b64 = id_token.split(".")[1]
            padding = "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
            info.email = payload.get("email")
            auth_meta = payload.get("https://api.openai.com/auth") or {}
            info.plan = auth_meta.get("chatgpt_plan_type")
            info.chatgpt_user_id = auth_meta.get("chatgpt_user_id")
        except Exception:  # noqa: BLE001
            pass
    return info


async def auth_mtime(path: str = AUTH_PATH) -> float | None:
    rc, out, _ = await _run(*_nsenter("stat", "-c", "%Y", path), timeout=5)
    if rc != 0:
        return None
    try:
        return float(out.strip())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Rotation state file
# ---------------------------------------------------------------------------


@dataclass
class RotationState:
    active: str | None = None
    """Name of the account currently copied into /root/.codex/auth.json."""

    order: list[str] = field(default_factory=list)
    """Round-robin order. New accounts appended at the end."""

    exhausted_until: dict[str, float] = field(default_factory=dict)
    """Account name → unix ts when it stops being considered exhausted."""

    last_used: dict[str, float] = field(default_factory=dict)
    """Account name → unix ts of most recent successful run."""


async def _read_state() -> RotationState:
    rc, out, _ = await _run(*_nsenter("cat", STATE_PATH), timeout=5)
    if rc != 0 or not out.strip():
        return RotationState()
    try:
        blob = json.loads(out)
    except json.JSONDecodeError:
        return RotationState()
    return RotationState(
        active=blob.get("active"),
        order=list(blob.get("order") or []),
        exhausted_until=dict(blob.get("exhausted_until") or {}),
        last_used=dict(blob.get("last_used") or {}),
    )


async def _write_state(state: RotationState) -> None:
    blob = json.dumps(
        {
            "active": state.active,
            "order": state.order,
            "exhausted_until": state.exhausted_until,
            "last_used": state.last_used,
        },
        indent=2,
    )
    await _run(*_nsenter("mkdir", "-p", ACCOUNTS_DIR), timeout=5)
    rc, _, err = await _run(
        *_nsenter("bash", "-lc", f"cat > {STATE_PATH}"),
        input_bytes=blob.encode("utf-8"),
        timeout=5,
    )
    if rc != 0:
        logger.warning("state_write_failed", err=err.strip())


# ---------------------------------------------------------------------------
# Backup files + rotation primitives
# ---------------------------------------------------------------------------


async def _list_backup_files() -> list[str]:
    """Filenames in ACCOUNTS_DIR (without .json suffix), excluding state file."""
    rc, out, _ = await _run(*_nsenter("ls", "-1", ACCOUNTS_DIR), timeout=5)
    if rc != 0:
        return []
    out_list: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("_") or not line.endswith(".json"):
            continue
        out_list.append(line[: -len(".json")])
    return out_list


async def _save_current_as(name: str) -> str:
    if not _NAME_RE.match(name):
        raise ValueError(f"invalid account name: {name!r}")
    await _run(*_nsenter("mkdir", "-p", ACCOUNTS_DIR), timeout=5)
    dest = f"{ACCOUNTS_DIR}/{name}.json"
    rc, _, err = await _run(*_nsenter("cp", AUTH_PATH, dest), timeout=10)
    if rc != 0:
        raise RuntimeError(f"cp failed: {err.strip() or rc}")
    await _run(*_nsenter("chmod", "600", dest), timeout=5)
    return dest


async def _swap_to(name: str) -> AuthInfo:
    if not _NAME_RE.match(name):
        raise ValueError(f"invalid account name: {name!r}")
    src = f"{ACCOUNTS_DIR}/{name}.json"
    rc, _, err = await _run(*_nsenter("test", "-f", src), timeout=5)
    if rc != 0:
        raise FileNotFoundError(f"no saved account named {name!r}")
    rc, _, err = await _run(*_nsenter("cp", src, AUTH_PATH), timeout=10)
    if rc != 0:
        raise RuntimeError(f"cp failed: {err.strip() or rc}")
    await _run(*_nsenter("chmod", "600", AUTH_PATH), timeout=5)
    info = await read_auth_info()
    if info is None:
        raise RuntimeError("auth.json swapped but could not be read back")
    return info


async def accounts_with_status() -> list[dict[str, Any]]:
    """High-level account list for /codex_accounts. One dict per saved account."""
    state = await _read_state()
    files = await _list_backup_files()
    # Also include any names referenced by state (defensive).
    all_names: list[str] = list(dict.fromkeys([*state.order, *files]))
    now = time.time()
    out: list[dict[str, Any]] = []
    for name in all_names:
        if name not in files:
            # Stale entry in state; skip.
            continue
        info = await read_auth_info(f"{ACCOUNTS_DIR}/{name}.json")
        ex_until = state.exhausted_until.get(name, 0)
        out.append({
            "name": name,
            "email": info.email if info else None,
            "plan": info.plan if info else None,
            "active": state.active == name,
            "exhausted": ex_until > now,
            "exhausted_until": ex_until,
            "last_used": state.last_used.get(name, 0),
        })
    return out


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------


@dataclass
class PendingLogin:
    user_id: int
    started_at: float
    url: str
    initial_mtime: float | None


_pending: PendingLogin | None = None


def get_pending() -> PendingLogin | None:
    return _pending


async def _kill_codex_login() -> None:
    await _run(*_nsenter("pkill", "-f", "codex login"), timeout=5)
    await asyncio.sleep(0.5)


async def start_login(*, user_id: int) -> PendingLogin:
    global _pending
    async with _global_lock:
        await _kill_codex_login()
        initial_mtime = await auth_mtime()
        await _run(*_nsenter("bash", "-lc", f": > {LOGIN_LOG}"), timeout=5)

        spawn_cmd = _nsenter(
            "bash", "-lc",
            f"nohup codex login > {LOGIN_LOG} 2>&1 & disown; echo OK",
        )
        rc, out, err = await _run(*spawn_cmd, timeout=10)
        if rc != 0:
            raise RuntimeError(f"failed to start codex login: {err.strip() or out.strip() or rc}")

        url: str | None = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            rc, log, _ = await _run(*_nsenter("cat", LOGIN_LOG), timeout=5)
            if rc == 0:
                m = re.search(r"https://auth\.openai\.com/oauth/authorize[^\s]+", log)
                if m:
                    url = m.group(0)
                    break
            await asyncio.sleep(0.5)

        if not url:
            raise RuntimeError("codex login started but no auth URL appeared in log within 10s")

        _pending = PendingLogin(
            user_id=user_id,
            started_at=time.monotonic(),
            url=url,
            initial_mtime=initial_mtime,
        )
        return _pending


async def complete_login(callback_url: str) -> tuple[AuthInfo, str]:
    """Finish OAuth, save backup with auto-derived name, register in rotation.

    Returns `(info, account_name)`.
    """
    global _pending
    pending = _pending
    if not pending:
        raise RuntimeError("no pending login — run /codex_login first")

    m = CALLBACK_URL_RE.search(callback_url)
    if not m:
        raise ValueError("not a codex callback URL")
    callback_url = m.group(0)

    async with _global_lock:
        rc, out, err = await _run(
            *_nsenter("curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", callback_url),
            timeout=30,
        )
        if rc != 0:
            raise RuntimeError(f"curl to localhost:1455 failed: {err.strip() or rc}")
        http_code = out.strip()
        if not http_code.startswith(("2", "3")):
            raise RuntimeError(f"callback returned HTTP {http_code}; codex may have rejected the code")

        deadline = time.monotonic() + 30
        info: AuthInfo | None = None
        while time.monotonic() < deadline:
            mt = await auth_mtime()
            if mt is not None and (pending.initial_mtime is None or mt > pending.initial_mtime):
                info = await read_auth_info()
                if info:
                    break
            await asyncio.sleep(0.5)
        if info is None:
            raise RuntimeError("auth.json was not updated within 30s — login may have failed")

        name = derive_name_from_email(info.email)
        await _save_current_as(name)

        state = await _read_state()
        if name not in state.order:
            state.order.append(name)
        state.active = name
        # New account is fresh, clear any stale exhausted flag.
        state.exhausted_until.pop(name, None)
        await _write_state(state)

        await _kill_codex_login()
        _pending = None
        return info, name


async def cancel_login() -> bool:
    global _pending
    had_pending = _pending is not None
    _pending = None
    await _kill_codex_login()
    return had_pending


def looks_like_callback_url(text: str) -> bool:
    return CALLBACK_URL_RE.search(text) is not None


# ---------------------------------------------------------------------------
# Rotation on rate limit
# ---------------------------------------------------------------------------


_RATE_LIMIT_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"rate.?limit",
        r"\b429\b",
        r"too many requests",
        r"usage limit",
        r"weekly limit",
        r"quota",
        r"exceeded.*(messages|tokens|limit)",
        r"reached.*limit",
        r"plan limit",
    ]
]


def is_rate_limit_error(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in _RATE_LIMIT_PATTERNS)


async def get_active_account() -> str | None:
    state = await _read_state()
    return state.active


async def mark_active_exhausted(cooldown_seconds: int = EXHAUSTED_COOLDOWN_SECONDS) -> str | None:
    """Mark currently active account as exhausted. Returns the marked name."""
    async with _global_lock:
        state = await _read_state()
        if not state.active:
            return None
        state.exhausted_until[state.active] = time.time() + cooldown_seconds
        marked = state.active
        await _write_state(state)
        return marked


async def mark_active_used() -> None:
    async with _global_lock:
        state = await _read_state()
        if not state.active:
            return
        state.last_used[state.active] = time.time()
        await _write_state(state)


async def rotate_to_next() -> AuthInfo | None:
    """Find the next non-exhausted account in rotation order and swap to it.

    Returns the new active account info, or None if there's no usable
    alternative (caller should surface a "all accounts exhausted" message).
    """
    async with _global_lock:
        state = await _read_state()
        if not state.order:
            return None
        # Filter to existing files only.
        files = set(await _list_backup_files())
        order = [n for n in state.order if n in files]
        if not order:
            return None

        now = time.time()
        # Start search after the current active.
        try:
            start = order.index(state.active) + 1 if state.active in order else 0
        except ValueError:
            start = 0
        n = len(order)
        for offset in range(n):
            cand = order[(start + offset) % n]
            if cand == state.active:
                continue
            ex = state.exhausted_until.get(cand, 0)
            if ex > now:
                continue
            # Found a candidate.
            info = await _swap_to(cand)
            state.active = cand
            await _write_state(state)
            return info

        # Fallback: nothing else not-exhausted. Try the soonest-to-reset one.
        best = min(
            (n for n in order if n != state.active),
            key=lambda n: state.exhausted_until.get(n, 0),
            default=None,
        )
        if best is None:
            return None
        info = await _swap_to(best)
        state.active = best
        await _write_state(state)
        return info


async def initialize_active_from_disk() -> None:
    """On bot startup, ensure /root/.codex/auth.json matches the recorded active.

    If state file says active=X but auth.json belongs to a different account
    (e.g. someone manually ran `codex login`), trust auth.json and update
    state. Also: if there's no state but there are backup files, register
    them in rotation order using the current auth.json as active.
    """
    async with _global_lock:
        state = await _read_state()
        files = await _list_backup_files()
        info = await read_auth_info()
        active_name_from_disk = derive_name_from_email(info.email) if info else None

        # Garbage-collect stale entries in state.order that no longer have a file.
        state.order = [n for n in state.order if n in files]

        if active_name_from_disk:
            # Make sure the active auth.json is mirrored as a backup.
            backup_path = f"{ACCOUNTS_DIR}/{active_name_from_disk}.json"
            rc, _, _ = await _run(*_nsenter("test", "-f", backup_path), timeout=5)
            if rc != 0:
                try:
                    await _save_current_as(active_name_from_disk)
                    files = await _list_backup_files()
                except Exception:  # noqa: BLE001
                    pass
            if active_name_from_disk not in state.order:
                state.order.append(active_name_from_disk)
            state.active = active_name_from_disk

        await _write_state(state)
        logger.info(
            "rotation_initialized",
            active=state.active,
            order=state.order,
            exhausted=list(state.exhausted_until.keys()),
        )
