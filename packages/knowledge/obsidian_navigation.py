from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from packages.knowledge.obsidian_projection import (
    PROJECTION_VERSION,
)


class ObsidianNavigationError(ValueError):
    """Raised when navigation pages cannot be rendered safely."""


_SAFE_COMPONENT_RE = re.compile(
    r"[^a-z0-9_-]+"
)

_CORE_INDEXES: dict[
    str,
    tuple[str, str],
] = {
    "task": (
        "Tasks.md",
        "Tasks",
    ),
    "incident": (
        "Incidents.md",
        "Incidents",
    ),
    "organization": (
        "Organizations.md",
        "Organizations",
    ),
}

_CORE_ORDER = (
    "task",
    "incident",
    "organization",
)


def _yaml_string(value: Any) -> str:
    return json.dumps(
        str(value),
        ensure_ascii=False,
    )


def _inline(value: Any) -> str:
    return " ".join(
        str(
            value
            if value is not None
            else ""
        ).split()
    )


def _wikilink_alias(value: Any) -> str:
    return (
        _inline(value)
        .replace(
            "|",
            r"\|",
        )
        .replace(
            "[",
            r"\[",
        )
        .replace(
            "]",
            r"\]",
        )
    )


def _entity_path(value: Any) -> Path:
    relative = Path(
        str(
            value
            if value is not None
            else ""
        )
    )

    if (
        relative.is_absolute()
        or not relative.parts
        or relative.parts[0] != "Entities"
        or ".." in relative.parts
        or relative.suffix.casefold() != ".md"
    ):
        raise ObsidianNavigationError(
            "Unsafe entity projection path: "
            f"{value!r}"
        )

    return relative


def _index_spec(
    entity_type: str,
) -> tuple[Path, str]:
    core = _CORE_INDEXES.get(
        entity_type
    )

    if core is not None:
        filename, title = core

        return (
            Path("Index") / filename,
            title,
        )

    slug = _SAFE_COMPONENT_RE.sub(
        "-",
        entity_type.casefold(),
    ).strip("-_")

    if not slug:
        raise ObsidianNavigationError(
            "Entity type cannot be represented "
            f"as a safe index path: {entity_type!r}"
        )

    return (
        Path("Index") / f"{slug}.md",
        entity_type,
    )


def _normalize_entries(
    entries: Sequence[
        Mapping[str, Any]
    ],
) -> list[dict[str, str]]:
    normalized: list[
        dict[str, str]
    ] = []

    seen_paths: set[str] = set()

    for item in entries:
        entity_id = _inline(
            item.get("entity_id")
        )

        entity_type = _inline(
            item.get("entity_type")
        ).casefold()

        canonical_name = _inline(
            item.get("canonical_name")
        )

        status = (
            _inline(
                item.get("status")
            )
            or "active"
        )

        relative = _entity_path(
            item.get("relative_path")
        )

        relative_path = (
            relative.as_posix()
        )

        if not entity_id:
            raise ObsidianNavigationError(
                "Navigation entry is missing entity_id."
            )

        if not entity_type:
            raise ObsidianNavigationError(
                "Navigation entry is missing entity_type."
            )

        if not canonical_name:
            raise ObsidianNavigationError(
                "Navigation entry is missing canonical_name."
            )

        if relative_path in seen_paths:
            raise ObsidianNavigationError(
                "Duplicate navigation entity path: "
                f"{relative_path}"
            )

        seen_paths.add(
            relative_path
        )

        normalized.append(
            {
                "entity_id": entity_id,
                "entity_type": entity_type,
                "canonical_name": canonical_name,
                "status": status,
                "relative_path": relative_path,
            }
        )

    normalized.sort(
        key=lambda item: (
            item["entity_type"],
            item["canonical_name"].casefold(),
            item["canonical_name"],
            item["entity_id"],
            item["relative_path"],
        )
    )

    return normalized


def _frontmatter(
    *,
    role: str,
    entity_type: str | None = None,
) -> list[str]:
    lines = [
        "---",
        (
            "workmind_projection: "
            f"{_yaml_string(PROJECTION_VERSION)}"
        ),
        "workmind_managed: true",
        "workmind_kind: navigation",
        (
            "navigation_role: "
            f"{_yaml_string(role)}"
        ),
    ]

    if entity_type is not None:
        lines.append(
            "entity_type: "
            f"{_yaml_string(entity_type)}"
        )

    lines.extend(
        [
            "tags:",
            "  - workmind",
            "  - workmind/navigation",
            "---",
        ]
    )

    return lines


