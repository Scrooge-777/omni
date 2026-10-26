"""Health report and verdict: pure functions, no hardware needed."""

from datetime import datetime

from omni import report
from tests.test_web import TOKEN, auth, client  # noqa: F401  (client fixture)

HEALTHY = {
    "cpu": {"name": "Test CPU", "usage_pct": 10, "clock_mhz": 3000, "temp_c": 55},
    "gpu": {"available": True, "name": "Test GPU", "util_pct": 3, "temp_c": 45, "power_w": 12},
    "memory": {"total": 16e9, "used": 6e9, "usage_pct": 37},
    "battery": {"present": True, "health_pct": 92.0, "wear_pct": 8.0, "grade": "Excellent", "cycles": 120,
                "percent": 80, "state": "charging", "full_mwh": 70000, "design_mwh": 76000},
    "storage": {"disks": [{"name": "NVMe", "health": "Healthy", "temp_c": 40, "wear_pct": 3}]},
}


def test_healthy_machine_has_no_issues():
    assert report.verdict(HEALTHY) == []


def test_verdict_finds_each_problem_and_sorts_worst_first():
    snap = {
        **HEALTHY,
        "cpu": {**HEALTHY["cpu"], "temp_c": 97},
        "memory": {**HEALTHY["memory"], "usage_pct": 95},
        "battery": {**HEALTHY["battery"], "health_pct": 60},
        "storage": {"disks": [{"name": "D", "health": "Warning"}]},
    }
    issues = report.verdict(snap)
    texts = [i["text"] for i in issues]
    assert any("CPU is very hot" in t for t in texts)
    assert any("Memory is almost full" in t for t in texts)
    assert any("40 %" in t for t in texts)
    assert any("Warning" in t for t in texts)
    levels = [i["level"] for i in issues]
    assert levels == sorted(levels, key=lambda lv: lv != "danger")


def test_warm_is_a_warning_not_danger():
    snap = {**HEALTHY, "cpu": {**HEALTHY["cpu"], "temp_c": 88}}
    assert [i["level"] for i in report.verdict(snap)] == ["warn"]


def test_custom_limits_are_respected():
    snap = {**HEALTHY, "cpu": {**HEALTHY["cpu"], "temp_c": 80}}
    assert report.verdict(snap) == []
    assert report.verdict(snap, {"cpu_temp_c": 80})[0]["level"] == "danger"


def test_low_battery_only_when_discharging():
    low = {**HEALTHY["battery"], "percent": 10}
    assert report.verdict({**HEALTHY, "battery": {**low, "state": "charging"}}) == []
    assert report.verdict({**HEALTHY, "battery": {**low, "state": "discharging"}})[0]["level"] == "danger"


def test_missing_sensors_do_not_crash():
    assert report.verdict({}) == []
    text = report.build({}, now=datetime(2026, 1, 2, 3, 4))
    assert "Laptop health report" in text and "2026-01-02 03:04" in text


def test_report_contents_and_privacy():
    profile = {"vendor": "HP", "model": "OMEN 16", "form_factor": "Laptop", "bios": "F.10"}
    text = report.build(HEALTHY, profile, [{"area": "Fans", "status": "ok", "detail": "x"}])
    for expected in ("HP OMEN 16", "Test CPU", "Test GPU", "92.0 %", "120", "NVMe", "Fans:** works", "✓"):
        assert expected in text
    assert text.endswith("\n")


def test_report_endpoint_needs_token_and_returns_markdown(client):  # noqa: F811
    assert client.get("/api/report").status_code == 401
    res = client.get("/api/report", headers=auth())
    assert res.status_code == 200
    assert res.text.startswith("# Laptop health report")


def test_websocket_refuses_foreign_origin(client):  # noqa: F811
    from starlette.websockets import WebSocketDisconnect

    import pytest

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"ws://localhost/ws/live?token={TOKEN}", headers={"origin": "https://evil.example"}):
            pass
    with client.websocket_connect(f"ws://localhost/ws/live?token={TOKEN}", headers={"origin": "http://localhost:5050"}) as ws:
        assert "Test CPU" in ws.receive_text()


def test_security_headers_present(client):  # noqa: F811
    res = client.get("/")
    assert res.headers["cross-origin-opener-policy"] == "same-origin"
    assert res.headers["cross-origin-resource-policy"] == "same-origin"
    assert "frame-ancestors 'none'" in res.headers["content-security-policy"]
