"""Which PC is this, and what can OMNI read and change on it?

OMNI is meant to work on any Windows laptop or desktop. Fan speeds, fan control and
charge limits are vendor-specific (each maker hides them behind its own firmware
interface), so OMNI first identifies the machine, then probes the interfaces that maker
uses and reports, per feature, whether it works here and if not, what would make it work.
"""

import psutil

from omni import win

# Win32_SystemEnclosure.ChassisTypes values that mean a portable machine.
PORTABLE_CHASSIS = {8, 9, 10, 11, 12, 14, 18, 21, 30, 31, 32}

VENDORS = [
    ("hewlett", "HP"), (" hp", "HP"), ("lenovo", "Lenovo"), ("dell", "Dell"), ("alienware", "Dell"),
    ("asus", "ASUS"), ("acer", "Acer"), ("micro-star", "MSI"), (" msi", "MSI"), ("microsoft", "Microsoft"),
    ("samsung", "Samsung"), ("razer", "Razer"), ("gigabyte", "Gigabyte"), ("huawei", "Huawei"),
    ("framework", "Framework"), ("apple", "Apple"), (" lg ", "LG"), ("toshiba", "Dynabook"), ("dynabook", "Dynabook"),
    ("xiaomi", "Xiaomi"), ("medion", "Medion"), ("clevo", "Clevo"), ("tongfang", "Tongfang"),
]

# Gaming / product lines that change what the firmware exposes.
LINES = [
    ("omen", "OMEN"), ("victus", "Victus"), ("legion", "Legion"), (" loq", "LOQ"), ("thinkpad", "ThinkPad"),
    ("ideapad", "IdeaPad"), ("yoga", "Yoga"), ("alienware", "Alienware"), ("xps", "XPS"), ("latitude", "Latitude"),
    ("inspiron", "Inspiron"), ("precision", "Precision"), (" rog", "ROG"), (" tuf", "TUF"), ("zenbook", "Zenbook"),
    ("vivobook", "Vivobook"), ("predator", "Predator"), ("nitro", "Nitro"), ("swift", "Swift"), ("aspire", "Aspire"),
    ("surface", "Surface"), ("elitebook", "EliteBook"), ("probook", "ProBook"), ("pavilion", "Pavilion"),
    ("envy", "ENVY"), ("spectre", "Spectre"), ("zbook", "ZBook"), ("blade", "Blade"), ("aorus", "AORUS"),
    ("katana", "Katana"), ("raider", "Raider"), ("stealth", "Stealth"),
]

IDENTIFY_SCRIPT = r"""
$cs = Get-CimInstance Win32_ComputerSystem
$p = Get-CimInstance Win32_ComputerSystemProduct
$b = Get-CimInstance Win32_BIOS
$bb = Get-CimInstance Win32_BaseBoard
$e = Get-CimInstance Win32_SystemEnclosure | Select-Object -First 1
[PSCustomObject]@{
  manufacturer = $cs.Manufacturer; model = $cs.Model; family = $cs.SystemFamily; pc_type = $cs.PCSystemType
  product_version = $p.Version; sku = $cs.SystemSKUNumber
  bios_vendor = $b.Manufacturer; bios_version = $b.SMBIOSBIOSVersion; bios_date = "$($b.ReleaseDate)"
  board = $bb.Product; chassis = @($e.ChassisTypes)
} | ConvertTo-Json -Compress
"""

# What each maker lets you change, and with which tool. Shown when OMNI can't do it itself.
VENDOR_TOOLS = {
    "HP": [
        ("Fan mode / max fan", "OMNI (OMEN & Victus), or OMEN Gaming Hub"),
        ("Battery charge limit", "BIOS → Adaptive Battery Optimizer, or HP Power Manager on business models"),
        ("Performance mode", "OMEN Gaming Hub → Performance Control"),
    ],
    "Lenovo": [
        ("Fan full speed / modes", "Lenovo Vantage or Legion Toolkit (Legion, LOQ); Fn+Q switches modes"),
        ("Battery charge limit", "Lenovo Vantage → Conservation mode (stops at about 80 %)"),
    ],
    "Dell": [
        ("Thermal mode", "Dell Power Manager / Dell Optimizer, or Alienware Command Center"),
        ("Battery charge limit", "Dell Power Manager → Battery settings → Custom, or the BIOS"),
        ("Fan readings in OMNI", "Install Dell Command | Monitor (free) and OMNI reads the fans"),
    ],
    "ASUS": [
        ("Fan curves & modes", "Armoury Crate or G-Helper (Silent / Balanced / Turbo)"),
        ("Battery charge limit", "MyASUS → Battery Health Charging, or G-Helper"),
    ],
    "Acer": [
        ("Fan control", "NitroSense or PredatorSense"),
        ("Battery charge limit", "Acer Care Center → Battery charge limit (supported models)"),
    ],
    "MSI": [
        ("Cooler Boost (max fan)", "MSI Center or Fn+F8 on many models"),
        ("Battery charge limit", "MSI Center → Battery Master"),
    ],
    "Microsoft": [
        ("Battery charge limit", "Surface UEFI → Enable Battery Limit"),
        ("Fans", "Surface devices don't expose fan control"),
    ],
    "Razer": [("Fan & performance", "Razer Synapse → Performance")],
    "Gigabyte": [("Fan & performance", "Gigabyte Control Center")],
    "Samsung": [("Battery charge limit", "Samsung Settings → Battery Life Extender")],
}


