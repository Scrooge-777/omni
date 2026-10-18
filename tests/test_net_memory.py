import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from omni import memory, nettest
from omni.config import Settings, SettingsError

# --------------------------------------------------------------------------- a local fake speed-test server


class _FakeSpeedServer(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    block = b"x" * 65536

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/cdn-cgi/trace"):
            body = b"colo=TST\nloc=IN\nhttp=http/1.1\n"
            self._send(body)
        elif self.path.startswith("/__down"):
            size = int(self.path.split("bytes=")[1])
            self.send_response(200)
            self.send_header("Content-Length", str(size))
            self.send_header("Server-Timing", "cfRequestDuration;dur=0.5")
            self.end_headers()
            sent = 0
            try:
                while sent < size:
                    chunk = self.block[: min(len(self.block), size - sent)]
                    self.wfile.write(chunk)
                    sent += len(chunk)
            except (ConnectionError, OSError):
                pass
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        remaining = length
        try:
            while remaining > 0:
                data = self.rfile.read(min(65536, remaining))
                if not data:
                    return
                remaining -= len(data)
        except (ConnectionError, OSError):
            return
        self._send(b"ok")

    def _send(self, body):
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def speed_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeSpeedServer)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_speed_test_against_local_server(speed_server):
    updates = []
    result = nettest.speed_test(speed_server, streams=2, seconds=2.0,
                                progress=lambda f, m, live=None: updates.append((f, m, live)))
    assert result["colo"] == "TST" and result["country"] == "IN"
    assert result["download_mbps"] > 0 and result["upload_mbps"] > 0
    assert result["latency_ms"] is not None and result["jitter_ms"] is not None
    assert result["loaded_latency_ms"] is not None
    assert result["bufferbloat_grade"] in {"A+", "A", "B", "C", "D", "F"}
    assert any(live for _, _, live in updates)  # live Mbps reported for the gauge
    assert updates[-1][0] == 1.0


def test_speed_test_parts_and_stop(speed_server):
    result = nettest.speed_test(speed_server, parts=["latency"], seconds=1)
    assert "download_mbps" not in result and result["latency_ms"] is not None
    stop = threading.Event()
    stop.set()
    started = time.time()
    nettest.speed_test(speed_server, parts=["download"], seconds=10, stop=stop)
    assert time.time() - started < 5  # a set stop event ends the test straight away


def test_speed_test_errors():
    with pytest.raises(nettest.NetTestError):
        nettest.speed_test("ftp://example.com")
    with pytest.raises(nettest.NetTestError):
        nettest.speed_test("http://127.0.0.1:9", parts=["latency"])  # nothing listens on port 9
    with pytest.raises(nettest.NetTestError):
        nettest.speed_test("http://127.0.0.1:9", parts=[])


def test_latency_summary_and_grades():
    s = nettest.summarize_latency([10, 12, 11, 30])
    assert s["latency_ms"] == 11.5 and s["jitter_ms"] == pytest.approx((2 + 1 + 19) / 3, abs=0.1)
    assert nettest.summarize_latency([]) == {"latency_ms": None, "jitter_ms": None}
    assert nettest.bufferbloat_grade(20, 22) == "A+"
    assert nettest.bufferbloat_grade(54, 120) == "C"
    assert nettest.bufferbloat_grade(20, 900) == "F"
    assert nettest.bufferbloat_grade(None, 10) is None


def test_dns_query_packet():
    packet = nettest.build_dns_query("www.example.com", 0x1234)
    assert struct.unpack(">HHHHHH", packet[:12]) == (0x1234, 0x0100, 1, 0, 0, 0)
    assert packet[12:] == b"\x03www\x07example\x03com\x00\x00\x01\x00\x01"


WLAN = """
    Name                   : Wi-Fi
    State                  : connected
    SSID                   : HomeNet
    BSSID                  : aa:bb:cc:dd:ee:ff
    Radio type             : 802.11ax
    Authentication         : WPA3-Personal
    Band                   : 5 GHz
    Channel                : 149
    Receive rate (Mbps)    : 1201
    Transmit rate (Mbps)   : 1201
    Signal                 : 100%
    Rssi                   : -43
"""


def test_parse_wlan():
    info = nettest.parse_wlan(WLAN)
    assert info["ssid"] == "HomeNet" and info["band"] == "5 GHz" and info["signal_pct"] == 100
    assert info["rssi_dbm"] == -43 and info["receive_mbps"] == 1201.0
    assert nettest.parse_wlan("There is no wireless interface on the system.") is None


# --------------------------------------------------------------------------- RAM map

def test_lists_from_struct():
    info = memory._MemoryLists()
    info.ZeroPageCount, info.FreePageCount, info.ModifiedPageCount, info.ModifiedNoWritePageCount = 10, 20, 3, 1
    info.PageCountByPriority[5] = 100
    lists = memory.lists_from_struct(info)
    assert lists["zeroed"] == 10 * 4096 and lists["free"] == 20 * 4096 and lists["modified"] == 4 * 4096
    assert lists["standby_by_priority"][5] == 100 * 4096


def test_build_map_adds_up():
    gb = 1024 ** 3
    lists = {"zeroed": 4 * gb, "free": 1 * gb, "modified": gb // 10, "bad": 0,
             "standby_by_priority": [0, gb, gb, 0, 0, 3 * gb, 0, 0], "repurposed_by_priority": [0] * 8}
    counters = {"private_ws": 6 * gb, "pool_nonpaged": gb, "pool_paged_resident": gb, "cache_ws": gb // 2,
                "driver_resident": 0, "code_resident": 0}
    m = memory.build_map(lists, counters, 24 * gb)
    assert m["standby"] == 5 * gb and m["available"] == 10 * gb
    assert m["active"] + m["available"] + m["modified"] == 24 * gb
    assert sum(m["active_parts"].values()) == m["active"]
    # counters larger than "in use" are scaled down instead of overflowing it
    m2 = memory.build_map(lists, {"private_ws": 40 * gb}, 24 * gb)
    assert sum(m2["active_parts"].values()) == pytest.approx(m2["active"], rel=1e-6)


def test_build_map_without_page_lists():
    m = memory.build_map(None, {"standby_normal": 100, "modified": 10, "free_zero": 50}, 1000)
    assert m["standby"] == 100 and m["free"] == 50 and m["standby_by_priority"] is None


def test_memory_command_validation():
    with pytest.raises(memory.MemoryError_):
        memory.run_command("format_c")
    with pytest.raises(memory.MemoryError_):
        memory.trim_process(4, set())


def test_speedtest_server_setting_validated(omni_home):
    s = Settings()
    with pytest.raises(SettingsError):
        s.update({"network": {"speedtest_server": "file:///etc/passwd"}})
    s.update({"network": {"speedtest_server": "https://speed.example.net", "streams": 8}})
    assert s.get("network", "streams") == 8
