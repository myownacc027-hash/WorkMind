from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from apps.mcp_server import audit
from packages.knowledge import openai_provider
from packages.security.redaction import (
    redact_data,
    redact_text,
)


@pytest.mark.parametrize(
    (
        "key",
        "expected_marker",
    ),
    [
        (
            "clientSecret",
            "[REDACTED:client_secret]",
        ),
        (
            "accessToken",
            "[REDACTED:access_token]",
        ),
        (
            "refreshToken",
            "[REDACTED:refresh_token]",
        ),
        (
            "privateKey",
            "[REDACTED:private_key]",
        ),
        (
            "authToken",
            "[REDACTED:auth_token]",
        ),
        (
            "bearerToken",
            "[REDACTED:bearer_token]",
        ),
        (
            "databasePassword",
            "[REDACTED:password]",
        ),
        (
            "awsSecretAccessKey",
            "[REDACTED:secret]",
        ),
        (
            "APIKey",
            "[REDACTED:api_key]",
        ),
    ],
)
def test_structured_camelcase_secret_keys(
    key: str,
    expected_marker: str,
) -> None:
    secret = (
        "synthetic-structured-secret"
    )

    original = {
        "metadata": {
            key: secret,
        },
    }

    result = redact_data(
        original
    )

    assert (
        result[
            "metadata"
        ][
            key
        ]
        == expected_marker
    )

    assert (
        original[
            "metadata"
        ][
            key
        ]
        == secret
    )


@pytest.mark.parametrize(
    "label",
    [
        "clientSecret",
        "accessToken",
        "refreshToken",
        "databasePassword",
        "awsSecretAccessKey",
    ],
)
def test_text_camelcase_secret_assignments(
    label: str,
) -> None:
    secret = (
        "synthetic-text-secret"
    )

    value = (
        f"{label}={secret}"
    )

    result = redact_text(
        value
    )

    assert (
        secret
        not in result
    )

    assert (
        "[REDACTED:"
        in result
    )


def test_encrypted_private_key_is_redacted(
) -> None:
    private_material = (
        "synthetic-encrypted-private-material"
    )

    value = (
        "-----BEGIN ENCRYPTED PRIVATE KEY-----\n"
        + private_material
        + "\n"
        "-----END ENCRYPTED PRIVATE KEY-----"
    )

    result = redact_text(
        value
    )

    assert (
        private_material
        not in result
    )

    assert (
        result
        == "[REDACTED:private_key]"
    )


def test_allowed_lease_token_is_root_only(
) -> None:
    top_level = (
        "synthetic-top-level-capability"
    )

    nested = (
        "synthetic-nested-capability"
    )

    value = {
        "lease_token": (
            top_level
        ),
        "packet": {
            "lease_token": (
                nested
            ),
        },
    }

    result = redact_data(
        value,
        allowed_keys={
            "lease_token",
        },
    )

    assert (
        result[
            "lease_token"
        ]
        == top_level
    )

    assert (
        result[
            "packet"
        ][
            "lease_token"
        ]
        == "[REDACTED:token]"
    )


def test_batch_mcp_preserves_only_top_level_lease_token(
    monkeypatch,
) -> None:
    top_level = (
        "synthetic-top-level-capability"
    )

    nested = (
        "synthetic-nested-capability"
    )

    monkeypatch.setattr(
        audit,
        "_write_log",
        lambda **kwargs: None,
    )

    @audit.audited_tool(
        "get_workmind_extraction_batch"
    )
    def tool():
        return {
            "lease_token": (
                top_level
            ),
            "packet": {
                "lease_token": (
                    nested
                ),
            },
        }

    result = tool()

    assert (
        result[
            "lease_token"
        ]
        == top_level
    )

    assert (
        result[
            "packet"
        ][
            "lease_token"
        ]
        == "[REDACTED:token]"
    )


def test_unexpected_mcp_exception_message_is_sanitized(
    monkeypatch,
) -> None:
    secret = (
        "synthetic-exception-secret"
    )

    logs: list[
        dict
    ] = []

    monkeypatch.setattr(
        audit,
        "_write_log",
        lambda **kwargs: (
            logs.append(
                kwargs
            )
        ),
    )

    @audit.audited_tool(
        "synthetic_exception_tool"
    )
    def tool():
        raise RuntimeError(
            "password="
            + secret
        )

    with pytest.raises(
        RuntimeError
    ) as caught:
        tool()

    message = str(
        caught.value
    )

    assert (
        secret
        not in message
    )

    assert (
        "[REDACTED:password]"
        in message
    )

    assert (
        len(
            logs
        )
        == 1
    )

    serialized_log = json.dumps(
        logs[0],
        ensure_ascii=False,
        default=str,
    )

    assert (
        secret
        not in serialized_log
    )


class _FakeResponse:
    def __init__(
        self,
        payload: dict,
    ) -> None:
        self.raw = json.dumps(
            payload
        ).encode(
            "utf-8"
        )

    def __enter__(
        self,
    ):
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        traceback,
    ):
        return None

    def read(
        self,
    ) -> bytes:
        return self.raw


def _settings():
    return SimpleNamespace(
        knowledge_extraction_remote_consent=True,
        knowledge_extraction_api_key=(
            "synthetic-provider-key"
        ),
        knowledge_extraction_api_base=(
            "https://example.test/v1"
        ),
        knowledge_extraction_model=(
            "synthetic-model"
        ),
        knowledge_extraction_timeout_seconds=30,
    )


def test_successful_provider_bundle_is_redacted_on_inbound_boundary(
    monkeypatch,
) -> None:
    secret = (
        "synthetic-provider-output-secret"
    )

    model_bundle = {
        "entities": [
            {
                "clientSecret": (
                    secret
                ),
            },
        ],
        "claims": [],
        "relations": [],
        "events": [],
    }

    response = {
        "id": "resp_test",
        "output_text": json.dumps(
            model_bundle
        ),
    }

    monkeypatch.setattr(
        openai_provider
        .urllib
        .request,
        "urlopen",
        lambda request, timeout: (
            _FakeResponse(
                response
            )
        ),
    )

    (
        bundle_json,
        response_json,
    ) = (
        openai_provider
        .extract_with_openai(
            {
                "source_ref": (
                    "workmind://email/test"
                ),
                "text": (
                    "safe evidence"
                ),
            },
            settings=_settings(),
        )
    )

    assert (
        secret
        not in bundle_json
    )

    parsed = json.loads(
        bundle_json
    )

    assert (
        parsed[
            "entities"
        ][0][
            "clientSecret"
        ]
        == "[REDACTED:client_secret]"
    )

    serialized_response = json.dumps(
        response_json,
        ensure_ascii=False,
    )

    assert (
        secret
        not in serialized_response
    )
