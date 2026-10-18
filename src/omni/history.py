"""Telemetry history in SQLite: periodic samples, daily battery capacity, benchmark results."""

import csv
import io
import json
import sqlite3
import threading
import time
from pathlib import Path

METRICS = [
    "cpu_pct", "cpu_temp_c", "cpu_mhz", "ram_pct",
    "gpu_pct", "gpu_temp_c", "gpu_w",
    "batt_pct", "batt_w", "plugged",
    "disk_read_bps", "disk_write_bps", "net_down_bps", "net_up_bps",
    "cpu_w", "cpu_cores_w", "igpu_w", "system_w", "fan_rpm",
]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS samples (ts REAL PRIMARY KEY, {", ".join(f"{m} REAL" for m in METRICS)});
CREATE TABLE IF NOT EXISTS battery_capacity (
    day TEXT PRIMARY KEY, full_mwh INTEGER, design_mwh INTEGER, cycles INTEGER);
CREATE TABLE IF NOT EXISTS bench_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, result TEXT);
"""


def sample_from_snapshot(snap: dict) -> dict:
    cpu, gpu, batt = snap.get("cpu", {}), snap.get("gpu", {}), snap.get("battery", {})
    storage, net, power = snap.get("storage", {}), snap.get("network", {}), snap.get("power", {})
    fan_speeds = [f["rpm"] for f in snap.get("fans", []) if f.get("rpm") is not None]
    return {
        "cpu_w": power.get("cpu_package_w"), "cpu_cores_w": power.get("cpu_cores_w"),
        "igpu_w": power.get("igpu_w"), "system_w": power.get("system_w"),
        "fan_rpm": max(fan_speeds) if fan_speeds else None,
        "cpu_pct": cpu.get("usage_pct"), "cpu_temp_c": cpu.get("temp_c"), "cpu_mhz": cpu.get("clock_mhz"),
        "ram_pct": snap.get("memory", {}).get("usage_pct"),
        "gpu_pct": gpu.get("util_pct"), "gpu_temp_c": gpu.get("temp_c"), "gpu_w": gpu.get("power_w"),
        "batt_pct": batt.get("percent"), "batt_w": batt.get("power_w"),
        "plugged": None if not batt.get("present") else int(bool(batt.get("plugged"))),
        "disk_read_bps": storage.get("read_bps"), "disk_write_bps": storage.get("write_bps"),
        "net_down_bps": net.get("down_bps"), "net_up_bps": net.get("up_bps"),
    }


class History:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._db.execute("PRAGMA journal_mode=WAL")
        # Databases from older versions lack newer metric columns: add them in place.
        existing = {row[1] for row in self._db.execute("PRAGMA table_info(samples)")}
        for metric in METRICS:
            if metric not in existing:
                self._db.execute(f"ALTER TABLE samples ADD COLUMN {metric} REAL")
        self._db.commit()

    def add_sample(self, sample: dict, ts: float | None = None):
        values = [sample.get(m) for m in METRICS]
        with self._lock:
            self._db.execute(
                f"INSERT OR REPLACE INTO samples (ts, {', '.join(METRICS)}) VALUES ({', '.join('?' * (len(METRICS) + 1))})",
                [ts or time.time(), *values],
            )
            self._db.commit()

    def query(self, metrics: list[str], since: float, until: float | None = None, points: int = 300) -> dict:
        """Average samples into at most `points` time buckets so long ranges stay light."""
        metrics = [m for m in metrics if m in METRICS]
        if not metrics:
            return {"ts": [], **{}}
        until = until or time.time()
        bucket = max(1.0, (until - since) / points)
        columns = ", ".join(f"AVG({m})" for m in metrics)
        with self._lock:
            rows = self._db.execute(
                f"SELECT CAST(ts / ? AS INTEGER) * ? AS b, {columns} FROM samples "
                "WHERE ts BETWEEN ? AND ? GROUP BY b ORDER BY b",
                (bucket, bucket, since, until),
            ).fetchall()
        result = {"ts": [row[0] for row in rows]}
        for i, metric in enumerate(metrics, start=1):
            result[metric] = [None if row[i] is None else round(row[i], 2) for row in rows]
        return result

    def export_csv(self, since: float, until: float | None = None) -> str:
        with self._lock:
            rows = self._db.execute(
                f"SELECT ts, {', '.join(METRICS)} FROM samples WHERE ts BETWEEN ? AND ? ORDER BY ts",
                (since, until or time.time()),
            ).fetchall()
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["time_utc", *METRICS])
        for row in rows:
            writer.writerow([time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(row[0])), *row[1:]])
        return out.getvalue()

    def record_capacity(self, full_mwh, design_mwh, cycles):
        if not full_mwh:
            return
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO battery_capacity VALUES (?, ?, ?, ?)",
                (time.strftime("%Y-%m-%d"), full_mwh, design_mwh, cycles),
            )
            self._db.commit()

    def capacity_history(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT day, full_mwh, design_mwh, cycles FROM battery_capacity ORDER BY day").fetchall()
        return [{"day": d, "full_mwh": f, "design_mwh": g, "cycles": c} for d, f, g, c in rows]

    def add_bench_result(self, kind: str, result: dict):
        with self._lock:
            self._db.execute("INSERT INTO bench_results (ts, kind, result) VALUES (?, ?, ?)",
                             (time.time(), kind, json.dumps(result)))
            self._db.commit()

    def bench_results(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, kind, result FROM bench_results ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{"ts": ts, "kind": kind, "result": json.loads(result)} for ts, kind, result in rows]

    def prune(self, retention_days: int):
        with self._lock:
            self._db.execute("DELETE FROM samples WHERE ts < ?", (time.time() - retention_days * 86400,))
            self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()
