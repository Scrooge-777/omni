"""Fans and extra sensors from vendor firmware and from other monitoring tools.

Windows has no standard fan-speed API, so OMNI combines several sources:

- HP OMEN / Victus: the HP WMI BIOS interface (the same calls OmenMon uses) gives fan
  speeds, a BIOS temperature and the "max fan" switch. Needs OMNI to run as administrator.
- LibreHardwareMonitor / OpenHardwareMonitor: when either app is running it publishes
  every sensor it can read (fans, voltages, temperatures, power) over WMI. This covers
  most desktops and many laptops (ASUS, Lenovo, Dell, MSI, Acer...).
- HWiNFO: with "Shared Memory Support" enabled, every HWiNFO sensor is read from its
  shared memory block.
- Vendor WMI providers: Dell Command | Monitor (Dell / Alienware), HP business BIOS
  sensors (EliteBook, ProBook, ZBook), Lenovo Legion / LOQ Gaming Zone, and the generic
  Win32_Fan class some desktops fill in.

Every source is optional; missing ones are skipped silently.
"""

import ctypes
import struct
import threading
from ctypes import wintypes

from omni.win import IS_WINDOWS

SENSOR_UNITS = {
    "Temperature": "°C", "Fan": "RPM", "Power": "W", "Voltage": "V", "Clock": "MHz", "Load": "%",
    "Control": "%", "Current": "A", "Data": "GB", "SmallData": "MB", "Throughput": "B/s", "Level": "%",
    "Frequency": "Hz", "Energy": "mWh", "Noise": "dBA", "Flow": "L/h",
}


class _ComThread:
    """COM must be initialised on each thread that uses it."""
    _local = threading.local()

    @classmethod
    def ensure(cls):
        if not getattr(cls._local, "ready", False):
            import pythoncom

            pythoncom.CoInitialize()
            cls._local.ready = True


def _wmi(namespace: str):
    import win32com.client

    _ComThread.ensure()
    return win32com.client.GetObject(f"winmgmts:{{impersonationLevel=impersonate}}!\\\\.\\{namespace}")


# --------------------------------------------------------------------------- HP OMEN / Victus

class HpBios:
    """HP WMI BIOS (hpqBIntM). Command 0x20008 with these command types (as used by OmenMon):
    0x10 fan count, 0x2D fan levels (x100 RPM), 0x23 BIOS temperature, 0x26/0x27 get/set max fan."""

    SIGN = [0x53, 0x45, 0x43, 0x55]  # "SECU"
    COMMAND = 0x20008
    FAN_NAMES = ["CPU fan", "GPU fan", "Fan 3", "Fan 4"]

    def __init__(self):
        self.available = False
        self.error = None
        self.fan_count = 0
        if not IS_WINDOWS:
            return
        try:
            self.svc = _wmi("root\\wmi")
            instances = list(self.svc.ExecQuery("SELECT * FROM hpqBIntM"))
            if not instances:
                return
            self.bios = instances[0]
            code, data = self._call(0x10)
            if code != 0:
                self.error = f"HP BIOS refused the request (code {code}); run OMNI as administrator"
                return
            self.fan_count = max(1, min(4, data[0])) if data else 2
            self.available = True
        except Exception as exc:  # not an HP, WMI blocked, or not admin
            self.error = None if "hpqBIntM" in str(exc) else str(exc)[:200]

    def _call(self, command_type: int, payload=(0, 0, 0, 0)):
        _ComThread.ensure()
        params = self.bios.Methods_("hpqBIOSInt128").InParameters.SpawnInstance_()
        data_in = self.svc.Get("hpqBDataIn").SpawnInstance_()
        data_in.Sign = self.SIGN
        data_in.Command = self.COMMAND
        data_in.CommandType = command_type
        data_in.Size = len(payload)
        data_in.hpqBData = list(payload)
        params.InData = data_in
        out = self.bios.ExecMethod_("hpqBIOSInt128", params).OutData
        return out.rwReturnCode, list(out.Data) if out.Data else []

    def read(self) -> dict:
        if not self.available:
            return {}
        code, levels = self._call(0x2D)
        fans = []
        if code == 0:
            for i in range(self.fan_count):
                fans.append({"name": self.FAN_NAMES[i], "rpm": levels[i] * 100 if i < len(levels) else None,
                             "source": "HP BIOS", "hardware": "Laptop"})
        code_t, temp = self._call(0x23)
        code_m, max_fan = self._call(0x26)
        return {
            "fans": fans,
            "sensors": [{"hardware": "HP BIOS", "type": "Temperature", "name": "BIOS temperature",
                         "value": temp[0], "unit": "°C", "source": "HP BIOS"}] if code_t == 0 and temp else [],
            "max_fan": bool(max_fan[0]) if code_m == 0 and max_fan else None,
        }

    def set_max_fan(self, on: bool) -> bool:
        code, _ = self._call(0x27, (1 if on else 0, 0, 0, 0))
        if code != 0:
            raise RuntimeError(f"HP BIOS refused the fan change (code {code})")
        return self.read().get("max_fan")


