from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import warnings
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

from packages.common.config import get_settings


class EmbeddingDependencyError(RuntimeError):
    pass


class EmbeddingServiceError(RuntimeError):
    pass


@lru_cache(maxsize=2)
def _load_model(model_name: str, cache_dir: str):
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise EmbeddingDependencyError(
            'FastEmbed is not installed. Run: '
            'python -m pip install -e ".[windows,embeddings,dev]"'
        ) from exc

    # This project has no legacy index created with CLS pooling. Both document
    # and query vectors use the same current mean-pooling behavior.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"The model .* now uses mean pooling instead of CLS embedding.*",
            category=UserWarning,
        )
        return TextEmbedding(
            model_name=model_name,
            cache_dir=cache_dir,
        )


def embed_texts_local(texts: Iterable[str]) -> list[list[float]]:
    values = list(texts)
    if not values:
        return []

    settings = get_settings()
    model = _load_model(
        settings.embedding_model,
        str(settings.embedding_cache_dir),
    )
    return [vector.tolist() for vector in model.embed(values)]


def warm_local_model() -> dict[str, Any]:
    settings = get_settings()
    started = time.monotonic()
    vectors = embed_texts_local(["WorkMind embedding service warmup"])
    dimension = len(vectors[0]) if vectors else 0
    return {
        "model": settings.embedding_model,
        "dimension": dimension,
        "cache_dir": str(settings.embedding_cache_dir),
        "load_seconds": round(time.monotonic() - started, 3),
    }


def _read_json_response(response: Any) -> dict[str, Any]:
    raw = response.read()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EmbeddingServiceError(
            "Embedding service returned invalid JSON."
        ) from exc
    if not isinstance(value, dict):
        raise EmbeddingServiceError(
            "Embedding service returned an invalid response object."
        )
    return value


def embedding_service_status(
    *,
    timeout_seconds: float = 1.5,
) -> dict[str, Any]:
    settings = get_settings()
    url = settings.embedding_service_url.rstrip("/") + "/health"

    try:
        with urllib.request.urlopen(
            url,
            timeout=timeout_seconds,
        ) as response:
            value = _read_json_response(response)
            value["reachable"] = True
            return value
    except Exception as exc:
        return {
            "reachable": False,
            "status": "offline",
            "url": settings.embedding_service_url,
            "error": str(exc),
        }


def _embed_via_service(
    texts: list[str],
    *,
    timeout_seconds: float,
) -> list[list[float]]:
    settings = get_settings()
    url = settings.embedding_service_url.rstrip("/") + "/embed"
    payload = json.dumps(
        {"texts": texts},
        ensure_ascii=False,
    ).encode("utf-8")

    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )

        remaining = max(1.0, deadline - time.monotonic())
        try:
            with urllib.request.urlopen(
                request,
                timeout=min(remaining, 60.0),
            ) as response:
                value = _read_json_response(response)
                vectors = value.get("vectors")
                if not isinstance(vectors, list):
                    raise EmbeddingServiceError(
                        "Embedding service response has no vectors list."
                    )
                return vectors
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 503:
                time.sleep(1.0)
                continue
            try:
                details = _read_json_response(exc)
            except Exception:
                details = {}
            raise EmbeddingServiceError(
                f"Embedding service HTTP {exc.code}: {details or exc.reason}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            break

    raise EmbeddingServiceError(
        f"Embedding service is unavailable: {last_error}"
    )


def embed_texts(texts: Iterable[str]) -> list[list[float]]:
    values = list(texts)
    if not values:
        return []

    settings = get_settings()
    if settings.embedding_service_enabled:
        try:
            return _embed_via_service(
                values,
                timeout_seconds=settings.embedding_service_timeout_seconds,
            )
        except EmbeddingServiceError:
            if not settings.embedding_service_fallback_local:
                raise

    return embed_texts_local(values)
