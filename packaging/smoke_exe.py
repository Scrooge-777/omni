"""Start a built OMNI.exe and check what the plain version/settings checks cannot:
the page loads, the API answers, and the live WebSocket connects and sends a snapshot.

usage: python packaging/smoke_exe.py dist/OMNI.exe
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

from websockets.sync.client import connect

PORT = 5099


def main(exe: str) -> int:
    # A one-file exe starts a child process that inherits stdout, so log to a file instead of a pipe
    # (a pipe would never close) and stop the whole process tree at the end.
    log = tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False)
    proc = subprocess.Popen([os.path.abspath(exe), "web", "--no-browser", "--port", str(PORT)],
                            stdout=log, stderr=subprocess.STDOUT, text=True)
    try:
        html = ""
        for _ in range(60):
            try:
                html = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2).read().decode()
                break
            except OSError:
                time.sleep(1)
        token = re.search(r'name="omni-token" content="([^"]+)"', html)
        if not token:
            print("FAIL: dashboard page did not load")
            return 1
        token = token.group(1)
        request = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/snapshot", headers={"X-OMNI-Token": token})
        snapshot = json.load(urllib.request.urlopen(request, timeout=15))
        assert "cpu" in snapshot, "snapshot has no cpu"
        with connect(f"ws://127.0.0.1:{PORT}/ws/live?token={token}", open_timeout=10) as ws:
            assert "cpu" in json.loads(ws.recv(timeout=15)), "websocket sent no snapshot"
        print("OK: page, API and live WebSocket all work")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        return 1
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
        log.close()
        with open(log.name, encoding="utf-8", errors="replace") as handle:
            output = handle.read()
        os.unlink(log.name)
        if "No supported WebSocket library" in output:
            print("FAIL: server reports no WebSocket library")
            os._exit(1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
