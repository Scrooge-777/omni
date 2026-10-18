"""Command line: `omni` starts the web dashboard; subcommands cover everything else."""

import argparse
import json
import socket
import sys
import threading
import time
import webbrowser

from rich.console import Console
from rich.table import Table

from omni import __version__, bench, cleanup, power, units
from omni.config import Settings, SettingsError, home_dir

console = Console()


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _omni_running_on(port: int) -> bool:
    """True if an OMNI dashboard already answers on this port."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
            return b'name="omni-token"' in response.read(4096)
    except OSError:
        return False


def cmd_web(args):
    settings = Settings()
    port = args.port or settings.get("general", "web_port")
    # A second copy would poll every sensor twice and write the same history database;
    # reuse the running one instead (e.g. after double-clicking OMNI.bat again).
    if _omni_running_on(port):
        url = f"http://localhost:{port}/"
        console.print(f"OMNI is already running: {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return

    import uvicorn

    from omni.collector import Collector
    from omni.web.app import create_app

    collector = Collector(settings)
    for candidate in range(port, port + 10):
        if _port_free(candidate):
            port = candidate
            break
    else:
        sys.exit(f"Ports {port}-{port + 9} are all in use")
    collector.start()
    app = create_app(collector)
    url = f"http://localhost:{port}/"
    console.print(f"[bold]OMNI {__version__}[/bold] dashboard: [link={url}]{url}[/link]  (Ctrl+C to stop)")
    if settings.get("general", "open_browser") and not args.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    finally:
        collector.stop()


def cmd_tui(_args):
    from omni.tui import run

    run()


def _snapshot():
    from omni.collector import Collector

    collector = Collector()
    collector.refresh_slow(force=True)
    collector.tick()
    time.sleep(1.0)  # rates and CPU % need two samples
    return collector.tick(), collector.settings


def cmd_status(args):
    snap, settings = _snapshot()
    if args.json:
        print(json.dumps(snap, indent=2, default=str))
        return
    t, d = settings.get("units", "temperature"), settings.get("units", "data")
    cpu, gpu, batt, mem = snap["cpu"], snap["gpu"], snap["battery"], snap["memory"]
    table = Table(title=f"OMNI {__version__}", show_header=False, title_justify="left")
    table.add_column(style="bold")
    table.add_column()
    table.add_row("CPU", f"{cpu['name']} · {units.percent(cpu['usage_pct'])} · {units.frequency(cpu['clock_mhz'])} · {units.temperature(cpu['temp_c'], t)}")
    if gpu.get("available"):
        load = "sleeping" if gpu.get("asleep") else units.percent(gpu.get("util_pct"))
        table.add_row("GPU", f"{gpu.get('name')} · {load} · {units.temperature(gpu.get('temp_c'), t)} · {units.power(gpu.get('power_w'))}")
    if batt.get("present"):
        table.add_row("Battery", f"{units.percent(batt['percent'])} {batt['state']} · {units.power(abs(batt['power_w'] or 0))} · "
                                 f"health {units.percent(batt['health_pct'], 1)} ({batt['grade']}) · {batt['cycles']} cycles")
    table.add_row("Memory", f"{units.data_size(mem['used'], d)} of {units.data_size(mem['total'], d)} ({units.percent(mem['usage_pct'])})")
    for disk in snap["storage"]["disks"]:
        table.add_row("SSD", f"{disk['name']} · {disk['health']} · {units.temperature(disk.get('temp_c'), t)} · wear {disk.get('wear_pct')} %")
    for alert in snap["alerts"]:
        table.add_row("Alert", alert["message"])
    console.print(table)


def cmd_battery(_args):
    snap, settings = _snapshot()
    b = snap["battery"]
    if not b.get("present"):
        console.print("No battery found.")
        return
    e = settings.get("units", "energy")
    rows = [
        ("Charge", f"{units.percent(b['percent'])} ({b['state']})"),
        ("Power", units.power(b["power_w"])),
        ("Voltage", f"{b['voltage_v']} V" if b["voltage_v"] else "—"),
        ("Full charge capacity", units.energy(b["full_mwh"], e)),
        ("Design capacity", f"{units.energy(b['design_mwh'], e)} (from {b['design_source']})"),
        ("Health", f"{units.percent(b['health_pct'], 1)} · {b['grade']}"),
        ("Cycles", str(b["cycles"])),
        ("Time left", units.duration(b["time_left_s"])),
    ]
    table = Table(title="Battery", show_header=False, title_justify="left")
    for k, v in rows:
        table.add_row(k, v)
    console.print(table)


def _progress(fraction, message):
    console.print(f"  [{fraction * 100:5.1f} %] {message}", end="\r")


def cmd_bench(args):
    from omni.history import History

    history = History(home_dir() / "history.db")
    kinds = ["cpu", "memory", "disk", "gpu"] if args.kind == "all" else [args.kind]
    d = Settings().get("units", "data")
    for kind in kinds:
        console.print(f"[bold]{kind.upper()} benchmark[/bold]")
        try:
            result = {
                "cpu": lambda: bench.cpu_benchmark(5, _progress),
                "memory": lambda: bench.memory_benchmark(512, 8, _progress),
                "disk": lambda: bench.disk_benchmark(args.folder, 1024, 5, _progress),
                "gpu": lambda: bench.gpu_benchmark(6, _progress),
            }[kind]()
        except bench.BenchError as exc:
            console.print(f"  [red]{exc}[/red]")
            continue
        history.add_bench_result(f"bench-{kind}", result)
        console.print(" " * 60, end="\r")
        for key, value in result.items():
            if key.endswith("_bps"):
                value = units.data_rate(value, d)
            console.print(f"  {key:<24} {value}")


def cmd_stress(args):
    from omni.collector import Collector

    collector = Collector()
    collector.start()
    console.print(f"Stressing {args.target.upper()} for {args.seconds} s. Ctrl+C stops early.")
    stop = threading.Event()
    try:
        result = bench.stress_test(args.target, args.seconds, collector.latest, _progress, stop)
    except KeyboardInterrupt:
        stop.set()
        return
    finally:
        collector.stop()
    t = collector.settings.get("units", "temperature")
    console.print(" " * 60, end="\r")
    console.print(f"CPU peak {units.temperature(result['cpu_temp_peak_c'], t)} · GPU peak "
                  f"{units.temperature(result['gpu_temp_peak_c'], t)} · GPU peak power {units.power(result['gpu_power_peak_w'])}")
    for finding in result["findings"]:
        console.print(f"  • {finding}")


def cmd_power(args):
    try:
        if args.action == "list":
            for plan in power.list_plans():
                console.print(f"{'*' if plan['active'] else ' '} {plan['name']:<28} {plan['guid']}")
            console.print(f"Power mode: {power.get_power_mode()}")
            status = power.TweakManager(home_dir()).status()
            for name, tweak in status["tweaks"].items():
                console.print(f"  {name:<20} plugged in {tweak.get('ac')}  battery {tweak.get('dc')}"
                              f"{'  (changed by OMNI)' if tweak.get('changed') else ''}")
        elif args.action == "plan":
            target = " ".join(args.values).lower()
            plan = next((p for p in power.list_plans() if target in (p["guid"], p["name"].lower())), None)
            if not plan:
                sys.exit(f"No power plan called {target!r}")
            console.print(f"Active plan: {power.set_plan(plan['guid'])['name']}")
        elif args.action == "mode":
            console.print(f"Power mode: {power.set_power_mode(args.values[0])}")
        elif args.action == "tweak":
            name, value = args.values
            power.TweakManager(home_dir()).apply(name, int(value))
            console.print(f"{name} on battery set to {value}. Undo with: omni power restore")
        elif args.action == "restore":
            power.TweakManager(home_dir()).restore()
            console.print("Original power settings restored.")
    except (power.PowerError, ValueError) as exc:
        sys.exit(str(exc))


def cmd_clean(args):
    settings = Settings()
    age = settings.get("cleanup", "min_age_hours")
    d = settings.get("units", "data")
    if not args.run:
        table = Table(title=f"Cleanup scan (files older than {age} h)", title_justify="left")
        for col in ("Key", "What", "Files", "Size", "Note"):
            table.add_column(col)
        for row in cleanup.scan(age):
            table.add_row(row["key"], row["label"], str(row["files"]), units.data_size(row["bytes"], d),
                          "" if row["allowed"] else "needs administrator")
        console.print(table)
        console.print("Delete with: omni clean --run user_temp crash_dumps ...")
        return
    report = cleanup.clean(args.run, age)
    console.print(f"Freed {units.data_size(report['deleted_bytes'], d)} from {report['deleted_files']} files; "
                  f"{report['skipped_files']} in-use files skipped.")
    for cat in report["categories"]:
        if "error" in cat:
            console.print(f"  {cat['key']}: {cat['error']}")


def cmd_speedtest(args):
    from omni import nettest

    settings = Settings()
    parts = args.parts or ["latency", "download", "upload"]
    try:
        result = nettest.speed_test(settings.get("network", "speedtest_server"), parts,
                                    settings.get("network", "streams"), settings.get("network", "seconds"),
                                    lambda f, message, live=None: console.print(f"  [{f * 100:5.1f} %] {message:<28}", end="\r"))
    except nettest.NetTestError as exc:
        sys.exit(str(exc))
    console.print(" " * 60, end="\r")
    console.print(f"Server: Cloudflare {result.get('colo') or '?'} ({result.get('country') or '?'})")
    for key, label, unit_ in [("download_mbps", "Download", "Mbps"), ("upload_mbps", "Upload", "Mbps"),
                              ("latency_ms", "Latency", "ms"), ("jitter_ms", "Jitter", "ms"),
                              ("loaded_latency_ms", "Latency under load", "ms")]:
        if result.get(key) is not None:
            console.print(f"  {label:<20} {result[key]} {unit_}")
    if result.get("bufferbloat_grade"):
        console.print(f"  {'Bufferbloat grade':<20} {result['bufferbloat_grade']}")
    if args.diagnostics:
        diag = nettest.diagnostics()
        for row in diag["ping"]:
            console.print(f"  ping {row['name']:<11} {row['latency_ms']} ms  loss {row['loss_pct']} %")
        for row in diag["dns"]:
            console.print(f"  DNS  {row['resolver']:<14} {row['median_ms']} ms{'  (current)' if row.get('current') else ''}")


def cmd_ram(args):
    from omni import memory

    d = Settings().get("units", "data")
    if args.empty:
        try:
            r = memory.run_command(args.empty)
        except memory.MemoryError_ as exc:
            sys.exit(str(exc))
        console.print(f"{r['label']}: done in {r['seconds']} s")
        for key in ("standby", "modified", "free"):
            console.print(f"  {key:<9} {units.data_size(r['before'].get(key), d)} → {units.data_size(r['after'].get(key), d)}")
        return
    ram = memory.MemoryMap()
    time.sleep(1.0)
    m = ram.read()
    table = Table(title="RAM map", show_header=False, title_justify="left")
    rows = [("Total", m["total"]), ("In use", m["active"]), *[(f"  {k.replace('_', ' ')}", v) for k, v in m["active_parts"].items()],
            ("Modified", m["modified"]), ("Standby (cache)", m["standby"]), ("Free", m["free"]), ("Zeroed", m["zeroed"])]
    for label, value in rows:
        table.add_row(label, units.data_size(value, d))
    if m["standby_by_priority"]:
        table.add_row("Standby by priority", " · ".join(f"{i}: {units.data_size(v, d)}" for i, v in enumerate(m["standby_by_priority"])))
    table.add_row("Committed", f"{units.data_size(m['committed'], d)} of {units.data_size(m['commit_limit'], d)}")
    console.print(table)
    console.print("Empty commands (admin): " + ", ".join(memory.COMMANDS) + "  e.g. omni ram --empty standby_low")


def cmd_history(args):
    from omni.history import History

    seconds = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}[args.range]
    text = History(home_dir() / "history.db").export_csv(time.time() - seconds)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        console.print(f"Wrote {args.output}")
    else:
        sys.stdout.write(text)


def cmd_settings(args):
    settings = Settings()
    if args.action == "get":
        print(json.dumps(settings.as_dict(), indent=2))
    elif args.action == "set":
        try:
            group, key = args.key.split(".", 1)
            settings.update({group: {key: args.value}})
        except (ValueError, SettingsError) as exc:
            sys.exit(f"Invalid setting: {exc}")
        console.print(f"{args.key} = {settings.get(group, key)}")
    elif args.action == "reset":
        settings.reset()
        console.print("Settings reset to defaults.")
    console.print(f"[grey50]File: {settings.path}[/grey50]")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omni", description="Laptop telemetry, battery health, benchmarks and power tuning.")
    parser.add_argument("--version", action="version", version=f"OMNI {__version__}")
    sub = parser.add_subparsers(dest="command")

    web = sub.add_parser("web", help="start the web dashboard (default)")
    web.add_argument("--port", type=int)
    web.add_argument("--no-browser", action="store_true")
    web.set_defaults(func=cmd_web)

    sub.add_parser("tui", help="terminal dashboard").set_defaults(func=cmd_tui)

    status = sub.add_parser("status", help="one-shot system summary")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    sub.add_parser("battery", help="battery health report").set_defaults(func=cmd_battery)

    b = sub.add_parser("bench", help="run benchmarks")
    b.add_argument("kind", choices=["cpu", "memory", "disk", "gpu", "all"])
    b.add_argument("--folder", help="folder for the disk test file (default: temp)")
    b.set_defaults(func=cmd_bench)

    s = sub.add_parser("stress", help="stress test with throttling report")
    s.add_argument("target", choices=["cpu", "gpu", "both"])
    s.add_argument("--seconds", type=int, default=120)
    s.set_defaults(func=cmd_stress)

    p = sub.add_parser("power", help="power plans, mode and battery tweaks")
    p.add_argument("action", choices=["list", "plan", "mode", "tweak", "restore"])
    p.add_argument("values", nargs="*", help="plan NAME | mode efficiency/balanced/performance | tweak NAME VALUE")
    p.set_defaults(func=cmd_power)

    c = sub.add_parser("clean", help="scan (default) or delete junk files")
    c.add_argument("--run", nargs="+", metavar="CATEGORY", help="categories to delete")
    c.set_defaults(func=cmd_clean)

    sp = sub.add_parser("speedtest", help="internet speed test (Cloudflare)")
    sp.add_argument("--parts", nargs="+", choices=["latency", "download", "upload"])
    sp.add_argument("--diagnostics", action="store_true", help="also ping 3 networks and compare DNS servers")
    sp.set_defaults(func=cmd_speedtest)

    r = sub.add_parser("ram", help="RAM map (RAMMap-style) and memory list commands")
    r.add_argument("--empty", choices=["working_sets", "modified", "standby", "standby_low", "system_ws"])
    r.set_defaults(func=cmd_ram)

    h = sub.add_parser("history", help="export recorded telemetry as CSV")
    h.add_argument("action", choices=["export"])
    h.add_argument("--range", choices=["1h", "24h", "7d", "30d"], default="24h")
    h.add_argument("-o", "--output")
    h.set_defaults(func=cmd_history)

    st = sub.add_parser("settings", help="show or change settings")
    st.add_argument("action", choices=["get", "set", "reset"])
    st.add_argument("key", nargs="?", help="group.key, e.g. units.temperature")
    st.add_argument("value", nargs="?")
    st.set_defaults(func=cmd_settings)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["web", *(argv or [])])
    if args.command == "settings" and args.action == "set" and (not args.key or args.value is None):
        parser.error("settings set needs a key and a value")
    args.func(args)
