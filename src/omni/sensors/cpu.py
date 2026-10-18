"""CPU load, live clocks (overall and per thread), core types, temperature and power.

psutil.cpu_freq() on Windows only returns the fixed base clock, so live clocks are
base clock x "% Processor Performance" from PDH, the same source Task Manager uses.

Hybrid CPUs: Windows reports one base clock (the P-core's) for every core, but E-core
performance % is relative to the E-core's own, lower base, which Windows doesn't expose
(and CPUID leaf 0x16 is blank under Windows' virtualisation-based security). So E-cores
get a relative speed ("perf_pct") but no MHz, rather than a wrong MHz figure.
Package/core/iGPU/DRAM watts come from Windows' Energy Meter counters (Intel and AMD
RAPL, exposed by Windows 10+ without any driver).
"""

import ctypes
import platform
from ctypes import wintypes

import psutil

from omni.units import kelvin_tenths_to_c
from omni.win import IS_WINDOWS
from omni.win.pdh import PdhQuery

# Windows Energy Meter instance -> our key. Values are in milliwatts.
RAPL_DOMAINS = {
    "rapl_package0_pkg": "package_w",
    "rapl_package0_pp0": "cores_w",
    "rapl_package0_pp1": "igpu_w",
    "rapl_package0_dram": "dram_w",
}


def cpu_name() -> str:
    if IS_WINDOWS:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    return platform.processor() or "Unknown CPU"


def core_topology() -> list[dict]:
    """Physical cores with their logical CPUs and efficiency class (hybrid Intel/ARM CPUs).

    A higher efficiency class means a faster (performance) core. On non-hybrid CPUs every
    core has class 0.
    """
    if not IS_WINDOWS:
        return []
    k32 = ctypes.windll.kernel32
    size = wintypes.DWORD(0)
    k32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(size))  # 0 = RelationProcessorCore
    if not size.value:
        return []
    buf = ctypes.create_string_buffer(size.value)
    if not k32.GetLogicalProcessorInformationEx(0, buf, ctypes.byref(size)):
        return []
    raw, offset, cores = buf.raw, 0, []
    while offset < size.value:
        record_size = int.from_bytes(raw[offset + 4:offset + 8], "little")
        flags, efficiency = raw[offset + 8], raw[offset + 9]
        group_count = int.from_bytes(raw[offset + 30:offset + 32], "little")
        threads = []
        for g in range(group_count):
            base = offset + 32 + g * 16  # GROUP_AFFINITY: KAFFINITY mask (8) + WORD group + 3 reserved WORDs
            mask = int.from_bytes(raw[base:base + 8], "little")
            group = int.from_bytes(raw[base + 8:base + 10], "little")
            threads += [group * 64 + bit for bit in range(64) if mask >> bit & 1]
        cores.append({"efficiency": efficiency, "smt": bool(flags & 1), "threads": threads})
        offset += record_size
    classes = sorted({c["efficiency"] for c in cores})
    hybrid = len(classes) > 1
    for index, core in enumerate(cores):
        core["index"] = index
        core["type"] = ("P" if core["efficiency"] == classes[-1] else "E") if hybrid else None
    return cores


class CpuSensor:
    def __init__(self):
        self.name = cpu_name()
        self.cores = psutil.cpu_count(logical=False) or 1
        self.threads = psutil.cpu_count(logical=True) or 1
        freq = psutil.cpu_freq()
        self.base_mhz = freq.max if freq and freq.max else None
        self.topology = core_topology()
        self.thread_type = {t: c["type"] for c in self.topology for t in c["threads"]}
        self.thread_core = {t: c["index"] for c in self.topology for t in c["threads"]}
        self.pdh = PdhQuery()
        self.pdh.add("perf", r"\Processor Information(_Total)\% Processor Performance")
        self.pdh.add("perf_each", r"\Processor Information(*)\% Processor Performance")
        self.pdh.add("zones", r"\Thermal Zone Information(*)\High Precision Temperature")
        self.has_rapl = self.pdh.add("energy", r"\Energy Meter(*)\Power")
        self.pdh.collect()
        psutil.cpu_percent(percpu=True)

    def _per_thread_perf(self, perf_each: dict) -> list:
        """% Processor Performance per logical CPU (100 = base clock)."""
        perf = [None] * self.threads
        for instance, value in (perf_each or {}).items():
            group, _, number = instance.partition(",")
            if not number.isdigit() or not group.isdigit():
                continue  # skip "_Total" and "0,_Total"
            index = int(group) * 64 + int(number)
            if index < self.threads:
                perf[index] = value
        return perf

    def read(self) -> dict:
        per_thread = psutil.cpu_percent(percpu=True)
        counters = self.pdh.collect()
        perf = counters.get("perf")
        clock = self.base_mhz * perf / 100 if perf and self.base_mhz else None
        zones = {
            name.strip("\\").replace("_TZ.", ""): round(kelvin_tenths_to_c(value), 1)
            for name, value in (counters.get("zones") or {}).items()
            if value > 2732  # ignore zones reporting 0 °C or below (unpopulated sensors)
        }
        power = {}
        for instance, milliwatts in (counters.get("energy") or {}).items():
            key = RAPL_DOMAINS.get(instance.lower())
            if key and milliwatts is not None and 0 < milliwatts < 1_000_000:
                power[key] = round(milliwatts / 1000, 2)
        perf_each = self._per_thread_perf(counters.get("perf_each"))
        threads = []
        for i in range(self.threads):
            kind = self.thread_type.get(i)
            perf_pct = perf_each[i]
            mhz = round(self.base_mhz * perf_pct / 100) if perf_pct and self.base_mhz and kind != "E" else None
            threads.append({"id": i, "load_pct": per_thread[i] if i < len(per_thread) else None, "clock_mhz": mhz,
                            "perf_pct": round(perf_pct) if perf_pct else None, "type": kind, "core": self.thread_core.get(i)})

        # The overall figure: on hybrid CPUs the _Total counter mixes both core types, so
        # report the fastest busy P-core instead (what "CPU speed" means to a user).
        if self.topology and any(t["type"] == "E" for t in threads):
            busy_p = [t["clock_mhz"] for t in threads if t["type"] == "P" and t["clock_mhz"] and (t["load_pct"] or 0) >= 5]
            all_p = [t["clock_mhz"] for t in threads if t["type"] == "P" and t["clock_mhz"]]
            clock = max(busy_p) if busy_p else (sorted(all_p)[len(all_p) // 2] if all_p else clock)
        return {
            "name": self.name,
            "cores": self.cores,
            "threads": self.threads,
            "p_cores": sum(1 for c in self.topology if c["type"] == "P"),
            "e_cores": sum(1 for c in self.topology if c["type"] == "E"),
            "usage_pct": round(sum(per_thread) / len(per_thread), 1) if per_thread else None,
            "per_core_pct": per_thread,
            "per_thread": threads,
            "clock_mhz": round(clock) if clock else None,
            "hybrid": any(t["type"] == "E" for t in threads),
            "base_mhz": self.base_mhz,
            "temp_c": max(zones.values()) if zones else None,
            "zones": zones,
            "power": power,
        }
