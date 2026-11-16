"""Check that native command failures stop the Windows bootstrap."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(PWSH is None, reason="PowerShell is required")
@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("pip", "pip upgrade failed with exit code 7"),
        ("compose", "docker compose up failed with exit code 7"),
        ("alembic", "Alembic migration failed with exit code 7"),
        ("none", "WorkMind bootstrap completed."),
    ],
)
def test_bootstrap_stops_on_native_command_failure(
    tmp_path: Path, failure: str, message: str
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(ROOT / "scripts" / "bootstrap.ps1", scripts / "bootstrap.ps1")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    venv_scripts = tmp_path / ".venv" / "Scripts"
    venv_scripts.mkdir(parents=True)
    (venv_scripts / "python.exe").touch()
    (venv_scripts / "Activate.ps1").write_text("", encoding="utf-8")

    # PowerShell functions stand in for native programs and set LASTEXITCODE
    # exactly as failed docker.exe and python.exe processes do.
    driver = tmp_path / "driver.ps1"
    driver.write_text(
        """
function python {
    $command = $args -join ' '
    $global:LASTEXITCODE = 0
    if ($command -match 'sys.version_info.major') { return '3.11' }
    if ($command -match 'sys.version_info\\[:2\\]') { return 'yes' }
    if ($env:FAIL_AT -eq 'pip' -and $command -match '-m pip') {
        $global:LASTEXITCODE = 7
    }
    if ($env:FAIL_AT -eq 'alembic' -and $command -match '-m alembic') {
        $global:LASTEXITCODE = 7
    }
}
function docker {
    $command = $args -join ' '
    $global:LASTEXITCODE = 0
    if ($env:FAIL_AT -eq 'compose' -and $command -match 'compose up') {
        $global:LASTEXITCODE = 7
    }
}
& (Join-Path $PSScriptRoot 'scripts/bootstrap.ps1')
exit $LASTEXITCODE
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(driver)],
        env={**os.environ, "FAIL_AT": failure},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr
    assert message in output
    if failure == "none":
        assert result.returncode == 0
        assert "workmind-outlook --lookback-days 7 --max-per-folder 50" in output
    else:
        assert result.returncode != 0
        assert "WorkMind bootstrap completed." not in output
