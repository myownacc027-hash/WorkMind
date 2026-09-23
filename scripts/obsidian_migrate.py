from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from packages.knowledge.obsidian_apply import apply_projection
from packages.knowledge.obsidian_export import build_projection_manifest


VAULT = Path(r"C:\AIKnowledge\obsidian-vault")

BACKUP_ROOT = (
    REPO_ROOT
    / ".workmind-backups"
)

EXPECTED_OLD_ENTITY_PATHS = {
    (
        "Entities/incident/"
        "88b5b074-0696-4475-93b7-a312fd8305ca.md"
    ),
    (
        "Entities/incident/"
        "8bff1ff9-d8b0-44d2-8a12-575039f4e11c.md"
    ),
    (
        "Entities/organization/"
        "084e910b-353e-470b-bd5a-4e07ec7cc88d.md"
    ),
    (
        "Entities/task/"
        "1eeb7b3e-6f3b-46c7-a2fe-be2fe89f1051.md"
    ),
    (
        "Entities/task/"
        "a9484547-a890-4650-8ea9-6f589dc7837a.md"
    ),
}

EXPECTED_NEW_ENTITY_PATHS = {
    (
        "Entities/incident/"
        "Инцидент Б24 №203548.md"
    ),
    (
        "Entities/incident/"
        "Инцидент Б24 №203823.md"
    ),
    (
        "Entities/organization/"
        "ДСЛ.md"
    ),
    (
        "Entities/task/"
        "Б24 №189422 "
        "«Оптимизация Кластерного отбора "
        "(Групповой отбор МП)».md"
    ),
    (
        "Entities/task/"
        "Б24 №201882 "
        "«Приемка - Оптимизация "
        "Кластерного отбора».md"
    ),
}

EXPECTED_NAVIGATION_PATHS = {
    "Home.md",
    "Index/Incidents.md",
    "Index/Organizations.md",
    "Index/Tasks.md",
}

EXPECTED_DESIRED_PATHS = (
    EXPECTED_NEW_ENTITY_PATHS
    | EXPECTED_NAVIGATION_PATHS
)

EXPECTED_CURRENT_MANAGED_PATHS = (
    EXPECTED_OLD_ENTITY_PATHS
    | EXPECTED_NAVIGATION_PATHS
)

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


def sha256(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def file_state(
    root: Path,
) -> dict[str, tuple[str, int]]:
    result: dict[
        str,
        tuple[str, int],
    ] = {}

    if not root.exists():
        return result

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
            sha256(path),
            path.stat().st_mtime_ns,
        )

    return result


def file_hashes(
    root: Path,
) -> dict[str, str]:
    return {
        relative: state[0]
        for relative, state
        in file_state(root).items()
    }


def is_managed(
    path: Path,
) -> bool:
    if (
        not path.is_file()
        or path.suffix.casefold() != ".md"
    ):
        return False

    try:
        text = path.read_text(
            encoding="utf-8"
        )
    except (UnicodeError, OSError):
        return False

    return (
        "workmind_managed: true"
        in text
    )


def managed_paths(
    root: Path,
    state: dict[str, tuple[str, int]],
) -> set[str]:
    result: set[str] = set()

    for relative in state:
        path = root / Path(relative)

        if is_managed(path):
            result.add(relative)

    return result


def verify_manifest() -> dict:
    manifest = build_projection_manifest(
        limit=None,
        vault_root=VAULT,
    )

    if not manifest["selection_complete"]:
        raise AssertionError(
            "Projection selection is incomplete."
        )

    if manifest["vault_writes_performed"]:
        raise AssertionError(
            "Dry-run manifest unexpectedly wrote files."
        )

    desired = {
        str(item["relative_path"])
        for item in manifest["entries"]
    }

    desired.update(
        str(item["relative_path"])
        for item
        in manifest["navigation_entries"]
    )

    if desired != EXPECTED_DESIRED_PATHS:
        missing = sorted(
            EXPECTED_DESIRED_PATHS
            - desired
        )

        unexpected = sorted(
            desired
            - EXPECTED_DESIRED_PATHS
        )

        raise AssertionError(
            "Current PostgreSQL projection differs "
            "from the projection that was audited. "
            f"Missing={missing}; "
            f"Unexpected={unexpected}. "
            "Run the read-only verification again."
        )

    print(
        "manifest_selection_complete=true"
    )
    print(
        f"manifest_entity_count={len(manifest['entries'])}"
    )
    print(
        "manifest_navigation_count="
        + str(
            len(
                manifest[
                    "navigation_entries"
                ]
            )
        )
    )
    print(
        f"manifest_projected_files={len(desired)}"
    )

    return manifest


