"""Every GPU (integrated and dedicated, any vendor) via DXGI + Windows GPU performance counters.

This is the same data Task Manager shows. Reading these counters does not wake a
sleeping Optimus GPU, unlike NVML, so it also works on battery.
"""

import ctypes
import re
import uuid
from ctypes import wintypes

from omni.win import IS_WINDOWS
from omni.win.pdh import PdhQuery

VENDORS = {0x10DE: "NVIDIA", 0x1002: "AMD", 0x1022: "AMD", 0x8086: "Intel", 0x5143: "Qualcomm", 0x1414: "Microsoft"}
DXGI_ADAPTER_FLAG_SOFTWARE = 2
_LUID_RE = re.compile(r"luid_0x([0-9a-f]+)_0x([0-9a-f]+)", re.I)
_ENGINE_RE = re.compile(r"luid_0x([0-9a-f]+)_0x([0-9a-f]+)_phys_\d+_eng_(\d+)_engtype_(\w+)", re.I)


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _DESC1(ctypes.Structure):
    _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint),
                ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint), ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                ("AdapterLuid", _LUID), ("Flags", ctypes.c_uint)]


def _vcall(obj, index, restype, *argtypes):
    """Call a COM method by vtable index."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])


def luid_key(high: int, low: int) -> str:
    return f"{high & 0xFFFFFFFF:08x}_{low & 0xFFFFFFFF:08x}"


def list_adapters() -> list[dict]:
    """Hardware adapters from DXGI (IDXGIFactory1::EnumAdapters1 / IDXGIAdapter1::GetDesc1)."""
    if not IS_WINDOWS:
        return []
    iid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID("770aae78-f26f-4dba-a829-253c83d1b387").bytes_le)
    factory = ctypes.c_void_p()
    try:
        if ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)) != 0:
            return []
    except (OSError, AttributeError):
        return []
    adapters, index = [], 0
    try:
        while True:
            adapter = ctypes.c_void_p()
            if _vcall(factory, 12, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
                    factory, index, ctypes.byref(adapter)) != 0:
                break  # DXGI_ERROR_NOT_FOUND: no more adapters
            desc = _DESC1()
            _vcall(adapter, 10, ctypes.c_long, ctypes.POINTER(_DESC1))(adapter, ctypes.byref(desc))
            _vcall(adapter, 2, ctypes.c_ulong)(adapter)  # Release
            index += 1
            if desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE:
                continue
            adapters.append({
                "luid": luid_key(desc.AdapterLuid.HighPart, desc.AdapterLuid.LowPart),
                "name": desc.Description.strip(),
                "vendor": VENDORS.get(desc.VendorId, f"0x{desc.VendorId:04x}"),
                "dedicated_total": desc.DedicatedVideoMemory or None,
                "integrated": desc.DedicatedVideoMemory < 512 * 1024 * 1024,
            })
    finally:
        _vcall(factory, 2, ctypes.c_ulong)(factory)
    return adapters


def engine_utilisation(samples: dict) -> dict:
    """{luid: {engine type: %}} the way Task Manager computes it: sum each engine over all
    processes, and the GPU's load is its busiest engine."""
    per_engine: dict[tuple, float] = {}
    for instance, value in samples.items():
        match = _ENGINE_RE.search(instance)
        if not match or value is None:
            continue
        key = (luid_key(int(match.group(1), 16), int(match.group(2), 16)), match.group(3), match.group(4))
        per_engine[key] = per_engine.get(key, 0.0) + value
    result: dict[str, dict] = {}
    for (luid, _engine, kind), value in per_engine.items():
        kinds = result.setdefault(luid, {})
        kinds[kind] = min(100.0, max(kinds.get(kind, 0.0), value))
    return result


def memory_by_luid(samples: dict) -> dict:
    result = {}
    for instance, value in (samples or {}).items():
        match = _LUID_RE.search(instance)
        if match:
            key = luid_key(int(match.group(1), 16), int(match.group(2), 16))
            result[key] = result.get(key, 0) + (value or 0)
    return result


class AdapterSensor:
    def __init__(self):
        self.adapters = list_adapters()
        self.pdh = PdhQuery()
        self.pdh.add("engines", r"\GPU Engine(*)\Utilization Percentage")
        self.pdh.add("dedicated", r"\GPU Adapter Memory(*)\Dedicated Usage")
        self.pdh.add("shared", r"\GPU Adapter Memory(*)\Shared Usage")
        self.pdh.collect()

    def read(self) -> list[dict]:
        counters = self.pdh.collect()
        engines = engine_utilisation(counters.get("engines") or {})
        dedicated = memory_by_luid(counters.get("dedicated"))
        shared = memory_by_luid(counters.get("shared"))
        result = []
        for adapter in self.adapters:
            kinds = engines.get(adapter["luid"], {})
            result.append({
                **adapter,
                "load_pct": round(max(kinds.values()), 1) if kinds else 0.0,
                "engines": {k: round(v, 1) for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])[:4]},
                "dedicated_used": dedicated.get(adapter["luid"]),
                "shared_used": shared.get(adapter["luid"]),
            })
        return result
