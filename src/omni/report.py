"""Plain-text health report: one page that answers "is this laptop in good shape?".

Handy when buying or selling a used laptop, or when asking for help: it contains no
account names, file names or network addresses, only hardware facts and readings.
"""

from datetime import datetime

from omni import __version__, units


def verdict(snap: dict, limits: dict | None = None) -> list[dict]:
    """Problems found in a snapshot, worst first. Empty list = healthy.

    Mirrors the dashboard's health banner, so both say the same thing.
    """
    limits = limits or {}
    cpu_limit = limits.get("cpu_temp_c", 95)
    gpu_limit = limits.get("gpu_temp_c", 87)
    low_battery = limits.get("battery_low_pct", 20)
    issues: list[dict] = []

    def add(level: str, text: str) -> None:
        issues.append({"level": level, "text": text})

    cpu, mem, batt = snap.get("cpu", {}), snap.get("memory", {}), snap.get("battery", {})
    cpu_temp = cpu.get("temp_c")
    if cpu_temp is not None:
        if cpu_temp >= cpu_limit:
            add("danger", f"CPU is very hot ({cpu_temp:.0f} °C)")
        elif cpu_temp >= cpu_limit - 10:
            add("warn", f"CPU is running warm ({cpu_temp:.0f} °C)")
    gpu = snap.get("gpu", {})
    gpu_temp = gpu.get("temp_c") if gpu.get("available") else None
    if gpu_temp is not None:
        if gpu_temp >= gpu_limit:
            add("danger", f"GPU is very hot ({gpu_temp:.0f} °C)")
        elif gpu_temp >= gpu_limit - 7:
            add("warn", f"GPU is running warm ({gpu_temp:.0f} °C)")
    if (mem.get("usage_pct") or 0) >= 90:
        add("warn", f"Memory is almost full ({mem['usage_pct']:.0f} %)")
    if batt.get("present"):
        health = batt.get("health_pct")
        if health is not None and health < 70:
            add("danger", f"Battery has lost {100 - health:.0f} % of its capacity")
        elif health is not None and health < 85:
            add("warn", f"Battery health is {health:.0f} %")
        if batt.get("state") == "discharging" and (batt.get("percent") or 100) <= low_battery:
            add("danger", f"Battery is low ({batt['percent']:.0f} %)")
    for disk in snap.get("storage", {}).get("disks", []):
        if disk.get("health") and disk["health"] != "Healthy":
            add("danger", f"Drive {disk.get('name')} reports \"{disk['health']}\"")
    issues.sort(key=lambda i: i["level"] != "danger")
    return issues


def build(snap: dict, profile: dict | None = None, capabilities: list[dict] | None = None,
          settings=None, now: datetime | None = None) -> str:
    """Markdown report for one snapshot. Pure function: no hardware access."""
    profile = profile or {}
    temp_unit = settings.get("units", "temperature") if settings else "C"
    data_unit = settings.get("units", "data") if settings else "metric"
    energy_unit = settings.get("units", "energy") if settings else "Wh"
    limits = settings.as_dict().get("alerts", {}) if settings else {}
    now = now or datetime.now()

    cpu, gpu, mem, batt = snap.get("cpu", {}), snap.get("gpu", {}), snap.get("memory", {}), snap.get("battery", {})
    lines = ["# Laptop health report", f"_OMNI {__version__} · {now:%Y-%m-%d %H:%M}_", ""]

    issues = verdict(snap, limits)
    lines.append("## Summary")
    if issues:
        lines += [f"- {'✖' if i['level'] == 'danger' else '▲'} {i['text']}" for i in issues]
    else:
        lines.append("- ✓ Temperatures, memory, battery and drives are in their normal range.")
    lines.append("")

    lines.append("## Machine")
    name = " ".join(filter(None, [profile.get("vendor"), profile.get("model")]))
    rows = [
        ("Model", name or None),
        ("Type", profile.get("form_factor")),
        ("BIOS", profile.get("bios")),
        ("CPU", cpu.get("name")),
        ("GPU", gpu.get("name") if gpu.get("available") else None),
        ("Memory", units.data_size(mem.get("total"), data_unit) if mem.get("total") else None),
    ]
    lines += [f"- **{k}:** {v}" for k, v in rows if v]
    lines.append("")

    if batt.get("present"):
        lines.append("## Battery")
        rows = [
            ("Health", f"{units.percent(batt.get('health_pct'), 1)} · {batt.get('grade') or 'n/a'}"),
            ("Wear", units.percent(batt.get("wear_pct"), 1) if batt.get("wear_pct") is not None else None),
            ("Charge cycles", batt.get("cycles")),
            ("Full charge capacity", units.energy(batt.get("full_mwh"), energy_unit) if batt.get("full_mwh") else None),
            ("Design capacity", units.energy(batt.get("design_mwh"), energy_unit) if batt.get("design_mwh") else None),
            ("Charge now", f"{units.percent(batt.get('percent'))} ({batt.get('state')})"),
        ]
        lines += [f"- **{k}:** {v}" for k, v in rows if v not in (None, "")]
        lines.append("")

    disks = snap.get("storage", {}).get("disks", [])
    if disks:
        lines.append("## Drives")
        for d in disks:
            wear = f" · wear {d['wear_pct']} %" if d.get("wear_pct") is not None else ""
            lines.append(f"- **{d.get('name')}:** {d.get('health')} · {units.temperature(d.get('temp_c'), temp_unit)}{wear}")
        lines.append("")

    lines.append("## Right now")
    lines += [
        f"- **CPU:** {units.percent(cpu.get('usage_pct'))} · {units.frequency(cpu.get('clock_mhz'))} · {units.temperature(cpu.get('temp_c'), temp_unit)}",
        f"- **Memory:** {units.data_size(mem.get('used'), data_unit)} of {units.data_size(mem.get('total'), data_unit)} ({units.percent(mem.get('usage_pct'))})",
    ]
    if gpu.get("available"):
        lines.append(f"- **GPU:** {units.percent(gpu.get('util_pct'))} · {units.temperature(gpu.get('temp_c'), temp_unit)} · {units.power(gpu.get('power_w'))}")
    lines.append("")

    if capabilities:
        labels = {"ok": "works", "partly": "partly", "needs": "needs setup", "none": "not available"}
        lines.append("## What OMNI can read on this PC")
        lines += [f"- **{c['area']}:** {labels.get(c['status'], c['status'])}" for c in capabilities]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
