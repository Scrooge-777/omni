"""Internet tests: speed test, bufferbloat, ping / packet loss, DNS comparison, Wi-Fi details.

The speed test uses Cloudflare's speed-test endpoints (the ones speed.cloudflare.com uses):
GET /__down?bytes=N to download, POST /__up to upload, /cdn-cgi/trace for the server
location. They are not an official API, so the server is a setting and every failure
is reported instead of producing a made-up number.

Method (as in Cloudflare's open-source engine and similar clients):
- latency: 20 tiny requests on a warm connection; median and jitter (mean change between
  consecutive samples), minus the server's own processing time from Server-Timing;
- download / upload: several parallel streams for a fixed time; the first second
  (TCP slow start) is ignored;
- loaded latency: latency probes while the download runs. The increase over idle latency
  is "bufferbloat", the lag you feel in calls and games when the line is busy.
"""

import http.client
import os
import random
import re
import socket
import statistics
import struct
import threading
import time
from urllib.parse import urlparse

from omni import win

DEFAULT_SERVER = "https://speed.cloudflare.com"
CHUNK = 64 * 1024
DOWNLOAD_BYTES = 25_000_000  # per request; Cloudflare refuses requests of 100 MB and more
HEADERS = {"User-Agent": "OMNI-speedtest/2.1", "Cache-Control": "no-store"}


class NetTestError(Exception):
    pass


def _noop(*_args, **_kwargs):
    pass


class _Endpoint:
    def __init__(self, base: str):
        parsed = urlparse(base)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise NetTestError(f"Invalid speed-test server {base!r}")
        self.https = parsed.scheme == "https"
        self.host = parsed.hostname
        self.port = parsed.port

    def connect(self, timeout: float = 10.0) -> http.client.HTTPConnection:
        cls = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
        return cls(self.host, self.port, timeout=timeout)


def _server_ms(response) -> float:
    """Server processing time from 'Server-Timing: cfRequestDuration;dur=1.2'."""
    match = re.search(r"dur=([\d.]+)", response.getheader("Server-Timing") or "")
    return float(match.group(1)) if match else 0.0


def _latency_once(conn) -> float:
    started = time.perf_counter()
    conn.request("GET", "/__down?bytes=0", headers=HEADERS)
    response = conn.getresponse()
    response.read()
    if response.status != 200:
        raise NetTestError(f"Server answered {response.status}")
    return max(0.0, (time.perf_counter() - started) * 1000 - _server_ms(response))


def summarize_latency(samples: list[float]) -> dict:
    if not samples:
        return {"latency_ms": None, "jitter_ms": None}
    jitter = statistics.mean(abs(a - b) for a, b in zip(samples, samples[1:])) if len(samples) > 1 else 0.0
    return {"latency_ms": round(statistics.median(samples), 1), "jitter_ms": round(jitter, 1),
            "latency_min_ms": round(min(samples), 1), "latency_max_ms": round(max(samples), 1)}


def bufferbloat_grade(idle_ms, loaded_ms) -> str | None:
    """Grades like Waveform's bufferbloat test, by latency increase under load."""
    if idle_ms is None or loaded_ms is None:
        return None
    increase = loaded_ms - idle_ms
    for limit, grade in ((5, "A+"), (30, "A"), (60, "B"), (200, "C"), (400, "D")):
        if increase < limit:
            return grade
    return "F"


class _Meter:
    """Counts bytes from several threads and reports throughput over a window."""

    def __init__(self):
        self.lock = threading.Lock()
        self.total = 0
        self.marks: list[tuple[float, int]] = []

    def add(self, n: int):
        with self.lock:
            self.total += n

    def mark(self):
        with self.lock:
            self.marks.append((time.perf_counter(), self.total))

    def rate_bps(self, skip_s: float = 1.0) -> float:
        """Bytes/s from skip_s after the start to the end (ignores TCP slow start)."""
        if len(self.marks) < 2:
            return 0.0
        start_t = self.marks[0][0] + skip_s
        first = next((m for m in self.marks if m[0] >= start_t), self.marks[0])
        last = self.marks[-1]
        return (last[1] - first[1]) / (last[0] - first[0]) if last[0] > first[0] else 0.0

    def recent_bps(self, window_s: float = 1.0) -> float:
        if len(self.marks) < 2:
            return 0.0
        last = self.marks[-1]
        earlier = next((m for m in reversed(self.marks) if last[0] - m[0] >= window_s), self.marks[0])
        return (last[1] - earlier[1]) / (last[0] - earlier[0]) if last[0] > earlier[0] else 0.0


