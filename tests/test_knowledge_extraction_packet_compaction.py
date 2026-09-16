from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace

from packages.knowledge import extraction


def _row(
    index: int,
    text: str,
):
    return SimpleNamespace(
        chunk_id=uuid.uuid4(),
        chunk_index=index,
        text=text,
    )


def _append(
    rows,
    *,
    chunks=None,
    current_chars=0,
    max_chunks=120,
    max_context_chars=60_000,
    source_id=None,
    source_ref=None,
):
    target_chunks = (
        chunks
        if chunks is not None
        else []
    )

    target_id = (
        source_id
        or uuid.uuid4()
    )

    target_ref = (
        source_ref
        or f"workmind://email/{target_id}"
    )

    chars = (
        extraction
        ._append_source_chunks(
            rows,
            chunks=target_chunks,
            current_chars=current_chars,
            max_chunks=max_chunks,
            max_context_chars=max_context_chars,
            source_ref=target_ref,
            source_type="email",
            source_id=target_id,
            source_date=None,
        )
    )

    return (
        target_chunks,
        chars,
    )


def test_default_packet_chunk_limit_is_120(
) -> None:
    config_source = (
        extraction
        .Path
        if False
        else None
    )

    from pathlib import Path

    config_text = Path(
        "packages/common/config.py"
    ).read_text(
        encoding="utf-8"
    )

    env_text = Path(
        ".env.example"
    ).read_text(
        encoding="utf-8"
    )

    assert (
        "knowledge_extraction_max_chunks: int = Field("
        in config_text
    )

    field_start = (
        config_text.index(
            "knowledge_extraction_max_chunks: int = Field("
        )
    )

    field_window = (
        config_text[
            field_start:
            field_start
            + 250
        ]
    )

    assert (
        "default=120"
        in field_window
    )

    assert (
        "KNOWLEDGE_EXTRACTION_MAX_CHUNKS=120"
        in env_text
    )


def test_same_source_raw_exact_duplicate_is_omitted(
) -> None:
    first = _row(
        0,
        "exact duplicate evidence",
    )

    duplicate = _row(
        1,
        "exact duplicate evidence",
    )

    unique = _row(
        2,
        "different evidence",
    )

    chunks, chars = _append(
        [
            first,
            duplicate,
            unique,
        ]
    )

    assert [
        item[
            "chunk_id"
        ]
        for item in chunks
    ] == [
        str(
            first.chunk_id
        ),
        str(
            unique.chunk_id
        ),
    ]

    assert chars == (
        len(
            first.text
        )
        + len(
            unique.text
        )
    )

    allowed = (
        extraction
        ._allowed_evidence_from_chunks(
            chunks
        )
    )

    assert [
        item[
            "chunk_id"
        ]
        for item in allowed
    ] == [
        str(
            first.chunk_id
        ),
        str(
            unique.chunk_id
        ),
    ]


def test_same_source_near_duplicate_is_preserved(
) -> None:
    first = _row(
        0,
        "status approved",
    )

    whitespace_variant = _row(
        1,
        "status  approved",
    )

    case_variant = _row(
        2,
        "Status approved",
    )

    chunks, _ = _append(
        [
            first,
            whitespace_variant,
            case_variant,
        ]
    )

    assert len(
        chunks
    ) == 3


def test_cross_source_exact_duplicate_is_preserved(
) -> None:
    shared = (
        "same text but different source provenance"
    )

    chunks: list[dict] = []

    source_a = uuid.uuid4()
    source_b = uuid.uuid4()

    _, chars = _append(
        [
            _row(
                0,
                shared,
            ),
        ],
        chunks=chunks,
        source_id=source_a,
        source_ref=(
            f"workmind://email/{source_a}"
        ),
    )

    _append(
        [
            _row(
                0,
                shared,
            ),
        ],
        chunks=chunks,
        current_chars=chars,
        source_id=source_b,
        source_ref=(
            f"workmind://email/{source_b}"
        ),
    )

    assert len(
        chunks
    ) == 2

    assert (
        chunks[
            0
        ][
            "source_ref"
        ]
        != chunks[
            1
        ][
            "source_ref"
        ]
    )


def test_partial_chunk_keeps_original_provenance(
) -> None:
    row = _row(
        7,
        "abcdefghij",
    )

    chunks, chars = _append(
        [
            row,
        ],
        max_context_chars=5,
    )

    assert chars == 5

    assert len(
        chunks
    ) == 1

    assert (
        chunks[
            0
        ][
            "chunk_id"
        ]
        == str(
            row.chunk_id
        )
    )

    assert (
        chunks[
            0
        ][
            "chunk_index"
        ]
        == 7
    )

    assert (
        chunks[
            0
        ][
            "text"
        ]
        == "abcde"
    )


def test_target_first_order_remains_priority_under_chunk_cap(
) -> None:
    target = _row(
        0,
        "target evidence",
    )

    history = _row(
        0,
        "history evidence",
    )

    chunks: list[dict] = []

    target_id = uuid.uuid4()
    history_id = uuid.uuid4()

    _, chars = _append(
        [
            target,
        ],
        chunks=chunks,
        max_chunks=1,
        source_id=target_id,
        source_ref=(
            f"workmind://email/{target_id}"
        ),
    )

    _append(
        [
            history,
        ],
        chunks=chunks,
        current_chars=chars,
        max_chunks=1,
        source_id=history_id,
        source_ref=(
            f"workmind://email/{history_id}"
        ),
    )

    assert len(
        chunks
    ) == 1

    assert (
        chunks[
            0
        ][
            "chunk_id"
        ]
        == str(
            target.chunk_id
        )
    )


def test_indexed_source_chunk_loader_uses_bounded_keyset_batches(
) -> None:
    first = _row(
        0,
        "a",
    )

    second = _row(
        1,
        "b",
    )

    third = _row(
        2,
        "c",
    )

    class Result:
        def __init__(
            self,
            rows,
        ):
            self._rows = rows

        def all(
            self,
        ):
            return list(
                self._rows
            )

    class Session:
        def __init__(
            self,
        ):
            self.calls = []

            self.batches = [
                [
                    first,
                    second,
                ],
                [
                    third,
                ],
            ]

        def execute(
            self,
            statement,
        ):
            self.calls.append(
                statement
            )

            return Result(
                self.batches[
                    len(
                        self.calls
                    )
                    - 1
                ]
            )

    session = Session()

    rows = list(
        extraction
        ._iter_indexed_source_chunks(
            session,
            source_type="email",
            source_id=uuid.uuid4(),
            batch_size=2,
        )
    )

    assert [
        row.chunk_index
        for row in rows
    ] == [
        0,
        1,
        2,
    ]

    assert len(
        session.calls
    ) == 2

    first_sql = str(
        session.calls[
            0
        ]
    ).upper()

    second_sql = str(
        session.calls[
            1
        ]
    ).upper()

    assert (
        "LIMIT"
        in first_sql
    )

    assert (
        "LIMIT"
        in second_sql
    )

    assert (
        "CHUNK_INDEX >"
        not in first_sql
    )

    assert (
        "CHUNK_INDEX >"
        in second_sql
    )

    source = inspect.getsource(
        extraction
        ._iter_indexed_source_chunks
    )

    assert (
        ".limit("
        in source
    )

    assert (
        "DocumentChunk.chunk_index"
        in source
    )


def test_loader_hard_caps_requested_batch_size_at_256(
) -> None:
    class Result:
        def all(
            self,
        ):
            return []

    class Session:
        def __init__(
            self,
        ):
            self.calls = []

        def execute(
            self,
            statement,
        ):
            self.calls.append(
                statement
            )

            return Result()

    session = Session()

    rows = list(
        extraction
        ._iter_indexed_source_chunks(
            session,
            source_type="email",
            source_id=uuid.uuid4(),
            batch_size=1000,
        )
    )

    assert rows == []

    assert len(
        session.calls
    ) == 1

    limit_clause = getattr(
        session.calls[
            0
        ],
        "_limit_clause",
        None,
    )

    assert limit_clause is not None

    assert getattr(
        limit_clause,
        "value",
        None,
    ) == 256


