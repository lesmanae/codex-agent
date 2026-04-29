"""Audio transcription via faster-whisper.

The model is downloaded once on first use into the cache dir below, then
loaded into a process-global instance. Transcription runs in a worker
thread so the asyncio loop isn't blocked.

Defaults to model `base` which is ~74MB and gives good accuracy/speed on
CPU. Override via `WHISPER_MODEL` env var (`tiny`, `base`, `small`,
`medium`, `large-v3`). `int8` quantization keeps memory + CPU low.
"""
from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
WHISPER_CACHE = Path(os.environ.get("WHISPER_CACHE", "/data/whisper-cache"))


def configure(model: str, cache: Path) -> None:
    """Override module defaults with values from Settings."""
    global WHISPER_MODEL, WHISPER_CACHE, _model
    WHISPER_MODEL = model
    WHISPER_CACHE = Path(cache)
    _model = None  # force reload on next call

_model_lock = threading.Lock()
_model = None  # populated lazily on first transcription


def _load_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        from faster_whisper import WhisperModel  # heavy import, do it lazily

        WHISPER_CACHE.mkdir(parents=True, exist_ok=True)
        logger.info(
            "whisper_loading",
            model=WHISPER_MODEL,
            cache=str(WHISPER_CACHE),
        )
        _model = WhisperModel(
            WHISPER_MODEL,
            device="cpu",
            compute_type="int8",
            download_root=str(WHISPER_CACHE),
        )
        logger.info("whisper_loaded", model=WHISPER_MODEL)
        return _model


def _transcribe_blocking(audio_path: Path, language: str | None = None) -> str:
    model = _load_model()
    # vad_filter trims long silences — meaningful speed-up on phone voice notes.
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=True,
        beam_size=1,
    )
    parts: list[str] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if text:
            parts.append(text)
    transcript = " ".join(parts).strip()
    logger.info(
        "whisper_done",
        path=str(audio_path),
        chars=len(transcript),
        detected_language=getattr(info, "language", None),
        prob=getattr(info, "language_probability", None),
    )
    return transcript


async def transcribe(audio_path: Path, language: str | None = None) -> str:
    """Public async entrypoint. Returns the transcript or an empty string on
    transient errors (so the caller can degrade gracefully)."""
    try:
        return await asyncio.to_thread(_transcribe_blocking, audio_path, language)
    except Exception as exc:  # noqa: BLE001
        logger.exception("whisper_failed", path=str(audio_path))
        return ""


async def transcribe_with_status(audio_path: Path, language: str | None = None) -> tuple[str, str | None]:
    """Same as transcribe(), but also returns an error message for the user
    if transcription failed. Returns `(transcript, error)`."""
    try:
        text = await asyncio.to_thread(_transcribe_blocking, audio_path, language)
        return text, None
    except ImportError:
        return "", "faster-whisper tidak terinstall di container. Cek Dockerfile + rebuild."
    except FileNotFoundError:
        return "", "File audio tidak ketemu setelah download."
    except Exception as exc:  # noqa: BLE001
        logger.exception("whisper_failed", path=str(audio_path))
        return "", f"Transcribe error: {type(exc).__name__}: {str(exc)[:200]}"
