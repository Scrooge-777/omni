"""Power plans, Windows 11 power mode, and reversible battery tweaks.

Every tweak first saves the current value of each setting it changes, so "Restore"
puts the laptop back exactly as it was. PCIe ASPM is deliberately never touched:
on some NVMe drives it causes the drive to drop off the bus (WHEA Event 17).
"""

import ctypes
import json
import re
import uuid
from pathlib import Path

from omni import win

SUB_PROCESSOR = "54533251-82be-4824-96c1-47b60b740d00"
TWEAKS = {
    "boost_on_battery": {
        "label": "Turbo Boost on battery",
        "setting": "be337238-0d82-4146-a960-4f3749d470c7",  # PERFBOOSTMODE: 0 = disabled
        "help": "Turning Turbo Boost off on battery usually cuts CPU power a lot for a small speed loss.",
    },
    "max_cpu_on_battery": {
        "label": "Maximum CPU state on battery",
        "setting": "bc5038f7-23e0-4960-96da-33abaf5935ec",  # PROCTHROTTLEMAX, percent
        "help": "Caps the CPU's top speed on battery. 99 % also disables boost on most Intel CPUs.",
    },
}

POWER_MODES = {
    "efficiency": "961cc777-2547-4f9d-8174-7d86181b8a7a",
    "balanced": "00000000-0000-0000-0000-000000000000",
    "performance": "ded574b5-45a0-4f42-8737-46345c09c238",
}

_PLAN_LINE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s+\((.+?)\)(\s*\*)?", re.I)


class PowerError(Exception):
    pass


def parse_plans(output: str) -> list[dict]:
    plans = []
    for match in _PLAN_LINE.finditer(output):
        plans.append({"guid": match.group(1).lower(), "name": match.group(2), "active": bool(match.group(3))})
    return plans


def _powercfg(*args: str) -> str:
    if not win.IS_WINDOWS:
        raise PowerError("Power settings are only available on Windows")
    result = win.run(["powercfg", *args], timeout=15)
    if result.returncode != 0:
        raise PowerError((result.stdout + result.stderr).strip() or f"powercfg {' '.join(args)} failed")
    return result.stdout


def list_plans() -> list[dict]:
    return parse_plans(_powercfg("/list"))


def active_plan() -> dict | None:
    return next((p for p in list_plans() if p["active"]), None)


def set_plan(guid: str) -> dict:
    plans = list_plans()
    plan = next((p for p in plans if p["guid"] == guid.lower()), None)
    if plan is None:
        raise PowerError("That power plan doesn't exist on this PC")
    _powercfg("/setactive", plan["guid"])
    current = active_plan()
    if not current or current["guid"] != plan["guid"]:
        raise PowerError("Windows did not switch the power plan")
    return current


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_str(cls, text: str) -> "_GUID":
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)

    def __str__(self) -> str:
        return str(uuid.UUID(bytes_le=bytes(self)))


def get_power_mode() -> str | None:
    """Windows 11 'Power mode' slider (only shown by Windows when the Balanced plan is active)."""
    if not win.IS_WINDOWS:
        return None
    guid = _GUID()
    try:
        if ctypes.WinDLL("powrprof.dll").PowerGetEffectiveOverlayScheme(ctypes.byref(guid)) != 0:
            return None
    except (OSError, AttributeError):
        return None
    current = str(guid)
    return next((name for name, value in POWER_MODES.items() if value == current), current)


def set_power_mode(mode: str) -> str:
    if mode not in POWER_MODES:
        raise PowerError(f"Unknown power mode {mode!r}")
    try:
        status = ctypes.WinDLL("powrprof.dll").PowerSetActiveOverlayScheme(_GUID.from_str(POWER_MODES[mode]))
    except (OSError, AttributeError) as exc:
        raise PowerError(f"Power mode API unavailable: {exc}") from None
    if status != 0:
        raise PowerError(f"Windows refused the power mode change (error {status})")
    return get_power_mode() or mode


