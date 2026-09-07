from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from packages.common.config import get_settings
from packages.processing.embeddings import (
    embed_texts_local,
    warm_local_model,
)

_started_at = time.time()
_state_lock = threading.Lock()
_embed_lock = threading.Lock()
_ready_event = threading.Event()
_state: dict[str, Any] = {
    "status": "starting",
    "error": None,
    "model": None,
    "dimension": None,
    "load_seconds": None,
}


def _set_state(**values: Any) -> None:
    with _state_lock:
        _state.update(values)


def _state_snapshot() -> dict[str, Any]:
    settings = get_settings()
    with _state_lock:
        value = dict(_state)
    value.update(
        {
            "service": "workmind-embedding",
            "pid": os.getpid(),
            "url": settings.embedding_service_url,
            "cache_dir": str(settings.embedding_cache_dir),
            "uptime_seconds": round(time.time() - _started_at, 1),
        }
    )
    return value


def _warmup() -> None:
    _set_state(status="loading", error=None)
    try:
        info = warm_local_model()
        _set_state(
            status="ready",
            model=info["model"],
            dimension=info["dimension"],
            load_seconds=info["load_seconds"],
            error=None,
        )
        _ready_event.set()
    except Exception as exc:
        _set_state(
            status="error",
            error=str(exc),
        )


async def health(_: Request) -> JSONResponse:
    snapshot = _state_snapshot()
    status_code = 200 if snapshot["status"] == "ready" else 503
    return JSONResponse(snapshot, status_code=status_code)


async def embed(request: Request) -> JSONResponse:
    snapshot = _state_snapshot()
    if snapshot["status"] != "ready":
        return JSONResponse(snapshot, status_code=503)

    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(
            {"error": "Request body must be valid JSON."},
            status_code=400,
        )

    texts = body.get("texts") if isinstance(body, dict) else None
    if not isinstance(texts, list):
        return JSONResponse(
            {"error": "Field 'texts' must be a JSON array."},
            status_code=400,
        )
    if len(texts) > 512:
        return JSONResponse(
            {"error": "A maximum of 512 texts is allowed per request."},
            status_code=413,
        )
    if any(not isinstance(item, str) for item in texts):
        return JSONResponse(
            {"error": "Every item in 'texts' must be a string."},
            status_code=400,
        )

    try:
        def calculate() -> list[list[float]]:
            with _embed_lock:
                return embed_texts_local(texts)

        vectors = await asyncio.to_thread(calculate)
    except Exception as exc:
        return JSONResponse(
            {
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
            status_code=500,
        )

    return JSONResponse(
        {
            "model": snapshot["model"],
            "dimension": snapshot["dimension"],
            "count": len(vectors),
            "vectors": vectors,
        }
    )


@asynccontextmanager
async def lifespan(_: Starlette):
    settings = get_settings()
    settings.embedding_service_pid_file.write_text(
        str(os.getpid()),
        encoding="utf-8",
    )

    warmup_task = asyncio.create_task(asyncio.to_thread(_warmup))
    try:
        yield
    finally:
        if not warmup_task.done():
            warmup_task.cancel()
        try:
            settings.embedding_service_pid_file.unlink(missing_ok=True)
        except OSError:
            pass


app = Starlette(
    debug=False,
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/embed", embed, methods=["POST"]),
    ],
    lifespan=lifespan,
)


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        app,
        host=settings.embedding_service_host,
        port=settings.embedding_service_port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
