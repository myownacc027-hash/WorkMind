
from __future__ import annotations

import json

from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy import text

from packages.database.models import (
    KnowledgeClaim,
    KnowledgeEntity,
    KnowledgeEvidence,
)
from packages.database.session import SessionLocal
from packages.sources.bitrix24_knowledge import (
    build_b24_knowledge_plan,
)


def _count(
    session,
    model,
) -> int:
    return int(
        session.scalar(
            select(
                func.count()
            ).select_from(
                model
            )
        )
        or 0
    )


def main() -> int:
    with SessionLocal() as session:
        revision = session.execute(
            text(
                "SELECT version_num "
                "FROM alembic_version"
            )
        ).scalar_one()

        if revision != "0012_bitrix24_sources":
            raise RuntimeError(
                "Expected database revision "
                "0012_bitrix24_sources, got "
                + str(
                    revision
                )
            )

        before = {
            "entities": _count(
                session,
                KnowledgeEntity,
            ),
            "claims": _count(
                session,
                KnowledgeClaim,
            ),
            "evidence": _count(
                session,
                KnowledgeEvidence,
            ),
        }

        plan = build_b24_knowledge_plan(
            session
        )

        summary = plan[
            "summary"
        ]

        print(
            "=== B24 DETERMINISTIC KNOWLEDGE DRY-RUN ==="
        )

        print(
            "database_revision="
            + str(
                revision
            )
        )

        for key in (
            "total_tasks",
            "create_entities",
            "update_entities",
            "noop_entities",
            "collision_entities",
            "identity_evidence_candidates",
            "people_to_create",
            "parent_relation_candidates",
            "unresolved_parent_reference_count",
        ):
            print(
                key
                + "="
                + str(
                    summary[key]
                )
            )

        print(
            "resolved_parent_ids="
            + json.dumps(
                summary[
                    "resolved_parent_ids"
                ],
                ensure_ascii=False,
            )
        )

        print(
            "unresolved_parent_ids="
            + json.dumps(
                summary[
                    "unresolved_parent_ids"
                ],
                ensure_ascii=False,
            )
        )

        errors: list[str] = []

        expected = {
            "total_tasks": 37,
            "create_entities": 36,
            "update_entities": 1,
            "noop_entities": 0,
            "collision_entities": 0,
            "identity_evidence_candidates": 37,
            "people_to_create": 0,
        }

        for key, value in expected.items():
            if summary.get(
                key
            ) != value:
                errors.append(
                    key
                    + ": expected "
                    + repr(
                        value
                    )
                    + ", got "
                    + repr(
                        summary.get(
                            key
                        )
                    )
                )

        if set(
            summary[
                "resolved_parent_ids"
            ]
        ) != {
            "94104",
            "175020",
            "185038",
            "199109",
        }:
            errors.append(
                "resolved_parent_ids differ "
                "from identity audit"
            )

        if set(
            summary[
                "unresolved_parent_ids"
            ]
        ) != {
            "43033",
            "58730",
            "58771",
            "156043",
            "156051",
        }:
            errors.append(
                "unresolved_parent_ids differ "
                "from identity audit"
            )

        entities = plan[
            "entities"
        ]

        by_id = {
            item[
                "task_external_id"
            ]: item
            for item in entities
        }

        task_189422 = by_id.get(
            "189422"
        )

        if task_189422 is None:
            errors.append(
                "Task 189422 missing from plan"
            )
        else:
            if (
                task_189422[
                    "action"
                ]
                != "update"
            ):
                errors.append(
                    "Task 189422 must update "
                    "the existing entity"
                )

            if (
                task_189422[
                    "existing_entity_id"
                ]
                !=
                "1eeb7b3e-6f3b-46c7-a2fe-be2fe89f1051"
            ):
                errors.append(
                    "Task 189422 resolved to "
                    "unexpected KnowledgeEntity"
                )

            if (
                task_189422[
                    "canonical_name"
                ]
                !=
                "Б24 №189422 "
                "«Оптимизация Кластерного отбора "
                "(Групповой отбор МП)»"
            ):
                errors.append(
                    "Task 189422 canonical name "
                    "normalization differs"
                )

            b24_attributes = (
                task_189422[
                    "desired_attributes"
                ].get(
                    "b24"
                )
            )

            if not isinstance(
                b24_attributes,
                dict,
            ):
                errors.append(
                    "Task 189422 has no b24 "
                    "attributes namespace"
                )
            else:
                if (
                    b24_attributes.get(
                        "source_title"
                    )
                    !=
                    "[ЗнИ]: Оптимизация "
                    "Кластерного отбора "
                    "(Групповой отбор МП)"
                ):
                    errors.append(
                        "Task 189422 source title "
                        "was not preserved verbatim"
                    )

                if (
                    b24_attributes.get(
                        "source_namespace"
                    )
                    != "b24_task"
                ):
                    errors.append(
                        "Task 189422 source namespace "
                        "is invalid"
                    )

        if "201882" in by_id:
            errors.append(
                "Knowledge task 201882 is not in "
                "the current B24 snapshot and must "
                "not be part of the plan"
            )

        print("")
        print(
            "=== ENTITY PLAN ==="
        )

        for item in entities:
            compact = {
                "task_id": (
                    item[
                        "task_external_id"
                    ]
                ),
                "action": (
                    item[
                        "action"
                    ]
                ),
                "existing_entity_id": (
                    item[
                        "existing_entity_id"
                    ]
                ),
                "canonical_name": (
                    item[
                        "canonical_name"
                    ]
                ),
                "changed_fields": (
                    item[
                        "changed_fields"
                    ]
                ),
                "evidence_source_ref": (
                    item[
                        "evidence_source_ref"
                    ]
                ),
                "parent_external_id": (
                    item[
                        "parent_external_id"
                    ]
                ),
                "parent_resolution": (
                    item[
                        "parent_resolution"
                    ]
                ),
            }

            print(
                "PLAN="
                + json.dumps(
                    compact,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )

        session.rollback()

        after = {
            "entities": _count(
                session,
                KnowledgeEntity,
            ),
            "claims": _count(
                session,
                KnowledgeClaim,
            ),
            "evidence": _count(
                session,
                KnowledgeEvidence,
            ),
        }

        if after != before:
            errors.append(
                "Knowledge table counts changed "
                "during dry-run"
            )

        print("")
        print(
            "knowledge_entities_before="
            + str(
                before[
                    "entities"
                ]
            )
        )

        print(
            "knowledge_entities_after="
            + str(
                after[
                    "entities"
                ]
            )
        )

        print(
            "knowledge_claims_before="
            + str(
                before[
                    "claims"
                ]
            )
        )

        print(
            "knowledge_claims_after="
            + str(
                after[
                    "claims"
                ]
            )
        )

        print(
            "knowledge_evidence_before="
            + str(
                before[
                    "evidence"
                ]
            )
        )

        print(
            "knowledge_evidence_after="
            + str(
                after[
                    "evidence"
                ]
            )
        )

        if errors:
            print(
                "knowledge_dry_run_valid=false"
            )

            for error in errors:
                print(
                    "DRY_RUN_ERROR="
                    + error
                )

            return 2

        print(
            "knowledge_dry_run_valid=true"
        )
        print(
            "database_writes=false"
        )
        print(
            "person_entities_created=false"
        )
        print(
            "synthetic_parent_entities_created=false"
        )
        print(
            "claims_created=false"
        )
        print(
            "evidence_created=false"
        )
        print(
            "qdrant_writes=false"
        )
        print(
            "deepseek_calls=false"
        )
        print(
            "obsidian_writes=false"
        )
        print(
            "RESULT=WORKMIND_B24_DETERMINISTIC_KNOWLEDGE_DRY_RUN_COMPLETE"
        )

        return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
