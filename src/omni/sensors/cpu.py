"""CPU load, live clock speed and temperature.

psutil.cpu_freq() on Windows only returns the fixed base clock, so the live clock is
base clock x "% Processor Performance" from PDH, the same source Task Manager uses.
"""

import platform

import psutil

from omni.units import kelvin_tenths_to_c
from omni.win import IS_WINDOWS
from omni.win.pdh import PdhQuery


def cpu_name() -> str:
    if IS_WINDOWS:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    return platform.processor() or "Unknown CPU"


class CpuSensor:
    def __init__(self):
        self.name = cpu_name()
        self.cores = psutil.cpu_count(logical=False) or 1
        self.threads = psutil.cpu_count(logical=True) or 1
        freq = psutil.cpu_freq()
        self.base_mhz = freq.max if freq and freq.max else None
        self.pdh = PdhQuery()
        self.pdh.add("perf", r"\Processor Information(_Total)\% Processor Performance")
        self.pdh.add("zones", r"\Thermal Zone Information(*)\High Precision Temperature")
        self.pdh.collect()
        psutil.cpu_percent(percpu=True)

    def read(self) -> dict:
        per_core = psutil.cpu_percent(percpu=True)
        counters = self.pdh.collect()
        perf = counters.get("perf")
        clock = self.base_mhz * perf / 100 if perf and self.base_mhz else None
        zones = {
            name.strip("\\").replace("_TZ.", ""): round(kelvin_tenths_to_c(value), 1)
            for name, value in (counters.get("zones") or {}).items()
            if value > 2732  # ignore zones reporting 0 °C or below (unpopulated sensors)
        }
        return {
            "name": self.name,
            "cores": self.cores,
            "threads": self.threads,
            "usage_pct": round(sum(per_core) / len(per_core), 1) if per_core else None,
            "per_core_pct": per_core,
            "clock_mhz": round(clock) if clock else None,
            "base_mhz": self.base_mhz,
            "temp_c": max(zones.values()) if zones else None,
            "zones": zones,
        }
