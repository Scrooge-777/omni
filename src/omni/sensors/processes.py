"""Top processes and a guarded process terminator."""

import os

import psutil


class ProcessSensor:
    """psutil needs the same Process object across calls to compute CPU %, so they are cached by pid."""

    def __init__(self):
        self._procs: dict[int, psutil.Process] = {}
        self.threads = psutil.cpu_count(logical=True) or 1

    def read(self, limit: int = 15, sort: str = "cpu") -> list[dict]:
        seen, rows = set(), []
        for proc in psutil.process_iter(["pid", "name", "memory_info", "username"]):
            pid = proc.info["pid"]
            if pid == 0:  # "System Idle Process" is idle time, not a real process
                continue
            seen.add(pid)
            cached = self._procs.setdefault(pid, proc)
            try:
                cpu = cached.cpu_percent(None) / self.threads  # Task Manager scale: 100 % = whole CPU
                mem = proc.info["memory_info"].rss if proc.info["memory_info"] else 0
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            rows.append({"pid": pid, "name": proc.info["name"] or "?", "cpu_pct": round(cpu, 1), "mem": mem})
        for pid in set(self._procs) - seen:
            del self._procs[pid]
        key = "mem" if sort == "mem" else "cpu_pct"
        rows.sort(key=lambda row: (row[key], row["mem"]), reverse=True)
        return rows[:limit]


class ProcessError(Exception):
    pass


def terminate(pid: int, protected: set[str]) -> dict:
    """End a process unless it is critical to Windows, this app, or on the protected list."""
    if pid in (0, 4) or pid == os.getpid() or pid in {p.pid for p in psutil.Process().parents()}:
        raise ProcessError("That process is part of Windows or OMNI and can't be ended here.")
    try:
        proc = psutil.Process(pid)
        name = proc.name()
    except psutil.NoSuchProcess:
        raise ProcessError("Process no longer exists.") from None
    if name.lower() in protected:
        raise ProcessError(f"{name} is on the protected list (Settings → Processes).")
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except psutil.TimeoutExpired:
        raise ProcessError(f"{name} did not exit within 3 s.") from None
    except psutil.AccessDenied:
        raise ProcessError(f"Access denied: {name} needs OMNI to run as administrator.") from None
    except psutil.NoSuchProcess:
        pass
    return {"pid": pid, "name": name}