def _download_worker(endpoint, deadline, meter, stop, errors):
    try:
        conn = endpoint.connect()
        while time.perf_counter() < deadline and not stop.is_set():
            conn.request("GET", f"/__down?bytes={DOWNLOAD_BYTES}", headers=HEADERS)
            response = conn.getresponse()
            if response.status != 200:
                raise NetTestError(f"Download refused ({response.status})")
            while time.perf_counter() < deadline and not stop.is_set():
                block = response.read(CHUNK)
                if not block:
                    break
                meter.add(len(block))
            else:
                conn.close()  # stopped mid-body: drop the connection rather than read the rest
                return
        conn.close()
    except (OSError, http.client.HTTPException, NetTestError) as exc:
        errors.append(str(exc))


def _upload_worker(endpoint, deadline, meter, stop, errors):
    payload = os.urandom(CHUNK)  # random data, so compression can't inflate the result
    size = DOWNLOAD_BYTES
    try:
        while time.perf_counter() < deadline and not stop.is_set():
            conn = endpoint.connect()
            conn.putrequest("POST", "/__up")
            conn.putheader("User-Agent", HEADERS["User-Agent"])
            conn.putheader("Content-Type", "application/octet-stream")
            conn.putheader("Content-Length", str(size))
            conn.endheaders()
            sent = 0
            while sent < size and time.perf_counter() < deadline and not stop.is_set():
                conn.send(payload)
                sent += len(payload)
                meter.add(len(payload))
            if sent >= size:
                conn.getresponse().read()
            conn.close()
    except (OSError, http.client.HTTPException) as exc:
        errors.append(str(exc))


def _transfer(kind: str, endpoint, seconds: float, streams: int, progress, stop, probe_latency=False):
    meter, errors, latencies = _Meter(), [], []
    deadline = time.perf_counter() + seconds
    worker = _download_worker if kind == "download" else _upload_worker
    threads = [threading.Thread(target=worker, args=(endpoint, deadline, meter, stop, errors), daemon=True)
               for _ in range(streams)]
    for t in threads:
        t.start()

    probe_conn = endpoint.connect() if probe_latency else None
    meter.mark()
    started = time.perf_counter()
    next_probe = started + 1.0
    while time.perf_counter() < deadline and not stop.is_set() and any(t.is_alive() for t in threads):
        time.sleep(0.25)
        meter.mark()
        live = meter.recent_bps() * 8 / 1e6
        progress(min(1.0, (time.perf_counter() - started) / seconds), f"{kind.capitalize()} {live:.1f} Mbps", live)
        if probe_conn and time.perf_counter() >= next_probe:
            next_probe = time.perf_counter() + 0.5
            try:
                latencies.append(_latency_once(probe_conn))
            except (OSError, http.client.HTTPException, NetTestError):
                probe_conn = endpoint.connect()
    for t in threads:
        t.join(3)
    meter.mark()
    if probe_conn:
        probe_conn.close()
    if meter.total == 0:
        raise NetTestError(f"{kind.capitalize()} failed: {errors[0] if errors else 'no data transferred'}")
    return meter, latencies, errors


def speed_test(server: str = DEFAULT_SERVER, parts=("latency", "download", "upload"), streams: int = 4,
               seconds: float = 8.0, progress=_noop, stop: threading.Event | None = None) -> dict:
    """Run the chosen parts. progress(fraction, message, live_mbps)."""
    stop = stop or threading.Event()
    endpoint = _Endpoint(server)
    result = {"server": server, "streams": streams, "parts": list(parts)}
    phases = [p for p in ("latency", "download", "upload") if p in parts]
    if not phases:
        raise NetTestError("Pick at least one part of the test")

    def phase_progress(index):
        return lambda f, message, live=None: progress((index + f) / len(phases), message, live)

    try:
        conn = endpoint.connect()
        conn.request("GET", "/cdn-cgi/trace", headers=HEADERS)
        trace = dict(line.split("=", 1) for line in conn.getresponse().read().decode().splitlines() if "=" in line)
        conn.close()
        result.update({"colo": trace.get("colo"), "country": trace.get("loc"), "protocol": trace.get("http")})
    except (OSError, http.client.HTTPException, ValueError):
        result.update({"colo": None, "country": None})

    for index, phase in enumerate(phases):
        if stop.is_set():
            break
        report = phase_progress(index)
        if phase == "latency":
            conn, samples = endpoint.connect(), []
            try:
                _latency_once(conn)  # warm-up: DNS, TCP and TLS handshakes
                for i in range(20):
                    if stop.is_set():
                        break
                    samples.append(_latency_once(conn))
                    report((i + 1) / 20, f"Latency {statistics.median(samples):.0f} ms")
            except (OSError, http.client.HTTPException) as exc:
                raise NetTestError(f"Could not reach {endpoint.host}: {exc}") from None
            finally:
                conn.close()
            result.update(summarize_latency(samples))
        else:
            meter, loaded, errors = _transfer(phase, endpoint, seconds, streams, report, stop, probe_latency=phase == "download")
            result[f"{phase}_mbps"] = round(meter.rate_bps() * 8 / 1e6, 1)
            result[f"{phase}_bytes"] = meter.total
            if errors:
                result[f"{phase}_warnings"] = sorted(set(errors))[:3]
            if loaded:
                result["loaded_latency_ms"] = round(statistics.median(loaded), 1)
    result["bufferbloat_grade"] = bufferbloat_grade(result.get("latency_ms"), result.get("loaded_latency_ms"))
    progress(1.0, "Done", None)
    return result


