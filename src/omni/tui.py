"""Terminal dashboard (rich). Keys: q quit, s toggle process sort, g toggle GPU detail."""

import sys
import time

from rich import box
from rich.console import Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from omni import units
from omni.collector import Collector

BLOCKS = " ▁▂▃▄▅▆▇█"


def spark(values, lo=0.0, hi=None, width=40) -> str:
    values = [v for v in values[-width:] if v is not None]
    if not values:
        return ""
    hi = hi if hi is not None else max(values) or 1
    return "".join(BLOCKS[min(8, max(0, int((v - lo) / ((hi - lo) or 1) * 8)))] for v in values)


def bar(pct, width=20) -> Text:
    pct = max(0.0, min(100.0, pct or 0))
    filled = round(pct / 100 * width)
    style = "red" if pct >= 90 else "yellow" if pct >= 70 else "cyan"
    return Text("█" * filled, style=style) + Text("░" * (width - filled), style="grey35")


class Dashboard:
    def __init__(self, collector: Collector):
        self.c = collector
        self.s = collector.settings

    def temp(self, value):
        return units.temperature(value, self.s.get("units", "temperature"))

    def size(self, value):
        return units.data_size(value, self.s.get("units", "data"))

    def rate(self, value):
        return units.data_rate(value, self.s.get("units", "data"))

    def render(self) -> Layout:
        snap = self.c.latest()
        layout = Layout()
        if not snap:
            layout.update(Panel("Collecting first sample…"))
            return layout
        live = self.c.live_series(120)
        series = lambda key: [p.get(key) for p in live]  # noqa: E731

        cpu, gpu, batt, mem = snap["cpu"], snap["gpu"], snap["battery"], snap["memory"]
        cpu_text = Group(
            Text.assemble(("Load ", "bold"), bar(cpu["usage_pct"]), f" {units.percent(cpu['usage_pct'])}"),
            Text(f"Clock {units.frequency(cpu['clock_mhz'])}   Temp {self.temp(cpu['temp_c'])}"),
            Text(spark(series("cpu_pct"), 0, 100), style="cyan"),
        )
        if gpu.get("asleep") and not gpu.get("paused"):
            gpu_text = Text(f"Sleeping (powered down)   {self.temp(gpu.get('temp_c'))}", style="grey62")
        elif gpu.get("available") and not gpu.get("paused"):
            gpu_text = Group(
                Text.assemble(("Load ", "bold"), bar(gpu.get("util_pct")), f" {units.percent(gpu.get('util_pct'))}"),
                Text(f"{self.temp(gpu.get('temp_c'))}   {units.power(gpu.get('power_w'))}   {units.frequency(gpu.get('clock_mhz'))}"),
                Text("Limited by: " + (", ".join(gpu["limits"]) or "nothing"), style="grey62"),
            )
        else:
            gpu_text = Text("Paused on battery" if gpu.get("paused") else gpu.get("error") or "Not available", style="grey62")
        if batt.get("present"):
            batt_text = Group(
                Text.assemble(("Charge ", "bold"), bar(batt["percent"]), f" {units.percent(batt['percent'])}"),
                Text(f"{batt['state']}   {units.power(abs(batt['power_w'] or 0))}   {units.duration(batt['time_left_s'])}"),
                Text(f"Health {units.percent(batt['health_pct'], 1)} ({batt['grade'] or '—'})   Cycles {batt['cycles'] or '—'}"),
            )
        else:
            batt_text = Text("No battery", style="grey62")
        mem_text = Group(
            Text.assemble(("RAM ", "bold"), bar(mem["usage_pct"]), f" {self.size(mem['used'])} / {self.size(mem['total'])}"),
            Text(f"Disk  read {self.rate(snap['storage']['read_bps'])}  write {self.rate(snap['storage']['write_bps'])}"),
            Text(f"Net   down {self.rate(snap['network']['down_bps'])}  up {self.rate(snap['network']['up_bps'])}"),
        )

        procs = Table(box=box.SIMPLE, expand=True, header_style="bold")
        procs.add_column("Process")
        procs.add_column("PID", justify="right")
        procs.add_column("CPU", justify="right")
        procs.add_column("Memory", justify="right")
        for p in snap["processes"][:12]:
            procs.add_row(p["name"], str(p["pid"]), f"{p['cpu_pct']:.1f} %", self.size(p["mem"]))

        alerts = "  ".join(a["message"] for a in snap.get("alerts", []))
        layout.split_column(
            Layout(Text(f" OMNI {snap['version']}  ·  {cpu['name']}  ·  q quit · s sort ({self.c.process_sort})", style="bold"), size=1),
            Layout(name="top", size=6),
            Layout(name="mid", size=6),
            Layout(Panel(procs, title="Top processes"), name="procs"),
            Layout(Text(alerts or " ", style="yellow"), size=1),  # empty Text would show Layout's placeholder
        )
        layout["top"].split_row(Layout(Panel(cpu_text, title="CPU")), Layout(Panel(gpu_text, title=gpu.get("name") or "GPU")))
        layout["mid"].split_row(Layout(Panel(batt_text, title="Battery")), Layout(Panel(mem_text, title="Memory · disk · network")))
        return layout


def _key():
    if sys.platform != "win32":
        return None
    import msvcrt

    if msvcrt.kbhit():
        return msvcrt.getwch().lower()
    return None


def run():
    collector = Collector()
    collector.start()
    dash = Dashboard(collector)
    try:
        with Live(dash.render(), refresh_per_second=4, screen=True) as live:
            while True:
                key = _key()
                if key == "q":
                    break
                if key == "s":
                    collector.process_sort = "mem" if collector.process_sort == "cpu" else "cpu"
                live.update(dash.render())
                time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        collector.stop()
