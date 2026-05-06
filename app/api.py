"""FastAPI server for the Codex Agent backend.

Exposes REST + WebSocket endpoints used by the Android (or any) client:

  Auth
    POST /api/auth/login                 — exchange PIN for bearer token
    GET  /api/auth/me                    — verify token

  Sessions (= threads)
    GET    /api/sessions                  — list all threads
    POST   /api/sessions                  — create new thread (auto-active)
    PATCH  /api/sessions/{id}             — rename
    DELETE /api/sessions/{id}             — delete
    POST   /api/sessions/{id}/activate    — set as active
    GET    /api/sessions/active           — fetch (or auto-create) active thread

  Messages
    GET  /api/sessions/{id}/messages       — list history
    POST /api/sessions/{id}/messages/clear — wipe history
    POST /api/sessions/{id}/uploads        — upload one or more files

  Codex accounts
    GET    /api/codex/accounts                    — list with active/exhausted status
    POST   /api/codex/accounts/{name}/activate    — swap auth.json to this account
    DELETE /api/codex/accounts/{name}             — remove backup + scrub state
    POST   /api/codex/login/start                 — begin OAuth, returns auth URL
    POST   /api/codex/login/complete              — submit callback URL to finalize
    POST   /api/codex/login/cancel                — abort pending login

  Skills
    GET  /api/skills                     — names + summaries

  Health
    GET  /api/health                     — service liveness probe
    GET  /api/info                       — public discovery (current URL + version)

  WebSocket
    WS   /api/ws/chat/{session_id}?token=...
         Send {"type":"user_message","text":"...","attachment_ids":[...]}
         Receive a stream of:
           # Legacy v1 events (always emitted, kept stable for old clients):
           {"type":"progress","text":"..."}
           {"type":"transcript","name":"...","transcript":"..."}
           {"type":"agent_message","text":"..."}
           {"type":"turn_done","ok":true,"rate_limited":false}
           # v2 rich events (server announces support via
           # /api/info -> stream_schema_version: 2):
           {"type":"tool_call","kind":"command_execution","id":"...",
            "status":"running|ok|failed","command":"...","stdout":...,
            "stderr":...,"exit_code":0,"ts":1234567890.0,"item":{...}}
           {"type":"tool_call","kind":"file_change","id":"...",
            "status":"ok","path":"...","change_kind":"edit","diff":...}
           {"type":"tool_call","kind":"web_search","id":"...",
            "status":"ok","query":"...","results":[...]}
           {"type":"tool_call","kind":"mcp_tool_call","id":"...",
            "status":"ok","name":"...","arguments":{...},"result":...}
           {"type":"plan","id":"...","status":"ok","steps":[...]}
           {"type":"reasoning","id":"...","status":"ok","text":"..."}
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
from pathlib import Path
from typing import Annotated, Any, AsyncIterator

import structlog
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Header,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import attachments as att_mod
from . import auth as auth_mod
from . import codex_auth, transcribe
from .codex_runner import run_codex_with_rotation
from .config import Settings, get_settings
from .db import BotDB
from .jailbreak import build_system_instruction
from .skills import Skill, load_skills
from .turn_bus import SessionBus, registry as turn_registry
from .ask_user import ASK_USER

logger = structlog.get_logger("api")

# Track active uploads per session_id+upload_id so the WS handler can resolve
# attachment_ids submitted with a user message back to file paths.
_uploads_by_id: dict[str, att_mod.Attachment] = {}


# ---------------------------------------------------------------------------
# App state container — populated at startup.
# ---------------------------------------------------------------------------


class AppState:
    settings: Settings
    db: BotDB
    skills: list[Skill]
    # Random token regenerated each backend start; injected as
    # CODEX_AGENT_TOKEN into codex subprocesses so back-channel scripts
    # (ask_user, etc.) can authenticate without sharing the user's PIN.
    agent_token: str = ""

    # Per-day usage rollup. Keys: "YYYY-MM-DD". Values: dict with
    # input_tokens / output_tokens / reasoning_tokens / total_turns.
    usage_by_day: dict[str, dict] = {}


state = AppState()


def _record_usage(usage: dict | None) -> None:
    """Aggregate a single turn's Codex usage into ``state.usage_by_day``.

    Codex CLI reports usage as keys like ``input_tokens``,
    ``cached_input_tokens``, ``output_tokens``, ``reasoning_output_tokens``,
    ``total_tokens``. We only sum the ones we display on mobile."""
    if not isinstance(usage, dict):
        return
    import datetime as _dt
    day = _dt.date.today().isoformat()
    bucket = state.usage_by_day.setdefault(
        day,
        {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "total_turns": 0},
    )
    def _g(k: str) -> int:
        v = usage.get(k)
        return int(v) if isinstance(v, (int, float)) else 0
    bucket["input_tokens"] += _g("input_tokens")
    bucket["output_tokens"] += _g("output_tokens")
    bucket["reasoning_tokens"] += _g("reasoning_output_tokens")
    bucket["total_turns"] += 1
    # Trim — keep last 14 days.
    if len(state.usage_by_day) > 14:
        for k in sorted(state.usage_by_day.keys())[:-14]:
            state.usage_by_day.pop(k, None)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    pin: str = Field(..., min_length=1, max_length=128)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: int


class SessionOut(BaseModel):
    id: int
    name: str
    created_at: int
    updated_at: int
    auto_named: bool
    msg_count: int = 0
    is_active: bool = False


class SessionCreateRequest(BaseModel):
    name: str | None = None


class SessionRenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class MessageOut(BaseModel):
    role: str
    content: str


class UploadOut(BaseModel):
    id: str
    kind: str
    name: str
    mime: str | None
    size: int
    host_path: str
    container_path: str
    transcript: str | None = None


class CodexAccountOut(BaseModel):
    name: str
    email: str | None
    plan: str | None
    active: bool
    exhausted: bool
    exhausted_until: float
    last_used: float


class CodexLoginStartOut(BaseModel):
    auth_url: str


class CodexLoginCompleteRequest(BaseModel):
    callback_url: str


class CodexLoginCompleteOut(BaseModel):
    name: str
    email: str | None
    plan: str | None


class CodexAccountActivateOut(BaseModel):
    name: str
    email: str | None
    plan: str | None


class TurnStateOut(BaseModel):
    busy: bool
    events: list[dict] = Field(default_factory=list)


class SkillOut(BaseModel):
    name: str
    summary: str


class SkillDetailOut(BaseModel):
    name: str
    summary: str
    content: str


class SkillUpsertRequest(BaseModel):
    content: str = Field(min_length=1)


class SkillCreateRequest(SkillUpsertRequest):
    name: str = Field(min_length=1, max_length=64)


class PinChangeRequest(BaseModel):
    current_pin: str
    new_pin: str = Field(min_length=4, max_length=64)


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------


async def require_token(
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    payload = auth_mod.verify_token(token, state.settings.api_jwt_secret)
    if payload is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    return payload


def _user_id() -> int:
    return state.settings.owner_user_id


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Bootstrap DB + skills + media + codex auth on the same event loop
    uvicorn runs the app on."""
    settings = get_settings()
    state.settings = settings
    # Per-startup token used by codex subprocess back-channel scripts.
    import secrets as _secrets
    state.agent_token = _secrets.token_urlsafe(32)

    db = BotDB(settings.db_path, settings.encryption_secret)
    await db.connect()
    state.db = db

    skills = load_skills(settings.skills_dir)
    state.skills = skills
    logger.info("skills_loaded", count=len(skills), names=[s.name for s in skills])

    att_mod.configure(settings.inbox_container, settings.inbox_host)
    transcribe.configure(settings.whisper_model, settings.whisper_cache)
    logger.info(
        "media_configured",
        inbox_container=str(settings.inbox_container),
        inbox_host=str(settings.inbox_host),
        whisper_model=settings.whisper_model,
        whisper_cache=str(settings.whisper_cache),
    )

    try:
        await codex_auth.initialize_active_from_disk()
    except Exception:  # noqa: BLE001
        logger.exception("codex_auth_init_failed")

    logger.info(
        "api_ready",
        host=settings.api_host,
        port=settings.api_port,
        cors=settings.cors_origin_list(),
        owner_user_id=settings.owner_user_id,
    )

    yield

    await db.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="codex-agent", version="0.6.0", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Log validation errors with the raw body so we can debug agent-side
    # callers like ``codex-ask-user`` whose stderr we don't always capture.
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse as _JSONResponse

    @app.exception_handler(RequestValidationError)
    async def _on_validation_error(req, exc: RequestValidationError):  # type: ignore[no-redef]
        try:
            raw = (await req.body()).decode("utf-8", errors="replace")[:2000]
        except Exception:  # noqa: BLE001
            raw = "<unreadable>"
        logger.warning(
            "request_validation_error",
            path=str(req.url.path),
            errors=exc.errors(),
            body=raw,
        )
        return _JSONResponse(status_code=422, content={"detail": exc.errors()})

    _register_routes(app)
    return app


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _register_routes(app: FastAPI) -> None:
    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "version": app.version}

    @app.get("/api/generated/{thread_id}/{filename}")
    async def get_generated_image(thread_id: str, filename: str) -> FileResponse:
        """Serve a PNG written by Codex CLI's built-in `image_gen` tool.

        We don't require auth here because (a) the filename is an opaque
        content-hash (ig_<sha>.png) that an attacker cannot guess, and (b)
        mobile Image components don't send the Authorization header easily.
        """
        # Guard against path traversal.
        if "/" in thread_id or "\\" in thread_id or ".." in thread_id:
            raise HTTPException(status_code=400, detail="bad thread_id")
        if "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(status_code=400, detail="bad filename")
        if not (filename.endswith(".png") or filename.endswith(".jpg") or filename.endswith(".webp")):
            raise HTTPException(status_code=400, detail="bad extension")
        host_path = Path("/host/root/.codex/generated_images") / thread_id / filename
        if not host_path.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(str(host_path), media_type="image/png")

    @app.get("/api/usage")
    async def get_usage(_: dict = Depends(require_token)) -> dict:
        """Return the per-day token usage snapshot. Mobile uses this to render
        a cost meter and warn before hitting the ChatGPT Plus rate limit."""
        import datetime as _dt
        days = sorted(state.usage_by_day.keys())
        today = _dt.date.today().isoformat()
        return {
            "ok": True,
            "today": state.usage_by_day.get(today, {
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_turns": 0,
            }),
            "days": [{"date": d, **state.usage_by_day[d]} for d in days],
        }

    # ------------------------------------------------------------------
    # Interactive ask-user channel
    # ------------------------------------------------------------------
    # The agent's CLI script (`scripts/ask_user.py`) calls /start to open
    # a multiple-choice question, then long-polls /wait for the answer.
    # The mobile app calls /answer when the user taps a chip. /start +
    # /wait require the per-startup CODEX_AGENT_TOKEN; /answer uses the
    # normal user bearer token.

    async def _require_agent_token(
        x_agent_token: Annotated[str | None, Header(alias="X-Agent-Token")] = None,
    ) -> None:
        if not state.agent_token or x_agent_token != state.agent_token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad agent token")

    @app.post("/api/ask-user/start")
    async def ask_user_start(
        body: dict,
        _: None = Depends(_require_agent_token),
    ) -> dict:
        # Accept loose payloads — the agent CLI may stringify ints, miss
        # boolean flags, or send ``options`` as a JSON-encoded string.
        try:
            session_id = int(body.get("session_id") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "session_id must be an integer")
        if session_id <= 0:
            raise HTTPException(400, "session_id required")
        question = str(body.get("question") or "").strip()
        if not question:
            raise HTTPException(400, "question required")
        raw_opts = body.get("options")
        if isinstance(raw_opts, str):
            # Tolerate "a,b,c" or JSON-encoded list strings.
            stripped = raw_opts.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                try:
                    raw_opts = json.loads(stripped)
                except Exception:  # noqa: BLE001
                    raw_opts = [p for p in stripped.strip("[]").split(",")]
            else:
                raw_opts = [p for p in stripped.split(",")]
        if not isinstance(raw_opts, list):
            raw_opts = []
        options = [str(o).strip() for o in raw_opts if str(o).strip()]
        allow_multiple = bool(body.get("allow_multiple") or False)
        allow_freetext_v = body.get("allow_freetext")
        allow_freetext = True if allow_freetext_v is None else bool(allow_freetext_v)

        bus = await turn_registry.get(session_id)
        pq = await ASK_USER.start(
            session_id=session_id,
            question=question,
            options=options,
            allow_multiple=allow_multiple,
            allow_freetext=allow_freetext,
        )
        await bus.publish({
            "type": "ask_user",
            "id": pq.id,
            "question": pq.question,
            "options": pq.options,
            "allow_multiple": pq.allow_multiple,
            "allow_freetext": pq.allow_freetext,
        })
        return {"ok": True, "id": pq.id}

    @app.post("/api/ask-user/wait")
    async def ask_user_wait(
        body: dict,
        _: None = Depends(_require_agent_token),
    ) -> dict:
        qid = str(body.get("id") or "").strip()
        if not qid:
            raise HTTPException(400, "id required")
        timeout = float(body.get("timeout") or 290.0)
        answer = await ASK_USER.wait(qid, timeout=min(timeout, 290.0))
        if answer is None:
            return {"ok": True, "pending": True}
        return {"ok": True, "pending": False, "answer": answer}

    @app.post("/api/ask-user/answer")
    async def ask_user_answer(
        body: dict,
        _: dict = Depends(require_token),
    ) -> dict:
        # ``body: dict`` (rather than a local BaseModel) — under
        # ``from __future__ import annotations``, FastAPI can't resolve a
        # locally-scoped Pydantic class and falls back to treating the
        # parameter as a query string, which produced 422s in v0.6.0.
        qid = str(body.get("id") or "").strip()
        if not qid:
            raise HTTPException(400, "id required")
        raw_ans = body.get("answer")
        if isinstance(raw_ans, list):
            answer = ", ".join(str(x) for x in raw_ans if str(x).strip())
        else:
            answer = str(raw_ans or "").strip()
        if not answer:
            raise HTTPException(400, "answer required")
        ok = await ASK_USER.answer(qid, answer)
        if not ok:
            raise HTTPException(404, "question not pending")
        return {"ok": True}

    @app.get("/api/info")
    async def info() -> dict:
        """Public discovery endpoint.

        Returns the current public URL the API is reachable at, plus version.
        The URL is read from a host-mounted file populated by the
        cloudflared tunnel watcher (no auth required so that a fresh client
        can bootstrap without ever talking to the API directly first).
        """
        public_url: str | None = None
        try:
            url_path = Path(state.settings.public_url_file)
            if url_path.is_file():
                public_url = url_path.read_text(encoding="utf-8").strip() or None
        except Exception:  # noqa: BLE001
            public_url = None
        return {
            "ok": True,
            "version": app.version,
            "public_url": public_url,
            "stream_schema_version": 2,
        }

    # ------ auth ----------------------------------------------------------

    @app.post("/api/auth/login", response_model=LoginResponse)
    async def login(req: LoginRequest) -> LoginResponse:
        if not auth_mod.pin_matches(req.pin, state.settings.api_pin):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong PIN")
        token, exp = auth_mod.issue_token(
            subject=str(state.settings.owner_user_id),
            secret=state.settings.api_jwt_secret,
            ttl_seconds=state.settings.api_token_ttl_seconds,
        )
        return LoginResponse(access_token=token, expires_at=exp)

    @app.get("/api/auth/me")
    async def me(payload: dict = Depends(require_token)) -> dict:
        return {"sub": payload.get("sub"), "exp": payload.get("exp")}

    # ------ sessions ------------------------------------------------------

    @app.get("/api/sessions", response_model=list[SessionOut])
    async def list_sessions(_: dict = Depends(require_token)) -> list[SessionOut]:
        threads = await state.db.list_threads(_user_id())
        active_id = await state.db.get_active_thread_id(_user_id())
        return [
            SessionOut(
                id=t["id"],
                name=t["name"],
                created_at=t["created_at"],
                updated_at=t["updated_at"],
                auto_named=t["auto_named"],
                msg_count=t.get("msg_count", 0),
                is_active=t["id"] == active_id,
            )
            for t in threads
        ]

    @app.post("/api/sessions", response_model=SessionOut, status_code=201)
    async def create_session(
        req: SessionCreateRequest,
        _: dict = Depends(require_token),
    ) -> SessionOut:
        name = (req.name or "").strip() or "Untitled"
        auto = not req.name
        tid = await state.db.create_thread(_user_id(), name, auto_named=auto)
        await state.db.set_active_thread(_user_id(), tid)
        t = await state.db.get_thread(tid)
        assert t
        return SessionOut(
            id=t["id"], name=t["name"],
            created_at=t["created_at"], updated_at=t["updated_at"],
            auto_named=t["auto_named"], msg_count=0, is_active=True,
        )

    @app.get("/api/sessions/active", response_model=SessionOut)
    async def get_active(_: dict = Depends(require_token)) -> SessionOut:
        t = await state.db.ensure_active_thread(_user_id())
        # Compute message count
        threads = await state.db.list_threads(_user_id())
        msg_count = next((x["msg_count"] for x in threads if x["id"] == t["id"]), 0)
        return SessionOut(
            id=t["id"], name=t["name"],
            created_at=t["created_at"], updated_at=t["updated_at"],
            auto_named=t["auto_named"], msg_count=msg_count, is_active=True,
        )

    @app.patch("/api/sessions/{session_id}", response_model=SessionOut)
    async def rename_session(
        session_id: int,
        req: SessionRenameRequest,
        _: dict = Depends(require_token),
    ) -> SessionOut:
        thr = await state.db.get_thread(session_id)
        if not thr or thr["user_id"] != _user_id():
            raise HTTPException(404, "session not found")
        await state.db.rename_thread(session_id, req.name, mark_manual=True)
        t = await state.db.get_thread(session_id)
        assert t
        active_id = await state.db.get_active_thread_id(_user_id())
        return SessionOut(
            id=t["id"], name=t["name"],
            created_at=t["created_at"], updated_at=t["updated_at"],
            auto_named=t["auto_named"], is_active=t["id"] == active_id,
        )

    @app.delete("/api/sessions/{session_id}", status_code=204)
    async def delete_session(
        session_id: int,
        _: dict = Depends(require_token),
    ) -> None:
        thr = await state.db.get_thread(session_id)
        if not thr or thr["user_id"] != _user_id():
            raise HTTPException(404, "session not found")
        await state.db.delete_thread(session_id)
        return None

    @app.post("/api/sessions/{session_id}/activate", response_model=SessionOut)
    async def activate_session(
        session_id: int,
        _: dict = Depends(require_token),
    ) -> SessionOut:
        thr = await state.db.get_thread(session_id)
        if not thr or thr["user_id"] != _user_id():
            raise HTTPException(404, "session not found")
        await state.db.set_active_thread(_user_id(), session_id)
        return SessionOut(
            id=thr["id"], name=thr["name"],
            created_at=thr["created_at"], updated_at=thr["updated_at"],
            auto_named=thr["auto_named"], is_active=True,
        )

    # ------ messages ------------------------------------------------------

    @app.get(
        "/api/sessions/{session_id}/messages",
        response_model=list[MessageOut],
    )
    async def list_messages(
        session_id: int,
        limit: int = Query(default=200, ge=1, le=2000),
        _: dict = Depends(require_token),
    ) -> list[MessageOut]:
        thr = await state.db.get_thread(session_id)
        if not thr or thr["user_id"] != _user_id():
            raise HTTPException(404, "session not found")
        msgs = await state.db.get_thread_history(session_id, limit)
        return [MessageOut(role=m["role"], content=m["content"]) for m in msgs]

    @app.post("/api/sessions/{session_id}/messages/clear", status_code=204)
    async def clear_messages(
        session_id: int,
        _: dict = Depends(require_token),
    ) -> None:
        thr = await state.db.get_thread(session_id)
        if not thr or thr["user_id"] != _user_id():
            raise HTTPException(404, "session not found")
        await state.db.clear_thread_history(session_id)
        return None

    @app.get(
        "/api/sessions/{session_id}/turn_state",
        response_model=TurnStateOut,
    )
    async def turn_state(
        session_id: int,
        _: dict = Depends(require_token),
    ) -> TurnStateOut:
        """Whether a codex turn is currently in flight + replay buffer.

        Clients use this on (re)entry to a chat to decide if they should
        immediately re-render an in-progress spinner + the partial output
        we've already streamed."""
        thr = await state.db.get_thread(session_id)
        if not thr or thr["user_id"] != _user_id():
            raise HTTPException(404, "session not found")
        bus = await turn_registry.get(session_id)
        return TurnStateOut(busy=bus.busy(), events=list(bus.events))

    # ------ uploads -------------------------------------------------------

    @app.post(
        "/api/sessions/{session_id}/uploads",
        response_model=list[UploadOut],
    )
    async def upload_files(
        session_id: int,
        files: list[UploadFile] = File(...),
        kind_hint: str | None = Form(default=None),
        transcribe_audio: bool = Form(default=True),
        _: dict = Depends(require_token),
    ) -> list[UploadOut]:
        thr = await state.db.get_thread(session_id)
        if not thr or thr["user_id"] != _user_id():
            raise HTTPException(404, "session not found")

        out: list[UploadOut] = []
        for f in files:
            data = await f.read()
            att = att_mod.save_upload(
                user_id=_user_id(),
                thread_id=session_id,
                original_name=f.filename or "file",
                data=data,
                mime=f.content_type,
                kind_hint=kind_hint,
            )
            upload_id = f"{session_id}:{att.container_path.name}"
            _uploads_by_id[upload_id] = att

            # Eagerly transcribe voice/audio so the client can show the text
            # before the next chat turn even starts.
            transcript: str | None = None
            if transcribe_audio and att.kind in {"voice", "audio"}:
                try:
                    transcript = await transcribe.transcribe(att.container_path)
                except Exception:  # noqa: BLE001
                    logger.exception("upload_transcribe_failed", path=str(att.container_path))

            out.append(UploadOut(
                id=upload_id,
                kind=att.kind,
                name=att.name,
                mime=att.mime,
                size=att.size,
                host_path=str(att.host_path),
                container_path=str(att.container_path),
                transcript=transcript,
            ))
        return out

    # ------ codex accounts -----------------------------------------------

    @app.get("/api/codex/accounts", response_model=list[CodexAccountOut])
    async def codex_accounts(_: dict = Depends(require_token)) -> list[CodexAccountOut]:
        accs = await codex_auth.accounts_with_status()
        return [CodexAccountOut(**a) for a in accs]

    @app.post("/api/codex/login/start", response_model=CodexLoginStartOut)
    async def codex_login_start(_: dict = Depends(require_token)) -> CodexLoginStartOut:
        try:
            pending = await codex_auth.start_login(user_id=_user_id())
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"codex login start failed: {exc}") from exc
        return CodexLoginStartOut(auth_url=pending.url)

    @app.post(
        "/api/codex/login/complete",
        response_model=CodexLoginCompleteOut,
    )
    async def codex_login_complete(
        req: CodexLoginCompleteRequest,
        _: dict = Depends(require_token),
    ) -> CodexLoginCompleteOut:
        try:
            info, name = await codex_auth.complete_login(req.callback_url)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"codex login complete failed: {exc}") from exc
        return CodexLoginCompleteOut(name=name, email=info.email, plan=info.plan)

    @app.post("/api/codex/login/cancel", status_code=204)
    async def codex_login_cancel(_: dict = Depends(require_token)) -> None:
        await codex_auth.cancel_login()
        return None

    @app.post(
        "/api/codex/accounts/{name}/activate",
        response_model=CodexAccountActivateOut,
    )
    async def codex_account_activate(
        name: str,
        _: dict = Depends(require_token),
    ) -> CodexAccountActivateOut:
        try:
            info = await codex_auth.set_active_account(name)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"activate failed: {exc}") from exc
        return CodexAccountActivateOut(name=name, email=info.email, plan=info.plan)

    @app.delete("/api/codex/accounts/{name}", status_code=204)
    async def codex_account_delete(
        name: str,
        _: dict = Depends(require_token),
    ) -> None:
        try:
            existed = await codex_auth.delete_account(name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not existed:
            raise HTTPException(404, f"no saved account named {name!r}")
        return None

    # ------ skills --------------------------------------------------------

    _SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

    def _slug_skill(name: str) -> str:
        s = (name or "").strip().lower().replace(" ", "-")
        s = re.sub(r"[^a-z0-9_-]", "", s)
        return s

    def _reload_skills() -> list[Skill]:
        skills = load_skills(state.settings.skills_dir)
        state.skills = skills
        logger.info("skills_reloaded", count=len(skills), names=[s.name for s in skills])
        return skills

    @app.get("/api/skills", response_model=list[SkillOut])
    async def list_skills(_: dict = Depends(require_token)) -> list[SkillOut]:
        return [SkillOut(name=s.name, summary=s.summary) for s in state.skills]

    @app.get("/api/skills/{name}", response_model=SkillDetailOut)
    async def get_skill(
        name: str, _: dict = Depends(require_token)
    ) -> SkillDetailOut:
        slug = _slug_skill(name)
        for s in state.skills:
            if s.name == slug:
                return SkillDetailOut(name=s.name, summary=s.summary, content=s.skill_md)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "skill not found")

    @app.post("/api/skills", response_model=SkillDetailOut, status_code=201)
    async def create_skill(
        req: SkillCreateRequest, _: dict = Depends(require_token)
    ) -> SkillDetailOut:
        slug = _slug_skill(req.name)
        if not slug or not _SKILL_NAME_RE.match(slug):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid skill name")
        skill_dir = state.settings.skills_dir / slug
        if skill_dir.exists():
            raise HTTPException(status.HTTP_409_CONFLICT, "skill already exists")
        skill_dir.mkdir(parents=True, exist_ok=False)
        (skill_dir / "SKILL.md").write_text(req.content, encoding="utf-8")
        skills = _reload_skills()
        for s in skills:
            if s.name == slug:
                return SkillDetailOut(name=s.name, summary=s.summary, content=s.skill_md)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "skill not loaded after write")

    @app.put("/api/skills/{name}", response_model=SkillDetailOut)
    async def update_skill(
        name: str,
        req: SkillUpsertRequest,
        _: dict = Depends(require_token),
    ) -> SkillDetailOut:
        slug = _slug_skill(name)
        skill_dir = state.settings.skills_dir / slug
        if not skill_dir.is_dir():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "skill not found")
        (skill_dir / "SKILL.md").write_text(req.content, encoding="utf-8")
        skills = _reload_skills()
        for s in skills:
            if s.name == slug:
                return SkillDetailOut(name=s.name, summary=s.summary, content=s.skill_md)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "skill not loaded after write")

    @app.delete("/api/skills/{name}", status_code=204)
    async def delete_skill(
        name: str, _: dict = Depends(require_token)
    ) -> Response:
        slug = _slug_skill(name)
        skill_dir = state.settings.skills_dir / slug
        if not skill_dir.is_dir():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "skill not found")
        import shutil
        shutil.rmtree(skill_dir)
        _reload_skills()
        return Response(status_code=204)

    # ------ PIN management ----------------------------------------------

    @app.post("/api/auth/pin", status_code=204)
    async def change_pin(
        req: PinChangeRequest, _: dict = Depends(require_token)
    ) -> Response:
        if req.current_pin != state.settings.api_pin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "PIN saat ini salah")
        new_pin = req.new_pin.strip()
        if not new_pin:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "PIN baru kosong")
        # Persist to /host/opt/codex-agent/.env (the file env_file in compose).
        env_path = Path("/host/opt/codex-agent/.env")
        if not env_path.exists():
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, ".env file tidak ditemukan")
        existing = env_path.read_text(encoding="utf-8").splitlines()
        out_lines = []
        replaced = False
        for ln in existing:
            if ln.lstrip().startswith("API_PIN=") or ln.lstrip().startswith("API_PIN ="):
                out_lines.append(f"API_PIN={new_pin}")
                replaced = True
            else:
                out_lines.append(ln)
        if not replaced:
            out_lines.append(f"API_PIN={new_pin}")
        env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        # Update in-memory settings so subsequent /api/auth/login uses the new PIN
        # without requiring a container restart.
        state.settings.api_pin = new_pin
        get_settings.cache_clear()
        logger.info("pin_changed")
        return Response(status_code=204)

    # ------ chat WebSocket ------------------------------------------------

    @app.websocket("/api/ws/chat/{session_id}")
    async def chat_ws(
        ws: WebSocket,
        session_id: int,
        token: str = Query(...),
    ) -> None:
        # Verify token before accepting (raises before handshake completes).
        payload = auth_mod.verify_token(token, state.settings.api_jwt_secret)
        if payload is None:
            await ws.close(code=4401)
            return

        thr = await state.db.get_thread(session_id)
        if not thr or thr["user_id"] != _user_id():
            await ws.close(code=4404)
            return

        await ws.accept()
        await state.db.set_active_thread(_user_id(), session_id)

        bus = await turn_registry.get(session_id)
        queue = bus.subscribe()

        # Replay everything we've already published this turn so the client
        # can rebuild the partial UI on reconnect (back button, network blip).
        try:
            for ev in list(bus.events):
                await _ws_send(ws, ev)
            if bus.busy():
                await _ws_send(ws, {"type": "turn_resumed"})
        except Exception:  # noqa: BLE001
            bus.unsubscribe(queue)
            return

        async def _pump_in() -> None:
            """Read user_message frames and dispatch them to the bus."""
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await _ws_send(ws, {"type": "error", "text": "invalid JSON"})
                    continue

                mtype = msg.get("type")
                if mtype == "ping":
                    await _ws_send(ws, {"type": "pong"})
                    continue
                if mtype == "interrupt":
                    # Also wake up any pending ask_user the agent is blocked on,
                    # otherwise the codex script keeps long-polling and the
                    # task.cancel() below is delayed until next subprocess I/O.
                    await ASK_USER.cancel_for_session(session_id)
                    if bus.task is not None and not bus.task.done():
                        bus.task.cancel()
                        await _ws_send(ws, {"type": "info", "text": "turn dibatalkan"})
                    else:
                        await _ws_send(ws, {"type": "info", "text": "tidak ada turn yang berjalan"})
                    continue
                if mtype != "user_message":
                    await _ws_send(ws, {"type": "error", "text": f"unknown message type {mtype!r}"})
                    continue

                if bus.busy():
                    await _ws_send(ws, {
                        "type": "error",
                        "text": "another turn is still in progress; wait for turn_done.",
                    })
                    continue

                user_text = (msg.get("text") or "").strip()
                attachment_ids = list(msg.get("attachment_ids") or [])
                await _start_user_turn(
                    session_id=session_id,
                    user_text=user_text,
                    attachment_ids=attachment_ids,
                )

        async def _pump_out() -> None:
            """Drain the bus subscription and forward to the live socket."""
            while True:
                ev = await queue.get()
                await _ws_send(ws, ev)

        in_task = asyncio.create_task(_pump_in(), name=f"ws-in-{session_id}")
        out_task = asyncio.create_task(_pump_out(), name=f"ws-out-{session_id}")
        try:
            done, pending = await asyncio.wait(
                {in_task, out_task},
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for t in pending:
                t.cancel()
            for t in done:
                exc = t.exception()
                if exc and not isinstance(exc, WebSocketDisconnect):
                    logger.exception("ws_pump_failed", exc_info=exc)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("ws_unhandled")
        finally:
            bus.unsubscribe(queue)
            for t in (in_task, out_task):
                if not t.done():
                    t.cancel()


# ---------------------------------------------------------------------------
# Chat-turn engine (used by the WebSocket handler)
# ---------------------------------------------------------------------------


async def _ws_send(ws: WebSocket, payload: dict) -> None:
    try:
        await ws.send_text(json.dumps(payload, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        # client probably gone; let the outer loop deal with it.
        raise


async def _start_user_turn(
    *,
    session_id: int,
    user_text: str,
    attachment_ids: list[str],
) -> None:
    """Schedule a detached codex turn on the session's bus.

    The codex run is owned by the bus (not the WS), so client disconnects
    don't cancel it. Events are persisted to the bus's replay buffer +
    fanned out to all live subscribers."""

    async def _coro(bus: SessionBus) -> None:
        await _run_user_turn(
            bus=bus,
            session_id=session_id,
            user_text=user_text,
            attachment_ids=attachment_ids,
        )

    bus, started = await turn_registry.start_turn(session_id, _coro)
    if not started:
        # Another turn already running — should have been caught by the
        # caller, but stay defensive.
        await bus.publish({
            "type": "error",
            "text": "another turn is still in progress; wait for turn_done.",
        })


async def _run_user_turn(
    *,
    bus: SessionBus,
    session_id: int,
    user_text: str,
    attachment_ids: list[str],
) -> None:
    """Detached codex turn body — emits everything via the bus."""
    settings = state.settings
    db = state.db

    # Resolve attachment_ids to actual files
    images: list[str] = []
    descriptions: list[str] = []
    transcripts: list[str] = []
    resolved: list[att_mod.Attachment] = []
    for aid in attachment_ids:
        att = _uploads_by_id.get(aid)
        if not att:
            await bus.publish({"type": "warning", "text": f"unknown attachment_id {aid}"})
            continue
        resolved.append(att)
        descriptions.append(att_mod.describe(att))
        if att.kind == "photo":
            images.append(str(att.host_path))

    # Transcribe any voice/audio that didn't already have a transcript on upload
    for att in resolved:
        if att.kind not in {"voice", "audio"}:
            continue
        await bus.publish({
            "type": "progress",
            "text": f"🎤 Transcribing {att.name}…",
        })
        try:
            tx = await transcribe.transcribe(att.container_path)
        except Exception:  # noqa: BLE001
            logger.exception("transcribe_failed_in_turn", path=str(att.container_path))
            tx = ""
        if tx:
            transcripts.append(f"[{att.name}] {tx}")
            await bus.publish({
                "type": "transcript",
                "name": att.name,
                "transcript": tx,
            })

    # Build augmented prompt
    prompt_parts: list[str] = []
    if user_text:
        prompt_parts.append(user_text)
    if descriptions:
        prompt_parts.append("\nAttachments (already saved on the host):\n" + "\n".join(descriptions))
    if transcripts:
        prompt_parts.append("\nAudio transcripts:\n" + "\n\n".join(transcripts))
    final_user_text = "\n\n".join([p for p in prompt_parts if p]).strip()

    if not final_user_text:
        await bus.publish({"type": "error", "text": "empty message"})
        await bus.publish({"type": "turn_done", "ok": False, "error": "empty message", "rate_limited": False})
        return

    # Persist user message + maybe auto-rename thread.
    await db.append_message(
        chat_id=session_id, user_id=settings.owner_user_id,
        role="user", content=final_user_text, thread_id=session_id,
    )
    await bus.publish({
        "type": "user_message_persisted",
        "content": final_user_text,
    })
    thr = await db.get_thread(session_id)
    if thr and thr["auto_named"] and (thr["name"].startswith("Untitled") or thr["name"] == "Untitled"):
        # Rename only on first user message
        seed = user_text if user_text else (descriptions[0] if descriptions else "")
        new_name = _slugify_for_thread(seed)
        logger.info(
            "autoname_check",
            session_id=session_id,
            current_name=thr["name"],
            seed=seed[:50],
            new_name=new_name,
            will_rename=bool(new_name) and new_name != thr["name"],
        )
        if new_name and new_name != thr["name"]:
            await db.rename_thread(session_id, new_name, mark_manual=False)
            final_name = (await db.get_thread(session_id) or {}).get("name", new_name)
            await bus.publish({"type": "session_renamed", "id": session_id, "name": final_name})

    # Build prompt context
    history = await db.get_thread_history(session_id, settings.history_max_messages)
    # Drop the just-appended message — codex_runner adds it back via user_text
    if history and history[-1]["role"] == "user":
        history = history[:-1]
    system_instruction = build_system_instruction(state.skills)

    async def _on_progress(text: str) -> None:
        await bus.publish({"type": "progress", "text": text})

    async def _on_event(ev: dict) -> None:
        # Forward the rich v2 event for clients that understand the schema.
        await bus.publish(ev)
        # Derive a human-readable status label from the event so mobile can
        # render a persistent "Codex sedang …" header without parsing rich
        # events itself.
        label = _status_label_for_event(ev)
        if label:
            await bus.publish({"type": "task_status", "status": "working", "label": label})

    # Forward model + reasoning_effort to the Codex CLI so we always run on
    # the configured model (default: gpt-5.5) with reasoning summaries enabled.
    extra_codex_args: list[str] = [
        "-m", settings.codex_model,
        "-c", f"model_reasoning_effort={settings.codex_reasoning_effort}",
        "-c", "model_reasoning_summary=detailed",
    ]

    await bus.publish({"type": "turn_started"})
    await bus.publish({"type": "task_status", "status": "working", "label": "Codex sedang berpikir…"})
    try:
        result = await run_codex_with_rotation(
            system_instruction=system_instruction,
            history=history,
            user_text=final_user_text,
            images=images or None,
            on_progress=_on_progress,
            on_event=_on_event,
            sandbox=settings.codex_sandbox,
            workdir=str(settings.codex_workdir),
            extra_args=extra_codex_args,
            timeout_seconds=settings.codex_timeout_seconds,
            codex_home=settings.codex_home,
            session_id=session_id,
            agent_token=state.agent_token,
            agent_loopback_url=settings.agent_loopback_url,
        )
    except asyncio.CancelledError:
        await bus.publish({"type": "task_status", "status": "idle", "label": None})
        await bus.publish({
            "type": "turn_done",
            "ok": False,
            "error": "turn cancelled",
            "rate_limited": False,
        })
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("codex_turn_failed")
        await bus.publish({"type": "task_status", "status": "idle", "label": None})
        await bus.publish({
            "type": "turn_done",
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "rate_limited": False,
        })
        return

    if result.text:
        await db.append_message(
            chat_id=session_id, user_id=settings.owner_user_id,
            role="assistant", content=result.text, thread_id=session_id,
        )
    await bus.publish({
        "type": "agent_message",
        "text": result.text,
    })
    _record_usage(result.usage)
    await bus.publish({"type": "task_status", "status": "idle", "label": None})
    await bus.publish({
        "type": "turn_done",
        "ok": result.error is None,
        "error": result.error,
        "rate_limited": result.rate_limited,
        "usage": result.usage,
    })


def _status_label_for_event(ev: dict) -> str | None:
    """Map a v2 rich event to a short status string suitable for the mobile
    header. Returns ``None`` when the event shouldn't update the status."""
    t = ev.get("type")
    if t == "reasoning":
        return "Berpikir…" if ev.get("status") == "running" else None
    if t == "tool_call":
        kind = ev.get("kind")
        status = ev.get("status")
        if kind == "command_execution":
            cmd = (ev.get("command") or "").strip().splitlines()[0:1]
            head = cmd[0][:48] + ("…" if cmd and len(cmd[0]) > 48 else "") if cmd else ""
            if status == "running":
                return f"Menjalankan: {head}" if head else "Menjalankan shell…"
            if status == "ok":
                return None
            if status == "failed":
                return f"Shell gagal: {head}" if head else "Shell gagal"
        if kind == "file_change":
            path = ev.get("path") or ""
            short = path.split("/")[-1] if path else ""
            change = ev.get("change_kind") or "edit"
            if status == "running":
                return f"Mengedit {short}…" if short else "Mengedit file…"
            if status == "ok":
                return None
            if status == "failed":
                return f"Edit {short} gagal" if short else "Edit gagal"
            return f"{change} {short}"
        if kind == "web_search":
            q = ev.get("query") or ""
            if status == "running":
                return f"Mencari: {q[:48]}…" if q else "Mencari di web…"
            return None
        if kind == "mcp_tool_call":
            n = ev.get("name") or "tool"
            if status == "running":
                return f"MCP {n}…"
            return None
    if t == "plan":
        steps = ev.get("steps") or []
        if isinstance(steps, list) and steps:
            inprog = next((s for s in steps if isinstance(s, dict) and s.get("status") == "in_progress"), None)
            if inprog and isinstance(inprog, dict):
                title = inprog.get("title") or ""
                if title:
                    return f"Step: {title[:60]}"
        return "Update plan…"
    return None


def _slugify_for_thread(text: str, max_len: int = 40) -> str:
    """Mirror of db._slugify_for_thread (kept here to avoid the import cycle)."""
    import re as _re
    text = (text or "").strip().splitlines()[0] if text else ""
    text = _re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text