# --------------------------------------------------------------------------- ping & DNS

PING_TARGETS = [("Cloudflare", "1.1.1.1"), ("Google", "8.8.8.8"), ("Quad9", "9.9.9.9")]
DNS_RESOLVERS = [("Cloudflare", "1.1.1.1"), ("Google", "8.8.8.8"), ("Quad9", "9.9.9.9")]


def tcp_ping(host: str, port: int = 443, count: int = 10, timeout: float = 2.0) -> dict:
    """Round-trip time of TCP handshakes (works without admin rights, unlike ICMP ping)."""
    times, lost = [], 0
    for _ in range(count):
        started = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                times.append((time.perf_counter() - started) * 1000)
        except OSError:
            lost += 1
        time.sleep(0.05)
    return {"host": host, **summarize_latency(times), "loss_pct": round(lost / count * 100)}


def build_dns_query(name: str, query_id: int) -> bytes:
    header = struct.pack(">HHHHHH", query_id, 0x0100, 1, 0, 0, 0)  # recursion desired, one question
    question = b"".join(bytes([len(part)]) + part.encode() for part in name.split(".")) + b"\0"
    return header + question + struct.pack(">HH", 1, 1)  # type A, class IN


def dns_lookup_ms(resolver: str, name: str, timeout: float = 2.0) -> float | None:
    query_id = random.randrange(65536)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        started = time.perf_counter()
        try:
            sock.sendto(build_dns_query(name, query_id), (resolver, 53))
            data, _ = sock.recvfrom(512)
        except OSError:
            return None
    if len(data) < 12 or struct.unpack(">H", data[:2])[0] != query_id:
        return None
    return (time.perf_counter() - started) * 1000


def current_dns_server() -> str | None:
    """The IPv4 DNS server Windows is using now (queried directly, so Windows' own DNS cache
    doesn't make it look faster than the others)."""
    servers = win.powershell_json(
        "Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object { $_.ServerAddresses } | "
        "Select-Object -First 1 -ExpandProperty ServerAddresses | ConvertTo-Json -Compress", timeout=10)
    if isinstance(servers, list):
        servers = servers[0] if servers else None
    return servers if isinstance(servers, str) else None


def dns_compare(names=("www.google.com", "www.wikipedia.org", "www.microsoft.com")) -> list[dict]:
    """Median lookup time per resolver, all queried the same way (one UDP query each)."""
    resolvers = list(DNS_RESOLVERS)
    current = current_dns_server()
    if current and current not in {address for _, address in resolvers}:
        resolvers.insert(0, ("This PC's DNS", current))
    rows = []
    for label, resolver in resolvers:
        times = [ms for name in names for _ in range(2) if (ms := dns_lookup_ms(resolver, name)) is not None]
        rows.append({"resolver": label, "address": resolver, "current": resolver == current,
                     "median_ms": round(statistics.median(times), 1) if times else None, "failed": not times})
    return rows


def diagnostics(progress=_noop, stop=None) -> dict:
    results = {"ping": [], "dns": []}
    for i, (label, host) in enumerate(PING_TARGETS):
        if stop and stop.is_set():
            break
        progress(i / 4, f"Ping {label}")
        results["ping"].append({"name": label, **tcp_ping(host)})
    progress(0.75, "Comparing DNS servers")
    results["dns"] = dns_compare()
    progress(1.0, "Done")
    return results


# --------------------------------------------------------------------------- Wi-Fi

def parse_wlan(text: str) -> dict | None:
    fields = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields.setdefault(key.strip().lower(), value.strip())
    if not fields.get("state") or "ssid" not in fields:
        return None
    signal = fields.get("signal", "").rstrip("%")
    return {
        "ssid": fields.get("ssid"),
        "state": fields.get("state"),
        "band": fields.get("band"),
        "channel": fields.get("channel"),
        "radio": fields.get("radio type"),
        "signal_pct": int(signal) if signal.isdigit() else None,
        "rssi_dbm": int(fields["rssi"]) if fields.get("rssi", "").lstrip("-").isdigit() else None,
        "receive_mbps": float(fields["receive rate (mbps)"]) if fields.get("receive rate (mbps)") else None,
        "transmit_mbps": float(fields["transmit rate (mbps)"]) if fields.get("transmit rate (mbps)") else None,
        "auth": fields.get("authentication"),
    }


def wifi_info() -> dict | None:
    if not win.IS_WINDOWS:
        return None
    try:
        result = win.run(["netsh", "wlan", "show", "interfaces"], timeout=5)
    except (OSError, win.subprocess.TimeoutExpired):
        return None
    return parse_wlan(result.stdout) if result.returncode == 0 else None
