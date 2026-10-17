"""User settings, stored as JSON in %APPDATA%\\OMNI (or $OMNI_HOME).

Every setting has a default, a type and (for numbers) a range, so a hand-edited or
partial settings file can never put the app into a broken state.
"""

import copy
import json
import os
import threading
from pathlib import Path

# group -> key -> (default, allowed) where allowed is (min, max) for numbers or a tuple of choices.
SCHEMA: dict[str, dict[str, tuple]] = {
    "general": {
        "refresh_interval_s": (1.0, (0.5, 10.0)),
        "theme": ("system", ("system", "dark", "light")),
        "web_port": (5050, (1024, 65535)),
        "open_browser": (True, None),
    },
    "units": {
        "temperature": ("C", ("C", "F")),
        "data": ("si", ("si", "iec")),
        "energy": ("Wh", ("Wh", "mWh")),
    },
    "gpu": {
        "enabled": (True, None),
        "poll_on_battery": (False, None),
    },
    "history": {
        "enabled": (True, None),
        "sample_every_s": (10, (5, 300)),
        "retention_days": (30, (1, 365)),
    },
    "alerts": {
        "enabled": (True, None),
        "battery_low_pct": (20, (0, 90)),
        "charge_limit_pct": (80, (0, 100)),
        "cpu_temp_c": (95, (50, 110)),
        "gpu_temp_c": (87, (50, 110)),
        "ssd_temp_c": (70, (40, 90)),
    },
    "battery": {
        "design_capacity_override_mwh": (0, (0, 500_000)),
    },
    "cleanup": {
        "min_age_hours": (24, (0, 720)),
    },
    "processes": {
        "protected": ("System, Registry, smss.exe, csrss.exe, wininit.exe, winlogon.exe, services.exe, "
                      "lsass.exe, svchost.exe, dwm.exe, explorer.exe, MsMpEng.exe", None),
    },
}

DEFAULTS = {group: {key: spec[0] for key, spec in keys.items()} for group, keys in SCHEMA.items()}


def home_dir() -> Path:
    base = os.environ.get("OMNI_HOME") or os.path.join(os.environ.get("APPDATA", str(Path.home())), "OMNI")
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


class SettingsError(ValueError):
    pass


def _coerce(group: str, key: str, value):
    if group not in SCHEMA or key not in SCHEMA[group]:
        raise SettingsError(f"Unknown setting {group}.{key}")
    default, allowed = SCHEMA[group][key]
    if isinstance(default, bool):
        if isinstance(value, str):
            if value.lower() in ("true", "on", "yes", "1"):
                return True
            if value.lower() in ("false", "off", "no", "0"):
                return False
        if isinstance(value, bool):
            return value
        raise SettingsError(f"{group}.{key} must be true or false")
    if isinstance(default, (int, float)):
        try:
            number = type(default)(float(value))
        except (TypeError, ValueError):
            raise SettingsError(f"{group}.{key} must be a number") from None
        low, high = allowed
        if not low <= number <= high:
            raise SettingsError(f"{group}.{key} must be between {low} and {high}")
        return number
    value = str(value)
    if allowed and value not in allowed:
        raise SettingsError(f"{group}.{key} must be one of: {', '.join(allowed)}")
    return value


class Settings:
    """Thread-safe settings store. Invalid values in the file fall back to defaults."""

    def __init__(self, path: Path | None = None):
        self.path = path or home_dir() / "settings.json"
        self._lock = threading.Lock()
        self._data = copy.deepcopy(DEFAULTS)
        self._load()

    def _load(self):
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for group, values in stored.items() if isinstance(stored, dict) else []:
            if not isinstance(values, dict):
                continue
            for key, value in values.items():
                try:
                    self._data[group][key] = _coerce(group, key, value)
                except (SettingsError, KeyError):
                    pass

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, group: str, key: str):
        with self._lock:
            return self._data[group][key]

    def as_dict(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, changes: dict) -> dict:
        """Apply {group: {key: value}} atomically: all values are validated before any is stored."""
        validated = {}
        for group, values in changes.items():
            if not isinstance(values, dict):
                raise SettingsError(f"{group} must be an object")
            for key, value in values.items():
                validated[(group, key)] = _coerce(group, key, value)
        with self._lock:
            for (group, key), value in validated.items():
                self._data[group][key] = value
            self.save()
            return copy.deepcopy(self._data)

    def reset(self) -> dict:
        with self._lock:
            self._data = copy.deepcopy(DEFAULTS)
            self.save()
            return copy.deepcopy(self._data)

    def protected_processes(self) -> set[str]:
        return {name.strip().lower() for name in self.get("processes", "protected").split(",") if name.strip()}
