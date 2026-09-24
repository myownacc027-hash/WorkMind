from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from packages.common.config import Settings
from packages.knowledge.extraction_schema import (
    BUNDLE_SCHEMA,
    normalize_provider_bundle,
)
from packages.knowledge.openai_provider import (
    KnowledgeProviderError,
    _output_text,
)
from packages.security.redaction import (
    redact_data,
    redact_text,
)


_ALLOWED_REASONING_EFFORTS = {
    "none",
    "low",
    "high",
    "max",
}


def extract_with_deepseek(
    packet: dict[str, Any],
    *,
    settings: Settings,
) -> tuple[str, dict[str, Any]]:
    """
    Convert one WorkMind extraction packet into a candidate knowledge bundle.

    This provider is deliberately limited to candidate generation. It does not
    validate evidence semantically and never persists knowledge. The existing
    WorkMind validation/apply pipeline remains authoritative.
    """

    if not settings.knowledge_extraction_remote_consent:
        raise KnowledgeProviderError(
            "Remote extraction is not authorized. Set "
            "KNOWLEDGE_EXTRACTION_REMOTE_CONSENT=true only after approving "
            "sending extraction packets to the configured API endpoint."
        )

    api_key = settings.deepseek_api_key.strip()

    if not api_key:
        raise KnowledgeProviderError(
            "DEEPSEEK_API_KEY is empty."
        )

    base_url = settings.deepseek_base_url.rstrip("/")

    if not base_url:
        raise KnowledgeProviderError(
            "DEEPSEEK_BASE_URL is empty."
        )

    model = settings.deepseek_model.strip()

    if not model:
        raise KnowledgeProviderError(
            "DEEPSEEK_MODEL is empty."
        )

    effort = (
        settings
        .deepseek_reasoning_effort
        .strip()
        .lower()
    )

    if effort not in _ALLOWED_REASONING_EFFORTS:
        raise KnowledgeProviderError(
            "DEEPSEEK_REASONING_EFFORT must be one of: "
            "none, low, high, max."
        )

    instructions = (
        "You are WorkMind Knowledge Extractor. Convert only the supplied "
        "extraction packet into a conservative structured knowledge bundle. "
        "Follow the packet's own instructions and ontology. Never invent "
        "facts, IDs, dates, people, entities, relationships, or evidence. "
        "Every entity must contain key, entity_type, and canonical_name. "
        "The key is the local identifier used inside this bundle. entity_id "
        "is not a key: include entity_id only when copying the exact UUID "
        "of a matching identity_candidate; otherwise omit entity_id. "
        "Always return all four top-level arrays: entities, claims, "
        "relations, and events. Use an empty array when a section has no "
        "items. Events do not support a confidence field; never include "
        "confidence inside an event. "
        "Every claim, relation, and event must use evidence explicitly "
        "allowed by the packet. Use only evidence_type values permitted by "
        "the output schema. evidence_role is optional; normally omit it and "
        "let WorkMind default it to the enclosing evidence_type. If supplied, "
        "it must use a value permitted by the output schema. "
        "Reuse identity candidates only as instructed "
        "by the packet. Prefer an empty result over an unsupported fact. "
        "Credential-like values may be replaced by [REDACTED:*] markers; "
        "never infer or reconstruct them. Return JSON only."
    )

    # Corporate/work content may be sent when remote consent is enabled.
    # Credential-like values remain redacted because they are not useful
    # knowledge-extraction input.
    safe_packet = redact_data(
        packet
    )

    payload = {
        "model": model,
        "instructions": instructions,
        "input": json.dumps(
            safe_packet,
            ensure_ascii=False,
        ),
        "reasoning": {
            "effort": effort,
        },
        "max_output_tokens": int(
            settings.deepseek_max_output_tokens
        ),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "workmind_knowledge_bundle",
                "description": (
                    "Evidence-backed WorkMind entities, claims, "
                    "relations, and events."
                ),
                "schema": BUNDLE_SCHEMA,
            }
        },
        "stream": False,
    }

    request = urllib.request.Request(
        f"{base_url}/responses",
        data=json.dumps(
            payload,
            ensure_ascii=False,
        ).encode(
            "utf-8"
        ),
        headers={
            "Authorization": (
                f"Bearer {api_key}"
            ),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": (
                "WorkMind-DeepSeek-Provider/1.0"
            ),
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=float(
                settings.deepseek_timeout_seconds
            ),
        ) as response:
            raw = response.read().decode(
                "utf-8",
                errors="replace",
            )

    except urllib.error.HTTPError as exc:
        body = redact_text(
            exc.read().decode(
                "utf-8",
                errors="replace",
            )[:4000]
        )

        raise KnowledgeProviderError(
            "DeepSeek Responses API returned "
            f"HTTP {exc.code}: {body}"
        ) from exc

    except urllib.error.URLError as exc:
        reason = redact_text(
            str(
                exc.reason
            )
        )

        raise KnowledgeProviderError(
            "DeepSeek Responses API is unreachable: "
            f"{reason}"
        ) from exc

    try:
        response_json = json.loads(
            raw
        )

    except json.JSONDecodeError as exc:
        raise KnowledgeProviderError(
            "DeepSeek Responses API returned non-JSON content."
        ) from exc

    if not isinstance(
        response_json,
        dict,
    ):
        raise KnowledgeProviderError(
            "DeepSeek Responses API returned a non-object response."
        )

    status = str(
        response_json.get(
            "status"
        )
        or ""
    ).strip()

    if status != "completed":
        details = redact_data(
            response_json.get(
                "incomplete_details"
            )
            or response_json.get(
                "error"
            )
            or {}
        )

        raise KnowledgeProviderError(
            "DeepSeek Responses API did not complete: "
            f"status={status or '<missing>'}; "
            f"details={json.dumps(details, ensure_ascii=False)}"
        )

    text = _output_text(
        response_json
    )

    if not text:
        raise KnowledgeProviderError(
            "DeepSeek Responses API returned no output_text."
        )

    try:
        bundle = json.loads(
            text
        )

    except json.JSONDecodeError as exc:
        raise KnowledgeProviderError(
            "DeepSeek model output is not valid JSON: "
            f"{exc.msg}"
        ) from exc

    if not isinstance(
        bundle,
        dict,
    ):
        raise KnowledgeProviderError(
            "DeepSeek model output is not a JSON object."
        )

    try:
        normalized_bundle, normalization_actions = (
            normalize_provider_bundle(
                bundle
            )
        )
    except (TypeError, ValueError) as exc:
        raise KnowledgeProviderError(
            "DeepSeek model output cannot be normalized: "
            f"{exc}"
        ) from exc

    safe_bundle = redact_data(
        normalized_bundle
    )

    safe_response = redact_data(
        response_json
    )

    if normalization_actions:
        safe_response[
            "workmind_normalization"
        ] = {
            "actions": normalization_actions,
        }

    return (
        json.dumps(
            safe_bundle,
            ensure_ascii=False,
        ),
        safe_response,
    )
