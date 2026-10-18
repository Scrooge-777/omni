"""Background collector: polls every sensor, keeps a short live buffer, writes history, raises alerts.

Three threads:
- the main loop (every refresh interval): CPU, GPUs, battery, memory, disks, network, processes;
- the sensor loop (every sensors.interval_s): fans and bridges (HP BIOS, LibreHardwareMonitor,
  HWiNFO), which use COM and must stay on one thread;
- the slow loop (every 5 s): PowerShell-backed battery details and SSD health.
"""

import collections
import queue
import threading
import time
import traceback

from omni import __version__, device
from omni.alerts import AlertEngine
from omni.config import Settings, home_dir
from omni.history import History, sample_from_snapshot
from omni.sensors.adapters import AdapterSensor
from omni.sensors.battery import BatterySensor
from omni.sensors.bridges import HardwareMonitorBridge, HpBios, HwinfoBridge, VendorFans
from omni.sensors.cpu import CpuSensor
from omni.sensors.gpu import GpuSensor
from omni.sensors.processes import ProcessSensor
from omni.sensors.system import MemorySensor, NetworkSensor, StorageSensor

LIVE_BUFFER = 1800  # samples kept for live charts: the 15-minute chart window even at 0.5 s refresh
ENERGY_KEYS = ["cpu_package_w", "cpu_cores_w", "igpu_w", "dram_w", "dgpu_w", "system_w"]


