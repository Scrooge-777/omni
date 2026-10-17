"""Battery level, charge/discharge power, capacity, wear and cycle count.

Fast values (percent, plugged in) come from psutil every tick. Detailed values come
from the root/wmi battery classes every 15 s. The design capacity is read from
`powercfg /batteryreport /xml` once a day, because BatteryStaticData is empty on many
laptops (including HP OMEN), instead of being hardcoded.
"""

import json
import os
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import psutil

from omni import win

DETAIL_INTERVAL_S = 15
REPORT_MAX_AGE_S = 24 * 3600

WMI_SCRIPT = r"""
$s = Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus -ErrorAction SilentlyContinue | Select-Object -First 1
$f = Get-CimInstance -Namespace root/wmi -ClassName BatteryFullChargedCapacity -ErrorAction SilentlyContinue | Select-Object -First 1
$c = Get-CimInstance -Namespace root/wmi -ClassName BatteryCycleCount -ErrorAction SilentlyContinue | Select-Object -First 1
$d = Get-CimInstance -Namespace root/wmi -ClassName BatteryStaticData -ErrorAction SilentlyContinue | Select-Object -First 1
[PSCustomObject]@{
  remaining = $s.RemainingCapacity; charge_rate = $s.ChargeRate; discharge_rate = $s.DischargeRate
  voltage = $s.Voltage; charging = $s.Charging; discharging = $s.Discharging
  full = $f.FullChargedCapacity; cycles = $c.CycleCount; design = $d.DesignedCapacity
} | ConvertTo-Json -Compress
"""


def health_grade(health_pct):
    if health_pct is None:
        return None
    if health_pct >= 95:
        return "Excellent"
    if health_pct >= 85:
        return "Good"
    if health_pct >= 70:
        return "Fair"
    return "Worn: consider replacing"


def read_battery_report(cache_file: Path) -> dict:
    """Design/full capacity and cycles from the Windows battery report, cached for a day."""
    try:
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        if time.time() - cached.get("fetched_at", 0) < REPORT_MAX_AGE_S:
            return cached
    except (OSError, json.JSONDecodeError):
        pass
    if not win.IS_WINDOWS:
        return {}
    fd, xml_path = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        result = win.run(["powercfg", "/batteryreport", "/xml", "/output", xml_path], timeout=30)
        if result.returncode != 0:
            return {}
        info = parse_battery_report(Path(xml_path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ET.ParseError, win.subprocess.TimeoutExpired):
        return {}
    finally:
        Path(xml_path).unlink(missing_ok=True)
    if info:
        info["fetched_at"] = time.time()
        cache_file.write_text(json.dumps(info), encoding="utf-8")
    return info


def parse_battery_report(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    # The report uses a default XML namespace; match tags by local name.
    battery = next((el for el in root.iter() if el.tag.split("}")[-1] == "Battery"), None)
    if battery is None:
        return {}
    fields = {child.tag.split("}")[-1]: (child.text or "").strip() for child in battery}

    def number(key):
        try:
            return int(fields[key])
        except (KeyError, ValueError):
            return None

    return {
        "manufacturer": fields.get("Manufacturer") or None,
        "chemistry": fields.get("Chemistry") or None,
        "design_mwh": number("DesignCapacity"),
        "full_mwh": number("FullChargeCapacity"),
        "cycles": number("CycleCount"),
    }


class BatterySensor:
    def __init__(self, settings, state_dir: Path):
        self.settings = settings
        self.report_cache = state_dir / "battery-report.json"
        self.details: dict = {}
        self.report: dict = {}
        self._details_at = 0.0
        self.present = psutil.sensors_battery() is not None

    def refresh_details(self, force: bool = False):
        """Slow (PowerShell) refresh. The collector calls this from its background thread."""
        if not self.present or (not force and time.time() - self._details_at < DETAIL_INTERVAL_S):
            return
        self._details_at = time.time()
        self.details = win.powershell_json(WMI_SCRIPT) or self.details
        if not self.report or time.time() - self.report.get("fetched_at", 0) > REPORT_MAX_AGE_S:
            self.report = read_battery_report(self.report_cache) or self.report

    def read(self) -> dict:
        info = psutil.sensors_battery()
        if info is None:
            return {"present": False}
        d, report = self.details, self.report

        override = self.settings.get("battery", "design_capacity_override_mwh")
        design = override or d.get("design") or report.get("design_mwh")
        full = d.get("full") or report.get("full_mwh")
        health = round(full / design * 100, 1) if design and full else None

        # Windows reports rates in mW; one of them is non-zero depending on direction.
        charge_w = (d.get("charge_rate") or 0) / 1000
        discharge_w = (d.get("discharge_rate") or 0) / 1000
        if info.power_plugged:
            state = "charging" if charge_w > 0 or d.get("charging") else "full" if info.percent >= 99 else "plugged in"
        else:
            state = "discharging"
        remaining_mwh = d.get("remaining")

        time_left = None
        if state == "discharging" and info.secsleft not in (psutil.POWER_TIME_UNKNOWN, psutil.POWER_TIME_UNLIMITED):
            time_left = info.secsleft
        elif state == "charging" and charge_w > 0 and full and remaining_mwh is not None:
            time_left = max(0, (full - remaining_mwh) / 1000 / charge_w * 3600)

        return {
            "present": True,
            "percent": round(info.percent, 1),
            "plugged": bool(info.power_plugged),
            "state": state,
            "power_w": round(charge_w if info.power_plugged else -discharge_w, 2),
            "voltage_v": round(d["voltage"] / 1000, 2) if d.get("voltage") else None,
            "remaining_mwh": remaining_mwh,
            "full_mwh": full,
            "design_mwh": design,
            "design_source": "settings" if override else "wmi" if d.get("design") else "battery report" if design else None,
            "health_pct": health,
            "wear_pct": round(100 - health, 1) if health is not None else None,
            "grade": health_grade(health),
            "cycles": d.get("cycles") or report.get("cycles"),
            "time_left_s": time_left,
            "manufacturer": report.get("manufacturer"),
            "chemistry": report.get("chemistry"),
        }
