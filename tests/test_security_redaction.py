from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from apps.mcp_server import audit
from packages.knowledge import openai_provider
from packages.security.redaction import (
    contains_redaction,
    redact_data,
    redact_text,
)


def test_redacts_password_assignments_and_preserves_labels(
) -> None:
    value = "\n".join(
        [
            "password=hunter2",
            'password: "two words secret"',
            "пароль: synthetic-secret",
        ]
    )

    redacted = redact_text(
        value
    )

    assert "hunter2" not in redacted
    assert "two words secret" not in redacted
    assert "synthetic-secret" not in redacted

    assert (
        redacted.count(
            "[REDACTED:password]"
        )
        == 3
    )


def test_redacts_structured_sensitive_keys_recursively(
) -> None:
    original = {
        "source_id": (
            "12345678-1234-1234-"
            "1234-123456789abc"
        ),
        "knowledge_extraction_api_key": (
            "synthetic-api-secret"
        ),
        "nested": {
            "refresh_token": (
                "synthetic-refresh-secret"
            ),
            "token_count": 7,
        },
    }

    result = redact_data(
        original
    )

    assert (
        result["source_id"]
        == original["source_id"]
    )

    assert (
        result[
            "knowledge_extraction_api_key"
        ]
        == "[REDACTED:api_key]"
    )

    assert (
        result["nested"][
            "refresh_token"
        ]
        == "[REDACTED:refresh_token]"
    )

    assert (
        result["nested"][
            "token_count"
        ]
        == 7
    )

    # Internal data must not be modified.
    assert (
        original[
            "knowledge_extraction_api_key"
        ]
        == "synthetic-api-secret"
    )


def test_redacts_authorization_credentials_and_jwt(
) -> None:
    jwt_value = (
        "eyJabcdefgh."
        "abcdefgh."
        "abcdefgh"
    )

    value = (
        "Authorization: Bearer "
        "abcdefghijklmnop\n"
        "Proxy-Authorization: Basic "
        "QWxhZGRpbjpvcGVuIHNlc2FtZQ==\n"
        f"session={jwt_value}"
    )

    redacted = redact_text(
        value
    )

    assert (
        "abcdefghijklmnop"
        not in redacted
    )

    assert (
        "QWxhZGRpbjpvcGVuIHNlc2FtZQ=="
        not in redacted
    )

    assert (
        jwt_value
        not in redacted
    )

    assert (
        "[REDACTED:bearer_token]"
        in redacted
    )

    assert (
        "[REDACTED:basic_credential]"
        in redacted
    )

    assert (
        "[REDACTED:jwt]"
        in redacted
    )


@pytest.mark.parametrize(
    (
        "secret",
        "marker",
    ),
    [
        (
            "sk-abcdefghijklmnopqrstuvwxyz",
            "[REDACTED:api_key]",
        ),
        (
            (
                "github_pat_"
                "abcdefghijklmnopqrstuvwxyz123456"
            ),
            "[REDACTED:api_key]",
        ),
        (
            (
                "ghp_"
                "abcdefghijklmnopqrstuvwxyz123456"
            ),
            "[REDACTED:api_key]",
        ),
        (
            (
                "glpat-"
                "abcdefghijklmnopqrstuv"
            ),
            "[REDACTED:access_token]",
        ),
        (
            (
                "hf_"
                "abcdefghijklmnopqrstuvwxyz"
            ),
            "[REDACTED:access_token]",
        ),
        (
            (
                "xoxb-1234567890-"
                "abcdefghijklmnop"
            ),
            "[REDACTED:access_token]",
        ),
        (
            "AKIAABCDEFGHIJKLMNOP",
            "[REDACTED:access_key]",
        ),
        (
            (
                "AIza"
                "abcdefghijklmnopqrstuvwxyz123456"
            ),
            "[REDACTED:api_key]",
        ),
    ],
)
def test_redacts_known_token_shapes(
    secret: str,
    marker: str,
) -> None:
    redacted = redact_text(
        "credential "
        + secret
    )

    assert (
        secret
        not in redacted
    )

    assert (
        marker
        in redacted
    )


