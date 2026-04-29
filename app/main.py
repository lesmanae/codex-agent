"""Entrypoint: configure logging + run uvicorn against the FastAPI app.

All bootstrap (DB connect, skill load, media config, codex auth init) is
handled inside the FastAPI lifespan in `app.api`.
"""
from __future__ import annotations

import logging
import sys

import structlog
import uvicorn

from .api import create_app
from .config import get_settings


def _configure_logging(level: str) -> None:
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
    )
    logging.basicConfig(level=level.upper(), stream=sys.stdout, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    settings = get_settings()
    _configure_logging(settings.log_level)
    log = structlog.get_logger("main")

    app = create_app()
    log.info("starting_uvicorn", host=settings.api_host, port=settings.api_port)

    config = uvicorn.Config(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        access_log=True,
        ws="auto",
    )
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
