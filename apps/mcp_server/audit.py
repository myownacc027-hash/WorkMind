from __future__ import annotations

import functools
import hashlib
import logging
import time
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

from packages.database.models import AgentActionLog
from packages.database.session import SessionLocal

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:2000]
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value[:50]]
    if isinstance(value, dict):
        return {
            str(key)[:128]: _safe_value(item)
            for key, item in list(value.items())[:50]
        }
    return repr(value)[:500]


def _redacted_text(value: Any) -> dict[str, Any]:
    raw = str(value)
    return {
        "redacted": True,
        "length": len(raw),
        "sha256": hashlib.sha256(
            raw.encode("utf-8")
        ).hexdigest(),
    }


def _request_payload(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    tool_name: str,
) -> dict[str, Any]:
    redact_positional_bundle = (
        tool_name == "extract_workmind_knowledge"
    )
    payload: dict[str, Any] = {
        "args": [
            _redacted_text(item)
            if redact_positional_bundle and index == 0
            else _safe_value(item)
            for index, item in enumerate(args)
        ],
        "kwargs": {
            key: (
                _redacted_text(value)
                if key == "bundle_json"
                else _safe_value(value)
            )
            for key, value in kwargs.items()
        },
    }
    return payload


def _response_summary(result: Any) -> tuple[dict[str, Any], int | None, str, str | None]:
    if not isinstance(result, dict):
        return {
            "result_type": type(result).__name__,
        }, None, "completed", None

    result_count = result.get("count")
    if not isinstance(result_count, int):
        items = result.get("results")
        result_count = len(items) if isinstance(items, list) else None
    ok = result.get("ok")
    failed = ok is False or bool(result.get("error"))
    error_type = (
        str(result.get("error_type"))
        if result.get("error_type")
        else None
    )
    summary = {
        "ok": not failed,
        "result_count": result_count,
        "keys": sorted(str(key) for key in result.keys())[:50],
        "error_type": error_type,
    }
    return (
        summary,
        result_count,
        "error" if failed else "completed",
        error_type,
    )


def _write_log(
    *,
    tool_name: str,
    request_json: dict[str, Any],
    status: str,
    duration_ms: int,
    result_count: int | None,
    error_type: str | None,
    response_summary_json: dict[str, Any],
) -> None:
    try:
        with SessionLocal() as session:
            session.add(
                AgentActionLog(
                    tool_name=tool_name,
                    transport="stdio",
                    status=status,
                    duration_ms=duration_ms,
                    result_count=result_count,
                    error_type=error_type,
                    request_json=request_json,
                    response_summary_json=response_summary_json,
                )
            )
            session.commit()
    except Exception as exc:
        # Audit failure must never break a read request.
        logger.warning(
            "mcp_audit_write_failed",
            tool_name=tool_name,
            error_type=type(exc).__name__,
            error=str(exc),
        )


def audited_tool(
    tool_name: str,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            started = time.perf_counter()
            request_json = _request_payload(
                args,
                kwargs,
                tool_name=tool_name,
            )
            try:
                result = func(*args, **kwargs)
                (
                    summary,
                    result_count,
                    status,
                    error_type,
                ) = _response_summary(result)
                return result
            except Exception as exc:
                summary = {
                    "ok": False,
                    "error_type": type(exc).__name__,
                }
                result_count = None
                status = "error"
                error_type = type(exc).__name__
                raise
            finally:
                duration_ms = max(
                    0,
                    round((time.perf_counter() - started) * 1000),
                )
                _write_log(
                    tool_name=tool_name,
                    request_json=request_json,
                    status=status,
                    duration_ms=duration_ms,
                    result_count=result_count,
                    error_type=error_type,
                    response_summary_json=summary,
                )

        return wrapper
    return decorator
