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
    relative = Path(relative_path)

    if relative.is_absolute():
        raise ObsidianExportError(
            "Projection path must be relative."
        )

    if (
        not relative.parts
        or relative.parts[0] != "Entities"
        or ".." in relative.parts
        or relative.suffix.casefold() != ".md"
    ):
        raise ObsidianExportError(
            "Unsafe projection path: "
            f"{relative_path!r}"
        )

    root_resolved = root.resolve(
        strict=False
    )

    target = root.joinpath(
        *relative.parts
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

    The manifest hash must still match. This prevents applying stale
    output when structured knowledge changed after manifest creation.
    """

    payloads: dict[str, bytes] = {}

    entries = manifest.get(
        "entries",
        []
    )

    if not isinstance(entries, list):
        raise ObsidianExportError(
            "Invalid projection manifest."
        )

    for entry in entries:
        if not isinstance(entry, Mapping):
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

        payloads[relative_path] = payload

    return payloads


def _preflight_vault(
    root: Path,
    payloads: Mapping[str, bytes],
    *,
    prune: bool,
) -> dict[str, Any]:
    """
    Validate all overwrite/delete decisions before modifying the vault.
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

    entities_root = root / "Entities"

    if entities_root.exists():
        if entities_root.is_symlink():
            raise ObsidianExportError(
                "Entities directory must not be a symlink."
            )

        if not entities_root.is_dir():
            raise ObsidianExportError(
                "Entities path exists but is not a directory."
            )

    targets: dict[Path, bytes] = {}
    unchanged: set[Path] = set()

    for relative_path, payload in payloads.items():
        target = _safe_target_path(
            root,
            relative_path,
        )

        if target in targets:
            raise ObsidianExportError(
                "Duplicate projection target: "
                f"{relative_path}"
            )

        targets[target] = payload

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

        if not _is_managed_file(target):
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

        if existing_hash == desired_hash:
            unchanged.add(target)

    stale_managed: list[Path] = []
    preserved_unmanaged = 0

    if (
        prune
        and entities_root.exists()
    ):
        for path in entities_root.rglob(
            "*.md"
        ):
            if path.is_symlink():
                preserved_unmanaged += 1
                continue

            if not path.is_file():
                continue

            if path in targets:
                continue

            if _is_managed_file(path):
                stale_managed.append(path)
            else:
                preserved_unmanaged += 1

    stale_managed.sort(
        key=lambda item: str(item)
    )

    return {
        "targets": targets,
        "unchanged": unchanged,
        "stale_managed": stale_managed,
        "preserved_unmanaged": preserved_unmanaged,
    }


def apply_projection(
    *,
    limit: int = 100,
    status: str | None = None,
    include_excerpt: bool = False,
    vault_root: str | Path | None = None,
    prune: bool = True,
) -> dict[str, Any]:
    """
    Safely persist a complete WorkMind Markdown projection.

    PostgreSQL remains the source of truth.
    """

    manifest = build_projection_manifest(
        limit=limit,
        status=status,
        include_excerpt=include_excerpt,
        vault_root=vault_root,
    )

    if not manifest.get(
        "selection_complete"
    ):
        raise ObsidianExportError(
            "Refusing to apply incomplete projection. "
            "Increase limit until selection_complete=true."
        )

    root = Path(
        str(manifest["vault_root"])
    )

    payloads = _render_manifest_payloads(
        manifest,
        include_excerpt=include_excerpt,
    )

    preflight = _preflight_vault(
        root,
        payloads,
        prune=prune,
    )

    targets: dict[Path, bytes] = (
        preflight["targets"]
    )

    unchanged: set[Path] = (
        preflight["unchanged"]
    )

    stale_managed: list[Path] = (
        preflight["stale_managed"]
    )

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
            dir=str(root.parent),
        )
    )

    written = 0
    pruned = 0

    try:
        staged: dict[Path, Path] = {}

        for target, payload in targets.items():
            if target in unchanged:
                continue

            relative = target.resolve(
                strict=False
            ).relative_to(
                root.resolve(
                    strict=False
                )
            )

            staged_path = staging_root.joinpath(
                *relative.parts
            )

            staged_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            staged_path.write_bytes(
                payload
            )

            staged[target] = staged_path

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

        # Remove obsolete files only when ownership is still verified.
        for path in stale_managed:
            if (
                path.exists()
                and _is_managed_file(path)
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
            manifest["projection_version"]
        ),
        "manifest_version": (
            manifest["manifest_version"]
        ),
        "vault_root": str(root),
        "manifest_fingerprint": (
            manifest["manifest_fingerprint"]
        ),
        "selected_entity_count": (
            manifest["selected_entity_count"]
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