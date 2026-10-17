"""Thin wrappers around Windows APIs (PowerShell, PDH counters, power settings)."""

import ctypes
import json
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000


def run(args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Run a program without a console window and without a shell."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0,
    )


def powershell_json(script: str, timeout: float = 15.0):
    """Run a PowerShell snippet that ends in ConvertTo-Json and return the parsed value, or None."""
    if not IS_WINDOWS:
        return None
    try:
        result = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False
