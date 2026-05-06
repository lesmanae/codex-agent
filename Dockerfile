FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# util-linux ships nsenter (we drop into PID 1's namespaces from inside the
# container so Codex actually operates on the host filesystem/network with
# the host's /root/.codex auth). curl/dns tools stay for ad-hoc debugging.
# ffmpeg is required by faster-whisper to decode voice-note OGG/Opus and to
# extract audio from videos when codex is asked to transcribe a clip.
RUN apt-get update && apt-get install -y --no-install-recommends \
        util-linux \
        ca-certificates \
        curl \
        iputils-ping \
        dnsutils \
        less \
        git \
        ffmpeg \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/
RUN pip install --upgrade pip && pip install \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.30.0" \
    "python-multipart>=0.0.9" \
    "websockets>=13.0" \
    "aiosqlite>=0.20.0" \
    "cryptography>=43.0.0" \
    "pydantic>=2.8.0" \
    "pydantic-settings>=2.5.0" \
    "structlog>=24.4.0" \
    "httpx>=0.27.2" \
    "faster-whisper>=1.0.3"

COPY app /app/app
COPY skills /app/skills

VOLUME ["/data"]
ENV DB_PATH=/data/bot.sqlite \
    SKILLS_DIR=/app/skills \
    WHISPER_CACHE=/data/whisper-cache \
    API_HOST=0.0.0.0 \
    API_PORT=8001

EXPOSE 8001

CMD ["python", "-m", "app.main"]
