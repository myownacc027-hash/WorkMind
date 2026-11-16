"""Exercise the complete migration chain against an isolated PostgreSQL 17."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy import inspect

from packages.database import models  # noqa: F401
from packages.database.base import Base

ROOT = Path(__file__).resolve().parents[1]
REVISIONS = (
    "0001_initial",
    "0002_history_import",
    "0003_attachment_text",
    "0004_hybrid_search_fts",
    "0005_agent_action_log",
    "0006_external_documents",
    "0007_structured_knowledge",
    "0008_temporal_claim_identity",
    "0009_knowledge_extraction_queue",
    "0010_source_metadata_evidence",
    "0011_content_addressed_storage",
    "0012_bitrix24_sources",
)


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )


@pytest.fixture
def postgres_url() -> str:
    try:
        _docker("info", "--format", "{{.ServerVersion}}")
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("Docker engine is required for the PostgreSQL migration integration test")

    name = f"workmind-migration-test-{uuid.uuid4().hex[:12]}"
    _docker(
        "run",
        "--rm",
        "-d",
        "--name",
        name,
        "-e",
        "POSTGRES_DB=workmind_migration_test",
        "-e",
        "POSTGRES_USER=migration_test",
        "-e",
        "POSTGRES_PASSWORD=migration_test",
        "-p",
        "127.0.0.1::5432",
        "postgres:17-alpine",
    )
    try:
        port = _docker("port", name, "5432/tcp").stdout.strip().rsplit(":", 1)[1]
        url = (
            "postgresql+psycopg://migration_test:migration_test"
            f"@127.0.0.1:{port}/workmind_migration_test"
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as conn:
                    version = conn.execute("SHOW server_version_num").fetchone()[0]
                    assert 170000 <= int(version) < 180000
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    pytest.fail("Temporary PostgreSQL 17 did not become ready")
                time.sleep(0.5)
        yield url
    finally:
        _docker("stop", name)


def _upgrade(url: str, revision: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_fresh_postgres_migrates_through_head_and_restarts(postgres_url: str) -> None:
    engine = sa.create_engine(postgres_url)
    try:
        for revision in REVISIONS:
            _upgrade(postgres_url, revision)
            with engine.connect() as conn:
                assert (
                    conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
                    == revision
                )

            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            message_columns = {column["name"] for column in inspector.get_columns("email_messages")}
            attachment_columns = {column["name"] for column in inspector.get_columns("attachments")}
            if revision == "0001_initial":
                assert tables == {
                    "alembic_version",
                    "mailboxes",
                    "email_threads",
                    "email_messages",
                    "email_recipients",
                    "attachments",
                    "document_chunks",
                    "sync_state",
                    "processing_jobs",
                }
                assert "dedupe_key" not in message_columns
                assert "extracted_text_path" not in attachment_columns
                assert "stored_object_id" not in attachment_columns
            if revision == "0002_history_import":
                assert "dedupe_key" in message_columns
                assert {"email_locations", "history_import_states"} <= tables
            if revision == "0003_attachment_text":
                assert {
                    "extracted_text_path",
                    "metadata_json",
                    "processed_at",
                } <= attachment_columns
            if revision == "0011_content_addressed_storage":
                assert "stored_object_id" in attachment_columns

        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
        for table in Base.metadata.tables.values():
            actual = {column["name"] for column in inspector.get_columns(table.name)}
            assert actual == set(table.columns.keys()), table.name

        indexes = {index["name"] for index in inspector.get_indexes("email_messages")}
        assert {"ix_email_messages_dedupe_key", "uq_email_messages_mailbox_dedupe"} <= indexes
        attachment_fks = {fk["referred_table"] for fk in inspector.get_foreign_keys("attachments")}
        assert {"email_messages", "stored_objects"} <= attachment_fks

        _upgrade(postgres_url, "head")
        with engine.connect() as conn:
            assert (
                conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0012_bitrix24_sources"
            )
            assert conn.execute(sa.text("SELECT count(*) FROM alembic_version")).scalar_one() == 1
    finally:
        engine.dispose()