# --------------------------------------------------------------------------- LibreHardwareMonitor

class HardwareMonitorBridge:
    """Reads LibreHardwareMonitor (or OpenHardwareMonitor) sensors over WMI while that app runs."""

    NAMESPACES = [("root\\LibreHardwareMonitor", "LibreHardwareMonitor"), ("root\\OpenHardwareMonitor", "OpenHardwareMonitor")]

    def read(self) -> dict:
        if not IS_WINDOWS:
            return {}
        for namespace, label in self.NAMESPACES:
            try:
                svc = _wmi(namespace)
                hardware = {h.Identifier: h.Name for h in svc.ExecQuery("SELECT Identifier, Name FROM Hardware")}
                rows = list(svc.ExecQuery("SELECT Name, SensorType, Value, Parent FROM Sensor"))
            except Exception:
                continue  # namespace absent: the app isn't running
            if not rows:
                continue
            sensors = [{
                "hardware": hardware.get(r.Parent, r.Parent), "type": r.SensorType, "name": r.Name,
                "value": round(float(r.Value), 2) if r.Value is not None else None,
                "unit": SENSOR_UNITS.get(r.SensorType, ""), "source": label,
            } for r in rows]
            return split_fans(sensors)
        return {}


# --------------------------------------------------------------------------- HWiNFO

HWINFO_SHM = "Global\\HWiNFO_SENS_SM2"
HWINFO_SIGNATURE = 0x53695748  # "HWiS"
HWINFO_TYPES = {1: "Temperature", 2: "Voltage", 3: "Fan", 4: "Current", 5: "Power", 6: "Clock", 7: "Load", 8: "Other"}
HWINFO_HEADER = struct.Struct("<IIIqIIIIII")  # signature, version, revision, poll time, sensor/reading section info


def parse_hwinfo(buf: bytes) -> dict:
    """Parse an HWiNFO shared-memory snapshot (layout from HWiNFO's SDK, #pragma pack(1))."""
    if len(buf) < HWINFO_HEADER.size:
        return {}
    sig, _ver, _rev, _poll, s_off, s_size, s_num, r_off, r_size, r_num = HWINFO_HEADER.unpack_from(buf)
    if sig != HWINFO_SIGNATURE:
        return {}

    def text(raw: bytes) -> str:
        return raw.split(b"\0", 1)[0].decode("latin-1").strip()

    names = []
    for i in range(s_num):
        base = s_off + i * s_size
        # dwSensorID, dwSensorInst, szSensorNameOrig[128], szSensorNameUser[128]
        names.append(text(buf[base + 8 + 128:base + 8 + 256]) or text(buf[base + 8:base + 8 + 128]))
    sensors = []
    for i in range(r_num):
        base = r_off + i * r_size
        kind, sensor_index, _reading_id = struct.unpack_from("<III", buf, base)
        label = text(buf[base + 12 + 128:base + 12 + 256]) or text(buf[base + 12:base + 12 + 128])
        unit = text(buf[base + 12 + 256:base + 12 + 256 + 16])
        (value,) = struct.unpack_from("<d", buf, base + 12 + 256 + 16)
        if kind not in HWINFO_TYPES:
            continue
        sensors.append({"hardware": names[sensor_index] if sensor_index < len(names) else "HWiNFO",
                        "type": HWINFO_TYPES[kind], "name": label, "value": round(value, 2),
                        "unit": unit, "source": "HWiNFO"})
    return split_fans(sensors)


class HwinfoBridge:
    def read(self) -> dict:
        if not IS_WINDOWS:
            return {}
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenFileMappingW.restype = wintypes.HANDLE
        k32.MapViewOfFile.restype = ctypes.c_void_p
        k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
        k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k32.OpenFileMappingW(0x0004, False, HWINFO_SHM)  # FILE_MAP_READ
        if not handle:
            return {}  # HWiNFO not running, or shared memory support is off
        try:
            view = k32.MapViewOfFile(handle, 0x0004, 0, 0, 0)
            if not view:
                return {}
            try:
                header = ctypes.string_at(view, HWINFO_HEADER.size)
                fields = HWINFO_HEADER.unpack(header)
                total = max(fields[4] + fields[5] * fields[6], fields[7] + fields[8] * fields[9])
                return parse_hwinfo(ctypes.string_at(view, total))
            finally:
                k32.UnmapViewOfFile(view)
        finally:
            k32.CloseHandle(handle)


