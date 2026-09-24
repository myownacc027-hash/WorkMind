from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from packages.knowledge import deepseek_provider
from packages.knowledge.openai_provider import (
    KnowledgeProviderError,
)


class _FakeResponse:
    def __init__(
        self,
        value: dict,
    ) -> None:
        self._value = value

    def __enter__(
        self,
    ):
        return self

    def __exit__(
        self,
        *_args,
    ) -> None:
        return None

    def read(
        self,
    ) -> bytes:
        return json.dumps(
            self._value
        ).encode(
            "utf-8"
        )


def _settings(
    **overrides,
):
    values = {
        "knowledge_extraction_remote_consent": True,
        "deepseek_api_key": "synthetic-deepseek-key",
        "deepseek_base_url": "https://api.deepseek.com",
        "deepseek_model": "deepseek-flash",
        "deepseek_reasoning_effort": "none",
        "deepseek_max_output_tokens": 16384,
        "deepseek_timeout_seconds": 120.0,
    }

    values.update(
        overrides
    )

    return SimpleNamespace(
        **values
    )


def test_deepseek_requires_remote_consent() -> None:
    with pytest.raises(
        KnowledgeProviderError,
        match="Remote extraction is not authorized",
    ):
        deepseek_provider.extract_with_deepseek(
            {},
            settings=_settings(
                knowledge_extraction_remote_consent=False,
            ),
        )


def test_deepseek_requires_api_key() -> None:
    with pytest.raises(
        KnowledgeProviderError,
        match="DEEPSEEK_API_KEY is empty",
    ):
        deepseek_provider.extract_with_deepseek(
            {},
            settings=_settings(
                deepseek_api_key="",
            ),
        )


def test_deepseek_rejects_unknown_reasoning_effort() -> None:
    with pytest.raises(
        KnowledgeProviderError,
        match="DEEPSEEK_REASONING_EFFORT",
    ):
        deepseek_provider.extract_with_deepseek(
            {},
            settings=_settings(
                deepseek_reasoning_effort="turbo",
            ),
        )


def test_deepseek_builds_responses_payload_and_redacts(
    monkeypatch,
) -> None:
    secret = "synthetic-request-secret"
    captured = {}

    def fake_urlopen(
        request,
        timeout,
    ):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["authorization"] = request.headers.get(
            "Authorization"
        )
        captured["payload"] = json.loads(
            request.data.decode(
                "utf-8"
            )
        )

        return _FakeResponse(
            {
                "id": "resp_deepseek_test",
                "status": "completed",
                "model": "deepseek-flash",
                "output_text": json.dumps(
                    {
                        "entities": [],
                        "claims": [],
                        "relations": [],
                        "events": [],
                    }
                ),
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "total_tokens": 110,
                    "input_tokens_details": {
                        "cached_tokens": 0,
                    },
                    "output_tokens_details": {
                        "reasoning_tokens": 0,
                    },
                },
            }
        )

    monkeypatch.setattr(
        deepseek_provider
        .urllib
        .request,
        "urlopen",
        fake_urlopen,
    )

    packet = {
        "packet_contract_version": "wm-ke-v2",
        "chunks": [
            {
                "source_ref": "workmind://email/test",
                "chunk_id": "chunk-test",
                "text": (
                    "Project Alpha status is active. "
                    f"password={secret}"
                ),
            }
        ],
        "allowed_evidence": [
            {
                "source_ref": "workmind://email/test",
                "chunk_id": "chunk-test",
            }
        ],
    }

    bundle_json, response_json = (
        deepseek_provider
        .extract_with_deepseek(
            packet,
            settings=_settings(),
        )
    )

    assert captured["url"] == (
        "https://api.deepseek.com/responses"
    )

    assert captured["timeout"] == 120.0

    assert captured[
        "authorization"
    ] == "Bearer synthetic-deepseek-key"

    payload = captured[
        "payload"
    ]

    assert payload[
        "model"
    ] == "deepseek-flash"

    assert payload[
        "reasoning"
    ] == {
        "effort": "none"
    }

    assert payload[
        "max_output_tokens"
    ] == 16384

    assert payload[
        "stream"
    ] is False

    assert payload[
        "text"
    ][
        "format"
    ][
        "type"
    ] == "json_schema"

    assert payload[
        "text"
    ][
        "format"
    ][
        "schema"
    ] == deepseek_provider.BUNDLE_SCHEMA

    serialized_input = payload[
        "input"
    ]

    assert secret not in serialized_input

    assert json.loads(
        bundle_json
    ) == {
        "entities": [],
        "claims": [],
        "relations": [],
        "events": [],
    }

    assert response_json[
        "id"
    ] == "resp_deepseek_test"

    assert response_json[
        "usage"
    ][
        "output_tokens_details"
    ][
        "reasoning_tokens"
    ] == 0


def test_deepseek_rejects_incomplete_response(
    monkeypatch,
) -> None:
    def fake_urlopen(
        _request,
        timeout,
    ):
        assert timeout == 120.0

        return _FakeResponse(
            {
                "id": "resp_incomplete",
                "status": "incomplete",
                "incomplete_details": {
                    "reason": "max_output_tokens",
                },
            }
        )

    monkeypatch.setattr(
        deepseek_provider
        .urllib
        .request,
        "urlopen",
        fake_urlopen,
    )

    with pytest.raises(
        KnowledgeProviderError,
        match="did not complete",
    ):
        deepseek_provider.extract_with_deepseek(
            {},
            settings=_settings(),
        )
