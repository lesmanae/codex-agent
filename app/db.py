"""SQLite persistence for per-user conversation history, threads, admin
flags, and small key-value config.

Threads model:
  - Each user has zero or more `threads` (named, persistent).
  - Exactly one thread is `active` at a time per user — the bot routes
    new messages to that thread.
  - On a user's first ever message, a thread named "Untitled" is created
    automatically; once the first user message is recorded, the thread is
    auto-renamed to the first ~40 chars of that message (ChatGPT style).
  - Each row in `messages` carries a `thread_id` so history is
    per-thread, not per-user.

The `chat_id` column on `messages` is kept around for backwards-compat
(old rows had no thread) but is no longer used for fetching history.
"""
from __future__ import annotations

import base64
import hashlib
import re
import time
from pathlib import Path
from typing import Any

import aiosqlite
from cryptography.fernet import Fernet


def _fernet(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _slugify_for_thread(text: str, max_len: int = 40) -> str:
    """Turn a free-form user message into a short, displayable thread name."""
    text = (text or "").strip().splitlines()[0] if text else ""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "Untitled"
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


class BotDB:
    def __init__(self, path: Path, encryption_secret: str) -> None:
        self.path = path
        self._fernet = _fernet(encryption_secret)
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        # Tables first (idempotent); indices that reference new columns come
        # after the legacy ALTER step so the migration ordering is safe.
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                api_key_enc BLOB,
                model TEXT,
                system_prompt_override TEXT,
                is_admin INTEGER DEFAULT 0,
                updated_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS global_settings (
                key TEXT PRIMARY KEY,
                value_enc BLOB,
                updated_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                thread_id INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_messages_chatuser
                ON messages(chat_id, user_id, id);
            CREATE TABLE IF NOT EXISTS chat_sessions (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                codex_thread_id TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS threads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                auto_named INTEGER NOT NULL DEFAULT 1,
                UNIQUE(user_id, name)
            );
            CREATE INDEX IF NOT EXISTS idx_threads_user_updated
                ON threads(user_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS active_thread (
                user_id INTEGER PRIMARY KEY,
                thread_id INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            """
        )
        # Backfill: ensure legacy DBs have the thread_id column on messages.
        await self._ensure_messages_thread_id()
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, id)"
        )
        await self._conn.commit()

    async def _ensure_messages_thread_id(self) -> None:
        async with self.conn.execute("PRAGMA table_info(messages)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if "thread_id" not in cols:
            await self.conn.execute("ALTER TABLE messages ADD COLUMN thread_id INTEGER")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("BotDB.connect() not awaited yet")
        return self._conn

    # ---- admin flags -------------------------------------------------------

    async def set_user_admin(self, user_id: int, is_admin: bool) -> None:
        await self.conn.execute(
            """INSERT INTO user_settings(user_id, is_admin, updated_at)
               VALUES(?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   is_admin = excluded.is_admin,
                   updated_at = excluded.updated_at""",
            (user_id, 1 if is_admin else 0, int(time.time())),
        )
        await self.conn.commit()

    async def is_user_admin(self, user_id: int) -> bool:
        async with self.conn.execute(
            "SELECT is_admin FROM user_settings WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        return bool(row[0]) if row else False

    async def admin_count(self) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) FROM user_settings WHERE is_admin = 1"
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    # ---- threads -----------------------------------------------------------

    async def list_threads(self, user_id: int) -> list[dict[str, Any]]:
        async with self.conn.execute(
            """SELECT t.id, t.name, t.created_at, t.updated_at, t.auto_named,
                      (SELECT COUNT(*) FROM messages m WHERE m.thread_id = t.id) AS msg_count
               FROM threads t
               WHERE t.user_id = ?
               ORDER BY t.updated_at DESC""",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "created_at": r[2],
                "updated_at": r[3],
                "auto_named": bool(r[4]),
                "msg_count": r[5],
            }
            for r in rows
        ]

    async def get_thread_by_name(
        self, user_id: int, name: str
    ) -> dict[str, Any] | None:
        async with self.conn.execute(
            """SELECT id, name, created_at, updated_at, auto_named
               FROM threads WHERE user_id = ? AND name = ?""",
            (user_id, name),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "created_at": row[2],
            "updated_at": row[3],
            "auto_named": bool(row[4]),
        }

    async def get_thread(self, thread_id: int) -> dict[str, Any] | None:
        async with self.conn.execute(
            """SELECT id, user_id, name, created_at, updated_at, auto_named
               FROM threads WHERE id = ?""",
            (thread_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "user_id": row[1],
            "name": row[2],
            "created_at": row[3],
            "updated_at": row[4],
            "auto_named": bool(row[5]),
        }

    async def create_thread(
        self, user_id: int, name: str, auto_named: bool = False
    ) -> int:
        # Make name unique by appending " (n)" if conflict.
        base = name.strip() or "Untitled"
        candidate = base
        suffix = 2
        while await self.get_thread_by_name(user_id, candidate) is not None:
            candidate = f"{base} ({suffix})"
            suffix += 1
        now = int(time.time())
        async with self.conn.execute(
            """INSERT INTO threads(user_id, name, created_at, updated_at, auto_named)
               VALUES(?, ?, ?, ?, ?)""",
            (user_id, candidate, now, now, 1 if auto_named else 0),
        ) as cur:
            tid = cur.lastrowid
        await self.conn.commit()
        return int(tid or 0)

    async def rename_thread(
        self, thread_id: int, new_name: str, mark_manual: bool = True
    ) -> str:
        thr = await self.get_thread(thread_id)
        if not thr:
            return ""
        base = new_name.strip() or "Untitled"
        candidate = base
        suffix = 2
        while True:
            other = await self.get_thread_by_name(thr["user_id"], candidate)
            if other is None or other["id"] == thread_id:
                break
            candidate = f"{base} ({suffix})"
            suffix += 1
        await self.conn.execute(
            """UPDATE threads SET name = ?, updated_at = ?, auto_named = ?
               WHERE id = ?""",
            (
                candidate,
                int(time.time()),
                0 if mark_manual else 1,
                thread_id,
            ),
        )
        await self.conn.commit()
        return candidate

    async def touch_thread(self, thread_id: int) -> None:
        await self.conn.execute(
            "UPDATE threads SET updated_at = ? WHERE id = ?",
            (int(time.time()), thread_id),
        )
        await self.conn.commit()

    async def delete_thread(self, thread_id: int) -> int:
        await self.conn.execute(
            "DELETE FROM messages WHERE thread_id = ?", (thread_id,)
        )
        await self.conn.execute(
            "DELETE FROM active_thread WHERE thread_id = ?", (thread_id,)
        )
        async with self.conn.execute(
            "DELETE FROM threads WHERE id = ?", (thread_id,)
        ) as cur:
            n = cur.rowcount or 0
        await self.conn.commit()
        return n

    async def get_active_thread_id(self, user_id: int) -> int | None:
        async with self.conn.execute(
            "SELECT thread_id FROM active_thread WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else None

    async def set_active_thread(self, user_id: int, thread_id: int) -> None:
        await self.conn.execute(
            """INSERT INTO active_thread(user_id, thread_id, updated_at)
               VALUES(?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   thread_id = excluded.thread_id,
                   updated_at = excluded.updated_at""",
            (user_id, thread_id, int(time.time())),
        )
        await self.conn.commit()

    async def ensure_active_thread(self, user_id: int) -> dict[str, Any]:
        """Return the user's active thread, creating an Untitled one if none."""
        tid = await self.get_active_thread_id(user_id)
        if tid is not None:
            t = await self.get_thread(tid)
            if t:
                return t
        # Either there's no active thread, or the recorded one was deleted —
        # fall back to the most recent thread, or create a fresh "Untitled".
        threads = await self.list_threads(user_id)
        if threads:
            t = await self.get_thread(threads[0]["id"])
            if t:
                await self.set_active_thread(user_id, t["id"])
                return t
        new_id = await self.create_thread(user_id, "Untitled", auto_named=True)
        await self.set_active_thread(user_id, new_id)
        t = await self.get_thread(new_id)
        assert t
        return t

    # ---- conversation history (now thread-scoped) -------------------------

    async def append_message(
        self,
        chat_id: int,
        user_id: int,
        role: str,
        content: str,
        thread_id: int | None = None,
    ) -> None:
        await self.conn.execute(
            """INSERT INTO messages(chat_id, user_id, role, content, created_at, thread_id)
               VALUES(?, ?, ?, ?, ?, ?)""",
            (chat_id, user_id, role, content, int(time.time()), thread_id),
        )
        if thread_id is not None:
            await self.conn.execute(
                "UPDATE threads SET updated_at = ? WHERE id = ?",
                (int(time.time()), thread_id),
            )
        await self.conn.commit()

    async def get_thread_history(
        self, thread_id: int, limit: int
    ) -> list[dict[str, Any]]:
        async with self.conn.execute(
            """SELECT role, content FROM messages
               WHERE thread_id = ?
               ORDER BY id DESC LIMIT ?""",
            (thread_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    async def clear_thread_history(self, thread_id: int) -> int:
        async with self.conn.execute(
            "DELETE FROM messages WHERE thread_id = ?", (thread_id,)
        ) as cur:
            n = cur.rowcount or 0
        await self.conn.commit()
        return n

    # ---- legacy chat-scoped helpers (kept for back-compat) ---------------

    async def get_history(
        self, chat_id: int, user_id: int, limit: int
    ) -> list[dict[str, Any]]:
        async with self.conn.execute(
            """SELECT role, content FROM messages
               WHERE chat_id = ? AND user_id = ? AND thread_id IS NULL
               ORDER BY id DESC LIMIT ?""",
            (chat_id, user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    async def clear_history(self, chat_id: int, user_id: int) -> int:
        async with self.conn.execute(
            "DELETE FROM messages WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ) as cur:
            await self.conn.commit()
            return cur.rowcount or 0

    # ---- codex thread mapping (for future `codex exec resume` use) --------

    async def set_codex_thread(
        self, chat_id: int, user_id: int, thread_id: str | None
    ) -> None:
        await self.conn.execute(
            """INSERT INTO chat_sessions(chat_id, user_id, codex_thread_id, updated_at)
               VALUES(?, ?, ?, ?)
               ON CONFLICT(chat_id, user_id) DO UPDATE SET
                   codex_thread_id = excluded.codex_thread_id,
                   updated_at = excluded.updated_at""",
            (chat_id, user_id, thread_id, int(time.time())),
        )
        await self.conn.commit()

    async def get_codex_thread(self, chat_id: int, user_id: int) -> str | None:
        async with self.conn.execute(
            "SELECT codex_thread_id FROM chat_sessions WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row and row[0] else None