def _render_index(
    entity_type: str,
    title: str,
    rows: Sequence[
        Mapping[str, str]
    ],
) -> str:
    lines = _frontmatter(
        role="index",
        entity_type=entity_type,
    )

    lines.extend(
        [
            "",
            f"# {title}",
            "",
            "> [!warning] Managed WorkMind projection",
            (
                "> This index is generated from PostgreSQL "
                "and may be overwritten."
            ),
            "",
            f"- **Entities:** {len(rows)}",
            "",
            "## Entries",
            "",
        ]
    )

    if not rows:
        lines.append(
            "_No entities._"
        )
    else:
        for item in rows:
            relative = Path(
                item["relative_path"]
            )

            target = (
                relative.with_suffix("")
                .as_posix()
            )

            alias = _wikilink_alias(
                item["canonical_name"]
            )

            status = _inline(
                item["status"]
            )

            lines.append(
                f"- [[{target}|{alias}]] "
                f"_[{status}]_"
            )

    return (
        "\n".join(lines).rstrip()
        + "\n"
    )


def _render_home(
    groups: Sequence[
        tuple[
            str,
            Path,
            str,
            int,
        ]
    ],
    *,
    total_entities: int,
) -> str:
    lines = _frontmatter(
        role="home"
    )

    lines.extend(
        [
            "",
            "# WorkMind",
            "",
            "> [!warning] Managed WorkMind projection",
            (
                "> This navigation page is generated from "
                "PostgreSQL and may be overwritten."
            ),
            "",
            "## Knowledge",
            "",
            f"- **Entities:** {total_entities}",
        ]
    )

    for (
        _entity_type,
        index_path,
        title,
        count,
    ) in groups:
        target = (
            index_path.with_suffix("")
            .as_posix()
        )

        lines.append(
            f"- [[{target}|{title}]] — {count}"
        )

    lines.extend(
        [
            "",
            "## Source of truth",
            "",
            "PostgreSQL structured knowledge.",
        ]
    )

    return (
        "\n".join(lines).rstrip()
        + "\n"
    )


def render_navigation_pages(
    entries: Sequence[
        Mapping[str, Any]
    ],
) -> dict[str, str]:
    """
    Render deterministic WorkMind navigation pages.

    This function performs no filesystem or database I/O.
    """

    if (
        isinstance(
            entries,
            (str, bytes, bytearray),
        )
        or not isinstance(
            entries,
            Sequence,
        )
    ):
        raise ObsidianNavigationError(
            "Navigation entries must be a sequence."
        )

    normalized = _normalize_entries(
        entries
    )

    grouped: dict[
        str,
        list[dict[str, str]],
    ] = {
        entity_type: []
        for entity_type in _CORE_ORDER
    }

    for item in normalized:
        grouped.setdefault(
            item["entity_type"],
            [],
        ).append(item)

    extra_types = sorted(
        (
            entity_type
            for entity_type in grouped
            if entity_type not in _CORE_ORDER
        ),
        key=str.casefold,
    )

    ordered_types = [
        *_CORE_ORDER,
        *extra_types,
    ]

    pages: dict[str, str] = {}
    home_groups: list[
        tuple[
            str,
            Path,
            str,
            int,
        ]
    ] = []

    used_index_paths: set[str] = set()

    for entity_type in ordered_types:
        index_path, title = _index_spec(
            entity_type
        )

        relative_index = (
            index_path.as_posix()
        )

        if relative_index in used_index_paths:
            raise ObsidianNavigationError(
                "Duplicate navigation index path: "
                f"{relative_index}"
            )

        used_index_paths.add(
            relative_index
        )

        rows = grouped[
            entity_type
        ]

        pages[
            relative_index
        ] = _render_index(
            entity_type,
            title,
            rows,
        )

        home_groups.append(
            (
                entity_type,
                index_path,
                title,
                len(rows),
            )
        )

    pages["Home.md"] = _render_home(
        home_groups,
        total_entities=len(
            normalized
        ),
    )

    return {
        path: pages[path]
        for path in sorted(pages)
    }
