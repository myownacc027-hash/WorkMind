from pathlib import Path


SOURCE = Path(
    "packages/common/locking.py"
).read_text(encoding="utf-8")


def test_single_instance_lock_attempts_os_lock_first() -> None:
    function_source = SOURCE.split(
        "def single_instance_lock",
        1,
    )[1]

    lock_position = function_source.index(
        "acquired = _try_lock(handle)"
    )
    initialize_position = function_source.index(
        "_ensure_lock_byte(handle)"
    )

    assert lock_position < initialize_position


def test_contention_yields_false() -> None:
    function_source = SOURCE.split(
        "def single_instance_lock",
        1,
    )[1]

    assert "if not acquired:" in function_source
    assert "yield False" in function_source
