"""API tests against a fake collector, so they run anywhere (CI included) without real sensors."""

import time

import pytest
from fastapi.testclient import TestClient

from omni.config import Settings
from omni.history import History
from omni.web.app import create_app

TOKEN = "test-token"


class FakeCollector:
    def __init__(self, home):
        self.settings = Settings()
        self.history = History(home / "h.db")
        self.process_sort = "cpu"
        self.gpu = type("G", (), {"name": "Test GPU", "error": None})()
        self.battery = type("B", (), {"present": True})()
        self.snap = {"ts": time.time(), "cpu": {"name": "Test CPU", "usage_pct": 5}, "gpu": {}, "battery": {},
                     "memory": {}, "storage": {}, "network": {}, "processes": [], "alerts": []}

    def latest(self):
        return self.snap

    def tick(self):
        return self.snap

    def live_series(self, seconds):
        return [{"ts": self.snap["ts"], "cpu_pct": 5}]

    def subscribe(self, cb):
        pass

    def unsubscribe(self, cb):
        pass


@pytest.fixture
def client(omni_home):
    omni_home.mkdir(parents=True, exist_ok=True)
    app = create_app(FakeCollector(omni_home), token=TOKEN)
    return TestClient(app, base_url="http://localhost")


def auth(**extra):
    return {"X-OMNI-Token": TOKEN, **extra}


def test_page_embeds_token_and_csp(client):
    res = client.get("/")
    assert res.status_code == 200
    assert TOKEN in res.text
    assert "script-src 'self'" in res.headers["content-security-policy"]


def test_api_requires_token(client):
    assert client.get("/api/snapshot").status_code == 401
    assert client.get("/api/snapshot", headers={"X-OMNI-Token": "wrong"}).status_code == 401
    assert client.get("/api/snapshot", headers=auth()).json()["cpu"]["name"] == "Test CPU"


def test_foreign_host_rejected(client):
    assert client.get("/", headers={"Host": "attacker.example"}).status_code == 403
    assert client.get("/api/snapshot", headers=auth(Host="attacker.example:5050")).status_code == 403


def test_websocket_requires_token(client):
    with pytest.raises(Exception):
        with client.websocket_connect("ws://localhost/ws/live?token=wrong") as ws:
            ws.receive_text()
    with pytest.raises(Exception):  # right token, foreign host
        with client.websocket_connect(f"ws://attacker.example/ws/live?token={TOKEN}") as ws:
            ws.receive_text()
    with client.websocket_connect(f"ws://localhost/ws/live?token={TOKEN}") as ws:
        assert "Test CPU" in ws.receive_text()


def test_settings_round_trip(client):
    data = client.get("/api/settings", headers=auth()).json()
    assert data["values"]["units"]["temperature"] == "C"
    assert data["schema"]["units"]["temperature"]["allowed"] == ["C", "F"]
    res = client.put("/api/settings", headers=auth(), json={"units": {"temperature": "F"}})
    assert res.json()["values"]["units"]["temperature"] == "F"
    assert client.put("/api/settings", headers=auth(), json={"units": {"temperature": "K"}}).status_code == 400
    assert client.post("/api/settings/reset", headers=auth()).json()["values"]["units"]["temperature"] == "C"


def test_history_endpoints(client):
    assert client.get("/api/history?metrics=cpu_pct&range=1h", headers=auth()).json()["ts"] == []
    assert client.get("/api/history?metrics=cpu_pct&range=99y", headers=auth()).status_code == 400
    assert client.get("/api/history/export", headers=auth()).text.startswith("time_utc,")


def test_input_validation(client):
    assert client.post("/api/processes/sort", headers=auth(), json={"sort": "evil"}).status_code == 400
    assert client.post("/api/processes/4/end", headers=auth()).status_code == 400
    assert client.post("/api/bench/nothing", headers=auth()).status_code == 404
    assert client.post("/api/stress", headers=auth(), json={"target": "cpu", "seconds": 5}).status_code == 400
    assert client.post("/api/cleanup/run", headers=auth(), json={"categories": ["../../etc"]}).status_code == 400
    assert client.get("/api/jobs/999", headers=auth()).status_code == 404
