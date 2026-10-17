"""Memory, storage and network sensors (psutil, plus SSD health via Storage cmdlets)."""

import time

import psutil

from omni import win

SSD_INTERVAL_S = 300

SSD_SCRIPT = r"""
Get-PhysicalDisk | ForEach-Object {
  $r = $_ | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue
  [PSCustomObject]@{
    name = $_.FriendlyName; media = "$($_.MediaType)"; bus = "$($_.BusType)"; health = "$($_.HealthStatus)"
    size = $_.Size; temp_c = $r.Temperature; temp_max_c = $r.TemperatureMax; wear_pct = $r.Wear
    power_on_hours = $r.PowerOnHours; read_errors = $r.ReadErrorsTotal; write_errors = $r.WriteErrorsTotal
  }
} | ConvertTo-Json -Compress
"""


class RateCounter:
    """Turns ever-increasing byte counters into per-second rates."""

    def __init__(self):
        self._last: dict[str, tuple[float, float]] = {}

    def rate(self, key: str, value: float):
        now = time.monotonic()
        previous = self._last.get(key)
        self._last[key] = (now, value)
        if previous is None or now <= previous[0] or value < previous[1]:
            return 0.0
        return (value - previous[1]) / (now - previous[0])


class MemorySensor:
    def read(self) -> dict:
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return {
            "total": vm.total,
            "used": vm.total - vm.available,
            "available": vm.available,
            "usage_pct": vm.percent,
            "swap_total": swap.total,
            "swap_used": swap.used,
        }


class StorageSensor:
    def __init__(self):
        self.rates = RateCounter()
        self.disks: list = []
        self._disks_at = 0.0

    def refresh_health(self, force: bool = False):
        if not force and time.time() - self._disks_at < SSD_INTERVAL_S:
            return
        self._disks_at = time.time()
        result = win.powershell_json(SSD_SCRIPT, timeout=20)
        if result is not None:
            self.disks = result if isinstance(result, list) else [result]

    def read(self) -> dict:
        io = psutil.disk_io_counters()
        volumes = []
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            volumes.append({
                "mount": part.mountpoint, "fs": part.fstype,
                "total": usage.total, "used": usage.used, "free": usage.free, "usage_pct": usage.percent,
            })
        return {
            "read_bps": self.rates.rate("read", io.read_bytes) if io else None,
            "write_bps": self.rates.rate("write", io.write_bytes) if io else None,
            "volumes": volumes,
            "disks": self.disks,
        }


class NetworkSensor:
    def __init__(self):
        self.rates = RateCounter()

    def read(self) -> dict:
        io = psutil.net_io_counters()
        return {
            "down_bps": self.rates.rate("recv", io.bytes_recv),
            "up_bps": self.rates.rate("sent", io.bytes_sent),
            "total_down": io.bytes_recv,
            "total_up": io.bytes_sent,
        }
