import os
import time

import pytest

from omni import cleanup, power
from omni.alerts import AlertEngine
from omni.bench import summarize_stress
from omni.config import Settings
from omni.history import History
from omni.sensors.battery import health_grade, parse_battery_report
from omni.sensors.gpu import decode_reasons
from omni.sensors.processes import ProcessError, terminate

POWERCFG_LIST = """
Existing Power Schemes (* Active)
-----------------------------------
Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)
Power Scheme GUID: fe89c13e-763b-466a-983b-39240c5e657c  (Ultimate Performance) *
"""

BATTERY_XML = """<?xml version="1.0"?>
<BatteryReport xmlns="http://schemas.microsoft.com/battery/2012">
  <Batteries><Battery>
    <Id>Primary</Id><Manufacturer>HP</Manufacturer><Chemistry>LION</Chemistry>
    <DesignCapacity>83028</DesignCapacity><FullChargeCapacity>80492</FullChargeCapacity><CycleCount>32</CycleCount>
  </Battery></Batteries>
</BatteryReport>"""


def test_parse_power_plans():
    plans = power.parse_plans(POWERCFG_LIST)
    assert [p["name"] for p in plans] == ["Balanced", "Ultimate Performance"]
    assert [p["active"] for p in plans] == [False, True]


def test_guid_round_trip():
    text = power.POWER_MODES["efficiency"]
    assert str(power._GUID.from_str(text)) == text


def test_parse_battery_report():
    info = parse_battery_report(BATTERY_XML)
    assert info == {"manufacturer": "HP", "chemistry": "LION", "design_mwh": 83028, "full_mwh": 80492, "cycles": 32}


def test_health_grades():
    assert health_grade(96.9) == "Excellent"
    assert health_grade(80) == "Fair"
    assert health_grade(50).startswith("Worn")
    assert health_grade(None) is None


def test_gpu_reason_decoding():
    assert decode_reasons(0x4 | 0x40) == ["Power limit", "Thermal (hardware)"]
    assert decode_reasons(0) == []


def _snap(**battery):
    base = {"present": True, "percent": 50, "plugged": False, "state": "discharging"}
    return {"cpu": {"temp_c": 60}, "gpu": {}, "storage": {"disks": []}, "battery": {**base, **battery}}


def test_alerts_have_hysteresis(omni_home):
    engine = AlertEngine(Settings())
    snap = _snap()
    snap["cpu"]["temp_c"] = 96
    assert any(a["key"] == "cpu_hot" for a in engine.evaluate(snap))
    snap["cpu"]["temp_c"] = 93  # below 95 but within the 5 °C band: still active
    assert any(a["key"] == "cpu_hot" for a in engine.evaluate(snap))
    snap["cpu"]["temp_c"] = 88
    assert not any(a["key"] == "cpu_hot" for a in engine.evaluate(snap))
    assert len(engine.log) == 1  # fired once, not on every sample


def test_battery_alerts(omni_home):
    engine = AlertEngine(Settings())
    assert any(a["key"] == "battery_low" for a in engine.evaluate(_snap(percent=15)))
    assert not engine.evaluate(_snap(percent=15, plugged=True, state="plugged in"))
    # charge reminder only while actively charging past the limit
    assert any(a["key"] == "charge_limit" for a in engine.evaluate(_snap(percent=85, plugged=True, state="charging")))
    assert not engine.evaluate(_snap(percent=85, plugged=True, state="full"))


def test_history_query_and_export(omni_home, tmp_path):
    h = History(tmp_path / "h.db")
    now = time.time()
    for i in range(120):
        h.add_sample({"cpu_pct": i, "cpu_temp_c": 50}, ts=now - 120 + i)
    data = h.query(["cpu_pct", "bogus"], now - 200, now, points=10)
    assert set(data) == {"ts", "cpu_pct"}
    assert len(data["ts"]) <= 11
    assert h.export_csv(now - 200).splitlines()[0].startswith("time_utc,cpu_pct")
    h.prune(retention_days=0)
    assert h.query(["cpu_pct"], 0)["ts"] == []
    h.add_bench_result("bench-cpu", {"single_score": 1})
    assert h.bench_results()[0]["result"] == {"single_score": 1}
    h.record_capacity(80492, 83028, 32)
    assert h.capacity_history()[0]["cycles"] == 32


def test_terminate_refuses_critical_processes(omni_home):
    with pytest.raises(ProcessError):
        terminate(4, set())
    with pytest.raises(ProcessError):
        terminate(os.getpid(), set())


def test_stress_summary_detects_clock_drop():
    samples = [{"t": i, "cpu_mhz": 4500 if i < 10 else 3500, "cpu_temp_c": 80 + i % 5, "gpu_limits": []} for i in range(40)]
    summary = summarize_stress("cpu", samples)
    assert summary["cpu_temp_peak_c"] == 84
    assert "fell" in summary["findings"][0]
    gpu = summarize_stress("gpu", [{"t": 1, "gpu_limits": ["Power limit"]}])
    assert gpu["gpu_limits_seen"] == ["Power limit"]


@pytest.fixture
def junk(tmp_path, monkeypatch):
    temp = tmp_path / "Temp"
    (temp / "sub").mkdir(parents=True)
    old, new = temp / "old.tmp", temp / "new.tmp"
    old.write_bytes(b"x" * 1000)
    new.write_bytes(b"y" * 500)
    (temp / "sub" / "old2.tmp").write_bytes(b"z" * 300)
    past = time.time() - 3 * 86400
    for path in (old, temp / "sub" / "old2.tmp"):
        os.utime(path, (past, past))
    outside = tmp_path / "Important"
    outside.mkdir()
    (outside / "keep.txt").write_text("precious")
    os.utime(outside / "keep.txt", (past, past))
    monkeypatch.setenv("TEMP", str(temp))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    return temp, outside


def test_cleanup_scan_and_clean_only_old_files(junk):
    temp, outside = junk
    row = next(r for r in cleanup.scan(24) if r["key"] == "user_temp")
    assert (row["files"], row["bytes"]) == (2, 1300)
    report = cleanup.clean(["user_temp"], 24)
    assert (report["deleted_files"], report["deleted_bytes"]) == (2, 1300)
    assert (temp / "new.tmp").exists()  # newer than the age limit
    assert not (temp / "sub").exists()  # emptied folder removed
    assert (outside / "keep.txt").exists()


def test_cleanup_never_follows_links(junk):
    temp, outside = junk
    link = temp / "link-to-important"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or admin")
    cleanup.clean(["user_temp"], 24)
    assert (outside / "keep.txt").exists()
