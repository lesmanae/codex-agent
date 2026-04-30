"""Async driver for the Codex CLI.

Each chat turn spawns `codex exec --json --sandbox <level>` as a subprocess,
feeds the rendered prompt over stdin, and parses the JSONL event stream from
stdout. Progress events (command executions, file edits, web searches, plan
updates) are forwarded to a caller-supplied callback so the API layer can
push them to the client over WebSocket. The final agent message is returned
as a single string when the run completes.

Why subprocess: `@openai/codex` is a Rust/Node CLI authenticated against the
user's ChatGPT Plus account. There is no first-party Python SDK that uses
ChatGPT auth. Subprocess is the cleanest integration.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import structlog

from . import codex_auth

logger = structlog.get_logger(__name__)

ProgressCb = Callable[[str], Awaitable[None]]
"""Legacy text-only progress callback. Receives the formatted one-liner
(e.g. ``'✓ `ls -la`'``)."""

EventCb = Callable[[dict[str, Any]], Awaitable[None]]
"""Rich structured-event callback. Receives a dict shaped according to the
``stream_schema_version=2`` schema documented in ``docs/event-schema.md``.
Key fields:

- ``type``: ``"tool_call"``, ``"plan"``, ``"reasoning"``
- ``id``: stable ID for the item across started/completed (Codex item id
  when present, else a generated UUID)
- ``kind``: subtype within tool_call (``command_execution``, ``file_change``,
  ``web_search``, ``mcp_tool_call``)
- ``status``: ``"running"``, ``"ok"``, ``"failed"``
- ``ts``: epoch seconds (float)
- ``item``: the raw Codex CLI item for forward-compat
- additional well-known fields per kind (``command``, ``stdout``, ``stderr``,
  ``exit_code``, ``path``, ``change_kind``, ``query``, ``name``,
  ``arguments``, ``steps``, ``text``).
