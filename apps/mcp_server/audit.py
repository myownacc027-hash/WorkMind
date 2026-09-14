from __future__ import annotations

import functools
import inspect
import logging
import time
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar, cast

from packages.database.models import AgentActionLog
from packages.database.session import SessionLocal
from packages.security.redaction import redact_data, redact_text

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")


def _safe_value(
    value: Any,
) -> Any:
    if (
        value is None
        or isinstance(
            value,
            (
                bool,
                int,
                float,
            ),
        )
    ):
        return value

    if isinstance(
        value,
        str,
    ):
        return redact_text(
            value
        )[:2000]

    if isinstance(
        value,
        (
            list,
            tuple,
        ),
    ):
        return [
            _safe_value(
                item
            )
            for item
            in value[:50]
        ]

    if isinstance(
        value,
        dict,
    ):
        sanitized = (
            redact_data(
                value
            )
        )

        return {
            str(
                key
            )[:128]: (
                _safe_value(
                    item
                )
            )
            for (
                key,
                item,
            ) in list(
                sanitized.items()
            )[:50]
        }

    return redact_text(
        repr(
            value
        )
    )[:500]


def _redacted_text(
    value: Any,
) -> dict[str, Any]:
    # Do not persist raw length or a hash of
    # a potentially sensitive payload.
    del value

    return {
        "redacted": True,
    }


def _safe_named_value(
    key: str,
    value: Any,
) -> Any:
    """
    Sanitize a value with knowledge of its parameter name.

    This is required for opaque capabilities such as lease_token:
    the token value itself may have no recognizable secret shape.
    """

    if key == "bundle_json":
        return _redacted_text(
            value
        )

    sanitized = redact_data(
        {
            key: value,
        }
    )

    if isinstance(
        sanitized,
        dict,
    ):
        return _safe_value(
            sanitized.get(
                key
            )
        )

    return _safe_value(
        value
    )


def _request_payload(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    tool_name: str,
    parameter_names: tuple[
        str,
        ...,
    ] = (),
) -> dict[str, Any]:
    """
    Build a sanitized audit representation of MCP input.

    Parameter names are used when available so opaque secrets
    are redacted even when their value has no recognizable shape.
    """

    redact_positional_bundle = (
        tool_name
        == "extract_workmind_knowledge"
    )

    safe_args: list[Any] = []

    for index, item in enumerate(
        args
    ):
        parameter_name = (
            parameter_names[index]
            if index
            < len(
                parameter_names
            )
            else None
        )

        if parameter_name is not None:
            safe_args.append(
                _safe_named_value(
                    parameter_name,
                    item,
                )
            )

        elif (
            redact_positional_bundle
            and index == 0
        ):
            # Backward-compatible protection for direct
            # _request_payload() callers without a signature.
            safe_args.append(
                _redacted_text(
                    item
                )
            )

        else:
            safe_args.append(
                _safe_value(
                    item
                )
            )

    safe_kwargs = {
        key: _safe_named_value(
            key,
            value,
        )
        for key, value
        in kwargs.items()
    }

    return {
        "args": safe_args,
        "kwargs": safe_kwargs,
    }


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
            error=redact_text(str(exc)),
        )


def audited_tool(
    tool_name: str,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        parameter_names = tuple(
            inspect.signature(
                func
            ).parameters
        )

        response_allowed_keys = (
            {
                "lease_token",
            }
            if tool_name
            == "get_workmind_extraction_batch"
            else set()
        )

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            started = time.perf_counter()
            request_json = _request_payload(
                args,
                kwargs,
                tool_name=tool_name,
                parameter_names=parameter_names,
            )
            try:
                raw_result = func(*args, **kwargs)

                # audited_tool sits inside @mcp.tool(),
                # so this is the common outbound MCP boundary.
                #
                # lease_token is a required WorkMind protocol
                # capability: clients need it for validate/release.
                # It is allowed only in MCP responses. Audit logs
                # and remote provider packets remain strict.
                result = cast(
                    R,
                    redact_data(
                        raw_result,
                        allowed_keys=(
                            response_allowed_keys
                        ),
                    ),
                )

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

                # Unexpected exceptions cross the MCP boundary too.
                # Never re-raise their original potentially sensitive
                # message. Preserve the original error type in audit
                # metadata, but expose only the sanitized message.
                safe_message = redact_text(
                    str(
                        exc
                    )
                )

                raise RuntimeError(
                    safe_message
                ) from None
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
