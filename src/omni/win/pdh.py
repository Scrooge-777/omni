"""Windows Performance Data Helper (PDH) counters via ctypes.

Reading counters in-process is far cheaper than spawning PowerShell's Get-Counter
every second, so live CPU clock and ACPI temperatures come from here.
"""

import ctypes
from ctypes import wintypes

from omni.win import IS_WINDOWS

PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_NOCAP100 = 0x00008000
PDH_MORE_DATA = 0x800007D2
PDH_CSTATUS_VALID_DATA = 0x0
PDH_CSTATUS_NEW_DATA = 0x1


class _CounterValue(ctypes.Structure):
    class _Value(ctypes.Union):
        _fields_ = [
            ("longValue", ctypes.c_long),
            ("doubleValue", ctypes.c_double),
            ("largeValue", ctypes.c_longlong),
        ]

    _fields_ = [("CStatus", wintypes.DWORD), ("value", _Value)]


class _CounterValueItem(ctypes.Structure):
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _CounterValue)]


class PdhQuery:
    """A PDH query holding named counters. Wildcard paths return {instance: value}."""

    def __init__(self):
        self._pdh = ctypes.WinDLL("pdh.dll") if IS_WINDOWS else None
        self._query = wintypes.HANDLE()
        self._counters: dict[str, tuple[wintypes.HANDLE, bool]] = {}
        if self._pdh and self._pdh.PdhOpenQueryW(None, None, ctypes.byref(self._query)) != 0:
            self._pdh = None

    @property
    def available(self) -> bool:
        return self._pdh is not None

    def add(self, name: str, path: str) -> bool:
        if not self._pdh:
            return False
        handle = wintypes.HANDLE()
        if self._pdh.PdhAddEnglishCounterW(self._query, path, None, ctypes.byref(handle)) != 0:
            return False
        self._counters[name] = (handle, "*" in path)
        return True

    def collect(self) -> dict:
        """Sample all counters. Rate counters need two collections before they are valid."""
        if not self._pdh or self._pdh.PdhCollectQueryData(self._query) != 0:
            return {}
        values = {}
        for name, (handle, is_array) in self._counters.items():
            values[name] = self._read_array(handle) if is_array else self._read_single(handle)
        return values

    def _read_single(self, handle):
        value = _CounterValue()
        status = self._pdh.PdhGetFormattedCounterValue(
            handle, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100, None, ctypes.byref(value)
        )
        if status != 0 or value.CStatus not in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA):
            return None
        return value.value.doubleValue

    def _read_array(self, handle) -> dict:
        size = wintypes.DWORD(0)
        count = wintypes.DWORD(0)
        flags = PDH_FMT_DOUBLE | PDH_FMT_NOCAP100
        status = self._pdh.PdhGetFormattedCounterArrayW(handle, flags, ctypes.byref(size), ctypes.byref(count), None)
        if ctypes.c_uint32(status).value != PDH_MORE_DATA or size.value == 0:
            return {}
        buffer = ctypes.create_string_buffer(size.value)
        status = self._pdh.PdhGetFormattedCounterArrayW(handle, flags, ctypes.byref(size), ctypes.byref(count), buffer)
        if status != 0:
            return {}
        items = ctypes.cast(buffer, ctypes.POINTER(_CounterValueItem))
        result = {}
        for i in range(count.value):
            item = items[i]
            if item.FmtValue.CStatus in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA):
                result[item.szName] = item.FmtValue.value.doubleValue
        return result

    def close(self):
        if self._pdh:
            self._pdh.PdhCloseQuery(self._query)
            self._pdh = None