def _match(text: str, table):
    text = f" {text.lower()} "
    return next((label for key, label in table if key in text), None)


def identify() -> dict:
    """Make, model, product line, BIOS and form factor. Cheap enough to run once at start."""
    raw = win.powershell_json(IDENTIFY_SCRIPT, timeout=20) or {}
    manufacturer = (raw.get("manufacturer") or "").strip()
    model = (raw.get("model") or "").strip()
    family = (raw.get("family") or "").strip()
    # Lenovo puts the friendly name ("Legion 5 15ACH6") in the product version, not the model.
    friendly = (raw.get("product_version") or "").strip() if manufacturer.lower().startswith("lenovo") else ""
    chassis = [c for c in raw.get("chassis") or [] if isinstance(c, int)]
    battery = psutil.sensors_battery() is not None
    portable = bool(set(chassis) & PORTABLE_CHASSIS) or raw.get("pc_type") == 2 or battery
    vendor = _match(f"{manufacturer} {model}", VENDORS) or (manufacturer.split()[0] if manufacturer else "Unknown")
    line = _match(f"{model} {family} {friendly}", LINES)
    return {
        "vendor": vendor,
        "manufacturer": manufacturer or None,
        "model": friendly or model or None,
        "model_code": model if friendly else None,
        "family": family or None,
        "line": line,
        "form_factor": "Laptop" if portable else "Desktop",
        "bios": " ".join(filter(None, [raw.get("bios_version"), (raw.get("bios_date") or "")[:10]])) or None,
        "board": raw.get("board"),
        "sku": raw.get("sku"),
    }


def capabilities(device: dict, snap: dict, sources: list[str], admin: bool) -> list[dict]:
    """Per feature: works here (ok) / partly / needs something / not available, and why."""
    cpu, gpu, batt, power = snap.get("cpu", {}), snap.get("gpu", {}), snap.get("battery", {}), snap.get("power", {})
    fans = snap.get("fans", [])
    control = snap.get("fan_control")
    vendor = device.get("vendor")
    fan_sources = sorted({f["source"] for f in fans})
    caps = []

    def add(area, status, detail, how=None):
        caps.append({"area": area, "status": status, "detail": detail, "how": how})

    add("CPU load & clocks", "ok", "Windows performance counters")
    if cpu.get("temp_c") is not None:
        add("CPU temperature", "ok", f"ACPI thermal zone ({', '.join(cpu.get('zones', {})) or 'firmware'})")
    else:
        add("CPU temperature", "needs", "This firmware reports no ACPI temperature",
            "Run LibreHardwareMonitor or HWiNFO and OMNI reads the CPU sensor from it")
    if power.get("cpu_package_w") is not None:
        add("CPU power (watts)", "ok", "Processor energy counters (RAPL) through Windows")
    else:
        add("CPU power (watts)", "none", "Windows exposes no Energy Meter on this CPU")
    if gpu.get("available"):
        add("NVIDIA GPU details", "ok", f"NVIDIA driver (NVML) · {gpu.get('name')}")
    gpus = snap.get("gpus", [])
    add("GPU load (all GPUs)", "ok" if gpus else "none", ", ".join(g["name"] for g in gpus) or "No GPU counters")
    if batt.get("present"):
        detail = f"Health from {batt.get('design_source') or 'unknown source'}; {batt.get('cycles') or '?'} cycles"
        add("Battery health", "ok" if batt.get("health_pct") is not None else "partly", detail)
    else:
        add("Battery health", "none", "No battery (desktop)")
    disks = snap.get("storage", {}).get("disks", [])
    add("SSD health", "ok" if disks else "partly", "Windows storage reliability counters" if disks else "Reading…")

    if fans:
        add("Fan speeds", "ok", f"{len(fans)} fan(s) via {', '.join(fan_sources)}")
    else:
        how = {
            "Dell": "Install Dell Command | Monitor, or run LibreHardwareMonitor / HWiNFO",
            "Lenovo": "Run OMNI as administrator (Legion / LOQ), or use LibreHardwareMonitor / HWiNFO",
            "HP": "Run OMNI as administrator, or use LibreHardwareMonitor / HWiNFO",
        }.get(vendor, "Run LibreHardwareMonitor (as administrator) or HWiNFO with Shared Memory Support")
        add("Fan speeds", "needs", f"No fan sensor found for this {device.get('form_factor', 'PC').lower()}", how)
    if control:
        add("Fan control", "ok", "Maximum fan speed switch (HP BIOS)")
    else:
        tool = dict(VENDOR_TOOLS.get(vendor, [])).get
        add("Fan control", "none", "Not available through OMNI on this model",
            tool("Fan control") or tool("Fan full speed / modes") or tool("Fan curves & modes") or tool("Cooler Boost (max fan)")
            or tool("Fan & performance") or tool("Thermal mode") or "Use the maker's control app")
    add("Power plans & battery tweaks", "ok", "Windows power API")
    if not admin:
        add("Administrator", "partly", "Running without admin rights",
            "Some fan sources and the RAM map's Empty commands need OMNI to run as administrator")
    for source in sources:
        if "unavailable" in source:
            add("Note", "partly", source)
    return caps
