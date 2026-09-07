from pathlib import Path


SOURCE = Path(
    "apps/sync_runner/main.py"
).read_text(encoding="utf-8")


def test_normal_terminal_helper_is_not_exception_based() -> None:
    assert "def _emit_and_exit" not in SOURCE
    assert "except typer.Exit:" not in SOURCE


def test_lock_contention_returns_without_status_overwrite() -> None:
    start = SOURCE.index("if not acquired:")
    end = SOURCE.index(
        "if (",
        start,
    )
    block = SOURCE[start:end]

    assert "_echo_result(result)" in block
    assert "return" in block
    assert "_write_result(" not in block


def test_success_exit_is_outside_main_try() -> None:
    assert (
        'if result["status"] == "completed_with_errors":'
        in SOURCE
    )


def test_contradictory_mode_flags_are_rejected() -> None:
    assert (
        "--full and --force-reconciliation are mutually"
        in SOURCE
    )
    assert "--disable-reconciliation and " in SOURCE
    assert (
        "--force-reconciliation are mutually exclusive."
        in SOURCE
    )