"""


@dataclass
class CodexResult:
    text: str
    """Final agent message (concatenation of all agent_message items)."""

    thread_id: str | None = None
    error: str | None = None
    usage: dict | None = None
    items: list[dict] = field(default_factory=list)
    """Raw event items, useful for debugging."""

    rate_limited: bool = False
    """Set when the run failed because the active account hit a rate/usage limit."""

    stderr_tail: str = ""
    """Last lines of stderr — kept around so callers can detect rate-limit phrases."""

    _pending_agent_msg: tuple[str, str] | None = None
    """Internal: (item_id, text) of the most recent agent_message seen.

    Codex CLI emits multiple ``agent_message`` items per turn — early ones
    are 'preambles' (narration of what the model is about to do), the last
    one is the user-visible final answer. We hold the latest one pending;
    whenever a new agent_message arrives, the previous pending one is
    flushed as a ``reasoning`` rich event (shows up in ThinkingBlock).
    """


def _short(s: str, n: int = 200) -> str:
    s = s.replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _render_history(history: list[dict[str, str]]) -> str:
    if not history:
        return ""
    lines = ["", "## Previous conversation", ""]
    for m in history:
        role = "User" if m.get("role") == "user" else "Assistant"
        lines.append(f"### {role}")
        lines.append(m.get("content", "").strip())
        lines.append("")
    return "\n".join(lines)


def build_prompt(
    *,
    system_instruction: str,
    history: list[dict[str, str]],
    user_text: str,
) -> str:
    """Compose the single prompt string we feed to `codex exec`.

    Codex exec is stateless per invocation (unless using `resume`). We bake
    the system instruction + skill bundle + truncated history + new user
    message into a single markdown blob.
    """
    parts: list[str] = [system_instruction.rstrip()]
    hist = _render_history(history)
    if hist:
        parts.append(hist)
    parts.append("## New user message\n")
    parts.append(user_text.strip())
    parts.append("\nAnswer the new user message. Use tools (shell, file r/w, web) freely on the VPS. Match the user's language.")
    return "\n\n".join(parts)


async def run_codex(
    *,
    system_instruction: str,
    history: list[dict[str, str]],
    user_text: str,
    images: list[str] | None = None,
    on_progress: ProgressCb | None = None,
    on_event: EventCb | None = None,
    sandbox: str = "danger-full-access",
    workdir: str = "/workspace",
    extra_args: list[str] | None = None,
    timeout_seconds: int = 600,
    codex_home: str | None = None,
    use_nsenter: bool = True,
) -> CodexResult:
    """Run a single Codex exec turn and return the final agent message.

    The bot container has `pid: host` + `privileged: true`, so we shell out
    to `nsenter -t 1 -a -- codex exec ...`. That makes Codex run in the
    host's namespaces with full root, so Codex's built-in shell tool acts
    directly on the host and picks up the host's /root/.codex auth.

    Streams JSONL events from `codex exec --json` and routes them:
    - `item.started` / `item.completed` of type `command_execution`,
      `file_change`, `web_search`, `mcp_tool_call`, `plan_update`,
      `reasoning` → progress callback.
    - `item.completed` of type `agent_message` → appended to final text.
    - `turn.completed` → captures usage.
    - `turn.failed` / `error` → captures error.
    """
    prompt = build_prompt(
        system_instruction=system_instruction,
        history=history,
        user_text=user_text,
    )

    codex_cmd = [
        "codex",
        "exec",
        "--json",
        "--sandbox",
        sandbox,
        "--skip-git-repo-check",
        "--cd",
        workdir,
    ]
    for img in images or []:
        codex_cmd.extend(["-i", img])
    codex_cmd.append("-")  # read prompt from stdin
    if extra_args:
        codex_cmd[2:2] = list(extra_args)

    if use_nsenter:
        env_assigns = []
        if codex_home:
            env_assigns.append(f"CODEX_HOME={codex_home}")
        # `env -i` keeps the env clean; we forward only what codex needs.
        cmd = [
            "nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--",
            "env",
            *env_assigns,
            "HOME=/root",
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            *codex_cmd,
        ]
        env = None  # subprocess inherits container env, but env(1) overrides
    else:
        cmd = codex_cmd
        env = os.environ.copy()
        if codex_home:
            env["CODEX_HOME"] = codex_home

    logger.info("codex_spawn", cmd=cmd, prompt_chars=len(prompt))

    # Bump the StreamReader buffer well past asyncio's 64KB default so large
    # JSONL events from codex (file dumps, decompiled bytecode, big stdout
    # captures) don't blow up with "Separator is found, but chunk is longer
    # than limit". 16 MiB per line is enough for any sensible event.
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        limit=16 * 1024 * 1024,
    )

    assert proc.stdin and proc.stdout and proc.stderr
    proc.stdin.write(prompt.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()

    result = CodexResult(text="")
    final_chunks: list[str] = []
    stderr_buf: list[str] = []

    async def _safe_readline(stream: asyncio.StreamReader) -> bytes | None:
        """Read one line, but recover from oversized lines by draining + skipping
        instead of crashing the whole turn."""
        try:
            return await stream.readline()
        except asyncio.LimitOverrunError as e:
            # The separator IS in the buffer past the limit. Drain the bytes
            # we already have plus everything up to and including the next \n,
            # discard them, and continue. Returns the giant line truncated.
            try:
                truncated = await stream.readexactly(e.consumed)
            except asyncio.IncompleteReadError as ie:
                truncated = ie.partial
            # Now consume up to and including the actual newline (raw read,
            # also chunked to avoid another LimitOverrunError).
            while True:
                try:
                    chunk = await stream.read(64 * 1024)
                except Exception:
                    break
                if not chunk:
                    break
                truncated += chunk
                if b"\n" in chunk:
                    break
            logger.warning(
                "codex_oversized_line",
                bytes=len(truncated),
                preview=_short(truncated[:500].decode("utf-8", "replace"), 200),
            )
            return truncated  # likely unparseable, but consumer will tolerate
        except ValueError:
            # Catch-all for any other readline limit edge case in older
            # Python; treat as EOF on this stream.
            return None

    async def consume_stderr() -> None:
        while True:
            raw = await _safe_readline(proc.stderr)  # type: ignore[arg-type]
            if not raw:
                break
            try:
                line = raw.decode("utf-8", "replace").rstrip()
            except Exception:
                continue
            if line:
                stderr_buf.append(line)
                logger.debug("codex_stderr", line=_short(line, 500))

    async def consume_stdout() -> None:
        while True:
            raw = await _safe_readline(proc.stdout)  # type: ignore[arg-type]
            if not raw:
                break
            try:
                line = raw.decode("utf-8", "replace").rstrip()
            except Exception:
                continue
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                # Non-JSON line (rare with --json). Treat as final text fallback.
                final_chunks.append(line)
                continue
            await _handle_event(evt, result, final_chunks, on_progress, on_event)

    consumer_task = asyncio.gather(consume_stdout(), consume_stderr())

    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        result.error = f"codex timed out after {timeout_seconds}s"

    await consumer_task

    stderr_tail = "\n".join(stderr_buf[-20:])
    result.stderr_tail = stderr_tail

    if proc.returncode not in (0, None) and not result.error:
        result.error = f"codex exited {proc.returncode}: {_short(stderr_tail, 500)}"

    if final_chunks:
        result.text = "\n".join(final_chunks).strip()

    # Rate-limit detection — error message OR stderr tail OR final text.
    haystack = " ".join(filter(None, [result.error or "", stderr_tail, result.text]))
    if codex_auth.is_rate_limit_error(haystack):
        result.rate_limited = True

    if result.error and not result.text:
        result.text = f"⚠️ Codex error: {result.error}"

    logger.info(
        "codex_done",
        rc=proc.returncode,
        text_chars=len(result.text),
        items=len(result.items),
        error=result.error,
        rate_limited=result.rate_limited,
        usage=result.usage,
    )

    return result


async def run_codex_with_rotation(
    *,
    system_instruction: str,
    images: list[str] | None = None,
    history: list[dict[str, str]],
    user_text: str,
    on_progress: ProgressCb | None = None,
    on_event: EventCb | None = None,
    sandbox: str = "danger-full-access",
    workdir: str = "/workspace",
    extra_args: list[str] | None = None,
    timeout_seconds: int = 600,
    codex_home: str | None = None,
    max_rotations: int = 5,
) -> CodexResult:
    """Run codex; on rate-limit error, rotate to the next account and retry.

    Stops when (a) we get a non-rate-limit result, or (b) we've rotated
    max_rotations times, or (c) there is no usable next account.
    """
    rotated_through: list[str] = []
    for attempt in range(max_rotations + 1):
        result = await run_codex(
            system_instruction=system_instruction,
            history=history,
            user_text=user_text,
            images=images,
            on_progress=on_progress,
            on_event=on_event,
            sandbox=sandbox,
            workdir=workdir,
            extra_args=extra_args,
            timeout_seconds=timeout_seconds,
            codex_home=codex_home,
        )
        if not result.rate_limited:
            try:
                await codex_auth.mark_active_used()
            except Exception:  # noqa: BLE001
                pass
            return result

        marked = await codex_auth.mark_active_exhausted()
        if marked:
            rotated_through.append(marked)
        logger.warning(
            "codex_rate_limited", marked_exhausted=marked, attempt=attempt + 1,
        )
        if on_progress is not None:
            try:
                await on_progress(f"⏭ rate-limit pada `{marked}` — coba akun lain")
            except Exception:  # noqa: BLE001
                pass

        next_info = await codex_auth.rotate_to_next()
        if next_info is None:
            result.error = (
                f"All accounts rate-limited (rotated through: {', '.join(rotated_through) or 'none'})."
            )
            result.text = f"⚠️ {result.error}"
            return result

    result.error = (
        f"Rate-limit retry budget exhausted after {max_rotations} rotations "
        f"(through: {', '.join(rotated_through)})."
    )
    result.text = f"⚠️ {result.error}"
    return result


async def _handle_event(
    evt: dict,
    result: CodexResult,
    final_chunks: list[str],
    on_progress: ProgressCb | None,
    on_event: EventCb | None,
) -> None:
    etype = evt.get("type", "")
    if etype == "thread.started":
        result.thread_id = evt.get("thread_id")
        return
    if etype == "turn.completed":
        result.usage = evt.get("usage")
        # Flush the last pending agent_message as the final answer text.
        if result._pending_agent_msg is not None:
            final_chunks.append(result._pending_agent_msg[1])
            result._pending_agent_msg = None
        return
    if etype in ("turn.failed", "error"):
        msg = evt.get("error", {}).get("message") if isinstance(evt.get("error"), dict) else evt.get("message")
        result.error = str(msg or evt)
        # Still surface whatever partial answer we had.
        if result._pending_agent_msg is not None:
            final_chunks.append(result._pending_agent_msg[1])
            result._pending_agent_msg = None
        return

    if etype not in ("item.started", "item.completed", "item.updated"):
        return

    item = evt.get("item") or {}
    itype = item.get("type", "")
    result.items.append(item)

    # Emit a compact log line per item so we can diagnose which item-types
    # this Codex CLI version actually ships. Keeps only the keys, never the
    # full body (which may be large).
    logger.info(
        "codex_item",
        etype=etype,
        itype=itype,
        keys=sorted(list(item.keys()))[:20],
    )

    # Agent messages: the LAST one of the turn is the user-visible answer;
    # all earlier ones are "preambles" (narration before/between tool calls)
    # and are re-routed into the thinking stream so the UI can show what the
    # model is about to do, rather than mixing the preamble into the final
    # answer bubble.
    if etype == "item.completed" and itype == "agent_message":
        text = item.get("text") or item.get("message") or ""
        if not text:
            return
        item_id = item.get("id") or f"am_{int(time.time() * 1000)}"
        prev = result._pending_agent_msg
        result._pending_agent_msg = (item_id, text)
        if prev is not None and on_event is not None:
            prev_id, prev_text = prev
            try:
                await on_event({
                    "type": "reasoning",
                    "id": prev_id,
                    "status": "ok",
                    "text": prev_text,
                    "ts": time.time(),
                    "item": {"type": "agent_message", "id": prev_id},
                    "source": "preamble",
                })
            except Exception:  # noqa: BLE001
                pass
        return

    if on_event is not None:
        rich = _to_rich_event(etype, itype, item)
        if rich is not None:
            try:
                await on_event(rich)
            except Exception:  # noqa: BLE001
                pass

    if on_progress is None:
        return

    label = _format_progress_event(etype, itype, item)
    if label:
        try:
            await on_progress(label)
        except Exception:  # noqa: BLE001
            pass


def _to_rich_event(etype: str, itype: str, item: dict) -> dict | None:
    """Convert a Codex CLI item event into the v2 rich event schema.

    Returns None for item types we do not surface (e.g. agent_message,
    which is handled via the final-text path).
    """
    started = etype == "item.started"
    completed = etype == "item.completed"
    if not (started or completed):
        return None

    item_id = str(item.get("id") or item.get("item_id") or uuid.uuid4())
    base: dict[str, Any] = {
        "id": item_id,
        "ts": time.time(),
        "item": item,
    }

    if itype in ("command_execution", "file_change", "web_search", "mcp_tool_call"):
        base["type"] = "tool_call"
        base["kind"] = itype
        if started:
            base["status"] = "running"
        else:
            raw_status = item.get("status")
            ec = item.get("exit_code")
            if raw_status == "failed" or (ec not in (None, 0)):
                base["status"] = "failed"
            else:
                base["status"] = "ok"

        if itype == "command_execution":
            base["command"] = item.get("command") or ""
            if completed:
                base["exit_code"] = item.get("exit_code")
                base["stdout"] = item.get("stdout")
                base["stderr"] = item.get("stderr")
                base["aggregated_output"] = item.get("aggregated_output")
        elif itype == "file_change":
            base["path"] = item.get("path")
            base["change_kind"] = item.get("change_kind") or item.get("operation") or "edit"
            if completed:
                base["diff"] = item.get("diff") or item.get("unified_diff")
                base["contents"] = item.get("contents") or item.get("new_contents")
        elif itype == "web_search":
            base["query"] = item.get("query") or ""
            if completed:
                base["results"] = item.get("results")
        elif itype == "mcp_tool_call":
            base["name"] = item.get("name") or item.get("tool") or "mcp"
            base["arguments"] = item.get("arguments") or item.get("args")
            if completed:
                base["result"] = item.get("result") or item.get("output")
        return base

    if itype == "plan_update":
        if not completed:
            return None
        base["type"] = "plan"
        base["status"] = "ok"
        base["steps"] = item.get("steps") or []
        return base

    if itype == "reasoning":
        # Codex CLI has shipped reasoning text under several field names
        # depending on version. Try everything we've seen in the wild.
        text = (
            item.get("text")
            or item.get("message")
            or item.get("summary")
            or item.get("content")
            or item.get("reasoning")
            or item.get("thought")
        )
        # Some versions wrap the text in an array of content parts, e.g.
        # [{"type": "text", "text": "..."}] or [{"text": "..."}].
        if not text:
            parts = item.get("parts") or item.get("content_parts")
            if isinstance(parts, list):
                chunks: list[str] = []
                for p in parts:
                    if isinstance(p, str):
                        chunks.append(p)
                    elif isinstance(p, dict):
                        t = p.get("text") or p.get("content") or ""
                        if t:
                            chunks.append(str(t))
                text = "\n".join(chunks) if chunks else None
        logger.info(
            "reasoning_item",
            completed=completed,
            has_text=bool(text),
            keys=sorted(list(item.keys())),
            preview=(str(text)[:80] if text else None),
        )
        if not text:
            return None
        base["type"] = "reasoning"
        base["status"] = "ok" if completed else "running"
        base["text"] = str(text)
        return base

    return None


def _format_progress_event(etype: str, itype: str, item: dict) -> str | None:
    """Render a Codex event as a one-line progress string for the client."""
    started = etype == "item.started"
    completed = etype == "item.completed"
    icon_run = "🛠"
    icon_ok = "✓"
    icon_fail = "✗"

    if itype == "command_execution":
        cmd = item.get("command") or ""
        cmd_short = _short(cmd, 100)
        if started:
            return f"{icon_run} `{cmd_short}`"
        if completed:
            status = item.get("status", "")
            ec = item.get("exit_code")
            mark = icon_fail if (status == "failed" or (ec not in (None, 0))) else icon_ok
            return f"{mark} `{cmd_short}`"
    elif itype == "file_change":
        path = item.get("path") or ""
        op = item.get("change_kind") or item.get("operation") or "edit"
        if completed:
            return f"📝 {op} `{_short(path, 80)}`"
    elif itype == "web_search":
        q = item.get("query") or ""
        if started:
            return f"🌐 search: {_short(q, 80)}"
    elif itype == "mcp_tool_call":
        name = item.get("name") or item.get("tool") or "mcp"
        if started:
            return f"🔌 mcp: {name}"
        if completed:
            return f"{icon_ok} mcp: {name}"
    elif itype == "plan_update":
        if completed:
            steps = item.get("steps") or []
            head = next((s.get("title") for s in steps if s.get("status") == "in_progress"), None)
            if head:
                return f"📋 {_short(head, 100)}"
    elif itype == "reasoning":
        # Skip — too noisy.
        return None
    return None
