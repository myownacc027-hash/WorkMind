from __future__ import annotations

import io
import json
import logging
import sys

import structlog

from packages.common.logging import configure_logging


def test_unicode_outlook_event_does_not_fail_on_legacy_windows_stdout(
    monkeypatch,
) -> None:
    """Regression for UnicodeEncodeError('charmap') in redirected CLI logs."""
    raw_stdout = io.BytesIO()
    raw_stderr = io.BytesIO()
    stdout = io.TextIOWrapper(
        raw_stdout, encoding="cp1251", errors="strict", write_through=True
    )
    stderr = io.TextIOWrapper(
        raw_stderr, encoding="cp1251", errors="strict", write_through=True
    )

    original_structlog_config = structlog.get_config()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(sys, "stdout", stdout)
            patch.setattr(sys, "stderr", stderr)
            # Do not attach fake test streams to process-global stdlib handlers.
            patch.setattr(logging, "basicConfig", lambda **kwargs: None)

            configure_logging("INFO")
            structlog.get_logger("test_history_windows_stdout").warning(
                "attachment_sync_failed",
                folder="Сетевой архив\\Входящие",
                subject="Ошибка 💥 / Japanese 日本語",
                attachment_name="📊 report.xlsx",
            )
            stdout.flush()

        event = json.loads(raw_stdout.getvalue().decode("utf-8").strip())
        assert event["event"] == "attachment_sync_failed"
        assert event["subject"] == "Ошибка 💥 / Japanese 日本語"
        assert event["attachment_name"] == "📊 report.xlsx"
        assert event["folder"] == "Сетевой архив\\Входящие"
        assert stdout.encoding.lower().replace("-", "") == "utf8"
        assert stderr.encoding.lower().replace("-", "") == "utf8"
    finally:
        structlog.configure(**original_structlog_config)