def verify_wikilinks() -> None:
    broken: list[str] = []
    uuid_links: list[str] = []

    total = 0
    entity_total = 0

    for source in sorted(
        VAULT.rglob("*.md")
    ):
        text = source.read_text(
            encoding="utf-8"
        )

        for raw_target in WIKILINK.findall(
            text
        ):
            total += 1

            target = raw_target.split(
                "#",
                1,
            )[0].strip()

            if not target:
                continue

            if target.startswith(
                "Entities/"
            ):
                entity_total += 1

                if UUID_STEM.fullmatch(
                    Path(target).name
                ):
                    uuid_links.append(
                        target
                    )

            target_path = (
                VAULT
                / Path(
                    target + ".md"
                )
            )

            if not target_path.is_file():
                broken.append(
                    (
                        source.relative_to(
                            VAULT
                        ).as_posix()
                        + " -> "
                        + target
                    )
                )

    if broken:
        raise AssertionError(
            "Broken wikilinks after migration: "
            + "; ".join(broken)
        )

    if uuid_links:
        raise AssertionError(
            "UUID entity wikilinks remain: "
            + "; ".join(uuid_links)
        )

    print(
        f"wikilink_count={total}"
    )
    print(
        f"entity_wikilink_count={entity_total}"
    )
    print(
        "broken_wikilinks=0"
    )
    print(
        "uuid_entity_wikilinks=0"
    )


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--yes",
        action="store_true",
    )

    args = parser.parse_args()

    print(
        "==================== "
        "SEND OUTPUT TO CHAT FROM HERE "
        "===================="
    )
    print()

    backup: Path | None = None

    try:
        print(
            "=== OBSIDIAN-01G4 "
            "CONTROLLED REAL VAULT MIGRATION ==="
        )

        if not args.yes:
            raise RuntimeError(
                "Explicit --yes is required."
            )

        if not VAULT.is_dir():
            raise AssertionError(
                f"Vault does not exist: {VAULT}"
            )

        print(
            f"vault={VAULT}"
        )

        print()
        print(
            "=== PRE-MIGRATION MANIFEST ==="
        )

        verify_manifest()

        before = file_state(
            VAULT
        )

        managed_before = managed_paths(
            VAULT,
            before,
        )

        unmanaged_before = (
            set(before)
            - managed_before
        )

        if (
            managed_before
            != EXPECTED_CURRENT_MANAGED_PATHS
        ):
            missing = sorted(
                EXPECTED_CURRENT_MANAGED_PATHS
                - managed_before
            )

            unexpected = sorted(
                managed_before
                - EXPECTED_CURRENT_MANAGED_PATHS
            )

            raise AssertionError(
                "Real vault managed state differs "
                "from the audited state. "
                f"Missing={missing}; "
                f"Unexpected={unexpected}."
            )

        print(
            f"before_file_count={len(before)}"
        )
        print(
            f"before_managed_count={len(managed_before)}"
        )
        print(
            f"before_unmanaged_count={len(unmanaged_before)}"
        )

        print()
        print(
            "=== UNMANAGED FILES TO PRESERVE ==="
        )

        for relative in sorted(
            unmanaged_before
        ):
            print(relative)

        print()
        print(
            "=== BACKUP ==="
        )

        BACKUP_ROOT.mkdir(
            parents=True,
            exist_ok=True,
        )

        stamp = datetime.now().strftime(
            "%Y%m%d-%H%M%S"
        )

        backup = (
            BACKUP_ROOT
            / (
                "obsidian-vault-before-01g4-"
                + stamp
            )
        )

        shutil.copytree(
            VAULT,
            backup,
            copy_function=shutil.copy2,
        )

        original_hashes = file_hashes(
            VAULT
        )

        backup_hashes = file_hashes(
            backup
        )

        if (
            original_hashes
            != backup_hashes
        ):
            raise AssertionError(
                "Backup hash verification failed."
            )

        print(
            f"backup={backup}"
        )
        print(
            f"backup_file_count={len(backup_hashes)}"
        )
        print(
            "backup_hashes_verified=true"
        )

        print()
        print(
            "=== FIRST APPLY ==="
        )

        first = apply_projection(
            vault_root=VAULT,
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
            first["pruned_count"]
            != len(
                EXPECTED_OLD_ENTITY_PATHS
            )
        ):
            raise AssertionError(
                "Unexpected prune count."
            )

        if (
            first["written_count"]
            + first["unchanged_count"]
            != len(
                EXPECTED_DESIRED_PATHS
            )
        ):
            raise AssertionError(
                "Unexpected projected target count."
            )

        after_first = file_state(
            VAULT
        )

        managed_after = managed_paths(
            VAULT,
            after_first,
        )

        if (
            managed_after
            != EXPECTED_DESIRED_PATHS
        ):
            missing = sorted(
                EXPECTED_DESIRED_PATHS
                - managed_after
            )

            unexpected = sorted(
                managed_after
                - EXPECTED_DESIRED_PATHS
            )

            raise AssertionError(
                "Managed vault state is incorrect "
                "after migration. "
                f"Missing={missing}; "
                f"Unexpected={unexpected}."
            )

        for relative in (
            EXPECTED_OLD_ENTITY_PATHS
        ):
            if (
                VAULT
                / Path(relative)
            ).exists():
                raise AssertionError(
                    "Old UUID note remains: "
                    + relative
                )

        for relative in (
            EXPECTED_NEW_ENTITY_PATHS
        ):
            if not (
                VAULT
                / Path(relative)
            ).is_file():
                raise AssertionError(
                    "New human-readable note "
                    "is missing: "
                    + relative
                )

        print(
            "old_uuid_notes_remaining=0"
        )
        print(
            "human_entity_notes=5"
        )
        print(
            "managed_projection_files=9"
        )

        print()
        print(
            "=== UNMANAGED PRESERVATION ==="
        )

        changed_unmanaged: list[str] = []
        missing_unmanaged: list[str] = []

        for relative in sorted(
            unmanaged_before
        ):
            if relative not in after_first:
                missing_unmanaged.append(
                    relative
                )
                continue

            if (
                before[relative]
                != after_first[relative]
            ):
                changed_unmanaged.append(
                    relative
                )

        if missing_unmanaged:
            raise AssertionError(
                "Unmanaged files disappeared: "
                + "; ".join(
                    missing_unmanaged
                )
            )

        if changed_unmanaged:
            raise AssertionError(
                "Unmanaged files changed: "
                + "; ".join(
                    changed_unmanaged
                )
                + ". Keep Obsidian closed "
                "while running migration."
            )

        print(
            "unmanaged_missing=0"
        )
        print(
            "unmanaged_hash_or_timestamp_changes=0"
        )

        if any(
            relative.startswith(
                ".obsidian/"
            )
            for relative
            in EXPECTED_DESIRED_PATHS
        ):
            raise AssertionError(
                ".obsidian is targeted "
                "by WorkMind projection."
            )

        print(
            "obsidian_config_targeted=false"
        )

        print()
        print(
            "=== WIKILINK VERIFY ==="
        )

        verify_wikilinks()

        print()
        print(
            "=== SECOND APPLY / IDEMPOTENCE ==="
        )

        second = apply_projection(
            vault_root=VAULT,
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
                "Second apply wrote files."
            )

        if (
            second["unchanged_count"]
            != len(
                EXPECTED_DESIRED_PATHS
            )
        ):
            raise AssertionError(
                "Unexpected second-apply "
                "unchanged count."
            )

        if second["pruned_count"] != 0:
            raise AssertionError(
                "Second apply pruned files."
            )

        if second[
            "vault_writes_performed"
        ]:
            raise AssertionError(
                "Second apply reports writes."
            )

        after_second = file_state(
            VAULT
        )

        if (
            after_first
            != after_second
        ):
            raise AssertionError(
                "Second apply changed "
                "hashes or timestamps."
            )

        stage_dirs = list(
            VAULT.parent.glob(
                ".obsidian-vault.workmind-stage-*"
            )
        )

        if stage_dirs:
            raise AssertionError(
                "Staging directories remain: "
                + repr(stage_dirs)
            )

        print(
            "idempotence=true"
        )
        print(
            "second_apply_hash_or_timestamp_changes=0"
        )
        print(
            "remaining_stage_dirs=0"
        )

        print()
        print(
            "RESULT="
            "OBSIDIAN_01G4_REAL_VAULT_MIGRATION_PASSED"
        )

        print(
            f"backup_preserved={backup}"
        )

        return 0

    except Exception:
        print()
        print(
            "RESULT="
            "OBSIDIAN_01G4_REAL_VAULT_MIGRATION_FAILED"
        )

        if backup is not None:
            print(
                f"backup_preserved={backup}"
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