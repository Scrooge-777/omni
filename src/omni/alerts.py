"""Threshold alerts with hysteresis, so a value hovering at the limit doesn't fire repeatedly."""

import time
from dataclasses import dataclass, field

HYSTERESIS = {"temp": 5.0, "pct": 3.0}


@dataclass
class Alert:
    key: str
    level: str  # "warning" or "info"
    message: str
    since: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {"key": self.key, "level": self.level, "message": self.message, "since": self.since}


class AlertEngine:
    def __init__(self, settings):
        self.settings = settings
        self.active: dict[str, Alert] = {}
        self.log: list[dict] = []

    def _check(self, key, triggered, cleared, level, message):
        if key in self.active:
            if cleared:
                del self.active[key]
        elif triggered:
            alert = Alert(key, level, message)
            self.active[key] = alert
            self.log.insert(0, alert.as_dict())
            del self.log[100:]

    def evaluate(self, snap: dict) -> list[dict]:
        if not self.settings.get("alerts", "enabled"):
            self.active.clear()
            return []
        get = lambda key: self.settings.get("alerts", key)  # noqa: E731
        t, p = HYSTERESIS["temp"], HYSTERESIS["pct"]

        for key, temp, limit, label in [
            ("cpu_hot", snap.get("cpu", {}).get("temp_c"), get("cpu_temp_c"), "CPU"),
            ("gpu_hot", snap.get("gpu", {}).get("temp_c"), get("gpu_temp_c"), "GPU"),
        ]:
            if temp is not None:
                self._check(key, temp >= limit, temp < limit - t, "warning", f"{label} is at {temp:.0f} °C (limit {limit} °C)")

        for disk in snap.get("storage", {}).get("disks", []):
            temp, limit = disk.get("temp_c"), get("ssd_temp_c")
            if temp:
                self._check(f"ssd_hot:{disk.get('name')}", temp >= limit, temp < limit - t, "warning",
                            f"{disk.get('name')} is at {temp} °C (limit {limit} °C)")

        batt = snap.get("battery", {})
        if batt.get("present"):
            pct, low, cap = batt["percent"], get("battery_low_pct"), get("charge_limit_pct")
            discharging = not batt["plugged"]
            self._check("battery_low", low and discharging and pct <= low, not discharging or pct > low + p,
                        "warning", f"Battery low: {pct:.0f} %. Plug in soon.")
            # Only while actually charging: firmware that already holds the charge at a limit isn't nagged.
            charging = batt.get("state") == "charging"
            self._check("charge_limit", cap and cap < 100 and charging and pct >= cap,
                        not charging or pct < cap - p, "info",
                        f"Battery at {pct:.0f} %. Unplugging around {cap} % reduces long-term wear.")
        return [a.as_dict() for a in self.active.values()]
