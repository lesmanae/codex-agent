"""Interactive ask-user channel for the agent.

Why this exists
---------------
We want the agent to be able to *pause* a running turn and ask the user a
multiple-choice question (e.g. "Stack apa? React/Vue/Svelte?"). The mobile
app renders the options as tappable chips and posts the user's answer back
over REST.

Flow
----
1. The agent invokes ``scripts/ask_user.py`` via its sandboxed shell tool.
   The script POSTs ``/api/ask-user/start`` with the question + options. The
   backend creates a pending question (``AskUser`` row) bound to the
   session, stores an asyncio.Future for the eventual answer, and emits an
   ``ask_user`` event over the SessionBus so all subscribed mobile clients
   render the chips.

2. The script then long-polls ``/api/ask-user/wait`` (5 minute timeout per
   poll). Inside the backend, this endpoint awaits the future for up to
   ~290s and returns 408 on timeout so the script can re-poll without the
   request being killed by the reverse proxy.

3. The user taps a chip in the app → ``POST /api/ask-user/answer`` resolves
   the future. The next poll returns the chosen text. The script prints
   the answer to stdout and exits 0; the agent reads stdout and continues
   the turn with the user's choice.

The pending-question state is process-local (in-memory). That's fine
because the agent CLI process runs inside the same backend process as the
WS, so they share memory. If the backend restarts mid-question the agent
turn is also dead, so we don't need persistent state.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger("ask_user")


@dataclass
class PendingQuestion:
    id: str
    session_id: int
    question: str
    options: list[str]
    allow_multiple: bool
    allow_freetext: bool
    created_at: float
    # Always created from the running event loop in ``AskUserRegistry.start``.
    future: asyncio.Future[str] = field(default=None)  # type: ignore[assignment]


class AskUserRegistry:
    """In-memory registry of pending interactive questions."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingQuestion] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        session_id: int,
        question: str,
        options: list[str],
        allow_multiple: bool = False,
        allow_freetext: bool = True,
    ) -> PendingQuestion:
        loop = asyncio.get_running_loop()
        pq = PendingQuestion(
            id=secrets.token_urlsafe(12),
            session_id=session_id,
            question=question.strip(),
            options=[str(o).strip() for o in options if str(o).strip()],
            allow_multiple=bool(allow_multiple),
            allow_freetext=bool(allow_freetext),
            created_at=time.time(),
            future=loop.create_future(),
        )
        async with self._lock:
            self._pending[pq.id] = pq
        logger.info(
            "ask_user_start",
            id=pq.id,
            session_id=session_id,
            options_count=len(pq.options),
        )
        return pq

    async def answer(self, qid: str, response: str) -> bool:
        """Resolve a pending question. Returns True if it was pending."""
        async with self._lock:
            pq = self._pending.pop(qid, None)
        if pq is None:
            return False
        if not pq.future.done():
            pq.future.set_result(response)
        logger.info("ask_user_answer", id=qid, len=len(response))
        return True

    async def wait(self, qid: str, timeout: float = 290.0) -> str | None:
        """Block until answered or timeout. Returns ``None`` on timeout so
        the caller can re-poll. Returns the answer string on success."""
        async with self._lock:
            pq = self._pending.get(qid)
        if pq is None:
            return ""  # already answered (or never existed) — caller exits

        try:
            return await asyncio.wait_for(asyncio.shield(pq.future), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def cancel_for_session(self, session_id: int) -> int:
        """Cancel all pending questions for a session (e.g. user pressed Stop)."""
        cancelled = 0
        async with self._lock:
            ids = [qid for qid, pq in self._pending.items() if pq.session_id == session_id]
            for qid in ids:
                pq = self._pending.pop(qid)
                if not pq.future.done():
                    pq.future.set_result("__cancelled__")
                cancelled += 1
        if cancelled:
            logger.info("ask_user_cancel_session", session_id=session_id, cancelled=cancelled)
        return cancelled


# Process-global singleton.
ASK_USER = AskUserRegistry()
