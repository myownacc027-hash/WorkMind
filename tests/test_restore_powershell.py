"""Keep the restore wrapper safe in Windows PowerShell 5.1."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESTORE_SCRIPT = ROOT / "scripts" / "backup" / "restore_workmind.ps1"
WINDOWS_POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None


def test_restore_wrapper_is_ascii_safe_for_windows_powershell_51() -> None:
    # Windows PowerShell 5.1 misinterprets UTF-8 without a BOM as the
    # active ANSI codepage. Box-drawing glyphs can corrupt quoted strings,
    # leading to misleading MissingEndCurlyBrace parser errors.
    raw = RESTORE_SCRIPT.read_bytes()
    assert all(byte < 128 for byte in raw), (
        "restore_workmind.ps1 must remain ASCII, or its encoding contract "
        "and Windows PowerShell 5.1 parser test must be updated together."
    )


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Requires Windows PowerShell 5.1",
)
def test_restore_wrapper_parses_in_real_windows_powershell() -> None:
    command = (
        "$tokens = $null; $parseErrors = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "$env:WORKMIND_RESTORE_PS1, [ref]$tokens, [ref]$parseErrors) "
        "| Out-Null; "
        "if ($parseErrors.Count -ne 0) { "
        "$parseErrors | ForEach-Object { Write-Output $_.Message }; "
        "exit 1 }; exit 0"
    )
    result = subprocess.run(
        [
            WINDOWS_POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        env={**os.environ, "WORKMIND_RESTORE_PS1": str(RESTORE_SCRIPT)},
        capture_output=True,
        text=True,
        errors="replace",
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Requires Windows PowerShell 5.1",
)
@pytest.mark.parametrize("database", ["workmind", "WORKMIND"])
def test_restore_wrapper_blocks_production_without_confirmation(
    tmp_path: Path, database: str
) -> None:
    # Execute only the destructive-restore guard in an isolated copy:
    # the copied engine and python.exe files are placeholders and must
    # never be executed by this test.
    scripts = tmp_path / "scripts" / "backup"
    scripts.mkdir(parents=True)
    target = scripts / "restore_workmind.ps1"
    shutil.copyfile(RESTORE_SCRIPT, target)
    (scripts / "_restore_engine.py").touch()

    venv = tmp_path / ".venv" / "Scripts"
    venv.mkdir(parents=True)
    (venv / "python.exe").touch()
    backup = tmp_path / "backup"
    backup.mkdir()

    result = subprocess.run(
        [
            WINDOWS_POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(target),
            "-BackupPath",
            str(backup),
            "-TargetDatabase",
            database,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=20,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "SAFETY BLOCK" in output
    assert "ConfirmDestructiveRestore" in output
    assert "RESTORE SUCCEEDED" not in output