def test_redacts_url_password_and_private_key(
) -> None:
    value = (
        "postgresql://dbuser:"
        "synthetic-db-password"
        "@db.local/workmind\n"
        "-----BEGIN PRIVATE KEY-----\n"
        "synthetic-private-material\n"
        "-----END PRIVATE KEY-----"
    )

    redacted = redact_text(
        value
    )

    assert (
        "synthetic-db-password"
        not in redacted
    )

    assert (
        "synthetic-private-material"
        not in redacted
    )

    assert (
        "postgresql://dbuser:"
        "[REDACTED:password]"
        "@db.local/workmind"
        in redacted
    )

    assert (
        "[REDACTED:private_key]"
        in redacted
    )


def test_does_not_redact_normal_identifiers_and_policy_text(
) -> None:
    values = [
        "password policy requires rotation",
        (
            "workmind://email/"
            "12345678-1234-1234-"
            "1234-123456789abc"
        ),
        "sender@example.test",
        "СТ-199",
        "ИВП 912",
        (
            "content_hash="
            "0123456789abcdef"
            "0123456789abcdef"
        ),
        "token_count=42",
    ]

    for value in values:
        assert (
            redact_text(
                value
            )
            == value
        )


def test_contains_redaction_recurses(
) -> None:
    assert contains_redaction(
        {
            "results": [
                {
                    "matched_text": (
                        "password="
                        "[REDACTED:password]"
                    )
                }
            ]
        }
    )

    assert not contains_redaction(
        {
            "source_ref": (
                "workmind://email/abc"
            ),
        }
    )


def test_audit_request_payload_sanitizes_normal_arguments(
) -> None:
    secret = (
        "synthetic-request-secret"
    )

    payload = (
        audit._request_payload(
            (),
            {
                "query": (
                    f"password={secret}"
                ),
                "options": {
                    "access_token": secret,
                },
            },
            tool_name=(
                "search_workmind"
            ),
        )
    )

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
    )

    assert (
        secret
        not in serialized
    )

    assert (
        "[REDACTED:password]"
        in serialized
    )

    assert (
        "[REDACTED:access_token]"
        in serialized
    )


def test_audit_bundle_payload_is_fully_withheld_without_hash_or_length(
) -> None:
    secret = (
        "synthetic-bundle-secret"
    )

    payload = (
        audit._request_payload(
            (
                json.dumps(
                    {
                        "password": (
                            secret
                        ),
                    }
                ),
            ),
            {},
            tool_name=(
                "extract_workmind_knowledge"
            ),
        )
    )

    assert (
        payload["args"][0]
        == {
            "redacted": True,
        }
    )

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
    )

    assert (
        secret
        not in serialized
    )

    assert (
        "sha256"
        not in serialized
    )

    assert (
        "length"
        not in serialized
    )


def test_audited_tool_redacts_return_value_before_boundary(
    monkeypatch,
) -> None:
    secret = (
        "synthetic-result-secret"
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
        "synthetic_tool"
    )
    def tool():
        return {
            "count": 1,
            "results": [
                {
                    "source_ref": (
                        "workmind://email/test"
                    ),
                    "matched_text": (
                        f"password={secret}"
                    ),
                }
            ],
        }

    result = tool()

    serialized = json.dumps(
        result,
        ensure_ascii=False,
    )

    assert (
        secret
        not in serialized
    )

    assert (
        "[REDACTED:password]"
        in serialized
    )

    assert (
        result[
            "results"
        ][0][
            "source_ref"
        ]
        == "workmind://email/test"
    )

    assert (
        len(
            logs
        )
        == 1
    )

    assert (
        logs[0][
            "result_count"
        ]
        == 1
    )


def test_audited_tool_preserves_required_lease_token(
    monkeypatch,
) -> None:
    lease_token = (
        "synthetic-lease-capability"
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
                lease_token
            ),
            "packet": {
                "text": (
                    "safe evidence"
                ),
            },
        }

    result = tool()

    assert (
        result[
            "lease_token"
        ]
        == lease_token
    )


def test_audit_request_redacts_lease_token(
) -> None:
    lease_token = (
        "synthetic-lease-capability"
    )

    payload = (
        audit._request_payload(
            (),
            {
                "lease_token": (
                    lease_token
                ),
            },
            tool_name=(
                "validate_workmind_extraction_job"
            ),
        )
    )

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
    )

    assert (
        lease_token
        not in serialized
    )

    assert (
        "[REDACTED:token]"
        in serialized
    )


