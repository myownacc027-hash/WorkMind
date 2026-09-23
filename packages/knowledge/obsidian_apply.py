from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from packages.knowledge.obsidian_export import (
    ObsidianExportError,
    _assert_complete_service_snapshot,
    _load_entity_snapshot,
    _projection_counts,
    build_projection_manifest,
    normalize_service_snapshot,
)
from packages.knowledge.obsidian_navigation import (
    render_navigation_pages,
)
from packages.knowledge.obsidian_projection import (
    entity_relative_path,
    render_entity_markdown,
)


def _managed_frontmatter(text: str) -> bool:
    """
    Return True only for Markdown explicitly owned by WorkMind.

    A marker outside YAML frontmatter does not make a file managed.
    """

    normalized = text.replace(
        "\r\n",
        "\n",
    )

    if not normalized.startswith("---\n"):
        return False

    end = normalized.find(
        "\n---\n",
        4,
    )

    if end < 0:
        return False

    frontmatter = normalized[4:end].splitlines()

    managed = any(
        line.strip() == "workmind_managed: true"
        for line in frontmatter
    )

    projection = ""

    for line in frontmatter:
        clean = line.strip()

        if not clean.startswith(
            "workmind_projection:"
        ):
            continue

        projection = (
            clean.split(":", 1)[1]
            .strip()
            .strip("\"'")
        )

        break

    return (
        managed
        and projection.startswith(
            "wm-obsidian-"
        )
    )


def _is_managed_file(path: Path) -> bool:
    if (
        not path.exists()
        or not path.is_file()
        or path.is_symlink()
    ):
        return False

    try:
        text = path.read_text(
            encoding="utf-8"
        )
    except (OSError, UnicodeError):
        return False

    return _managed_frontmatter(text)



def _safe_target_path(
    root: Path,
    relative_path: str,
) -> Path:
    relative = Path(
        relative_path
    )

    parts = relative.parts

    is_entity = (
        len(parts) >= 3
        and parts[0] == "Entities"
    )

    is_home = (
        parts == ("Home.md",)
    )

    is_index = (
        len(parts) == 2
        and parts[0] == "Index"
    )

    if (
        relative.is_absolute()
        or not parts
        or ".." in parts
        or relative.suffix.casefold() != ".md"
        or not (
            is_entity
            or is_home
            or is_index
        )
    ):
        raise ObsidianExportError(
            "Unsafe projection path: "
            f"{relative_path!r}"
        )

    root_resolved = root.resolve(
        strict=False
    )

    target = root.joinpath(
        *parts
    )

    target_resolved = target.resolve(
        strict=False
    )

    try:
        target_resolved.relative_to(
            root_resolved
        )
    except ValueError as exc:
        raise ObsidianExportError(
            "Projection target escapes vault root: "
            f"{relative_path!r}"
        ) from exc

    return target



