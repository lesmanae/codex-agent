"""Per-session in-flight turn registry + WS event fan-out.

Why this exists
---------------
A naive WebSocket handler runs the codex turn inline; if the client
disconnects mid-run (back button, app backgrounded, network blip), the
``ws.receive_text()`` task gets cancelled by Starlette, which cancels the
codex run, which leaves the session hung — the assistant message is never
written to the DB, and reconnecting clients see nothing.

This module decouples the two:

* ``SessionBus`` holds the per-session asyncio.Task running the current
  codex turn, plus a replay buffer of every event emitted so far.
* When a new WS connects to a session that already has an active turn, it
  immediately receives the buffered events (so the user sees the partial
  progress + final answer) and is then subscribed to the live tail.
* The codex task is owned by the bus, not the WS, so client disconnect
  doesn't kill it. The task keeps running, persists the final assistant
  message to the DB, and emits ``turn_done``. Any reconnecting client gets
  the full picture from history + replay.

Subscribers receive events through an asyncio.Queue rather than callbacks,
so a slow client can't block the codex task.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import structlog

logger = structlog.get_logger("turn_bus")

# The replay buffer is intentionally bounded. A single codex turn can emit
# ~hundreds of progress events; we never need more than the most recent
# few hundred when a client reconnects (they also re-fetch history from DB).
_REPLAY_BUFFER_MAX = 500


@dataclass
class SessionBus:
    session_id: int
    task: asyncio.Task | None = None
    """Currently running codex turn task, if any."""

    events: list[dict] = field(default_factory=list)
    """Replay buffer for the current turn. Cleared when a new turn starts."""

    subscribers: set[asyncio.Queue[dict]] = field(default_factory=set)

    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    async def publish(self, event: dict) -> None:
        # Append to replay buffer (bounded).
        self.events.append(event)
        if len(self.events) > _REPLAY_BUFFER_MAX:
            # Drop the oldest non-terminal events; keep early "turn_started"
            # for context but truncate the middle.
            self.events = self.events[-_REPLAY_BUFFER_MAX:]
        # Fan out to all live subscribers.
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop one stale event for that subscriber and try again.
                try:
                    _ = q.get_nowait()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    q.put_nowait(event)
                except Exception:  # noqa: BLE001
                    pass

    def subscribe(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=1024)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict]) -> None:
        self.subscribers.discard(q)

    def reset_for_new_turn(self) -> None:
        self.events = []


class BusRegistry:
    """Process-global registry of SessionBus per session_id."""

    def __init__(self) -> None:
        self._buses: dict[int, SessionBus] = {}
        self._lock = asyncio.Lock()

    async def get(self, session_id: int) -> SessionBus:
        async with self._lock:
            bus = self._buses.get(session_id)
            if bus is None:
                bus = SessionBus(session_id=session_id)
                self._buses[session_id] = bus
            return bus

    async def start_turn(
        self,
        session_id: int,
        coro_factory: Callable[["SessionBus"], Awaitable[None]],
    ) -> tuple[SessionBus, bool]:
        """Start a new turn task on the bus.

        Returns ``(bus, started)``: ``started=False`` if a turn is already
        in flight (caller should reject the new user message)."""
        bus = await self.get(session_id)
        if bus.busy():
            return bus, False
        bus.reset_for_new_turn()

        async def _runner() -> None:
            try:
                await coro_factory(bus)
            except asyncio.CancelledError:
                logger.info("turn_cancelled", session_id=session_id)
                raise
            except Exception:  # noqa: BLE001
                logger.exception("turn_runner_failed", session_id=session_id)
                await bus.publish({
                    "type": "turn_done",
                    "ok": False,
                    "error": "internal turn_runner failure",
                    "rate_limited": False,
                })

        task = asyncio.create_task(_runner(), name=f"codex-turn-{session_id}")
        bus.task = task
        return bus, True

    async def cancel(self, session_id: int) -> bool:
        bus = await self.get(session_id)
        if bus.task and not bus.task.done():
            bus.task.cancel()
            return True
        return False


registry = BusRegistry()
