import sqlite3
import struct

from omni.history import METRICS, History
from omni.sensors.adapters import engine_utilisation, luid_key, memory_by_luid
from omni.sensors.bridges import HWINFO_HEADER, HWINFO_SIGNATURE, parse_hwinfo, split_fans

LUID = "luid_0x00000000_0x00013b0a"


def test_engine_utilisation_like_task_manager():
    samples = {
        f"pid_1_{LUID}_phys_0_eng_0_engtype_3D": 30.0,
        f"pid_2_{LUID}_phys_0_eng_0_engtype_3D": 25.0,   # same engine, other process: summed
        f"pid_2_{LUID}_phys_0_eng_5_engtype_VideoDecode": 40.0,
        "pid_3_luid_0x00000000_0x00013752_phys_0_eng_0_engtype_3D": 5.0,
        "garbage": 99.0,
    }
    result = engine_utilisation(samples)
    nvidia = result[luid_key(0, 0x13B0A)]
    assert nvidia == {"3D": 55.0, "VideoDecode": 40.0}
    assert max(nvidia.values()) == 55.0  # the GPU's load is its busiest engine
    assert result[luid_key(0, 0x13752)] == {"3D": 5.0}


def test_engine_utilisation_caps_at_100():
    samples = {f"pid_{i}_{LUID}_phys_0_eng_0_engtype_3D": 60.0 for i in range(3)}
    assert engine_utilisation(samples)[luid_key(0, 0x13B0A)]["3D"] == 100.0


def test_memory_by_luid():
    assert memory_by_luid({f"{LUID}_phys_0": 1000.0, f"{LUID}_phys_1": 24.0}) == {luid_key(0, 0x13B0A): 1024.0}


def _hwinfo_blob():
    sensor_size, reading_size = 8 + 128 * 3, 12 + 128 * 2 + 16 + 8 * 4
    s_off = HWINFO_HEADER.size
    r_off = s_off + 2 * sensor_size
    header = HWINFO_HEADER.pack(HWINFO_SIGNATURE, 2, 0, 0, s_off, sensor_size, 2, r_off, reading_size, 3)

    def sensor(name):
        return struct.pack("<II", 1, 0) + name.encode().ljust(128, b"\0") + b"\0" * 256

    def reading(kind, index, label, unit, value):
        return (struct.pack("<III", kind, index, 7) + label.encode().ljust(128, b"\0") + b"\0" * 128
                + unit.encode().ljust(16, b"\0") + struct.pack("<dddd", value, 0, 0, 0))

    return (header + sensor("ASUS EC") + sensor("CPU [#0]")
            + reading(3, 0, "CPU Fan", "RPM", 3120.0)
            + reading(1, 1, "CPU Package", "°C".encode("latin-1").decode("latin-1"), 71.5)
            + reading(99, 1, "Unknown kind", "", 1.0))


def test_parse_hwinfo_shared_memory():
    data = parse_hwinfo(_hwinfo_blob())
    assert data["fans"] == [{"name": "CPU Fan", "rpm": 3120.0, "source": "HWiNFO", "hardware": "ASUS EC"}]
    temps = [s for s in data["sensors"] if s["type"] == "Temperature"]
    assert temps[0]["hardware"] == "CPU [#0]" and temps[0]["value"] == 71.5
    assert len(data["sensors"]) == 2  # unknown reading kinds are skipped


def test_parse_hwinfo_rejects_bad_signature():
    assert parse_hwinfo(b"\0" * 200) == {}
    assert parse_hwinfo(b"short") == {}


def test_split_fans_skips_missing_values():
    sensors = [
        {"hardware": "Board", "type": "Fan", "name": "Fan #1", "value": 900.0, "source": "LibreHardwareMonitor"},
        {"hardware": "Board", "type": "Fan", "name": "Fan #2", "value": None, "source": "LibreHardwareMonitor"},
        {"hardware": "Board", "type": "Temperature", "name": "System", "value": 40.0, "source": "LibreHardwareMonitor"},
    ]
    assert [f["name"] for f in split_fans(sensors)["fans"]] == ["Fan #1"]


def test_history_upgrades_old_database(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE samples (ts REAL PRIMARY KEY, cpu_pct REAL)")  # a 2.0-era schema
    db.execute("INSERT INTO samples VALUES (1, 5)")
    db.commit()
    db.close()
    h = History(path)
    columns = {row[1] for row in h._db.execute("PRAGMA table_info(samples)")}
    assert set(METRICS) <= columns
    h.add_sample({"cpu_w": 42.0, "fan_rpm": 2100}, ts=2)
    assert h.query(["fan_rpm"], 0, 10, points=10)["fan_rpm"][-1] == 2100
