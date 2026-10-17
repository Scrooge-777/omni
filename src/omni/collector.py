"""Background collector: polls every sensor, keeps a short live buffer, writes history, raises alerts."""

import collections
import threading
import time
import traceback

from omni import __version__
from omni.alerts import AlertEngine
from omni.config import Settings, home_dir
from omni.history import History, sample_from_snapshot
from omni.sensors.battery import BatterySensor
from omni.sensors.cpu import CpuSensor
from omni.sensors.gpu import GpuSensor
from omni.sensors.processes import ProcessSensor
from omni.sensors.system import MemorySensor, NetworkSensor, StorageSensor

LIVE_BUFFER = 600  # samples kept in memory for live charts (10 min at 1 s)


class Collector:
    def __init__(self, settings: Settings | None = None, history: History | None = None):
        self.settings = settings or Settings()
        state = home_dir()
        self.history = history or History(state / "history.db")
        self.cpu = CpuSensor()
        self.gpu = GpuSensor()
        self.battery = BatterySensor(self.settings, state)
        self.memory = MemorySensor()
        self.storage = StorageSensor()
        self.network = NetworkSensor()
        self.processes = ProcessSensor()
        self.alerts = AlertEngine(self.settings)
        self.live = collections.deque(maxlen=LIVE_BUFFER)
        self.snapshot: dict = {}
        self.process_sort = "cpu"
        self._gpu_cache: dict = {}
        self._last_history = 0.0
        self._last_prune = 0.0
        self._listeners: list = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def subscribe(self, callback):
        self._listeners.append(callback)

    def unsubscribe(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _read_gpu(self, on_battery: bool) -> dict:
        if not self.settings.get("gpu", "enabled"):
            return {"available": False, "error": "GPU polling is turned off in Settings"}
        if on_battery and not self.settings.get("gpu", "poll_on_battery"):
            # Reading NVML wakes a sleeping Optimus GPU, so keep the last reading instead.
            return {**self._gpu_cache, "paused": True} if self._gpu_cache else {
                "available": self.gpu.available, "paused": True, "name": self.gpu.name}
        self._gpu_cache = self.gpu.read()
        return self._gpu_cache

    def sample(self) -> dict:
        battery = self.battery.read()
        on_battery = battery.get("present") and not battery.get("plugged")
        snap = {
            "ts": time.time(),
            "version": __version__,
            "cpu": self.cpu.read(),
            "gpu": self._read_gpu(bool(on_battery)),
            "battery": battery,
            "memory": self.memory.read(),
            "storage": self.storage.read(),
            "network": self.network.read(),
            "processes": self.processes.read(limit=20, sort=self.process_sort),
        }
        snap["alerts"] = self.alerts.evaluate(snap)
        return snap

    def tick(self):
        snap = self.sample()
        compact = {"ts": snap["ts"], **sample_from_snapshot(snap)}
        with self._lock:
            self.snapshot = snap
            self.live.append(compact)
        if self.settings.get("history", "enabled") and snap["ts"] - self._last_history >= self.settings.get("history", "sample_every_s"):
            self._last_history = snap["ts"]
            self.history.add_sample(compact, snap["ts"])
            batt = snap["battery"]
            if batt.get("present"):
                self.history.record_capacity(batt.get("full_mwh"), batt.get("design_mwh"), batt.get("cycles"))
        if snap["ts"] - self._last_prune > 3600:
            self._last_prune = snap["ts"]
            self.history.prune(self.settings.get("history", "retention_days"))
        for callback in list(self._listeners):
            try:
                callback(snap)
            except Exception:  # a broken listener must not stop collection
                traceback.print_exc()
        return snap

    def latest(self) -> dict:
        with self._lock:
            return self.snapshot

    def live_series(self, seconds: int = 300) -> list[dict]:
        cutoff = time.time() - seconds
        with self._lock:
            return [s for s in self.live if s["ts"] >= cutoff]

    def refresh_slow(self, force: bool = False):
        """PowerShell-backed readings (battery details, SSD health) that take ~0.5 s each."""
        self.battery.refresh_details(force)
        self.storage.refresh_health(force)

    def _run_slow(self):
        while not self._stop.is_set():
            try:
                self.refresh_slow()
            except Exception:
                traceback.print_exc()
            self._stop.wait(5)

    def _run(self):
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.tick()
            except Exception:
                traceback.print_exc()
            interval = self.settings.get("general", "refresh_interval_s")
            self._stop.wait(max(0.1, interval - (time.monotonic() - started)))

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        threading.Thread(target=self._run_slow, name="omni-slow-sensors", daemon=True).start()
        self._thread = threading.Thread(target=self._run, name="omni-collector", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
