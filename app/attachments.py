"""Attachment storage helpers — framework-agnostic.

Each incoming attachment is saved to `<inbox>/<user_id>/<thread_id>/<ts>_<name>`
on the host filesystem. The path is shared with Codex (running on the host
via nsenter) so Codex can read/process it via its shell + file-read tools.

For images we additionally pass the file via `codex exec -i <file>` so
Codex's vision-capable model can consume the bytes directly. For documents,
videos, and unknown blobs we just describe them by path in the prompt and
let Codex decide what to do (read text, run ffmpeg, unzip, etc).

Voice and audio go through `transcribe.py` to produce a transcript that
the user message is augmented with.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

# Module-level defaults; overridden at runtime by `configure()` so tests and
# the API can point at different host/container paths.
_INBOX_CONTAINER = Path("/host/root/codex-workspace/inbox")
_INBOX_HOST = Path("/root/codex-workspace/inbox")


def configure(inbox_container: Path, inbox_host: Path) -> None:
    """Set the host vs container paths for the attachment inbox.

    The API writes new files under `inbox_container` (a path that exists
    inside the container), but tells codex to read them from `inbox_host`
    (the path codex sees when running on the host via nsenter). With the
    default docker-compose mount (`/:/host`), that's
    `/host/root/codex-workspace/inbox` vs `/root/codex-workspace/inbox`.
    """
    global _INBOX_CONTAINER, _INBOX_HOST
    _INBOX_CONTAINER = Path(inbox_container)
    _INBOX_HOST = Path(inbox_host)


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}
AUDIO_EXTS = {".ogg", ".oga", ".mp3", ".m4a", ".aac", ".flac", ".wav", ".opus"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


@dataclass
class Attachment:
    kind: str           # "photo" | "voice" | "audio" | "video" | "document"
    container_path: Path  # readable from inside the container (for whisper, etc.)
    host_path: Path     # path codex sees on the host (passed to codex `-i` / shell)
    name: str           # original filename (or synthesized)
    mime: str | None = None
    size: int = 0
    duration: int | None = None  # seconds, for audio/voice/video

    @property
    def path(self) -> Path:  # back-compat alias used by transcribe + tests
        return self.container_path

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "container_path": str(self.container_path),
            "host_path": str(self.host_path),
            "name": self.name,
            "mime": self.mime,
            "size": self.size,
            "duration": self.duration,
        }


@dataclass
class IngestResult:
    attachments: list[Attachment] = field(default_factory=list)
    transcript: str = ""           # concatenation of audio transcriptions
    descriptions: list[str] = field(default_factory=list)  # short summary lines for the prompt
    images: list[Path] = field(default_factory=list)       # paths to pass via codex `-i`


def _safe_name(s: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", s or "file")
    return s[:80] or "file"


def _inbox_dir(user_id: int, thread_id: int) -> tuple[Path, Path]:
    """Return (container_dir, host_dir) for this user/thread, creating the
    container-side directory."""
    rel = Path(str(user_id)) / str(thread_id)
    cd = _INBOX_CONTAINER / rel
    hd = _INBOX_HOST / rel
    cd.mkdir(parents=True, exist_ok=True)
    return cd, hd


def classify_by_kind(kind_hint: str | None, path: Path, mime: str | None) -> str:
    """Return one of: photo, voice, audio, video, document.

    Order: explicit hint > mime prefix > extension fallback.
    """
    if kind_hint:
        h = kind_hint.lower()
        if h in {"photo", "voice", "audio", "video", "document"}:
            return h
    if mime:
        m = mime.lower()
        if m.startswith("image/"):
            return "photo"
        if m.startswith("audio/"):
            # voice notes typically come in as audio/ogg
            return "audio"
        if m.startswith("video/"):
            return "video"
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "photo"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    return "document"


def save_upload(
    user_id: int,
    thread_id: int,
    original_name: str,
    data: bytes,
    *,
    mime: str | None = None,
    kind_hint: str | None = None,
) -> Attachment:
    """Persist an uploaded blob to the inbox + return its Attachment record.

    `original_name` is sanitized; the on-disk filename is prefixed with a
    timestamp to avoid collisions. `kind_hint` (e.g. "voice") wins over
    extension-based classification when set.
    """
    cdir, hdir = _inbox_dir(user_id, thread_id)
    ts = int(time.time() * 1000)
    safe = _safe_name(original_name)
    fname = f"{ts}_{safe}"
    cpath = cdir / fname
    hpath = hdir / fname
    cpath.write_bytes(data)
    try:
        size = cpath.stat().st_size
    except OSError:
        size = len(data)
    kind = classify_by_kind(kind_hint, cpath, mime)
    return Attachment(
        kind=kind,
        container_path=cpath,
        host_path=hpath,
        name=safe,
        mime=mime,
        size=size,
    )


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024.0:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}TB"


def describe(att: Attachment) -> str:
    """Short human-readable description of an attachment for the prompt.
    Uses the host path so codex (running on host) can reach it directly."""
    bits: list[str] = []
    bits.append(f"kind={att.kind}")
    bits.append(f"path={att.host_path}")
    bits.append(f"name={att.name}")
    if att.mime:
        bits.append(f"mime={att.mime}")
    if att.size:
        bits.append(f"size={fmt_size(att.size)}")
    if att.duration:
        bits.append(f"duration={att.duration}s")
    return "- " + " | ".join(bits)


def cleanup_old(days: int = 7) -> None:
    """Best-effort cleanup of inbox files older than `days` days."""
    root_dir = _INBOX_CONTAINER
    if not root_dir.exists():
        return
    cutoff = time.time() - days * 86400
    for root, _dirs, files in os.walk(root_dir):
        for fn in files:
            p = Path(root) / fn
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
            except OSError:
                pass
