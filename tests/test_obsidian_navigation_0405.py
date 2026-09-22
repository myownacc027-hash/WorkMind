from __future__ import annotations

import pytest

from packages.knowledge.obsidian_navigation import (
    ObsidianNavigationError,
    render_navigation_pages,
)


def _entries():
    return [
        {
            "entity_id": "task-b",
            "entity_type": "task",
            "canonical_name": "Task B",
            "status": "active",
            "relative_path": (
                "Entities/task/"
                "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb.md"
            ),
        },
        {
            "entity_id": "incident-a",
            "entity_type": "incident",
            "canonical_name": "Incident A",
            "status": "active",
            "relative_path": (
                "Entities/incident/"
                "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.md"
            ),
        },
        {
            "entity_id": "task-a",
            "entity_type": "task",
            "canonical_name": "Task A",
            "status": "superseded",
            "relative_path": (
                "Entities/task/"
                "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.md"
            ),
        },
        {
            "entity_id": "organization-a",
            "entity_type": "organization",
            "canonical_name": "ДСЛ",
            "status": "active",
            "relative_path": (
                "Entities/organization/"
                "cccccccc-cccc-cccc-cccc-cccccccccccc.md"
            ),
        },
    ]


def test_navigation_has_stable_core_paths() -> None:
    pages = render_navigation_pages(
        _entries()
    )

    assert set(pages) == {
        "Home.md",
        "Index/Incidents.md",
        "Index/Organizations.md",
        "Index/Tasks.md",
    }

    for markdown in pages.values():
        assert (
            "workmind_managed: true"
            in markdown
        )
        assert (
            'workmind_projection: "wm-obsidian-v1"'
            in markdown
        )
        assert (
            "workmind_kind: navigation"
            in markdown
        )


def test_home_links_indexes_and_counts() -> None:
    pages = render_navigation_pages(
        _entries()
    )

    home = pages[
        "Home.md"
    ]

    assert "# WorkMind" in home
    assert "- **Entities:** 4" in home

    assert (
        "[[Index/Tasks|Tasks]] — 2"
        in home
    )

    assert (
        "[[Index/Incidents|Incidents]] — 1"
        in home
    )

    assert (
        "[[Index/Organizations|Organizations]] — 1"
        in home
    )


def test_index_links_stable_entity_paths_and_sorts_names() -> None:
    pages = render_navigation_pages(
        _entries()
    )

    tasks = pages[
        "Index/Tasks.md"
    ]

    task_a = (
        "[[Entities/task/"
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        "|Task A]] _[superseded]_"
    )

    task_b = (
        "[[Entities/task/"
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        "|Task B]] _[active]_"
    )

    assert task_a in tasks
    assert task_b in tasks

    assert (
        tasks.index(task_a)
        < tasks.index(task_b)
    )


def test_navigation_is_deterministic_for_input_order() -> None:
    first = render_navigation_pages(
        _entries()
    )

    second = render_navigation_pages(
        list(
            reversed(
                _entries()
            )
        )
    )

    assert first == second


def test_empty_projection_still_has_core_navigation() -> None:
    pages = render_navigation_pages(
        []
    )

    assert set(pages) == {
        "Home.md",
        "Index/Incidents.md",
        "Index/Organizations.md",
        "Index/Tasks.md",
    }

    assert (
        "- **Entities:** 0"
        in pages["Home.md"]
    )

    assert (
        "_No entities._"
        in pages["Index/Tasks.md"]
    )


def test_future_entity_type_gets_safe_index() -> None:
    pages = render_navigation_pages(
        [
            {
                "entity_id": "person-a",
                "entity_type": "person",
                "canonical_name": "Alice",
                "status": "active",
                "relative_path": (
                    "Entities/person/"
                    "dddddddd-dddd-dddd-dddd-dddddddddddd.md"
                ),
            }
        ]
    )

    assert (
        "Index/person.md"
        in pages
    )

    assert (
        "[[Index/person|person]] — 1"
        in pages["Home.md"]
    )

    assert (
        "[[Entities/person/"
        "dddddddd-dddd-dddd-dddd-dddddddddddd"
        "|Alice]] _[active]_"
        in pages["Index/person.md"]
    )


def test_unsafe_entity_path_is_rejected() -> None:
    with pytest.raises(
        ObsidianNavigationError,
        match="Unsafe entity projection path",
    ):
        render_navigation_pages(
            [
                {
                    "entity_id": "bad",
                    "entity_type": "task",
                    "canonical_name": "Bad",
                    "status": "active",
                    "relative_path": "../Bad.md",
                }
            ]
        )


def test_navigation_contains_no_generation_timestamp() -> None:
    pages = render_navigation_pages(
        _entries()
    )

    combined = "\n".join(
        pages.values()
    ).casefold()

    assert "generated_at" not in combined
    assert "generated at" not in combined