# --------------------------------------------------------------------------- vendor WMI providers

RPM_UNITS = 19  # CIM BaseUnits value for revolutions per minute


def numeric_fans(rows, source: str, hardware: str) -> list[dict]:
    """CIM numeric sensors (Dell DCIM_NumericSensor, HP_BIOSNumericSensor) -> fan readings.
    Each row is a dict with ElementName/Name, CurrentReading, BaseUnits and UnitModifier."""
    fans = []
    for row in rows:
        name = (row.get("ElementName") or row.get("Name") or "Fan").strip()
        if row.get("BaseUnits") != RPM_UNITS and "fan" not in name.lower():
            continue
        reading = row.get("CurrentReading")
        if reading is None:
            continue
        rpm = round(float(reading) * 10 ** (row.get("UnitModifier") or 0))
        fans.append({"name": name, "rpm": rpm, "source": source, "hardware": hardware})
    return fans


def _rows(svc, query: str, fields: list[str]) -> list[dict]:
    return [{f: getattr(item, f, None) for f in fields} for item in svc.ExecQuery(query)]


class VendorFans:
    """Fan speeds from maker WMI interfaces. Each probe is tried once; ones that are absent are
    remembered so later reads cost nothing. `status` records what was found, for the device card."""

    FIELDS = ["ElementName", "Name", "CurrentReading", "BaseUnits", "UnitModifier"]

    def __init__(self):
        self.status: dict[str, str] = {}
        self._dead: set[str] = set()

    def read(self) -> dict:
        if not IS_WINDOWS:
            return {}
        fans = []
        for name, probe in (("Dell Command | Monitor", self._dell), ("HP BIOS sensors", self._hp_business),
                            ("Lenovo Gaming Zone", self._lenovo), ("Windows Win32_Fan", self._win32_fan)):
            if name in self._dead:
                continue
            try:
                found = probe()
            except Exception as exc:
                found, text = None, str(exc)
                if "denied" in text.lower() or "0x80041003" in text:
                    self.status[name] = "needs admin"
                    continue
            if found is None:
                self._dead.add(name)
                self.status[name] = "not present"
            else:
                self.status[name] = f"{len(found)} fan(s)" if found else "present, no fan readings"
                fans += found
        return {"fans": fans, "sensors": [{"hardware": f["hardware"], "type": "Fan", "name": f["name"], "value": f["rpm"],
                                           "unit": "RPM", "source": f["source"]} for f in fans]}

    def _dell(self):
        try:
            svc = _wmi("root\\dcim\\sysman")
        except Exception:
            return None
        return numeric_fans(_rows(svc, "SELECT * FROM DCIM_NumericSensor", self.FIELDS), "Dell Command | Monitor", "Dell")

    def _hp_business(self):
        try:
            svc = _wmi("root\\HP\\InstrumentedBIOS")
            rows = _rows(svc, "SELECT * FROM HP_BIOSNumericSensor", self.FIELDS)
        except Exception:
            return None
        return numeric_fans(rows, "HP BIOS sensors", "HP")

    def _lenovo(self):
        svc = _wmi("root\\WMI")
        items = list(svc.ExecQuery("SELECT * FROM LENOVO_GAMEZONE_DATA"))
        if not items:
            return None
        zone, fans = items[0], []
        for i, method in enumerate(("GetFan1Speed", "GetFan2Speed")):
            try:
                value = zone.ExecMethod_(method).Data
            except Exception:
                continue
            if value:
                fans.append({"name": ["CPU fan", "GPU fan"][i], "rpm": int(value), "source": "Lenovo Gaming Zone",
                             "hardware": "Lenovo (experimental)"})
        return fans

    def _win32_fan(self):
        svc = _wmi("root\\cimv2")
        items = list(svc.ExecQuery("SELECT Name, DesiredSpeed FROM Win32_Fan"))
        if not items:
            return None
        return [{"name": item.Name or "Fan", "rpm": int(item.DesiredSpeed), "source": "Windows Win32_Fan", "hardware": "System"}
                for item in items if item.DesiredSpeed]


def split_fans(sensors: list[dict]) -> dict:
    fans = [{"name": s["name"], "rpm": s["value"], "source": s["source"], "hardware": s["hardware"]}
            for s in sensors if s["type"] == "Fan" and s["value"] is not None]
    return {"fans": fans, "sensors": sensors}