class _FakeResponse:
    def __init__(
        self,
        payload: dict,
    ) -> None:
        self._raw = json.dumps(
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
        return self._raw


def _remote_settings():
    return SimpleNamespace(
        knowledge_extraction_remote_consent=True,
        knowledge_extraction_api_key=(
            "synthetic-provider-api-key"
        ),
        knowledge_extraction_api_base=(
            "https://example.test/v1"
        ),
        knowledge_extraction_model=(
            "synthetic-model"
        ),
        knowledge_extraction_timeout_seconds=30,
    )


def test_openai_provider_redacts_packet_before_http_send(
    monkeypatch,
) -> None:
    secret = (
        "synthetic-outgoing-secret"
    )

    captured: dict[
        str,
        object,
    ] = {}

    def fake_urlopen(
        request,
        timeout,
    ):
        captured[
            "request"
        ] = request

        captured[
            "timeout"
        ] = timeout

        return _FakeResponse(
            {
                "id": "resp_test",
                "output_text": json.dumps(
                    {
                        "entities": [],
                        "claims": [],
                        "relations": [],
                        "events": [],
                    }
                ),
            }
        )

    monkeypatch.setattr(
        openai_provider
        .urllib
        .request,
        "urlopen",
        fake_urlopen,
    )

    packet = {
        "source_ref": (
            "workmind://email/test"
        ),
        "chunk_id": (
            "chunk-test"
        ),
        "text": (
            f"password={secret}"
        ),
        "metadata": {
            "access_token": (
                secret
            ),
        },
    }

    (
        bundle_json,
        response_json,
    ) = (
        openai_provider.extract_with_openai(
            packet,
            settings=(
                _remote_settings()
            ),
        )
    )

    request = captured[
        "request"
    ]

    body = json.loads(
        request.data.decode(
            "utf-8"
        )
    )

    serialized_input = (
        body["input"]
    )

    assert (
        secret
        not in serialized_input
    )

    assert (
        "[REDACTED:password]"
        in serialized_input
    )

    assert (
        "[REDACTED:access_token]"
        in serialized_input
    )

    assert (
        "workmind://email/test"
        in serialized_input
    )

    # Internal packet is intentionally
    # still raw and unchanged.
    assert (
        secret
        in packet["text"]
    )

    assert (
        json.loads(
            bundle_json
        )
        == {
            "entities": [],
            "claims": [],
            "relations": [],
            "events": [],
        }
    )

    assert (
        response_json[
            "id"
        ]
        == "resp_test"
    )


def test_openai_provider_redacts_http_error_body(
    monkeypatch,
) -> None:
    secret = (
        "synthetic-error-secret"
    )

    def fake_urlopen(
        request,
        timeout,
    ):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            {},
            io.BytesIO(
                (
                    "password="
                    + secret
                ).encode(
                    "utf-8"
                )
            ),
        )

    monkeypatch.setattr(
        openai_provider
        .urllib
        .request,
        "urlopen",
        fake_urlopen,
    )

    with pytest.raises(
        openai_provider
        .KnowledgeProviderError
    ) as caught:

        openai_provider.extract_with_openai(
            {
                "text": "safe",
            },
            settings=(
                _remote_settings()
            ),
        )

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

def test_audited_tool_redacts_positional_lease_token_from_audit(
    monkeypatch,
) -> None:
    lease_token = (
        "synthetic-positional-lease-capability"
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
        "validate_workmind_extraction_job"
    )
    def tool(
        job_id: str,
        lease_token: str,
    ):
        return {
            "ok": True,
        }

    result = tool(
        "synthetic-job",
        lease_token,
    )

    assert (
        result["ok"]
        is True
    )

    assert len(logs) == 1

    serialized = json.dumps(
        logs[0][
            "request_json"
        ],
        ensure_ascii=False,
    )

    assert (
        lease_token
        not in serialized
    )

    assert (
        "[REDACTED:token]"
        in serialized
    )


def test_lease_token_response_exception_is_limited_to_batch_tool(
    monkeypatch,
) -> None:
    lease_token = (
        "synthetic-response-lease-capability"
    )

    monkeypatch.setattr(
        audit,
        "_write_log",
        lambda **kwargs: None,
    )

    @audit.audited_tool(
        "synthetic_unrelated_tool"
    )
    def tool():
        return {
            "lease_token": (
                lease_token
            ),
        }

    result = tool()

    assert (
        result[
            "lease_token"
        ]
        == "[REDACTED:token]"
    )
