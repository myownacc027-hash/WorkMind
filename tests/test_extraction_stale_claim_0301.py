from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import packages.knowledge.extraction as extraction


class _ScalarRows:
    def __init__(
        self,
        rows,
    ) -> None:
        self._rows = list(
            rows
        )

    def __iter__(self):
        return iter(
            self._rows
        )

    def all(self):
        return list(
            self._rows
        )


class _FakeSession:
    def __init__(
        self,
        rows,
    ) -> None:
        self.rows = list(
            rows
        )

        self.commit_count = 0

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        tb,
    ):
        return False

    def execute(
        self,
        statement,
        *args,
        **kwargs,
    ):
        return None

    def scalars(
        self,
        statement,
        *args,
        **kwargs,
    ):
        return _ScalarRows(
            self.rows
        )

    def commit(self) -> None:
        self.commit_count += 1


def _job(
    *,
    source_hash: str,
) -> SimpleNamespace:

    return SimpleNamespace(
        id=uuid.uuid4(),
        source_type="document",
        source_id=uuid.uuid4(),
        source_content_hash=source_hash,
        status="pending",
        priority=0,
        attempts=0,
        available_at=None,
        lease_token=None,
        leased_at=None,
        lease_expires_at=None,
        completed_at=None,
        last_error=None,
    )


def test_claim_source_contains_bounded_stale_retirement() -> None:

    source = __import__(
        "inspect"
    ).getsource(
        extraction
        .claim_extraction_jobs
    )

    assert (
        "safe_limit + 32"
        in source
    )

    assert (
        "min(safe_limit + 32, 100)"
        in source
    )

    assert (
        ".limit(scan_limit)"
        in source
    )

    assert (
        "_source_hash("
        in source
    )

    assert (
        'job.status = "superseded"'
        in source
    )

    assert (
        'job.status = "leased"'
        in source
    )

    assert (
        "return leased_jobs"
        in source
    )


def test_stale_job_is_retired_before_current_job_is_leased(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    stale = _job(
        source_hash="old-hash"
    )

    current = _job(
        source_hash="current-hash"
    )


    session = _FakeSession(
        [
            stale,
            current,
        ]
    )


    monkeypatch.setattr(
        extraction,
        "SessionLocal",
        lambda: session,
    )


    monkeypatch.setattr(
        extraction,
        "release_expired_leases",
        lambda _session, now=None: 0,
    )


    monkeypatch.setattr(
        extraction,
        "get_settings",
        lambda: SimpleNamespace(
            knowledge_extraction_lease_minutes=15,
            knowledge_extraction_max_attempts=5,
        ),
    )


    current_hashes = {
        stale.source_id: "new-hash",
        current.source_id: "current-hash",
    }


    monkeypatch.setattr(
        extraction,
        "_source_hash",
        lambda _session, _source_type, source_id: (
            current_hashes[
                source_id
            ]
        ),
    )


    leased = (
        extraction
        .claim_extraction_jobs(
            limit=1
        )
    )


    assert len(leased) == 1

    assert (
        leased[0]
        is current
    )


    assert (
        stale.status
        == "superseded"
    )

    assert stale.attempts == 0

    assert (
        stale.lease_token
        is None
    )

    assert (
        stale.leased_at
        is None
    )

    assert (
        stale.lease_expires_at
        is None
    )

    assert (
        stale.completed_at
        is not None
    )

    assert (
        stale.last_error
        is not None
    )


    assert (
        current.status
        == "leased"
    )

    assert current.attempts == 1

    assert (
        current.lease_token
        is not None
    )

    assert (
        current.leased_at
        is not None
    )

    assert (
        current.lease_expires_at
        is not None
    )


def test_claim_never_leases_more_than_requested_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    first = _job(
        source_hash="hash-1"
    )

    second = _job(
        source_hash="hash-2"
    )


    session = _FakeSession(
        [
            first,
            second,
        ]
    )


    monkeypatch.setattr(
        extraction,
        "SessionLocal",
        lambda: session,
    )


    monkeypatch.setattr(
        extraction,
        "release_expired_leases",
        lambda _session, now=None: 0,
    )


    monkeypatch.setattr(
        extraction,
        "get_settings",
        lambda: SimpleNamespace(
            knowledge_extraction_lease_minutes=15,
            knowledge_extraction_max_attempts=5,
        ),
    )


    monkeypatch.setattr(
        extraction,
        "_source_hash",
        lambda _session, _source_type, source_id: (
            "hash-1"
            if source_id
            == first.source_id
            else "hash-2"
        ),
    )


    leased = (
        extraction
        .claim_extraction_jobs(
            limit=1
        )
    )


    assert leased == [
        first
    ]

    assert (
        first.status
        == "leased"
    )

    assert first.attempts == 1


    assert (
        second.status
        == "pending"
    )

    assert second.attempts == 0

    assert (
        second.lease_token
        is None
    )
