"""Prevent Windows Task Scheduler state regression in WorkMind backup."""

from __future__ import annotations

import subprocess

import pytest

from scripts.backup import _backup_engine as engine


def _response(stdout: str, returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["powershell"], returncode=returncode,
        stdout=stdout, stderr=stderr,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1\n", "1"),
        ("2\n", "2"),
        ("3\n", "3"),
        ("4\n", "4"),
        ("", None),
    ],
)
def test_task_state_requires_numeric_output(monkeypatch, value, expected):
    commands = []

    def fake_run_ps(command: str):
        commands.append(command)
        return _response(value)

    monkeypatch.setattr(engine, "run_ps", fake_run_ps)
    assert engine.task_state() == expected
    assert len(commands) == 1
    assert "[int]$task.State" in commands[0]
    assert "Get-ScheduledTask" in commands[0]


@pytest.mark.parametrize("unexpected", ["Ready", "Running", "Disabled", "???"])
def test_task_state_rejects_unexpected_output(monkeypatch, unexpected):
    monkeypatch.setattr(
        engine, "run_ps", lambda command: _response(unexpected)
    )
    with pytest.raises(RuntimeError, match="Unexpected scheduled task state"):
        engine.task_state()


def test_task_state_fails_closed_on_powershell_error(monkeypatch):
    monkeypatch.setattr(
        engine,
        "run_ps",
        lambda command: _response("", returncode=1, stderr="Access denied"),
    )
    with pytest.raises(RuntimeError, match="Access denied"):
        engine.task_state()


def test_wait_for_task_idle_handles_queued_and_running(monkeypatch):
    values = iter(["2", "4", "3"])
    calls = []
    sleeps = []
    monkeypatch.setattr(engine, "task_state", lambda: calls.append(1) or next(values))
    monkeypatch.setattr(engine.time, "sleep", lambda duration: sleeps.append(duration))

    engine.wait_for_task_idle(timeout_seconds=300)

    assert len(calls) == 3
    assert sleeps == [10, 10]


def test_disable_task_does_not_enable_previously_disabled_task(monkeypatch):
    monkeypatch.setattr(engine, "task_state", lambda: "1")
    monkeypatch.setattr(
        engine,
        "run_ps",
        lambda command: pytest.fail("Disabled task must not be changed"),
    )
    assert engine.disable_task() is False


def test_disable_task_tracks_own_state_change(monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "task_state", lambda: "3")
    monkeypatch.setattr(
        engine,
        "run_ps",
        lambda command: calls.append(command) or _response(""),
    )
    assert engine.disable_task() is True
    assert any("Disable-ScheduledTask" in command for command in calls)


def test_disable_task_without_scheduler(monkeypatch):
    monkeypatch.setattr(engine, "task_state", lambda: None)
    monkeypatch.setattr(
        engine,
        "run_ps",
        lambda command: pytest.fail("Missing task cannot be disabled"),
    )
    assert engine.disable_task() is False
