from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import tempfile
import traceback
import unicodedata
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from packages.knowledge.obsidian_apply import apply_projection
from packages.knowledge.obsidian_export import build_projection_manifest


DEFAULT_VAULT = Path(r"C:\AIKnowledge\obsidian-vault")

UUID_STEM = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)

WIKILINK = re.compile(
    r"\[\[([^\]|]+)"
    r"(?:\|[^\]]+)?\]\]"
)

WINDOWS_FORBIDDEN = re.compile(
    r'[<>:"/\\|?*]'
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def _file_state(
    root: Path,
) -> dict[str, tuple[str, int]]:
    if not root.exists():
        return {}

    result: dict[
        str,
        tuple[str, int],
    ] = {}

    for path in sorted(
        item
        for item in root.rglob("*")
        if item.is_file()
    ):
        relative = (
            path.relative_to(root)
            .as_posix()
        )

        result[relative] = (
            _sha256(path),
            path.stat().st_mtime_ns,
        )

    return result


def _desired_paths(
    manifest: dict,
) -> set[str]:
    entity_paths = {
        str(item["relative_path"])
        for item in manifest["entries"]
    }

    navigation_paths = {
        str(item["relative_path"])
        for item
        in manifest["navigation_entries"]
    }

    return (
        entity_paths
        | navigation_paths
    )


def _verify_manifest(
    manifest: dict,
) -> set[str]:
    if not manifest["selection_complete"]:
        raise AssertionError(
            "Projection selection is incomplete."
        )

    if manifest["vault_writes_performed"]:
        raise AssertionError(
            "Manifest unexpectedly wrote to vault."
        )

    entity_entries = list(
        manifest["entries"]
    )

    navigation_entries = list(
        manifest["navigation_entries"]
    )

    if not entity_entries:
        raise AssertionError(
            "Projection contains no entities."
        )

    normalized_paths: set[str] = set()

    for entry in entity_entries:
        relative = str(
            entry["relative_path"]
        )

        entity_id = str(
            entry["entity_id"]
        )

        canonical_name = str(
            entry["canonical_name"]
        )

        path = Path(relative)

        if len(path.parts) < 3:
            raise AssertionError(
                f"Invalid entity path: {relative}"
            )

        if path.parts[0] != "Entities":
            raise AssertionError(
                f"Entity is outside Entities/: {relative}"
            )

        if path.suffix.casefold() != ".md":
            raise AssertionError(
                f"Entity is not Markdown: {relative}"
            )

        if UUID_STEM.fullmatch(path.stem):
            raise AssertionError(
                f"UUID filename remains: {relative}"
            )

        if entity_id.casefold() in path.stem.casefold():
            raise AssertionError(
                f"Entity UUID leaked into filename: {relative}"
            )

        if WINDOWS_FORBIDDEN.search(path.stem):
            raise AssertionError(
                f"Windows-unsafe filename: {relative}"
            )

        if path.stem.endswith((" ", ".")):
            raise AssertionError(
                f"Unsafe trailing character: {relative}"
            )

        key = unicodedata.normalize(
            "NFC",
            relative,
        ).casefold()

        if key in normalized_paths:
            raise AssertionError(
                f"Projection path collision: {relative}"
            )

        normalized_paths.add(key)

        print(
            f"entity={canonical_name}"
        )
        print(
            f"path={relative}"
        )

    if len(navigation_entries) < 1:
        raise AssertionError(
            "Navigation projection is empty."
        )

    desired = _desired_paths(
        manifest
    )

    if "Home.md" not in desired:
        raise AssertionError(
            "Home.md is missing."
        )

    print(
        f"entity_count={len(entity_entries)}"
    )
    print(
        f"navigation_count={len(navigation_entries)}"
    )
    print(
        f"projected_file_count={len(desired)}"
    )
    print(
        "uuid_entity_filenames=0"
    )
    print(
        "path_collisions=0"
    )

    return desired


def _verify_written_vault(
    root: Path,
    desired: set[str],
) -> None:
    state = _file_state(
        root
    )

    actual = set(
        state
    )

    if actual != desired:
        missing = sorted(
            desired - actual
        )

        unexpected = sorted(
            actual - desired
        )

        raise AssertionError(
            "Temporary vault does not match manifest. "
            f"Missing={missing}; "
            f"Unexpected={unexpected}"
        )

    for relative in sorted(
        desired
    ):
        path = root / Path(
            relative
        )

        if path.suffix.casefold() != ".md":
            continue

        text = path.read_text(
            encoding="utf-8",
        )

        if "workmind_managed: true" not in text:
            raise AssertionError(
                f"Generated file is not managed: {relative}"
            )

    print(
        f"written_vault_files={len(actual)}"
    )
    print(
        "all_generated_files_managed=true"
    )


def _verify_wikilinks(
    root: Path,
) -> None:
    total_links = 0
    entity_links = 0
    broken: list[str] = []
    uuid_links: list[str] = []

    for source in sorted(
        root.rglob("*.md")
    ):
        text = source.read_text(
            encoding="utf-8",
        )

        for raw_target in WIKILINK.findall(
            text
        ):
            total_links += 1

            target = raw_target.split(
                "#",
                1,
            )[0].strip()

            if not target:
                continue

            if target.startswith(
                "Entities/"
            ):
                entity_links += 1

                entity_stem = Path(
                    target
                ).name

                if UUID_STEM.fullmatch(
                    entity_stem
                ):
                    uuid_links.append(
                        target
                    )

            target_path = (
                root
                / Path(
                    target + ".md"
                )
            )

            if not target_path.is_file():
                broken.append(
                    (
                        source.relative_to(
                            root
                        ).as_posix()
                        + " -> "
                        + target
                    )
                )

    if broken:
        raise AssertionError(
            "Broken wikilinks: "
            + "; ".join(
                broken
            )
        )

    if uuid_links:
        raise AssertionError(
            "UUID wikilinks remain: "
            + "; ".join(
                uuid_links
            )
        )

    print(
        f"wikilink_count={total_links}"
    )
    print(
        f"entity_wikilink_count={entity_links}"
    )
    print(
        "broken_wikilinks=0"
    )
    print(
        "uuid_entity_wikilinks=0"
    )


def temp_verify() -> None:
    print(
        "=== OBSIDIAN TEMP VAULT VERIFY ==="
    )

    parent = Path(
        tempfile.mkdtemp(
            prefix="workmind-obsidian-verify-"
        )
    )

    vault = parent / "vault"

    print(
        f"temporary_vault={vault}"
    )

    try:
        print()
        print(
            "=== MANIFEST ==="
        )

        manifest = (
            build_projection_manifest(
                limit=100,
                vault_root=vault,
            )
        )

        desired = _verify_manifest(
            manifest
        )

        if vault.exists():
            existing = list(
                vault.rglob("*")
            )

            if existing:
                raise AssertionError(
                    "Dry-run unexpectedly created files."
                )

        print(
            "dry_run_writes=false"
        )

        print()
        print(
            "=== FIRST APPLY ==="
        )

        first = apply_projection(
            vault_root=vault,
        )

        print(
            "written_count="
            + str(
                first["written_count"]
            )
        )

        print(
            "unchanged_count="
            + str(
                first["unchanged_count"]
            )
        )

        print(
            "pruned_count="
            + str(
                first["pruned_count"]
            )
        )

        if (
            first["written_count"]
            != len(desired)
        ):
            raise AssertionError(
                "Unexpected first apply write count."
            )

        if first["unchanged_count"] != 0:
            raise AssertionError(
                "First apply unexpectedly had unchanged files."
            )

        if first["pruned_count"] != 0:
            raise AssertionError(
                "First apply unexpectedly pruned files."
            )

        _verify_written_vault(
            vault,
            desired,
        )

        _verify_wikilinks(
            vault
        )

        state_first = _file_state(
            vault
        )

        print()
        print(
            "=== SECOND APPLY ==="
        )

        second = apply_projection(
            vault_root=vault,
        )

        print(
            "written_count="
            + str(
                second["written_count"]
            )
        )

        print(
            "unchanged_count="
            + str(
                second["unchanged_count"]
            )
        )

        print(
            "pruned_count="
            + str(
                second["pruned_count"]
            )
        )

        if second["written_count"] != 0:
            raise AssertionError(
                "Second apply is not idempotent."
            )

        if (
            second["unchanged_count"]
            != len(desired)
        ):
            raise AssertionError(
                "Unexpected unchanged count."
            )

        if second["pruned_count"] != 0:
            raise AssertionError(
                "Second apply unexpectedly pruned files."
            )

        if second["vault_writes_performed"]:
            raise AssertionError(
                "Second apply reports writes."
            )

        state_second = _file_state(
            vault
        )

        if state_first != state_second:
            raise AssertionError(
                "Second apply changed hashes or timestamps."
            )

        stage_dirs = list(
            parent.glob(
                ".vault.workmind-stage-*"
            )
        )

        if stage_dirs:
            raise AssertionError(
                f"Stage directories remain: {stage_dirs}"
            )

        print(
            "idempotence=true"
        )
        print(
            "hash_or_timestamp_changes=0"
        )
        print(
            "remaining_stage_dirs=0"
        )

    finally:
        shutil.rmtree(
            parent,
            ignore_errors=True,
        )

        print(
            "temporary_vault_cleaned=true"
        )

    print()
    print(
        "RESULT=OBSIDIAN_TEMP_VERIFY_PASSED"
    )


def real_readonly(
    vault: Path,
) -> None:
    print(
        "=== OBSIDIAN REAL VAULT READ-ONLY VERIFY ==="
    )

    print(
        f"vault={vault}"
    )

    if not vault.is_dir():
        raise AssertionError(
            f"Vault does not exist: {vault}"
        )

    before = _file_state(
        vault
    )

    manifest = (
        build_projection_manifest(
            limit=100,
            vault_root=vault,
        )
    )

    desired = _verify_manifest(
        manifest
    )

    managed: set[str] = set()
    unmanaged: set[str] = set()

    for relative in sorted(
        before
    ):
        path = vault / Path(
            relative
        )

        is_managed = False

        if path.suffix.casefold() == ".md":
            try:
                text = path.read_text(
                    encoding="utf-8"
                )

                is_managed = (
                    "workmind_managed: true"
                    in text
                )
            except UnicodeError:
                is_managed = False

        if is_managed:
            managed.add(
                relative
            )
        else:
            unmanaged.add(
                relative
            )

    planned_create = sorted(
        desired - managed
    )

    planned_stale = sorted(
        managed - desired
    )

    already_current = sorted(
        desired & managed
    )

    print()
    print(
        f"current_file_count={len(before)}"
    )
    print(
        f"current_managed_count={len(managed)}"
    )
    print(
        f"current_unmanaged_count={len(unmanaged)}"
    )
    print(
        f"desired_managed_count={len(desired)}"
    )
    print(
        f"already_current_count={len(already_current)}"
    )
    print(
        f"planned_create_count={len(planned_create)}"
    )
    print(
        f"planned_stale_managed_count={len(planned_stale)}"
    )

    print()
    print(
        "=== PLANNED NEW MANAGED PATHS ==="
    )

    for relative in planned_create:
        print(relative)

    print()
    print(
        "=== PLANNED STALE MANAGED PATHS ==="
    )

    for relative in planned_stale:
        print(relative)

    print()
    print(
        "=== UNMANAGED FILES TO PRESERVE ==="
    )

    for relative in sorted(
        unmanaged
    ):
        print(relative)

    if any(
        path.startswith(".obsidian/")
        for path in desired
    ):
        raise AssertionError(
            "Projection unexpectedly targets .obsidian."
        )

    after = _file_state(
        vault
    )

    if before != after:
        raise AssertionError(
            "Read-only verification changed the real vault."
        )

    print()
    print(
        "real_vault_changes=0"
    )
    print(
        "obsidian_config_targeted=false"
    )
    print(
        "RESULT=OBSIDIAN_REAL_READONLY_VERIFY_PASSED"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the WorkMind Obsidian projection."
        )
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    subparsers.add_parser(
        "temp",
        help=(
            "Build and apply the projection "
            "inside a temporary vault."
        ),
    )

    real = subparsers.add_parser(
        "real-readonly",
        help=(
            "Inspect the real vault without writes."
        ),
    )

    real.add_argument(
        "--vault",
        type=Path,
        default=DEFAULT_VAULT,
    )

    return parser.parse_args()


def main() -> int:
    print(
        "==================== "
        "SEND OUTPUT TO CHAT FROM HERE "
        "===================="
    )
    print()

    try:
        args = parse_args()

        if args.command == "temp":
            temp_verify()

        elif args.command == "real-readonly":
            real_readonly(
                args.vault
            )

        else:
            raise AssertionError(
                f"Unknown command: {args.command}"
            )

        return 0

    except Exception:
        print()
        print(
            "RESULT=OBSIDIAN_VERIFY_FAILED"
        )
        print()

        traceback.print_exc()

        return 1

    finally:
        print()
        print(
            "==================== "
            "END OUTPUT TO CHAT "
            "===================="
        )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )