from __future__ import annotations

import ast
import inspect
import textwrap
import uuid
from types import SimpleNamespace

from apps.mcp_server import services


def _attachment(
    attachment_id: str,
) -> dict:
    return {
        "attachment_id": (
            attachment_id
        ),
        "filename": "example.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 123,
        "archive_path": "C:/private/archive/path",
        "processing_status": "indexed",
        "content_hash": "synthetic-hash",
        "extracted_text_path": (
            "C:/private/text/path"
        ),
        "extraction_method": "test",
        "extracted_chars": 1000,
        "page_count": 2,
        "sheet_count": None,
        "last_error": "private-error",
        "processed_at": "2026-01-01T00:00:00+00:00",
        "metadata": {
            "private": "value",
        },
    }


def _email(
    source_id: str,
    *,
    body: str,
) -> dict:
    return {
        "source_id": source_id,
        "source_ref": (
            f"workmind://email/{source_id}"
        ),
        "thread_id": str(
            uuid.uuid4()
        ),
        "conversation_id": "conversation",
        "subject": "Subject",
        "sender_name": "Sender",
        "sender_email": "sender@example.test",
        "date": "2026-01-01T00:00:00+00:00",
        "sent_at": "2026-01-01T00:00:00+00:00",
        "received_at": "2026-01-01T00:00:00+00:00",
        "folder_path": "Inbox",
        "has_attachments": True,
        "processing_status": "indexed",
        "body_text": body,
        "body_truncated": False,
        "recipients": [
            {
                "email": "recipient@example.test",
            }
        ],
        "attachments": [
            _attachment(
                str(
                    uuid.uuid4()
                )
            )
        ],
        "locations": [
            {
                "path": "private",
            }
        ],
        "raw_json_path": "C:/private/raw.json",
        "content_hash": "private-hash",
    }


def test_select_context_thread_positions_is_bounded_and_keeps_latest(
) -> None:
    values = (
        services
        ._select_context_thread_positions(
            200,
            50,
            limit=8,
        )
    )

    assert len(values) == 8
    assert 50 in values

    assert {
        197,
        198,
        199,
    }.issubset(
        set(
            values
        )
    )

    assert values == sorted(
        values
    )


def test_compact_chunk_projection_deduplicates_exact_chunks(
) -> None:
    shared = (
        "Repeated quoted history "
        * 20
    )

    first = SimpleNamespace(
        id=uuid.uuid4(),
        chunk_index=0,
        text=shared,
    )

    duplicate = SimpleNamespace(
        id=uuid.uuid4(),
        chunk_index=1,
        text=shared,
    )

    unique = SimpleNamespace(
        id=uuid.uuid4(),
        chunk_index=2,
        text=(
            "New information "
            * 20
        ),
    )

    seen: set[str] = set()

    result = (
        services
        ._compact_context_chunks(
            [
                first,
                duplicate,
                unique,
            ],
            source_ref=(
                "workmind://email/test"
            ),
            char_limit=2000,
            seen_signatures=seen,
        )
    )

    assert (
        result[
            "duplicate_chunks_omitted"
        ]
        == 1
    )

    assert len(
        result[
            "chunk_refs"
        ]
    ) == 2

    assert (
        result[
            "body_text"
        ].count(
            "Repeated quoted history"
        )
        == 20
    )


def test_compact_email_removes_internal_paths_and_caps_body(
) -> None:
    source_id = str(
        uuid.uuid4()
    )

    compact = (
        services
        ._compact_email_payload(
            _email(
                source_id,
                body=(
                    "x"
                    * 20_000
                ),
            ),
            body_limit=50_000,
        )
    )

    assert len(
        compact[
            "body_text"
        ]
    ) == 6000

    assert (
        compact[
            "body_truncated"
        ]
        is True
    )

    assert (
        "raw_json_path"
        not in compact
    )

    assert (
        "content_hash"
        not in compact
    )

    attachment = (
        compact[
            "attachments"
        ][0]
    )

    assert (
        "archive_path"
        not in attachment
    )

    assert (
        "extracted_text_path"
        not in attachment
    )

    assert (
        "content_hash"
        not in attachment
    )

    assert (
        "metadata"
        not in attachment
    )


def test_context_evidence_deduplicates_excerpt_but_keeps_first_provenance(
) -> None:
    first_id = str(
        uuid.uuid4()
    )

    second_id = str(
        uuid.uuid4()
    )

    values = [
        {
            "chunk_id": first_id,
            "source_ref": (
                "workmind://email/a"
            ),
            "excerpt": (
                "Same evidence text"
            ),
        },
        {
            "chunk_id": second_id,
            "source_ref": (
                "workmind://email/a"
            ),
            "excerpt": (
                "  same   evidence "
                "text  "
            ),
        },
    ]

    result = (
        services
        ._dedupe_context_evidence(
            values
        )
    )

    assert len(
        result
    ) == 1

    assert (
        result[0][
            "chunk_id"
        ]
        == first_id
    )


def test_email_context_uses_compact_contract(
    monkeypatch,
) -> None:
    source_id = str(
        uuid.uuid4()
    )

    monkeypatch.setattr(
        services,
        "get_email",
        lambda *args, **kwargs: (
            _email(
                source_id,
                body=(
                    "b"
                    * 20_000
                ),
            )
        ),
    )

    monkeypatch.setattr(
        services,
        "_compact_thread_context",
        lambda *args, **kwargs: {
            "message_count": 200,
            "returned_message_count": 8,
            "messages_omitted": 192,
            "messages": [],
        },
    )

    monkeypatch.setattr(
        services,
        "_evidence_candidates",
        lambda **kwargs: [
            {
                "chunk_id": "chunk-1",
                "source_ref": (
                    f"workmind://email/{source_id}"
                ),
                "excerpt": "Evidence",
            },
            {
                "chunk_id": "chunk-2",
                "source_ref": (
                    f"workmind://email/{source_id}"
                ),
                "excerpt": "Evidence",
            },
        ],
    )

    result = (
        services
        .get_workmind_context(
            source_id,
            source_type="email",
            query="test",
            max_body_chars=100_000,
            max_body_chars_per_thread_message=(
                100_000
            ),
            max_thread_messages=100,
        )
    )

    assert (
        result[
            "context_contract_version"
        ]
        == 2
    )

    assert (
        result[
            "context_mode"
        ]
        == "compact"
    )

    assert len(
        result[
            "email"
        ][
            "body_text"
        ]
    ) == 6000

    assert (
        result[
            "context_limits"
        ][
            "thread_messages"
        ]
        == 8
    )

    assert (
        result[
            "context_limits"
        ][
            "thread_body_chars_per_message"
        ]
        == 2000
    )

    assert len(
        result[
            "evidence_candidates"
        ]
    ) == 1


def test_attachment_context_is_bounded_and_keeps_parent(
    monkeypatch,
) -> None:
    source_id = str(
        uuid.uuid4()
    )

    parent_id = str(
        uuid.uuid4()
    )

    attachment = {
        **_attachment(
            source_id
        ),
        "source_id": source_id,
        "source_ref": (
            f"workmind://attachment/{source_id}"
        ),
        "parent_source_id": (
            parent_id
        ),
        "parent_source_ref": (
            f"workmind://email/{parent_id}"
        ),
        "subject": "Subject",
        "sender_name": "Sender",
        "sender_email": "sender@example.test",
        "date": "2026-01-01T00:00:00+00:00",
        "folder_path": "Inbox",
        "extracted_text": (
            "z"
            * 30_000
        ),
        "text_truncated": False,
    }

    parent = _email(
        parent_id,
        body=(
            "p"
            * 30_000
        ),
    )

    parent[
        "attachments"
    ] = [
        attachment,
        _attachment(
            str(
                uuid.uuid4()
            )
        ),
    ]

    monkeypatch.setattr(
        services,
        "get_attachment",
        lambda *args, **kwargs: (
            attachment
        ),
    )

    monkeypatch.setattr(
        services,
        "get_email",
        lambda *args, **kwargs: (
            parent
        ),
    )

    monkeypatch.setattr(
        services,
        "_compact_thread_context",
        lambda *args, **kwargs: {
            "message_count": 20,
            "returned_message_count": 8,
            "messages_omitted": 12,
            "messages": [],
        },
    )

    monkeypatch.setattr(
        services,
        "_evidence_candidates",
        lambda **kwargs: [],
    )

    result = (
        services
        .get_workmind_context(
            source_id,
            source_type="attachment",
            max_attachment_text_chars=(
                100_000
            ),
        )
    )

    assert len(
        result[
            "attachment"
        ][
            "extracted_text"
        ]
    ) == 12_000

    assert len(
        result[
            "email"
        ][
            "body_text"
        ]
    ) == 6_000

    assert (
        result[
            "attachment"
        ][
            "parent_source_id"
        ]
        == parent_id
    )

    assert len(
        result[
            "sibling_attachments"
        ]
    ) == 1


def test_document_context_is_bounded(
    monkeypatch,
) -> None:
    source_id = str(
        uuid.uuid4()
    )

    document = {
        "document_id": source_id,
        "source_id": source_id,
        "source_ref": (
            f"workmind://document/{source_id}"
        ),
        "filename": "document.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 100,
        "source_date": "2026-01-01T00:00:00+00:00",
        "archive_path": "private",
        "processed_text_path": "private",
        "processing_status": "indexed",
        "content_hash": "private",
        "extraction_method": "test",
        "extracted_chars": 50_000,
        "page_count": 10,
        "sheet_count": None,
        "slide_count": None,
        "last_error": None,
        "metadata": {
            "private": "value",
        },
        "processed_at": "2026-01-01T00:00:00+00:00",
        "extracted_text": (
            "d"
            * 50_000
        ),
        "text_truncated": False,
    }

    monkeypatch.setattr(
        services,
        "get_external_document",
        lambda *args, **kwargs: (
            document
        ),
    )

    monkeypatch.setattr(
        services,
        "_evidence_candidates",
        lambda **kwargs: [],
    )

    result = (
        services
        .get_workmind_context(
            source_id,
            source_type="document",
            max_attachment_text_chars=(
                100_000
            ),
        )
    )

    assert len(
        result[
            "document"
        ][
            "extracted_text"
        ]
    ) == 12_000

    assert (
        "archive_path"
        not in result[
            "document"
        ]
    )

    assert (
        "content_hash"
        not in result[
            "document"
        ]
    )


def test_non_emitted_selected_chunk_does_not_hide_later_copy(
) -> None:
    shared_text = (
        "Selected source content "
        * 30
    )

    selected_chunk = SimpleNamespace(
        id=uuid.uuid4(),
        chunk_index=0,
        text=shared_text,
    )

    later_chunk = SimpleNamespace(
        id=uuid.uuid4(),
        chunk_index=0,
        text=shared_text,
    )

    seen: set[str] = set()

    selected_result = (
        services
        ._compact_context_chunks(
            [
                selected_chunk,
            ],
            source_ref=(
                "workmind://email/selected"
            ),
            char_limit=2000,
            seen_signatures=seen,
            emit_text=False,
        )
    )

    assert (
        selected_result[
            "body_text"
        ]
        == ""
    )

    assert (
        seen
        == set()
    )

    later_result = (
        services
        ._compact_context_chunks(
            [
                later_chunk,
            ],
            source_ref=(
                "workmind://email/later"
            ),
            char_limit=2000,
            seen_signatures=seen,
        )
    )

    assert (
        later_result[
            "body_text"
        ]
        == shared_text
    )

    assert (
        later_result[
            "duplicate_chunks_omitted"
        ]
        == 0
    )


def test_partially_emitted_chunk_does_not_hide_full_later_copy(
) -> None:
    shared_text = (
        "Long repeated context "
        * 200
    )

    first_chunk = SimpleNamespace(
        id=uuid.uuid4(),
        chunk_index=0,
        text=shared_text,
    )

    later_chunk = SimpleNamespace(
        id=uuid.uuid4(),
        chunk_index=0,
        text=shared_text,
    )

    seen: set[str] = set()

    first_result = (
        services
        ._compact_context_chunks(
            [
                first_chunk,
            ],
            source_ref=(
                "workmind://email/first"
            ),
            char_limit=500,
            seen_signatures=seen,
        )
    )

    assert len(
        first_result[
            "body_text"
        ]
    ) == 500

    assert (
        first_result[
            "body_truncated"
        ]
        is True
    )

    assert (
        seen
        == set()
    )

    later_result = (
        services
        ._compact_context_chunks(
            [
                later_chunk,
            ],
            source_ref=(
                "workmind://email/later"
            ),
            char_limit=2000,
            seen_signatures=seen,
        )
    )

    assert (
        later_result[
            "duplicate_chunks_omitted"
        ]
        == 0
    )

    assert (
        later_result[
            "body_text"
        ]
    )


def test_fully_emitted_chunk_deduplicates_later_copy(
) -> None:
    shared_text = (
        "Fully visible context "
        * 20
    )

    first_chunk = SimpleNamespace(
        id=uuid.uuid4(),
        chunk_index=0,
        text=shared_text,
    )

    duplicate_chunk = SimpleNamespace(
        id=uuid.uuid4(),
        chunk_index=0,
        text=shared_text,
    )

    seen: set[str] = set()

    first_result = (
        services
        ._compact_context_chunks(
            [
                first_chunk,
            ],
            source_ref=(
                "workmind://email/first"
            ),
            char_limit=2000,
            seen_signatures=seen,
        )
    )

    assert (
        first_result[
            "body_text"
        ]
        == shared_text
    )

    assert len(
        seen
    ) == 1

    duplicate_result = (
        services
        ._compact_context_chunks(
            [
                duplicate_chunk,
            ],
            source_ref=(
                "workmind://email/duplicate"
            ),
            char_limit=2000,
            seen_signatures=seen,
        )
    )

    assert (
        duplicate_result[
            "body_text"
        ]
        == ""
    )

    assert (
        duplicate_result[
            "duplicate_chunks_omitted"
        ]
        == 1
    )


def test_compact_thread_scans_ids_before_loading_bounded_rows(
) -> None:
    source = textwrap.dedent(
        inspect.getsource(
            services
            ._compact_thread_context
        )
    )

    tree = ast.parse(
        source
    )

    select_arguments: list[str] = []

    for node in ast.walk(
        tree
    ):
        if not isinstance(
            node,
            ast.Call,
        ):
            continue

        if not isinstance(
            node.func,
            ast.Name,
        ):
            continue

        if (
            node.func.id
            != "select"
        ):
            continue

        if not node.args:
            continue

        select_arguments.append(
            ast.unparse(
                node.args[0]
            )
        )

    # Full-thread discovery must scan only the lightweight ID column.
    assert (
        "EmailMessage.id"
        in select_arguments
    )

    # Full EmailMessage rows are still required for the bounded
    # selected subset, but there must be only one such select().
    assert (
        select_arguments.count(
            "EmailMessage"
        )
        == 1
    )

    # Chunk provenance query remains present.
    assert (
        "DocumentChunk"
        in select_arguments
    )


def test_raw_fallback_selected_body_does_not_seed_dedup(
) -> None:
    body = (
        "Selected unindexed body "
        * 40
    )

    seen: set[str] = set()

    result = (
        services
        ._compact_context_raw_body(
            body,
            char_limit=2000,
            seen_signatures=seen,
            emit_text=False,
        )
    )

    assert (
        result[
            "body_text"
        ]
        == ""
    )

    assert (
        result[
            "duplicate_chunks_omitted"
        ]
        == 0
    )

    assert (
        seen
        == set()
    )

    later = (
        services
        ._compact_context_raw_body(
            body,
            char_limit=2000,
            seen_signatures=seen,
            emit_text=True,
        )
    )

    assert (
        later[
            "body_text"
        ]
        == body
    )

    assert (
        later[
            "duplicate_chunks_omitted"
        ]
        == 0
    )


def test_raw_fallback_partial_body_does_not_seed_dedup(
) -> None:
    body = (
        "Very long unindexed body "
        * 300
    )

    seen: set[str] = set()

    first = (
        services
        ._compact_context_raw_body(
            body,
            char_limit=500,
            seen_signatures=seen,
            emit_text=True,
        )
    )

    assert len(
        first[
            "body_text"
        ]
    ) == 500

    assert (
        first[
            "body_truncated"
        ]
        is True
    )

    assert (
        seen
        == set()
    )

    second = (
        services
        ._compact_context_raw_body(
            body,
            char_limit=2000,
            seen_signatures=seen,
            emit_text=True,
        )
    )

    assert (
        second[
            "duplicate_chunks_omitted"
        ]
        == 0
    )

    assert (
        second[
            "body_text"
        ]
    )


def test_raw_fallback_full_body_deduplicates_once(
) -> None:
    body = (
        "Fully emitted unindexed body "
        * 20
    )

    seen: set[str] = set()

    first = (
        services
        ._compact_context_raw_body(
            body,
            char_limit=2000,
            seen_signatures=seen,
            emit_text=True,
        )
    )

    assert (
        first[
            "body_text"
        ]
        == body
    )

    assert len(
        seen
    ) == 1

    second = (
        services
        ._compact_context_raw_body(
            body,
            char_limit=2000,
            seen_signatures=seen,
            emit_text=True,
        )
    )

    assert (
        second[
            "body_text"
        ]
        == ""
    )

    assert (
        second[
            "duplicate_chunks_omitted"
        ]
        == 1
    )

    assert (
        second[
            "duplicate_chars_omitted"
        ]
        == len(
            body
        )
    )