def test_chunk_cap_at_full_batch_boundary_does_not_prefetch_next_batch(
) -> None:
    first = _row(
        0,
        "A",
    )

    duplicate = _row(
        1,
        "A",
    )

    second = _row(
        2,
        "B",
    )

    unexpected_next = _row(
        3,
        "C",
    )

    class Result:
        def __init__(
            self,
            rows,
        ):
            self._rows = rows

        def all(
            self,
        ):
            return list(
                self._rows
            )

    class Session:
        def __init__(
            self,
        ):
            self.calls = []

            self.batches = [
                [
                    first,
                    duplicate,
                    second,
                ],
                [
                    unexpected_next,
                ],
            ]

        def execute(
            self,
            statement,
        ):
            self.calls.append(
                statement
            )

            index = (
                len(
                    self.calls
                )
                - 1
            )

            if index >= len(
                self.batches
            ):
                raise AssertionError(
                    "Unexpected extra SQL batch"
                )

            return Result(
                self.batches[
                    index
                ]
            )

    session = Session()

    chunks: list[dict] = []

    source_id = uuid.uuid4()

    chars = (
        extraction
        ._append_source_chunks(
            extraction
            ._iter_indexed_source_chunks(
                session,
                source_type="email",
                source_id=source_id,
                batch_size=3,
            ),
            chunks=chunks,
            current_chars=0,
            max_chunks=2,
            max_context_chars=60_000,
            source_ref=(
                f"workmind://email/{source_id}"
            ),
            source_type="email",
            source_id=source_id,
            source_date=None,
        )
    )

    assert len(
        chunks
    ) == 2

    assert [
        item[
            "text"
        ]
        for item in chunks
    ] == [
        "A",
        "B",
    ]

    assert chars == 2

    # We reached max_chunks on the final row of the full first
    # batch. The consumer must stop before requesting another
    # generator value, otherwise the generator performs query #2.
    assert len(
        session.calls
    ) == 1


def test_char_cap_at_full_batch_boundary_does_not_prefetch_next_batch(
) -> None:
    first = _row(
        0,
        "aa",
    )

    second = _row(
        1,
        "bb",
    )

    third = _row(
        2,
        "c",
    )

    unexpected_next = _row(
        3,
        "d",
    )

    class Result:
        def __init__(
            self,
            rows,
        ):
            self._rows = rows

        def all(
            self,
        ):
            return list(
                self._rows
            )

    class Session:
        def __init__(
            self,
        ):
            self.calls = []

            self.batches = [
                [
                    first,
                    second,
                    third,
                ],
                [
                    unexpected_next,
                ],
            ]

        def execute(
            self,
            statement,
        ):
            self.calls.append(
                statement
            )

            index = (
                len(
                    self.calls
                )
                - 1
            )

            if index >= len(
                self.batches
            ):
                raise AssertionError(
                    "Unexpected extra SQL batch"
                )

            return Result(
                self.batches[
                    index
                ]
            )

    session = Session()

    chunks: list[dict] = []

    source_id = uuid.uuid4()

    chars = (
        extraction
        ._append_source_chunks(
            extraction
            ._iter_indexed_source_chunks(
                session,
                source_type="email",
                source_id=source_id,
                batch_size=3,
            ),
            chunks=chunks,
            current_chars=0,
            max_chunks=120,
            max_context_chars=5,
            source_ref=(
                f"workmind://email/{source_id}"
            ),
            source_type="email",
            source_id=source_id,
            source_date=None,
        )
    )

    assert chars == 5

    assert [
        item[
            "text"
        ]
        for item in chunks
    ] == [
        "aa",
        "bb",
        "c",
    ]

    # Exact char-cap hit happened on the final row of the first full
    # batch. No extra keyset query is allowed.
    assert len(
        session.calls
    ) == 1
