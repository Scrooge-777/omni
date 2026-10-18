"""RAM map: how physical memory is used, and RAMMap-style "Empty" commands.

Sysinternals RAMMap shows where every physical page is: in use (by processes, the
kernel, drivers, the file cache), on the standby list (cached data, ranked by priority
0-7), on the modified list (dirty pages waiting to be written), or free / zeroed. Its
Empty menu asks Windows to trim working sets and purge those lists.

This module reads the same lists with NtQuerySystemInformation(SystemMemoryListInformation)
plus the Memory performance counters, and issues the same commands with
NtSetSystemInformation / SetSystemFileCacheSize. The commands need administrator rights.

Freeing memory is rarely a speed-up: standby memory is a cache Windows reuses instantly,
and it refills as you work. Emptying it is mainly useful for testing (cold-start timing)
or when a leak has bloated a working set.
"""

import ctypes
import time
from ctypes import c_size_t, c_ulong, wintypes

import psutil

from omni import win
from omni.win.pdh import PdhQuery

SYSTEM_MEMORY_LIST_INFORMATION = 80
PAGE = 4096

# MEMORY_LIST_COMMAND values for NtSetSystemInformation(SystemMemoryListInformation, ...)
COMMANDS = {
    "working_sets": (2, "Empty working sets", "Trims every process's working set. Pages move to standby/modified lists and fault back in as needed."),
    "modified": (3, "Flush modified list", "Writes dirty pages to disk, turning them into standby pages."),
    "standby": (4, "Empty standby list", "Drops all cached file and program data. Apps may load slower until the cache refills."),
    "standby_low": (5, "Empty low-priority standby", "Drops only priority-0 cache pages (the least likely to be reused)."),
    "system_ws": (None, "Empty system working set", "Trims the file system cache working set."),
}

COUNTERS = {
    "standby_core": r"\Memory\Standby Cache Core Bytes",
    "standby_normal": r"\Memory\Standby Cache Normal Priority Bytes",
    "standby_reserve": r"\Memory\Standby Cache Reserve Bytes",
    "modified": r"\Memory\Modified Page List Bytes",
    "free_zero": r"\Memory\Free & Zero Page List Bytes",
    "pool_paged_resident": r"\Memory\Pool Paged Resident Bytes",
    "pool_nonpaged": r"\Memory\Pool Nonpaged Bytes",
    "pool_paged": r"\Memory\Pool Paged Bytes",
    "cache_ws": r"\Memory\Cache Bytes",
    "driver_resident": r"\Memory\System Driver Resident Bytes",
    "code_resident": r"\Memory\System Code Resident Bytes",
    "committed": r"\Memory\Committed Bytes",
    "commit_limit": r"\Memory\Commit Limit",
    "page_faults": r"\Memory\Page Faults/sec",
    "hard_faults": r"\Memory\Pages Input/sec",
    "private_ws": r"\Process(_Total)\Working Set - Private",
}


class _MemoryLists(ctypes.Structure):
    _fields_ = [("ZeroPageCount", c_size_t), ("FreePageCount", c_size_t), ("ModifiedPageCount", c_size_t),
                ("ModifiedNoWritePageCount", c_size_t), ("BadPageCount", c_size_t),
                ("PageCountByPriority", c_size_t * 8), ("RepurposedPagesByPriority", c_size_t * 8),
                ("ModifiedPageCountPageFile", c_size_t)]


class MemoryError_(Exception):
    pass


def read_lists() -> dict | None:
    """Physical page lists, in bytes. None if Windows refuses (very old Windows or non-Windows)."""
    if not win.IS_WINDOWS:
        return None
    info = _MemoryLists()
    status = ctypes.WinDLL("ntdll").NtQuerySystemInformation(
        SYSTEM_MEMORY_LIST_INFORMATION, ctypes.byref(info), ctypes.sizeof(info), None)
    if status != 0:
        return None
    return lists_from_struct(info)


def lists_from_struct(info: _MemoryLists) -> dict:
    return {
        "zeroed": info.ZeroPageCount * PAGE,
        "free": info.FreePageCount * PAGE,
        "modified": (info.ModifiedPageCount + info.ModifiedNoWritePageCount) * PAGE,
        "bad": info.BadPageCount * PAGE,
        "standby_by_priority": [n * PAGE for n in info.PageCountByPriority],
        "repurposed_by_priority": list(info.RepurposedPagesByPriority),
    }


def build_map(lists: dict | None, counters: dict, total: int) -> dict:
    """Combine page lists and counters into RAMMap-like categories (bytes)."""
    if lists:
        standby = sum(lists["standby_by_priority"])
        available = standby + lists["free"] + lists["zeroed"]
        modified, free, zeroed = lists["modified"], lists["free"], lists["zeroed"]
    else:  # counters only: no priority split, free and zeroed combined
        standby = sum(counters.get(k) or 0 for k in ("standby_core", "standby_normal", "standby_reserve"))
        modified, free, zeroed = counters.get("modified") or 0, counters.get("free_zero") or 0, 0
        available = standby + free
    active = max(0, total - available - modified)

    # Split "active" the way RAMMap's Use Counts groups it, as far as counters allow.
    parts = {
        "process_private": counters.get("private_ws") or 0,
        "nonpaged_pool": counters.get("pool_nonpaged") or 0,
        "paged_pool": counters.get("pool_paged_resident") or 0,
        "file_cache": counters.get("cache_ws") or 0,
        "drivers_code": (counters.get("driver_resident") or 0) + (counters.get("code_resident") or 0),
    }
    known = sum(parts.values())
    if known > active:  # counters are sampled at slightly different moments; never exceed the total
        scale = active / known if known else 0
        parts = {k: int(v * scale) for k, v in parts.items()}
        known = active
    parts["shared_other"] = max(0, active - known)  # mapped files, shareable pages, page tables, kernel stacks...
    return {
        "total": total,
        "active": active,
        "active_parts": parts,
        "standby": standby,
        "standby_by_priority": lists["standby_by_priority"] if lists else None,
        "repurposed_by_priority": lists["repurposed_by_priority"] if lists else None,
        "modified": modified,
        "free": free,
        "zeroed": zeroed,
        "available": available,
        "committed": counters.get("committed"),
        "commit_limit": counters.get("commit_limit"),
        "paged_pool_total": counters.get("pool_paged"),
        "page_faults_per_s": counters.get("page_faults"),
        "hard_faults_per_s": counters.get("hard_faults"),
    }


