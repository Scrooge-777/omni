"""NVIDIA GPU telemetry through NVML (nvml.dll), with no nvidia-smi subprocesses.

On Optimus laptops every NVML query wakes the dGPU, which costs battery. The
collector therefore skips GPU polling on battery unless gpu.poll_on_battery is on.
"""

import ctypes
from ctypes import byref, c_char, c_int, c_uint, c_ulonglong

NVML_TEMPERATURE_GPU = 0
CLOCK_GRAPHICS, CLOCK_SM, CLOCK_MEM = 0, 1, 2

# nvmlClocksEventReasons bitmask -> label. "Idle" is normal, not a problem.
CLOCK_EVENT_REASONS = {
    0x1: "Idle",
    0x2: "Application clocks",
    0x4: "Power limit",
    0x8: "Hardware slowdown",
    0x10: "Sync boost",
    0x20: "Thermal (software)",
    0x40: "Thermal (hardware)",
    0x80: "Power brake (hardware)",
    0x100: "Display clock",
}
LIMITING_REASONS = 0x4 | 0x8 | 0x20 | 0x40 | 0x80


class _Utilization(ctypes.Structure):
    _fields_ = [("gpu", c_uint), ("memory", c_uint)]


class _Memory(ctypes.Structure):
    _fields_ = [("total", c_ulonglong), ("free", c_ulonglong), ("used", c_ulonglong)]


def decode_reasons(mask: int) -> list[str]:
    return [label for bit, label in CLOCK_EVENT_REASONS.items() if mask & bit]


def median_of(history: list, value):
    """Median of the last five readings. NVML returns the odd garbage sample (power ~160 W with the
    clock at 0 MHz on an idle GPU, every ~10 s); a median of five drops one or two in a row."""
    if value is None:
        return None
    history.append(value)
    del history[:-5]
    return sorted(history)[len(history) // 2]


class GpuSensor:
    def __init__(self):
        self._power_history: list = []
        self._clock_history: list = []
        self._last_util = None
        self.lib = None
        self.handle = None
        self.name = None
        self.driver = None
        self.error = None
        try:
            lib = ctypes.WinDLL("nvml.dll")
        except (OSError, AttributeError):
            self.error = "NVIDIA driver library (nvml.dll) not found"
            return
        if lib.nvmlInit_v2() != 0:
            self.error = "NVML failed to initialise"
            return
        handle = ctypes.c_void_p()
        if lib.nvmlDeviceGetHandleByIndex_v2(0, byref(handle)) != 0:
            self.error = "No NVIDIA GPU found"
            return
        self.lib, self.handle = lib, handle
        name = (c_char * 96)()
        if lib.nvmlDeviceGetName(handle, name, 96) == 0:
            self.name = name.value.decode(errors="replace")
        driver = (c_char * 80)()
        if lib.nvmlSystemGetDriverVersion(driver, 80) == 0:
            self.driver = driver.value.decode(errors="replace")
        self.power_limit_w = self._uint("nvmlDeviceGetEnforcedPowerLimit", scale=1000)
        self.max_graphics_mhz = self._uint("nvmlDeviceGetMaxClockInfo", CLOCK_GRAPHICS)

    @property
    def available(self) -> bool:
        return self.lib is not None

    def _uint(self, func: str, *args, scale: float = 1.0):
        value = c_uint()
        try:
            status = getattr(self.lib, func)(self.handle, *args, byref(value))
        except AttributeError:
            return None
        return value.value / scale if status == 0 else None

    def _clock_reasons(self):
        mask = c_ulonglong()
        for func in ("nvmlDeviceGetCurrentClocksEventReasons", "nvmlDeviceGetCurrentClocksThrottleReasons"):
            try:
                if getattr(self.lib, func)(self.handle, byref(mask)) == 0:
                    return mask.value
            except AttributeError:
                continue
        return None

    def read(self) -> dict:
        if not self.available:
            return {"available": False, "error": self.error}
        util = _Utilization()
        util_ok = self.lib.nvmlDeviceGetUtilizationRates(self.handle, byref(util)) == 0
        if util_ok:
            self._last_util = (util.gpu, util.memory)
        elif self._last_util:
            # One failed call while the GPU is still awake is a glitch, not "no data": keep the last value.
            util.gpu, util.memory = self._last_util
            util_ok = self.lib.nvmlDeviceGetPerformanceState(self.handle, byref(c_int())) == 0
        mem = _Memory()
        mem_ok = self.lib.nvmlDeviceGetMemoryInfo(self.handle, byref(mem)) == 0
        pstate = c_int()
        pstate_ok = self.lib.nvmlDeviceGetPerformanceState(self.handle, byref(pstate)) == 0
        reasons = self._clock_reasons()
        # The driver sets the power-cap bit even at idle; it only means "throttled" under real load.
        busy = util_ok and util.gpu >= 20
        limits = decode_reasons(reasons & LIMITING_REASONS) if reasons is not None and busy else []
        power = self._uint("nvmlDeviceGetPowerUsage", scale=1000)
        if power is not None and (power < 0 or power > 1000):
            power = None  # a GPU entering deep sleep can return garbage instead of an error code
        power = median_of(self._power_history, power)
        clock = median_of(self._clock_history, self._uint("nvmlDeviceGetClockInfo", CLOCK_GRAPHICS))
        return {
            "available": True,
            "name": self.name,
            "driver": self.driver,
            "temp_c": self._uint("nvmlDeviceGetTemperature", NVML_TEMPERATURE_GPU),
            "power_w": power,
            "asleep": not util_ok and not pstate_ok,
            "power_limit_w": self.power_limit_w,
            "util_pct": util.gpu if util_ok else None,
            "mem_util_pct": util.memory if util_ok else None,
            "vram_used": mem.used if mem_ok else None,
            "vram_total": mem.total if mem_ok else None,
            "clock_mhz": clock,
            "mem_clock_mhz": self._uint("nvmlDeviceGetClockInfo", CLOCK_MEM),
            "max_clock_mhz": self.max_graphics_mhz,
            "pstate": f"P{pstate.value}" if pstate_ok else None,
            "limits": limits,
            "idle": bool(reasons & 0x1) if reasons is not None else None,
        }

    def close(self):
        if self.lib:
            self.lib.nvmlShutdown()
            self.lib = None
