from __future__ import annotations

import unicodedata

import hashlib
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select

from packages.common.config import get_settings
from packages.database.models import (
    KnowledgeClaim,
    KnowledgeEntity,
    KnowledgeEvent,
)
from packages.knowledge import service
from packages.knowledge.obsidian_navigation import (
    render_navigation_pages,
)
from packages.knowledge.obsidian_projection import (
    PROJECTION_VERSION,
    entity_relative_path,
    render_entity_markdown,
)


MANIFEST_VERSION = "wm-obsidian-manifest-v1"


class ObsidianExportError(RuntimeError):
    """Raised when a safe Obsidian projection cannot be built."""


def _mapping_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    return [
        dict(item)
        for item in value
        if isinstance(item, Mapping)
    ]


def _normalized_dated_claims(
    value: Any,
) -> list[dict[str, Any]]:
    """
    Flatten get_timeline()/get_entity() dated-claim envelopes.

    Service shape:
        {
            "timeline_at": ...,
            "time_source": ...,
            "time_semantics": ...,
            "claim": {...}
        }

    Projection shape:
        {
            ...claim fields...,
            "timeline_at": ...,
            "time_source": ...,
            "time_semantics": ...
        }
    """

    result: list[dict[str, Any]] = []

    for item in _mapping_rows(value):
        nested = item.get("claim")

        if isinstance(nested, Mapping):
            claim = dict(nested)

            for key in (
                "timeline_at",
                "time_source",
                "time_semantics",
            ):
                if key in item:
                    claim[key] = item[key]

            result.append(claim)
            continue

        # Already-normalized input remains supported.
        result.append(dict(item))

    return result


