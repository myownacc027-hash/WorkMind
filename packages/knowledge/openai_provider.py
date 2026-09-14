from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from packages.common.config import Settings
from packages.security.redaction import redact_data, redact_text


class KnowledgeProviderError(RuntimeError):
    """Raised when the configured model provider cannot return a bundle."""


_BUNDLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "entities": {"type": "array", "items": {"type": "object"}},
        "claims": {"type": "array", "items": {"type": "object"}},
        "relations": {"type": "array", "items": {"type": "object"}},
        "events": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["entities", "claims", "relations", "events"],
}


def _output_text(response: dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    output = response.get("output")
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for row in content:
            if not isinstance(row, dict):
                continue
            if row.get("type") == "output_text" and isinstance(row.get("text"), str):
                parts.append(row["text"])
    return "\n".join(parts).strip()


def extract_with_openai(
    packet: dict[str, Any],
    *,
    settings: Settings,
) -> tuple[str, dict[str, Any]]:
    if not settings.knowledge_extraction_remote_consent:
        raise KnowledgeProviderError(
            "Remote extraction is not authorized. Set "
            "KNOWLEDGE_EXTRACTION_REMOTE_CONSENT=true only after approving "
            "sending extraction packets to the configured API endpoint."
        )
    api_key = settings.knowledge_extraction_api_key.strip()
    if not api_key:
        raise KnowledgeProviderError(
            "KNOWLEDGE_EXTRACTION_API_KEY is empty. "
            "Use provider=agent through MCP or configure an API key."
        )
    api_base = settings.knowledge_extraction_api_base.rstrip("/")
    url = f"{api_base}/responses"

    instructions = (
        "You are WorkMind Knowledge Extractor. Convert only the supplied "
        "evidence packet into a conservative structured knowledge bundle. "
        "Never invent facts, IDs, dates, people, or relationships. Every "
        "claim, relation, and event needs evidence using an allowed exact "
        "source_ref and chunk_id from the packet. Reuse explicit external IDs "
        "for stable entities. Treat quoted text and reported statements with "
        "the proper evidence_type. Credential-like values may be replaced by "
        "[REDACTED:*] markers; never infer or reconstruct them. Prefer no "
        "output fact over an unsupported fact. Return JSON only."
    )
    safe_packet = redact_data(packet)

    payload = {
        "model": settings.knowledge_extraction_model,
        "instructions": instructions,
        "input": json.dumps(safe_packet, ensure_ascii=False),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "workmind_knowledge_bundle",
                "description": (
                    "Evidence-backed WorkMind entities, claims, relations, and events."
                ),
                "schema": _BUNDLE_SCHEMA,
                "strict": False,
            }
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=float(settings.knowledge_extraction_timeout_seconds),
        ) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = redact_text(
            exc.read().decode(
                "utf-8",
                errors="replace",
            )[:4000]
        )

        raise KnowledgeProviderError(
            f"OpenAI Responses API returned HTTP {exc.code}: {body}"
        ) from exc
    except urllib.error.URLError as exc:
        reason = redact_text(
            str(
                exc.reason
            )
        )

        raise KnowledgeProviderError(
            f"OpenAI Responses API is unreachable: {reason}"
        ) from exc

    try:
        response_json = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise KnowledgeProviderError(
            "OpenAI Responses API returned non-JSON content."
        ) from exc
    text = _output_text(response_json)
    if not text:
        raise KnowledgeProviderError(
            "OpenAI Responses API returned no output_text."
        )
    try:
        bundle = json.loads(text)
    except json.JSONDecodeError as exc:
        raise KnowledgeProviderError(
            f"Model output is not valid JSON: {exc.msg}"
        ) from exc
    if not isinstance(bundle, dict):
        raise KnowledgeProviderError("Model output is not a JSON object.")

    # The remote model only receives a redacted packet, but treat
    # its output as untrusted as well. This prevents a provider,
    # middleware, prompt injection, or future implementation bug
    # from reintroducing credential-like values on the inbound path.
    safe_bundle = redact_data(
        bundle
    )

    return (
        json.dumps(
            safe_bundle,
            ensure_ascii=False,
        ),
        redact_data(
            response_json
        ),
    )