def _render_manifest_payloads(
    manifest: Mapping[str, Any],
    *,
    include_excerpt: bool,
) -> dict[str, bytes]:
    """
    Re-read and re-render each entity immediately before persistence.

    Entity and navigation hashes must still match the manifest. This
    prevents applying stale output when structured knowledge or renderer
    behavior changed after manifest creation.

    Manifests without ``navigation_entries`` remain supported as the
    legacy entity-only contract.
    """

    payloads: dict[
        str,
        bytes,
    ] = {}

    navigation_source: list[
        dict[str, Any]
    ] = []

    entries = manifest.get(
        "entries",
        []
    )

    if not isinstance(
        entries,
        list,
    ):
        raise ObsidianExportError(
            "Invalid projection manifest."
        )

    for entry in entries:
        if not isinstance(
            entry,
            Mapping,
        ):
            raise ObsidianExportError(
                "Invalid manifest entry."
            )

        entity_id = str(
            entry.get("entity_id")
            or ""
        )

        relative_path = str(
            entry.get("relative_path")
            or ""
        )

        expected_sha256 = str(
            entry.get("content_sha256")
            or ""
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

        current_path = (
            entity_relative_path(
                snapshot
            ).as_posix()
        )

        if current_path != relative_path:
            raise ObsidianExportError(
                "Projection path changed between "
                "manifest and apply for entity "
                f"{entity_id}."
            )

        markdown = render_entity_markdown(
            snapshot
        )

        payload = markdown.encode(
            "utf-8"
        )

        actual_sha256 = hashlib.sha256(
            payload
        ).hexdigest()

        if actual_sha256 != expected_sha256:
            raise ObsidianExportError(
                "Projection content changed between "
                "manifest and apply for entity "
                f"{entity_id}."
            )

        if relative_path in payloads:
            raise ObsidianExportError(
                "Duplicate projection target: "
                f"{relative_path}"
            )

        payloads[
            relative_path
        ] = payload

        navigation_source.append(
            {
                "entity_id": entity_id,
                "entity_type": (
                    entry.get(
                        "entity_type"
                    )
                ),
                "canonical_name": (
                    entry.get(
                        "canonical_name"
                    )
                ),
                "status": entry.get(
                    "status"
                ),
                "relative_path": (
                    relative_path
                ),
            }
        )

    if (
        "navigation_entries"
        not in manifest
    ):
        return payloads

    navigation_entries = manifest.get(
        "navigation_entries"
    )

    if not isinstance(
        navigation_entries,
        list,
    ):
        raise ObsidianExportError(
            "Invalid navigation manifest."
        )

    navigation_pages = (
        render_navigation_pages(
            navigation_source
        )
    )

    expected_navigation: dict[
        str,
        str,
    ] = {}

    for entry in navigation_entries:
        if not isinstance(
            entry,
            Mapping,
        ):
            raise ObsidianExportError(
                "Invalid navigation manifest entry."
            )

        relative_path = str(
            entry.get(
                "relative_path"
            )
            or ""
        )

        expected_sha256 = str(
            entry.get(
                "content_sha256"
            )
            or ""
        )

        if (
            relative_path
            in expected_navigation
        ):
            raise ObsidianExportError(
                "Duplicate navigation target: "
                f"{relative_path}"
            )

        expected_navigation[
            relative_path
        ] = expected_sha256

    if set(
        expected_navigation
    ) != set(
        navigation_pages
    ):
        raise ObsidianExportError(
            "Navigation projection changed between "
            "manifest and apply."
        )

    for (
        relative_path,
        markdown,
    ) in navigation_pages.items():
        payload = markdown.encode(
            "utf-8"
        )

        actual_sha256 = hashlib.sha256(
            payload
        ).hexdigest()

        if (
            actual_sha256
            != expected_navigation[
                relative_path
            ]
        ):
            raise ObsidianExportError(
                "Navigation content changed between "
                "manifest and apply for "
                f"{relative_path}."
            )

        if relative_path in payloads:
            raise ObsidianExportError(
                "Duplicate projection target: "
                f"{relative_path}"
            )

        payloads[
            relative_path
        ] = payload

    return payloads



def _preflight_vault(
    root: Path,
    payloads: Mapping[str, bytes],
    *,
    prune: bool,
    manage_navigation: bool = False,
) -> dict[str, Any]:
    """
    Validate every overwrite/delete decision before modifying the vault.

    WorkMind owns only:
    - Entities/**/*.md
    - Home.md when navigation is present
    - Index/*.md when navigation is present

    Other vault content, including .obsidian, is outside the managed
    projection surface.
    """

    if root.exists():
        if root.is_symlink():
            raise ObsidianExportError(
                "Vault root must not be a symlink."
            )

        if not root.is_dir():
            raise ObsidianExportError(
                "Vault root exists but is not a directory."
            )

    entities_root = (
        root
        / "Entities"
    )

    if entities_root.exists():
        if entities_root.is_symlink():
            raise ObsidianExportError(
                "Entities directory must not be a symlink."
            )

        if not entities_root.is_dir():
            raise ObsidianExportError(
                "Entities path exists but is not a directory."
            )

    index_root = (
        root
        / "Index"
    )

    if (
        manage_navigation
        and index_root.exists()
    ):
        if index_root.is_symlink():
            raise ObsidianExportError(
                "Index directory must not be a symlink."
            )

        if not index_root.is_dir():
            raise ObsidianExportError(
                "Index path exists but is not a directory."
            )

    targets: dict[
        Path,
        bytes,
    ] = {}

    unchanged: set[
        Path
    ] = set()

    for (
        relative_path,
        payload,
    ) in payloads.items():
        target = _safe_target_path(
            root,
            relative_path,
        )

        if target in targets:
            raise ObsidianExportError(
                "Duplicate projection target: "
                f"{relative_path}"
            )

        targets[
            target
        ] = payload

        if not (
            target.exists()
            or target.is_symlink()
        ):
            continue

        if target.is_symlink():
            raise ObsidianExportError(
                "Refusing to replace symlink: "
                f"{target}"
            )

        if not target.is_file():
            raise ObsidianExportError(
                "Projection target exists "
                "but is not a file: "
                f"{target}"
            )

        if not _is_managed_file(
            target
        ):
            raise ObsidianExportError(
                "Refusing to overwrite "
                "unmanaged Markdown file: "
                f"{target}"
            )

        existing_hash = hashlib.sha256(
            target.read_bytes()
        ).hexdigest()

        desired_hash = hashlib.sha256(
            payload
        ).hexdigest()

        if (
            existing_hash
            == desired_hash
        ):
            unchanged.add(
                target
            )

    stale_managed: list[
        Path
    ] = []

    preserved_unmanaged = 0

    candidates: list[
        Path
    ] = []

    if prune:
        if entities_root.exists():
            candidates.extend(
                entities_root.rglob(
                    "*.md"
                )
            )

        if manage_navigation:
            home = (
                root
                / "Home.md"
            )

            if (
                home.exists()
                or home.is_symlink()
            ):
                candidates.append(
                    home
                )

            if index_root.exists():
                candidates.extend(
                    index_root.glob(
                        "*.md"
                    )
                )

    unique_candidates = sorted(
        set(candidates),
        key=lambda item: str(item),
    )

    for path in unique_candidates:
        if path.is_symlink():
            preserved_unmanaged += 1
            continue

        if not path.is_file():
            continue

        if path in targets:
            continue

        if _is_managed_file(
            path
        ):
            stale_managed.append(
                path
            )
        else:
            preserved_unmanaged += 1

    stale_managed.sort(
        key=lambda item: str(item)
    )

    return {
        "targets": targets,
        "unchanged": unchanged,
        "stale_managed": stale_managed,
        "preserved_unmanaged": (
            preserved_unmanaged
        ),
    }



def apply_projection(
    *,
    limit: int | None = None,
    status: str | None = None,
    include_excerpt: bool = False,
    vault_root: str | Path | None = None,
    prune: bool = True,
) -> dict[str, Any]:
    """
    Safely persist a complete WorkMind Markdown projection.

    PostgreSQL remains the source of truth.

    Applying a status-filtered subset is intentionally rejected. A
    writable Obsidian projection must represent the complete entity set;
    filtered views remain available through dry-run only.
    """

    clean_status = (
        status.strip()
        if status
        else None
    )

    if clean_status:
        raise ObsidianExportError(
            "Refusing to apply a filtered projection. "
            "Use dry-run for status-filtered views."
        )

    if limit is not None:
        raise ObsidianExportError(
            "Refusing to apply a limited projection. "
            "Use dry-run for limited previews."
        )

    manifest = build_projection_manifest(
        limit=None,
        status=None,
        include_excerpt=include_excerpt,
        vault_root=vault_root,
    )

    if not manifest.get(
        "selection_complete"
    ):
        raise ObsidianExportError(
            "Refusing to apply incomplete projection. "
            "Full writable projection must be complete."
        )

    root = Path(
        str(
            manifest[
                "vault_root"
            ]
        )
    )

    payloads = _render_manifest_payloads(
        manifest,
        include_excerpt=include_excerpt,
    )

    manage_navigation = (
        "navigation_entries"
        in manifest
    )

    preflight = _preflight_vault(
        root,
        payloads,
        prune=prune,
        manage_navigation=manage_navigation,
    )

    targets: dict[
        Path,
        bytes,
    ] = preflight[
        "targets"
    ]

    unchanged: set[
        Path
    ] = preflight[
        "unchanged"
    ]

    stale_managed: list[
        Path
    ] = preflight[
        "stale_managed"
    ]

    # Nothing in the vault has changed before this point.
    root.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    staging_root = Path(
        tempfile.mkdtemp(
            prefix=(
                f".{root.name}"
                ".workmind-stage-"
            ),
            dir=str(
                root.parent
            ),
        )
    )

    written = 0
    pruned = 0

    try:
        staged: dict[
            Path,
            Path,
        ] = {}

        for (
            target,
            payload,
        ) in targets.items():
            if target in unchanged:
                continue

            relative = target.resolve(
                strict=False
            ).relative_to(
                root.resolve(
                    strict=False
                )
            )

            staged_path = (
                staging_root.joinpath(
                    *relative.parts
                )
            )

            staged_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            staged_path.write_bytes(
                payload
            )

            staged[
                target
            ] = staged_path

        # Every new payload exists before the first target is changed.
        root.mkdir(
            parents=True,
            exist_ok=True,
        )

        for target in sorted(
            staged,
            key=lambda item: str(item),
        ):
            target.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            os.replace(
                staged[target],
                target,
            )

            written += 1

        # Remove obsolete files only while ownership is still verified.
        for path in stale_managed:
            if (
                path.exists()
                and _is_managed_file(
                    path
                )
            ):
                path.unlink()
                pruned += 1

    finally:
        shutil.rmtree(
            staging_root,
            ignore_errors=True,
        )

    return {
        "ok": True,
        "source_of_truth": "postgresql",
        "projection_version": (
            manifest[
                "projection_version"
            ]
        ),
        "manifest_version": (
            manifest[
                "manifest_version"
            ]
        ),
        "vault_root": str(
            root
        ),
        "manifest_fingerprint": (
            manifest[
                "manifest_fingerprint"
            ]
        ),
        "selected_entity_count": (
            manifest[
                "selected_entity_count"
            ]
        ),
        "selection_complete": True,
        "written_count": written,
        "unchanged_count": len(
            unchanged
        ),
        "pruned_count": pruned,
        "preserved_unmanaged_count": (
            preflight[
                "preserved_unmanaged"
            ]
        ),
        "vault_writes_performed": (
            written > 0
            or pruned > 0
        ),
    }