def normalize_service_snapshot(
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Convert packages.knowledge.service.get_entity() output into the
    stable renderer contract.

    No database access and no filesystem I/O occur here.
    """

    entity = {
        key: raw.get(key)
        for key in (
            "entity_id",
            "entity_ref",
            "entity_type",
            "canonical_name",
            "external_id",
            "status",
            "description",
            "attributes",
            "created_at",
            "updated_at",
        )
    }

    return {
        "entity": entity,
        "claims": _mapping_rows(
            raw.get("claims")
        ),
        "outgoing_relations": _mapping_rows(
            raw.get("outgoing_relations")
        ),
        "incoming_relations": _mapping_rows(
            raw.get("incoming_relations")
        ),
        "events": _mapping_rows(
            raw.get("events")
        ),
        "dated_claims": _normalized_dated_claims(
            raw.get("dated_claims")
        ),
        "identity_evidence": _mapping_rows(
            raw.get("identity_evidence")
        ),
    }


def _entity_uuid(
    entity_id: str | uuid.UUID,
) -> uuid.UUID:
    try:
        return uuid.UUID(str(entity_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ObsidianExportError(
            f"Invalid entity id: {entity_id!r}"
        ) from exc


def _scalar_count(
    session,
    statement,
) -> int:
    return int(
        session.scalar(statement)
        or 0
    )


def _projection_counts(
    entity_id: str | uuid.UUID,
) -> dict[str, int]:
    """
    Count the complete database-side projection surface.

    These counts are used to detect limits inside get_entity().
    A truncated service response is rejected rather than exported.
    """

    value = _entity_uuid(entity_id)

    with service.SessionLocal() as session:
        ordinary_claim_count = _scalar_count(
            session,
            select(func.count())
            .select_from(KnowledgeClaim)
            .where(
                KnowledgeClaim.subject_entity_id
                == value,
                KnowledgeClaim.object_entity_id.is_(
                    None
                ),
            ),
        )

        outgoing_relation_count = _scalar_count(
            session,
            select(func.count())
            .select_from(KnowledgeClaim)
            .where(
                KnowledgeClaim.subject_entity_id
                == value,
                KnowledgeClaim.object_entity_id.is_not(
                    None
                ),
            ),
        )

        incoming_relation_count = _scalar_count(
            session,
            select(func.count())
            .select_from(KnowledgeClaim)
            .where(
                KnowledgeClaim.object_entity_id
                == value
            ),
        )

        event_count = _scalar_count(
            session,
            select(func.count())
            .select_from(KnowledgeEvent)
            .where(
                KnowledgeEvent.entity_id
                == value
            ),
        )

        dated_claim_count = _scalar_count(
            session,
            select(func.count())
            .select_from(KnowledgeClaim)
            .where(
                KnowledgeClaim.subject_entity_id
                == value,
                KnowledgeClaim.object_entity_id.is_(
                    None
                ),
                or_(
                    KnowledgeClaim.observed_at.is_not(
                        None
                    ),
                    KnowledgeClaim.valid_from.is_not(
                        None
                    ),
                ),
            ),
        )

    return {
        "ordinary_claim_count": ordinary_claim_count,
        "outgoing_relation_count": (
            outgoing_relation_count
        ),
        "incoming_relation_count": (
            incoming_relation_count
        ),
        "event_count": event_count,
        "dated_claim_count": dated_claim_count,
    }


def _assert_complete_service_snapshot(
    raw: Mapping[str, Any],
    counts: Mapping[str, int],
) -> None:
    checks = (
        (
            "claims",
            "ordinary_claim_count",
        ),
        (
            "outgoing_relations",
            "outgoing_relation_count",
        ),
        (
            "incoming_relations",
            "incoming_relation_count",
        ),
        (
            "events",
            "event_count",
        ),
        (
            "dated_claims",
            "dated_claim_count",
        ),
    )

    failures: list[str] = []

    for response_key, count_key in checks:
        actual = len(
            _mapping_rows(
                raw.get(response_key)
            )
        )

        expected = int(
            counts.get(count_key, 0)
        )

        if actual != expected:
            failures.append(
                f"{response_key}: "
                f"service={actual}, database={expected}"
            )

    if failures:
        raise ObsidianExportError(
            "Incomplete knowledge snapshot; "
            "refusing truncated projection: "
            + "; ".join(failures)
        )



def _list_entities(
    *,
    limit: int | None = 100,
    status: str | None = None,
) -> dict[str, Any]:
    """
    List entities for an Obsidian projection.

    Numeric limits are intended for read-only preview manifests and remain
    capped at 1000 rows.

    limit=None means a complete projection and intentionally applies no SQL
    row limit. Writable projection uses this mode.
    """

    safe_limit = (
        None
        if limit is None
        else max(
            1,
            min(
                int(limit),
                1000,
            ),
        )
    )

    clean_status = (
        status.strip()
        if status
        else None
    )

    with service.SessionLocal() as session:
        count_statement = (
            select(func.count())
            .select_from(KnowledgeEntity)
        )

        entity_statement = select(
            KnowledgeEntity
        )

        if clean_status:
            count_statement = (
                count_statement.where(
                    KnowledgeEntity.status
                    == clean_status
                )
            )

            entity_statement = (
                entity_statement.where(
                    KnowledgeEntity.status
                    == clean_status
                )
            )

        total_count = _scalar_count(
            session,
            count_statement,
        )

        entity_statement = (
            entity_statement.order_by(
                KnowledgeEntity.entity_type,
                KnowledgeEntity.canonical_name,
                KnowledgeEntity.id,
            )
        )

        if safe_limit is not None:
            entity_statement = (
                entity_statement.limit(
                    safe_limit
                )
            )

        rows = list(
            session.scalars(
                entity_statement
            )
        )

    entities = [
        {
            "entity_id": str(row.id),
            "entity_type": row.entity_type,
            "canonical_name": row.canonical_name,
            "status": row.status,
        }
        for row in rows
    ]

    return {
        "total_count": total_count,
        "selected_count": len(entities),
        "entities": entities,
    }


def _load_entity_snapshot(
    entity_id: str,
    *,
    include_excerpt: bool,
) -> dict[str, Any]:
    return service.get_entity(
        entity_id,
        include_claims=True,
        include_relations=True,
        include_timeline=True,
        include_evidence=True,
        include_excerpt=include_excerpt,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def build_projection_manifest(
    *,
    limit: int | None = 100,
    status: str | None = None,
    include_excerpt: bool = False,
    vault_root: str | Path | None = None,
) -> dict[str, Any]:
    """
    Build a deterministic read-only export plan.

    PostgreSQL is read through the existing structured-knowledge service.
    No Markdown files or directories are created.
    """

    if vault_root is None:
        settings = get_settings()

        root = (
            settings.workmind_home
            / "obsidian-vault"
        )
    else:
        root = Path(vault_root)

    selection = _list_entities(
        limit=limit,
        status=status,
    )

    entries: list[dict[str, Any]] = []

    for summary in selection["entities"]:
        entity_id = str(
            summary["entity_id"]
        )

        raw = _load_entity_snapshot(
            entity_id,
            include_excerpt=include_excerpt,
        )

        counts = _projection_counts(
            entity_id
        )

        _assert_complete_service_snapshot(
            raw,
            counts,
        )

        snapshot = normalize_service_snapshot(
            raw
        )

        relative_path = (
            entity_relative_path(
                snapshot
            )
        )

        markdown = render_entity_markdown(
            snapshot
        )

        payload = markdown.encode(
            "utf-8"
        )

        target_path = root.joinpath(
            *relative_path.parts
        )

        entries.append(
            {
                "entity_id": entity_id,
                "entity_type": (
                    summary["entity_type"]
                ),
                "canonical_name": (
                    summary["canonical_name"]
                ),
                "status": summary["status"],
                "relative_path": (
                    relative_path.as_posix()
                ),
                "target_path": str(
                    target_path
                ),
                "content_sha256": (
                    hashlib.sha256(
                        payload
                    ).hexdigest()
                ),
                "content_chars": len(
                    markdown
                ),
                "content_bytes": len(
                    payload
                ),
                "counts": dict(
                    counts
                ),
            }
        )

    entries.sort(
        key=lambda item: (
            item["relative_path"],
            item["entity_id"],
        )
    )

    path_owners: dict[
        str,
        dict[str, Any],
    ] = {}

    for entry in entries:
        collision_key = unicodedata.normalize(
            "NFC",
            str(
                entry["relative_path"]
            ),
        ).casefold()

        previous = path_owners.get(
            collision_key
        )

        if previous is not None:
            raise ObsidianExportError(
                "Projection path collision between "
                "entities "
                f"{previous['entity_id']} and "
                f"{entry['entity_id']}: "
                f"{entry['relative_path']}"
            )

        path_owners[
            collision_key
        ] = entry

    navigation_pages = (
        render_navigation_pages(
            entries
        )
    )

    navigation_entries: list[
        dict[str, Any]
    ] = []

    for (
        relative_path,
        markdown,
    ) in navigation_pages.items():
        relative = Path(
            relative_path
        )

        payload = markdown.encode(
            "utf-8"
        )

        target_path = root.joinpath(
            *relative.parts
        )

        navigation_entries.append(
            {
                "navigation_role": (
                    "home"
                    if relative_path == "Home.md"
                    else "index"
                ),
                "relative_path": (
                    relative.as_posix()
                ),
                "target_path": str(
                    target_path
                ),
                "content_sha256": (
                    hashlib.sha256(
                        payload
                    ).hexdigest()
                ),
                "content_chars": len(
                    markdown
                ),
                "content_bytes": len(
                    payload
                ),
            }
        )

    navigation_entries.sort(
        key=lambda item: (
            item["relative_path"],
            item["navigation_role"],
        )
    )

    navigation_bytes = sum(
        int(item["content_bytes"])
        for item in navigation_entries
    )

    navigation_fingerprint = (
        _sha256_text(
            "\n".join(
                (
                    f"{item['relative_path']} "
                    f"{item['content_sha256']}"
                )
                for item in navigation_entries
            )
        )
    )

    total_bytes = sum(
        int(item["content_bytes"])
        for item in entries
    )

    manifest_fingerprint = (
        _sha256_text(
            "\n".join(
                (
                    f"{item['relative_path']} "
                    f"{item['content_sha256']}"
                )
                for item in entries
            )
        )
    )

    return {
        "manifest_version": MANIFEST_VERSION,
        "projection_version": PROJECTION_VERSION,
        "source_of_truth": "postgresql",
        "dry_run": True,
        "vault_writes_performed": False,
        "include_excerpt": bool(
            include_excerpt
        ),
        "status_filter": status,
        "vault_root": str(root),
        "total_entity_count": int(
            selection["total_count"]
        ),
        "selected_entity_count": len(
            entries
        ),
        "selection_complete": (
            len(entries)
            == int(
                selection["total_count"]
            )
        ),
        "total_content_bytes": (
            total_bytes
        ),
        "navigation_entry_count": len(
            navigation_entries
        ),
        "navigation_content_bytes": (
            navigation_bytes
        ),
        "projected_file_count": (
            len(entries)
            + len(navigation_entries)
        ),
        "projected_content_bytes": (
            total_bytes
            + navigation_bytes
        ),
        "navigation_fingerprint": (
            navigation_fingerprint
        ),
        "manifest_fingerprint": (
            manifest_fingerprint
        ),
        "entries": entries,
        "navigation_entries": (
            navigation_entries
        ),
    }
