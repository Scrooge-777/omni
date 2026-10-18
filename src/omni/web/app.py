"""Web dashboard API (FastAPI) and static UI.

Security model (the server only listens on 127.0.0.1, but any website open in your
browser can still send requests to localhost):
- every /api request must carry the per-run token from the X-OMNI-Token header (or
  ?token= for the WebSocket). Other sites can't read the token, and can't send custom
  headers cross-origin without a CORS preflight, which this server never approves;
- the Host header must be localhost/127.0.0.1, which blocks DNS-rebinding attacks.
"""

import asyncio
import hashlib
import json
import re
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from omni import __version__, bench, cleanup, memory, nettest, power, win
from omni.collector import Collector
from omni.config import SCHEMA, SettingsError, home_dir
from omni.jobs import JobBusy, JobManager
from omni.sensors.processes import ProcessError, terminate

STATIC = Path(__file__).parent / "static"
ALLOWED_HOSTS = {"localhost", "127.0.0.1"}
RANGES = {"15m": 900, "1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}


def asset_version() -> str:
    """Hash of the static files, appended to their URLs so an upgrade never runs a stale
    cached script against a new page (that left the UI stuck on "Connecting")."""
    digest = hashlib.sha1()
    for path in sorted(STATIC.iterdir()):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


def create_app(collector: Collector, token: str | None = None) -> FastAPI:
    token = token or secrets.token_urlsafe(24)
    version = asset_version()
    settings = collector.settings
    jobs = JobManager(on_done=lambda job: collector.history.add_bench_result(job.kind, job.result)
                      if job.kind.startswith("bench") or job.kind == "speedtest" else None)
    ram = memory.MemoryMap()
    tweaks = power.TweakManager(home_dir())
    app = FastAPI(title="OMNI", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token
    app.state.jobs = jobs

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "Forbidden host"}, status_code=403)
        if request.url.path.startswith("/api/") and not secrets.compare_digest(
                request.headers.get("x-omni-token", ""), token):
            return JSONResponse({"detail": "Missing or wrong token"}, status_code=401)
        response = await call_next(request)
        if request.url.path == "/":
            response.headers["Cache-Control"] = "no-store"  # carries this run's token
        elif request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"  # always revalidate (cheap: ETag)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self' ws://localhost:* ws://127.0.0.1:*; frame-ancestors 'none'")
        return response

    def run_blocking(func, *args):
        return asyncio.get_running_loop().run_in_executor(None, func, *args)

    # ------------------------------------------------------------------ pages

    @app.get("/", response_class=HTMLResponse)
    def index():
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        html = re.sub(r'(/static/[\w.-]+\.(?:js|css|svg))"', rf'\1?v={version}"', html)
        return html.replace("{{TOKEN}}", token).replace("{{VERSION}}", __version__)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # ------------------------------------------------------------------ telemetry

    @app.get("/api/snapshot")
    def snapshot():
        return collector.latest() or collector.tick()

    @app.get("/api/info")
    def info():
        snap = collector.latest() or {}
        return {"version": __version__, "admin": win.is_admin(), "cpu": snap.get("cpu", {}).get("name"),
                "gpu": collector.gpu.name, "gpu_error": collector.gpu.error, "battery": collector.battery.present,
                "sensor_sources": snap.get("sources", []), "data_dir": str(home_dir())}

    @app.get("/api/live")
    def live(seconds: int = 300):
        return collector.live_series(min(seconds, 900))

    @app.get("/api/history")
    def history(metrics: str, range: str = "1h"):
        seconds = RANGES.get(range)
        if not seconds:
            raise HTTPException(400, f"range must be one of {', '.join(RANGES)}")
        return collector.history.query(metrics.split(","), time.time() - seconds)

    @app.get("/api/history/export", response_class=PlainTextResponse)
    def history_export(range: str = "24h"):
        seconds = RANGES.get(range, 86400)
        csv_text = collector.history.export_csv(time.time() - seconds)
        return PlainTextResponse(csv_text, headers={"Content-Disposition": f'attachment; filename="omni-history-{range}.csv"'})

    @app.get("/api/battery/capacity")
    def battery_capacity():
        return collector.history.capacity_history()

    @app.websocket("/ws/live")
    async def ws_live(socket: WebSocket):
        host = (socket.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in ALLOWED_HOSTS or not secrets.compare_digest(socket.query_params.get("token", ""), token):
            await socket.close(code=4401)
            return
        await socket.accept()
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        loop = asyncio.get_running_loop()

        def push(snap):
            def put():
                if queue.full():
                    queue.get_nowait()  # drop the stale frame; the client only needs the newest
                queue.put_nowait(snap)
            loop.call_soon_threadsafe(put)

        collector.subscribe(push)
        try:
            if collector.latest():
                await socket.send_text(json.dumps(collector.latest(), default=str))
            while True:
                snap = await queue.get()
                await socket.send_text(json.dumps(snap, default=str))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            collector.unsubscribe(push)

    # ------------------------------------------------------------------ processes

    @app.post("/api/processes/sort")
    async def process_sort(request: Request):
        sort = (await request.json()).get("sort")
        if sort not in ("cpu", "mem"):
            raise HTTPException(400, "sort must be cpu or mem")
        collector.process_sort = sort
        return {"sort": sort}

    @app.post("/api/processes/{pid}/end")
    def end_process(pid: int):
        try:
            return terminate(pid, settings.protected_processes())
        except ProcessError as exc:
            raise HTTPException(400, str(exc)) from None

    # ------------------------------------------------------------------ fans & energy

    @app.post("/api/fans/max")
    async def fans_max(request: Request):
        on = (await request.json()).get("on")
        if not isinstance(on, bool):
            raise HTTPException(400, "on must be true or false")
        try:
            return {"max_fan": await run_blocking(collector.set_max_fan, on)}
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/energy/reset")
    def energy_reset():
        collector.reset_energy()
        return {"ok": True}

    # ------------------------------------------------------------------ RAM map

    @app.get("/api/memory/map")
    async def memory_map():
        def read():
            return {"map": ram.read(), "processes": ram.top_processes(), "admin": win.is_admin(),
                    "commands": {k: {"label": v[1], "help": v[2]} for k, v in memory.COMMANDS.items()}}
        return await run_blocking(read)

    @app.post("/api/memory/command")
    async def memory_command(request: Request):
        name = (await request.json()).get("command")
        try:
            return await run_blocking(memory.run_command, str(name))
        except memory.MemoryError_ as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/memory/trim/{pid}")
    async def memory_trim(pid: int):
        try:
            return await run_blocking(memory.trim_process, pid, settings.protected_processes())
        except memory.MemoryError_ as exc:
            raise HTTPException(400, str(exc)) from None

    # ------------------------------------------------------------------ internet

    @app.post("/api/net/speedtest")
    async def net_speedtest(request: Request):
        body = await request.json()
        parts = body.get("parts") or ["latency", "download", "upload"]
        if not isinstance(parts, list) or not set(parts) <= {"latency", "download", "upload"} or not parts:
            raise HTTPException(400, "parts must be a list of latency/download/upload")
        server = settings.get("network", "speedtest_server")
        streams, seconds = settings.get("network", "streams"), settings.get("network", "seconds")
        return start_job("speedtest", lambda job: nettest.speed_test(server, parts, streams, seconds, job.update, job.stop))

    @app.post("/api/net/diagnostics")
    def net_diagnostics():
        return start_job("netdiag", lambda job: nettest.diagnostics(job.update, job.stop))

    @app.get("/api/net/wifi")
    async def net_wifi():
        return await run_blocking(nettest.wifi_info)

    # ------------------------------------------------------------------ power

    @app.get("/api/power")
    async def power_status():
        def read():
            status = tweaks.status()
            status["plans"] = power.list_plans()
            status["mode"] = power.get_power_mode()
            return status
        try:
            return await run_blocking(read)
        except power.PowerError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/power/plan")
    async def power_plan(request: Request):
        body = await request.json()
        try:
            return await run_blocking(power.set_plan, str(body.get("guid", "")))
        except power.PowerError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/power/mode")
    async def power_mode(request: Request):
        body = await request.json()
        try:
            return {"mode": await run_blocking(power.set_power_mode, str(body.get("mode", "")))}
        except power.PowerError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/power/tweak")
    async def power_tweak(request: Request):
        body = await request.json()
        try:
            return await run_blocking(tweaks.apply, str(body.get("name", "")), int(body.get("dc_value")))
        except (power.PowerError, TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/power/restore")
    async def power_restore():
        try:
            return await run_blocking(tweaks.restore)
        except power.PowerError as exc:
            raise HTTPException(400, str(exc)) from None

    # ------------------------------------------------------------------ cleanup

    @app.get("/api/cleanup/scan")
    async def cleanup_scan():
        return await run_blocking(cleanup.scan, settings.get("cleanup", "min_age_hours"))

    @app.post("/api/cleanup/run")
    async def cleanup_run(request: Request):
        keys = (await request.json()).get("categories") or []
        valid = {c.key for c in cleanup.categories()}
        if not keys or not set(keys) <= valid:
            raise HTTPException(400, "Pick at least one valid category")
        age = settings.get("cleanup", "min_age_hours")
        return start_job("cleanup", lambda job: cleanup.clean(keys, age))

    # ------------------------------------------------------------------ benchmarks & stress

    def start_job(kind, func):
        try:
            return jobs.start(kind, func).as_dict()
        except JobBusy as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/bench/{kind}")
    def bench_start(kind: str):
        runners = {
            "cpu": lambda job: bench.cpu_benchmark(5, job.update, job.stop),
            "memory": lambda job: bench.memory_benchmark(512, 8, job.update, job.stop),
            "disk": lambda job: bench.disk_benchmark(None, 1024, 5, job.update, job.stop),
            "gpu": lambda job: bench.gpu_benchmark(6, job.update, job.stop),
        }
        if kind not in runners:
            raise HTTPException(404, "Unknown benchmark")
        return start_job(f"bench-{kind}", runners[kind])

    @app.post("/api/stress")
    async def stress_start(request: Request):
        body = await request.json()
        target, seconds = body.get("target"), body.get("seconds", 60)
        if target not in ("cpu", "gpu", "both") or not isinstance(seconds, (int, float)) or not 10 <= seconds <= 3600:
            raise HTTPException(400, "target must be cpu/gpu/both and seconds 10–3600")
        return start_job(f"stress-{target}",
                         lambda job: bench.stress_test(target, seconds, collector.latest, job.update, job.stop))

    @app.get("/api/jobs/current")
    def job_current():
        job = jobs.running()
        return job.as_dict() if job else None

    @app.get("/api/jobs/{job_id}")
    def job_get(job_id: int):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        return job.as_dict()

    @app.post("/api/jobs/{job_id}/stop")
    def job_stop(job_id: int):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        job.stop.set()
        return job.as_dict()

    @app.get("/api/bench/results")
    def bench_results():
        return collector.history.bench_results()

    # ------------------------------------------------------------------ settings

    @app.get("/api/settings")
    def settings_get():
        schema = {group: {key: {"default": spec[0], "allowed": spec[1]} for key, spec in keys.items()}
                  for group, keys in SCHEMA.items()}
        return {"values": settings.as_dict(), "schema": schema}

    @app.put("/api/settings")
    async def settings_put(request: Request):
        try:
            return {"values": settings.update(await request.json())}
        except SettingsError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/settings/reset")
    def settings_reset():
        return {"values": settings.reset()}

    return app
