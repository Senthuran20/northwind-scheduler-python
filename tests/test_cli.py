import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_demo(*args):
    env = os.environ.copy()
    env.pop("LLM_BASE_URL", None)
    env.pop("LLM_MODEL", None)
    return subprocess.run(
        [sys.executable, "main.py", *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_default_cli_demo_returns_the_scripted_recommendation():
    result = _run_demo()

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.strip() == (
        "Priya Raman and Sam Okafor can meet on 2026-11-03 "
        "from 13:00 to 14:00 (60 minutes)."
    )


def test_cli_rejects_a_request_for_a_different_weekday():
    result = _run_demo(
        "Find an hour for Priya and Sam on Friday afternoon"
    )

    assert result.returncode == 0
    assert "does not match the search date 2026-11-03 (Tuesday)" in result.stdout


def test_cli_accepts_next_tuesday_when_the_sample_has_tuesday_data():
    result = _run_demo(
        "Find an hour for Priya and Sam next Tuesday afternoon"
    )

    assert result.returncode == 0
    assert result.stdout.strip() == (
        "Priya Raman and Sam Okafor can meet on 2026-11-03 "
        "from 13:00 to 14:00 (60 minutes)."
    )


def test_cli_rejects_a_45_minute_request_against_one_hour_demo():
    result = _run_demo(
        "Find 45 minutes for Priya and Sam on Tuesday afternoon"
    )

    assert result.returncode == 0
    assert "requested meeting duration does not match the search duration" in result.stdout