def _powrprof():
    if not win.IS_WINDOWS:
        raise PowerError("Power settings are only available on Windows")
    return ctypes.WinDLL("powrprof.dll")


def _active_scheme_guid() -> _GUID:
    lib = _powrprof()
    pointer = ctypes.POINTER(_GUID)()
    if lib.PowerGetActiveScheme(None, ctypes.byref(pointer)) != 0:
        raise PowerError("Could not read the active power plan")
    guid = _GUID.from_buffer_copy(pointer.contents)
    ctypes.windll.kernel32.LocalFree(pointer)
    return guid


def read_tweak(name: str) -> dict:
    """Read through the power API: powercfg /query can't see hidden settings such as boost mode."""
    lib = _powrprof()
    scheme, sub, setting = _active_scheme_guid(), _GUID.from_str(SUB_PROCESSOR), _GUID.from_str(TWEAKS[name]["setting"])
    values = {}
    for kind, func in (("ac", lib.PowerReadACValueIndex), ("dc", lib.PowerReadDCValueIndex)):
        value = ctypes.c_ulong()
        status = func(None, ctypes.byref(scheme), ctypes.byref(sub), ctypes.byref(setting), ctypes.byref(value))
        values[kind] = value.value if status == 0 else None
    return values


class TweakManager:
    def __init__(self, state_dir: Path):
        self.backup_path = state_dir / "power-backup.json"

    def _backups(self) -> dict:
        try:
            return json.loads(self.backup_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def status(self) -> dict:
        plan = active_plan()
        backups = self._backups().get(plan["guid"], {}) if plan else {}
        tweaks = {}
        for name, tweak in TWEAKS.items():
            try:
                values = read_tweak(name)
            except PowerError as exc:
                values = {"error": str(exc)}
            tweaks[name] = {"label": tweak["label"], "help": tweak["help"], **values, "changed": name in backups}
        return {"plan": plan, "tweaks": tweaks, "can_restore": bool(backups)}

    def apply(self, name: str, dc_value: int) -> dict:
        if name not in TWEAKS:
            raise PowerError(f"Unknown tweak {name!r}")
        if name == "max_cpu_on_battery" and not 5 <= dc_value <= 100:
            raise PowerError("Maximum CPU state must be between 5 and 100 %")
        if name == "boost_on_battery" and dc_value not in range(0, 7):
            raise PowerError("Boost mode must be between 0 and 6")
        plan = active_plan()
        if not plan:
            raise PowerError("No active power plan found")
        backups = self._backups()
        plan_backup = backups.setdefault(plan["guid"], {})
        if name not in plan_backup:  # keep the original value, not the last tweaked one
            plan_backup[name] = read_tweak(name)["dc"]
            self.backup_path.write_text(json.dumps(backups, indent=2), encoding="utf-8")
        self._set_dc(name, dc_value)
        return self.status()

    def restore(self) -> dict:
        plan = active_plan()
        backups = self._backups()
        for name, value in backups.get(plan["guid"], {}).items() if plan else []:
            if value is not None:
                self._set_dc(name, value)
        if plan:
            backups.pop(plan["guid"], None)
            self.backup_path.write_text(json.dumps(backups, indent=2), encoding="utf-8")
        return self.status()

    @staticmethod
    def _set_dc(name: str, value: int):
        lib = _powrprof()
        scheme, sub, setting = _active_scheme_guid(), _GUID.from_str(SUB_PROCESSOR), _GUID.from_str(TWEAKS[name]["setting"])
        status = lib.PowerWriteDCValueIndex(None, ctypes.byref(scheme), ctypes.byref(sub), ctypes.byref(setting), ctypes.c_ulong(int(value)))
        if status != 0:
            raise PowerError(f"Windows refused the change (error {status}). Try running OMNI as administrator.")
        lib.PowerSetActiveScheme(None, ctypes.byref(scheme))  # re-apply so the change takes effect now
        if read_tweak(name)["dc"] != int(value):
            raise PowerError("Windows did not keep the new value")