class Collector:
    def __init__(self, settings: Settings | None = None, history: History | None = None):
        self.settings = settings or Settings()
        state = home_dir()
        self.history = history or History(state / "history.db")
        self.cpu = CpuSensor()
        self.gpu = GpuSensor()
        self.adapters = AdapterSensor()
        self.battery = BatterySensor(self.settings, state)
        self.memory = MemorySensor()
        self.storage = StorageSensor()
        self.network = NetworkSensor()
        self.processes = ProcessSensor()
        self.alerts = AlertEngine(self.settings)
        self.live = collections.deque(maxlen=LIVE_BUFFER)
        self.snapshot: dict = {}
        self.process_sort = "cpu"
        self.extra = {"fans": [], "sensors": [], "fan_control": None, "sources": []}
        self.energy_wh = {key: 0.0 for key in ENERGY_KEYS}
        self.energy_since = time.time()
        self._last_energy_ts = None
        self._gpu_cache: dict = {}
        self._last_history = 0.0
        self._last_prune = 0.0
        self._listeners: list = []
        self._commands: queue.Queue = queue.Queue()
        self._hp: HpBios | None = None
        self._vendor = VendorFans()
        self.device: dict | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def subscribe(self, callback):
        self._listeners.append(callback)

    def unsubscribe(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)

    # ------------------------------------------------------------------ main loop

    def _read_gpu(self, on_battery: bool) -> dict:
        if not self.settings.get("gpu", "enabled"):
            return {"available": False, "error": "GPU polling is turned off in Settings"}
        if on_battery and not self.settings.get("gpu", "poll_on_battery"):
            # Reading NVML wakes a sleeping Optimus GPU, so keep the last reading instead.
            return {**self._gpu_cache, "paused": True} if self._gpu_cache else {
                "available": self.gpu.available, "paused": True, "name": self.gpu.name}
        self._gpu_cache = self.gpu.read()
        return self._gpu_cache

    def _gpu_list(self, nvidia: dict) -> list[dict]:
        """All adapters from Windows counters, with NVML detail merged into the NVIDIA one."""
        gpus = self.adapters.read()
        for gpu in gpus:
            if gpu["vendor"] == "NVIDIA" and nvidia.get("available") and not nvidia.get("paused"):
                gpu.update({k: nvidia.get(k) for k in ("temp_c", "power_w", "power_limit_w", "clock_mhz", "limits", "asleep")})
        return gpus

    def _power(self, snap: dict) -> dict:
        cpu_power = snap["cpu"].get("power", {})
        gpu, batt = snap["gpu"], snap["battery"]
        power = {
            "cpu_package_w": cpu_power.get("package_w"),
            "cpu_cores_w": cpu_power.get("cores_w"),
            "igpu_w": cpu_power.get("igpu_w"),
            "dram_w": cpu_power.get("dram_w") or None,  # 0 means "not measured" on most laptops
            "dgpu_w": None if gpu.get("paused") or gpu.get("asleep") else gpu.get("power_w"),
            "system_w": -batt["power_w"] if batt.get("present") and not batt.get("plugged") and batt.get("power_w") else None,
        }
        if power["system_w"] is not None:
            measured = sum(v for k, v in power.items() if k in ("cpu_package_w", "dgpu_w") and v)
            power["rest_w"] = round(max(0.0, power["system_w"] - measured), 2)  # screen, SSD, Wi-Fi, fans...
        now = snap["ts"]
        if self._last_energy_ts is not None:
            hours = min(now - self._last_energy_ts, 10) / 3600  # cap gaps (sleep/hibernate)
            for key in ENERGY_KEYS:
                if power.get(key):
                    self.energy_wh[key] += power[key] * hours
        self._last_energy_ts = now
        power["energy_wh"] = {k: round(v, 3) for k, v in self.energy_wh.items()}
        power["energy_since"] = self.energy_since
        return power

    def sample(self) -> dict:
        battery = self.battery.read()
        on_battery = battery.get("present") and not battery.get("plugged")
        gpu = self._read_gpu(bool(on_battery))
        snap = {
            "ts": time.time(),
            "version": __version__,
            "cpu": self.cpu.read(),
            "gpu": gpu,
            "gpus": self._gpu_list(gpu),
            "battery": battery,
            "memory": self.memory.read(),
            "storage": self.storage.read(),
            "network": self.network.read(),
            "processes": self.processes.read(limit=20, sort=self.process_sort),
            **self.extra,
        }
        snap["power"] = self._power(snap)
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

    def reset_energy(self):
        self.energy_wh = {key: 0.0 for key in ENERGY_KEYS}
        self.energy_since = time.time()

    # ------------------------------------------------------------------ sensor (fan) loop

    def refresh_sensors(self):
        """Fans and bridges. Must run on the sensor thread (COM objects belong to it)."""
        fans, sensors, sources, control = [], [], [], None
        if self.settings.get("sensors", "hp_bios"):
            if self._hp is None:
                self._hp = HpBios()
            if self._hp.available:
                data = self._hp.read()
                fans += data.get("fans", [])
                sensors += data.get("sensors", [])
                sources.append("HP BIOS")
                control = {"kind": "hp_max_fan", "max_fan": data.get("max_fan")}
            elif self._hp.error:
                sources.append(f"HP BIOS unavailable: {self._hp.error}")
        if self.settings.get("sensors", "vendor_wmi") and not fans:
            data = self._vendor.read()
            fans += data.get("fans", [])
            sensors += data.get("sensors", [])
            sources += sorted({f["source"] for f in data.get("fans", [])})
        for enabled, bridge in (("hwmon_bridge", HardwareMonitorBridge), ("hwinfo_bridge", HwinfoBridge)):
            if not self.settings.get("sensors", enabled):
                continue
            data = bridge().read()
            if data.get("sensors"):
                fans += data["fans"]
                sensors += data["sensors"]
                sources.append(data["sensors"][0]["source"])
        self.extra = {"fans": fans, "sensors": sensors, "fan_control": control, "sources": sources}

    def set_max_fan(self, on: bool, timeout: float = 10.0):
        """Queue a fan command for the sensor thread and wait for its result."""
        done, result = threading.Event(), {}

        def command():
            if not (self._hp and self._hp.available):
                raise RuntimeError("Fan control is only available on HP OMEN / Victus laptops (run as administrator)")
            result["max_fan"] = self._hp.set_max_fan(on)
            self.refresh_sensors()

        self._commands.put((command, done, result))
        if not done.wait(timeout):
            raise RuntimeError("The sensor thread did not respond")
        if "error" in result:
            raise RuntimeError(result["error"])
        return result["max_fan"]

    def provider_status(self) -> dict:
        """Which fan sources were tried on this machine and what each found."""
        hp = "not present"
        if self._hp is not None:
            hp = f"{self._hp.fan_count} fan(s), max-fan switch" if self._hp.available else (
                "needs admin" if self._hp.error else "not present")
        status = {"HP OMEN / Victus BIOS": hp, **self._vendor.status}
        names = {f["source"] for f in self.extra.get("fans", [])}
        for label in ("LibreHardwareMonitor", "OpenHardwareMonitor", "HWiNFO"):
            status[label] = "running" if any(s.startswith(label) for s in self.extra.get("sources", [])) else "not running"
            if label in names:
                status[label] += ", reports fans"
        return status

    def _run_sensors(self):
        if self.device is None:
            try:
                self.device = device.identify()
            except Exception:
                traceback.print_exc()
                self.device = {"vendor": "Unknown", "form_factor": "PC"}
        while not self._stop.is_set():
            try:
                self.refresh_sensors()
            except Exception:
                traceback.print_exc()
            deadline = time.monotonic() + self.settings.get("sensors", "interval_s")
            while time.monotonic() < deadline and not self._stop.is_set():
                try:
                    command, done, result = self._commands.get(timeout=0.2)
                except queue.Empty:
                    continue
                try:
                    command()
                except Exception as exc:
                    result["error"] = str(exc)
                done.set()

    # ------------------------------------------------------------------ slow loop

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
        threading.Thread(target=self._run_sensors, name="omni-fans", daemon=True).start()
        self._thread = threading.Thread(target=self._run, name="omni-collector", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
