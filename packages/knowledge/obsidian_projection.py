from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


PROJECTION_VERSION = "wm-obsidian-v1"
MAX_EXCERPT_CHARS = 600

_SAFE_COMPONENT_RE = re.compile(
    r"[^a-z0-9_-]+"
)


class ObsidianProjectionError(ValueError):
    """Raised when a structured-knowledge snapshot cannot be projected."""


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return []

    return [
        item
        for item in value
        if isinstance(item, Mapping)
    ]


def _entity(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = snapshot.get("entity")

    if isinstance(nested, Mapping):
        return nested

    if (
        snapshot.get("entity_id") is not None
        or snapshot.get("id") is not None
    ):
        return snapshot

    raise ObsidianProjectionError(
        "Projection snapshot does not contain an entity."
    )


def _entity_id(entity: Mapping[str, Any]) -> str:
    raw = (
        entity.get("entity_id")
        or entity.get("id")
    )

    if raw is None:
        raise ObsidianProjectionError(
            "Entity does not contain entity_id."
        )

    try:
        return str(uuid.UUID(str(raw)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ObsidianProjectionError(
            f"Invalid entity_id: {raw!r}"
        ) from exc


def _entity_type(entity: Mapping[str, Any]) -> str:
    raw = str(
        entity.get("entity_type")
        or "other"
    ).strip().casefold()

    safe = _SAFE_COMPONENT_RE.sub(
        "-",
        raw,
    ).strip("-_")

    return safe or "other"


def _canonical_name(entity: Mapping[str, Any]) -> str:
    value = str(
        entity.get("canonical_name")
        or ""
    ).strip()

    if not value:
        raise ObsidianProjectionError(
            "Entity does not contain canonical_name."
        )

    return value


def _yaml_string(value: Any) -> str:
    return json.dumps(
        str(value),
        ensure_ascii=False,
    )


def _inline(value: Any) -> str:
    text = " ".join(
        str(value or "").split()
    )

    return (
        text
        .replace("\\", "\\\\")
        .replace("|", "\\|")
    )


def _code(value: Any) -> str:
    return (
        str(value or "")
        .replace("`", "'")
        .strip()
    )


def _excerpt(value: Any) -> str:
    text = " ".join(
        str(value or "").split()
    )

    if len(text) <= MAX_EXCERPT_CHARS:
        return text

    return (
        text[: MAX_EXCERPT_CHARS - 1]
        .rstrip()
        + "…"
    )


def entity_relative_path(
    snapshot: Mapping[str, Any],
) -> Path:
    """
    Return the stable path of one entity inside the managed projection.

    Canonical names are deliberately not used in filenames. A rename of an
    entity must update the existing projection rather than create a second
    note with another filename.
    """

    entity = _entity(snapshot)

    return (
        Path("Entities")
        / _entity_type(entity)
        / f"{_entity_id(entity)}.md"
    )


def _evidence_sort_key(
    item: Mapping[str, Any],
) -> tuple[str, str, str]:
    return (
        str(item.get("source_ref") or ""),
        str(item.get("chunk_id") or ""),
        str(item.get("evidence_role") or ""),
    )


def _evidence_lines(
    evidence: Any,
    *,
    indent: str = "  ",
) -> list[str]:
    rows = sorted(
        _rows(evidence),
        key=_evidence_sort_key,
    )

    lines: list[str] = []

    for item in rows:
        source_ref = _code(
            item.get("source_ref")
        )
        chunk_id = _code(
            item.get("chunk_id")
        )
        role = _inline(
            item.get("evidence_role")
            or item.get("role")
        )

        label = (
            f"{indent}- Evidence"
        )

        if role:
            label += f" ({role})"

        if source_ref:
            label += f": `{source_ref}`"

        if chunk_id:
            label += f" / chunk `{chunk_id}`"

        lines.append(label)

        excerpt = _excerpt(
            item.get("excerpt")
        )

        if excerpt:
            lines.append(
                f"{indent}  - Excerpt: {excerpt}"
            )

    return lines


def _claim_sort_key(
    item: Mapping[str, Any],
) -> tuple[str, str, str, str, str]:
    return (
        str(item.get("predicate") or ""),
        str(item.get("status") or ""),
        str(item.get("observed_at") or ""),
        str(item.get("valid_from") or ""),
        str(item.get("claim_id") or item.get("id") or ""),
    )


def _claim_lines(
    claims: list[Mapping[str, Any]],
) -> list[str]:
    lines: list[str] = []

    for claim in sorted(
        claims,
        key=_claim_sort_key,
    ):
        predicate = _inline(
            claim.get("predicate")
            or "claim"
        )

        object_text = _inline(
            claim.get("object_text")
        )

        claim_text = _inline(
            claim.get("claim_text")
        )

        status = _inline(
            claim.get("status")
        )

        claim_id = _code(
            claim.get("claim_id")
            or claim.get("id")
        )

        value = (
            object_text
            or claim_text
            or "(no value)"
        )

        line = (
            f"- **{predicate}**: {value}"
        )

        if status:
            line += f" _[{status}]_"

        lines.append(line)

        if claim_text and claim_text != object_text:
            lines.append(
                f"  - Statement: {claim_text}"
            )

        if claim_id:
            lines.append(
                f"  - Claim ID: `{claim_id}`"
            )

        observed_at = _inline(
            claim.get("observed_at")
        )

        valid_from = _inline(
            claim.get("valid_from")
        )

        valid_to = _inline(
            claim.get("valid_to")
        )

        if observed_at:
            lines.append(
                f"  - Observed: {observed_at}"
            )

        if valid_from or valid_to:
            lines.append(
                "  - Validity: "
                f"{valid_from or '…'}"
                " → "
                f"{valid_to or '…'}"
            )

        supersedes = _code(
            claim.get("supersedes_claim_id")
        )

        superseded_by = _code(
            claim.get("superseded_by_claim_id")
        )

        if supersedes:
            lines.append(
                f"  - Supersedes: `{supersedes}`"
            )

        if superseded_by:
            lines.append(
                f"  - Superseded by: `{superseded_by}`"
            )

        lines.extend(
            _evidence_lines(
                claim.get("evidence"),
            )
        )

    return lines


def _relation_sort_key(
    item: Mapping[str, Any],
) -> tuple[str, str, str, str]:
    return (
        str(item.get("relation_type") or ""),
        str(item.get("from_entity_ref") or ""),
        str(item.get("to_entity_ref") or ""),
        str(item.get("relation_id") or item.get("id") or ""),
    )


def _relation_target(
    item: Mapping[str, Any],
    *,
    direction: str,
) -> str:
    if direction == "outgoing":
        entity_value = item.get("to_entity")
        candidates = (
            item.get("to_entity_name"),
            item.get("to_canonical_name"),
            item.get("to_entity_ref"),
            item.get("to_entity_id"),
        )
    else:
        entity_value = item.get("from_entity")
        candidates = (
            item.get("from_entity_name"),
            item.get("from_canonical_name"),
            item.get("from_entity_ref"),
            item.get("from_entity_id"),
        )

    if isinstance(entity_value, Mapping):
        target_id = _inline(
            entity_value.get("entity_id")
        )
        target_type = _inline(
            entity_value.get("entity_type")
        )
        target_name = _inline(
            entity_value.get("canonical_name")
        )

        if target_id and target_type and target_name:
            try:
                relative_path = entity_relative_path(
                    {
                        "entity": {
                            "entity_id": target_id,
                            "entity_type": target_type,
                            "canonical_name": target_name,
                        }
                    }
                )
            except (TypeError, ValueError):
                pass
            else:
                link_path = relative_path.with_suffix(
                    ""
                ).as_posix()

                alias = (
                    target_name
                    .replace("|", r"\|")
                    .replace("[", r"\[")
                    .replace("]", r"\]")
                )

                return (
                    f"[[{link_path}|{alias}]]"
                )

    for candidate in candidates:
        value = _inline(candidate)

        if value:
            return value

    return "(unknown entity)"


def _relation_lines(
    relations: Any,
    *,
    direction: str,
) -> list[str]:
    rows = sorted(
        _rows(relations),
        key=_relation_sort_key,
    )

    lines: list[str] = []

    for item in rows:
        relation_type = _inline(
            item.get("relation_type")
            or "related_to"
        )

        target = _relation_target(
            item,
            direction=direction,
        )

        status = _inline(
            item.get("status")
        )

        line = (
            f"- **{relation_type}**"
            f" → {target}"
        )

        if status:
            line += f" _[{status}]_"

        lines.append(line)

        relation_id = _code(
            item.get("relation_id")
            or item.get("id")
        )

        if relation_id:
            lines.append(
                f"  - Relation ID: `{relation_id}`"
            )

        lines.extend(
            _evidence_lines(
                item.get("evidence"),
            )
        )

    return lines


def _event_sort_key(
    item: Mapping[str, Any],
) -> tuple[str, str, str]:
    return (
        str(item.get("occurred_at") or ""),
        str(item.get("event_type") or ""),
        str(item.get("event_id") or item.get("id") or ""),
    )


def _event_lines(
    events: Any,
) -> list[str]:
    rows = sorted(
        _rows(events),
        key=_event_sort_key,
    )

    lines: list[str] = []

    for item in rows:
        occurred_at = _inline(
            item.get("occurred_at")
        )

        event_type = _inline(
            item.get("event_type")
            or "event"
        )

        title = _inline(
            item.get("title")
            or item.get("description")
            or "(untitled)"
        )

        status = _inline(
            item.get("status")
        )

        prefix = (
            f"{occurred_at} — "
            if occurred_at
            else ""
        )

        line = (
            f"- {prefix}"
            f"**{event_type}**: {title}"
        )

        if status:
            line += f" _[{status}]_"

        lines.append(line)

        event_id = _code(
            item.get("event_id")
            or item.get("id")
        )

        if event_id:
            lines.append(
                f"  - Event ID: `{event_id}`"
            )

        lines.extend(
            _evidence_lines(
                item.get("evidence"),
            )
        )

    return lines


def _append_section(
    lines: list[str],
    title: str,
    content: list[str],
) -> None:
    lines.extend(
        [
            "",
            f"## {title}",
            "",
        ]
    )

    if content:
        lines.extend(content)
    else:
        lines.append("_None._")


def render_entity_markdown(
    snapshot: Mapping[str, Any],
) -> str:
    """
    Render one deterministic Obsidian note from normalized WorkMind data.

    This function performs no I/O and has no database dependency.
    """

    entity = _entity(snapshot)

    entity_id = _entity_id(entity)
    entity_type = _entity_type(entity)
    canonical_name = _canonical_name(entity)

    status = _inline(
        entity.get("status")
        or "active"
    )

    external_id = _inline(
        entity.get("external_id")
    )

    normalized_key = _inline(
        entity.get("normalized_key")
    )

    description = str(
        entity.get("description")
        or ""
    ).strip()

    claims = _rows(
        snapshot.get("claims")
    )

    active_claims = [
        item
        for item in claims
        if str(
            item.get("status")
            or "active"
        ).casefold()
        == "active"
    ]

    historical_claims = [
        item
        for item in claims
        if item not in active_claims
    ]

    identity_evidence = (
        snapshot.get("identity_evidence")
        if snapshot.get("identity_evidence") is not None
        else entity.get("identity_evidence")
    )

    lines = [
        "---",
        (
            "workmind_projection: "
            f"{_yaml_string(PROJECTION_VERSION)}"
        ),
        "workmind_managed: true",
        "workmind_kind: entity",
        (
            "workmind_entity_id: "
            f"{_yaml_string(entity_id)}"
        ),
        (
            "entity_type: "
            f"{_yaml_string(entity_type)}"
        ),
        (
            "status: "
            f"{_yaml_string(status)}"
        ),
        (
            "canonical_name: "
            f"{_yaml_string(canonical_name)}"
        ),
    ]

    if external_id:
        lines.append(
            "external_id: "
            f"{_yaml_string(external_id)}"
        )

    if normalized_key:
        lines.append(
            "normalized_key: "
            f"{_yaml_string(normalized_key)}"
        )

    lines.extend(
        [
            "aliases:",
            f"  - {_yaml_string(canonical_name)}",
            "tags:",
            "  - workmind",
            "  - workmind/entity",
            f"  - workmind/type/{entity_type}",
            "---",
            "",
            f"# {canonical_name}",
            "",
            "> [!warning] Managed WorkMind projection",
            "> This note is generated from PostgreSQL and may be overwritten.",
            "",
            f"- **Entity ID:** `{entity_id}`",
            f"- **Type:** `{entity_type}`",
            f"- **Status:** `{status}`",
        ]
    )

    if external_id:
        lines.append(
            f"- **External ID:** `{_code(external_id)}`"
        )

    if description:
        _append_section(
            lines,
            "Description",
            [
                description,
            ],
        )

    _append_section(
        lines,
        "Active claims",
        _claim_lines(active_claims),
    )

    _append_section(
        lines,
        "Historical claims",
        _claim_lines(historical_claims),
    )

    _append_section(
        lines,
        "Outgoing relations",
        _relation_lines(
            snapshot.get("outgoing_relations"),
            direction="outgoing",
        ),
    )

    _append_section(
        lines,
        "Incoming relations",
        _relation_lines(
            snapshot.get("incoming_relations"),
            direction="incoming",
        ),
    )

    events = (
        snapshot.get("events")
        if snapshot.get("events") is not None
        else snapshot.get("real_events")
    )

    _append_section(
        lines,
        "Timeline",
        _event_lines(events),
    )

    _append_section(
        lines,
        "Dated observations",
        _claim_lines(
            _rows(
                snapshot.get("dated_claims")
            )
        ),
    )

    _append_section(
        lines,
        "Identity evidence",
        _evidence_lines(
            identity_evidence,
            indent="",
        ),
    )

    return "\n".join(lines).rstrip() + "\n"