class MemoryMap:
    def __init__(self):
        self.pdh = PdhQuery()
        for key, path in COUNTERS.items():
            self.pdh.add(key, path)
        self.pdh.collect()

    def read(self) -> dict:
        counters = self.pdh.collect()
        return build_map(read_lists(), counters, psutil.virtual_memory().total)

    @staticmethod
    def top_processes(limit: int = 25) -> list[dict]:
        rows = []
        for proc in psutil.process_iter(["pid", "name", "memory_info"]):
            info = proc.info["memory_info"]
            if not info or proc.info["pid"] == 0:
                continue
            rows.append({"pid": proc.info["pid"], "name": proc.info["name"] or "?",
                         "working_set": info.rss, "private": getattr(info, "private", info.vms)})
        rows.sort(key=lambda r: r["working_set"], reverse=True)
        return rows[:limit]


# --------------------------------------------------------------------------- commands (admin)

class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _TokenPrivileges(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Luid", _LUID), ("Attributes", wintypes.DWORD)]


def _enable_privilege(name: str):
    advapi, k32 = ctypes.WinDLL("advapi32", use_last_error=True), ctypes.WinDLL("kernel32")
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.AdjustTokenPrivileges.argtypes = [wintypes.HANDLE, wintypes.BOOL, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(k32.GetCurrentProcess(), 0x0020 | 0x0008, ctypes.byref(token)):  # ADJUST | QUERY
        raise MemoryError_("Could not open the process token")
    try:
        luid = _LUID()
        if not advapi.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            raise MemoryError_(f"Unknown privilege {name}")
        tp = _TokenPrivileges(1, luid, 0x2)  # SE_PRIVILEGE_ENABLED
        advapi.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
        if ctypes.get_last_error() != 0:  # ERROR_NOT_ALL_ASSIGNED: not running elevated
            raise MemoryError_("OMNI must run as administrator for this command")
    finally:
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle(token)


def run_command(name: str) -> dict:
    """Run one RAMMap 'Empty' command and report how the page lists changed."""
    if name not in COMMANDS:
        raise MemoryError_(f"Unknown command {name!r}")
    if not win.IS_WINDOWS:
        raise MemoryError_("Only available on Windows")
    if not win.is_admin():
        raise MemoryError_("OMNI must run as administrator for this command")
    before = read_lists()
    started = time.perf_counter()
    code = COMMANDS[name][0]
    if code is None:
        _enable_privilege("SeIncreaseQuotaPrivilege")
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.SetSystemFileCacheSize.argtypes = [c_size_t, c_size_t, wintypes.DWORD]
        if not k32.SetSystemFileCacheSize(c_size_t(-1).value, c_size_t(-1).value, 0):
            raise MemoryError_(f"Windows refused (error {ctypes.get_last_error()})")
    else:
        _enable_privilege("SeProfileSingleProcessPrivilege")
        command = c_ulong(code)
        status = ctypes.WinDLL("ntdll").NtSetSystemInformation(
            SYSTEM_MEMORY_LIST_INFORMATION, ctypes.byref(command), ctypes.sizeof(command))
        if status != 0:
            raise MemoryError_(f"Windows refused (NTSTATUS 0x{status & 0xFFFFFFFF:08X})")
    after = read_lists()
    return {"command": name, "label": COMMANDS[name][1], "seconds": round(time.perf_counter() - started, 2),
            "before": summarize(before), "after": summarize(after)}


def summarize(lists: dict | None) -> dict:
    if not lists:
        return {}
    return {"standby": sum(lists["standby_by_priority"]), "modified": lists["modified"],
            "free": lists["free"] + lists["zeroed"]}


def trim_process(pid: int, protected: set[str]) -> dict:
    """EmptyWorkingSet for one process (like RAMMap's per-process trim). Its pages fault back in as used."""
    try:
        proc = psutil.Process(pid)
        name, before = proc.name(), proc.memory_info().rss
    except psutil.NoSuchProcess:
        raise MemoryError_("Process no longer exists") from None
    if pid in (0, 4) or name.lower() in protected:
        raise MemoryError_(f"{name} is protected")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi = ctypes.WinDLL("psapi")
    psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
    handle = k32.OpenProcess(0x0100 | 0x0400, False, pid)  # PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION
    if not handle:
        raise MemoryError_(f"Access denied to {name} (try running OMNI as administrator)")
    try:
        if not psapi.EmptyWorkingSet(handle):
            raise MemoryError_(f"Windows refused to trim {name}")
    finally:
        k32.CloseHandle(handle)
    after = psutil.Process(pid).memory_info().rss
    return {"pid": pid, "name": name, "before": before, "after": after}
