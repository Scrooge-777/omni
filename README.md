# OMNI

A laptop telemetry and tuning tool for Windows: live hardware dashboard, battery health,
real benchmarks, stress tests with throttling detection, safe cleanup and reversible
power tweaks. A ground-up rewrite of [ubat](https://github.com/Scrooge-777/ubat) (OMNI 1.x).

- **One engine, three interfaces:** web dashboard, terminal dashboard and command line all
  use the same sensor collector.
- **Metric everywhere:** °C, W, Wh, GB and MB/s by default. Fahrenheit, binary sizes (GiB)
  and mWh are options in Settings.
- **No made-up numbers:** values come from the hardware. When a sensor isn't available
  the UI says so instead of showing a placeholder value.

## Quick start

```powershell
git clone https://github.com/Scrooge-777/omni
cd omni
pip install -e .
omni            # opens the web dashboard at http://localhost:5050
```

Or double-click `OMNI.bat`. Requires Windows 10/11 and Python 3.11+.

| Command | What it does |
|---|---|
| `omni` / `omni web` | Web dashboard (`--port`, `--no-browser`) |
| `omni tui` | Terminal dashboard (`q` quit, `s` sort processes) |
| `omni status [--json]` | One-shot summary |
| `omni battery` | Battery health report |
| `omni device [--json]` | Identify this PC and list what OMNI can read and change on it |
| `omni bench cpu\|memory\|disk\|gpu\|all` | Benchmarks (saved to history) |
| `omni stress cpu\|gpu\|both --seconds 120` | Stress test with throttling report |
| `omni power list` | Plans, Windows power mode, battery tweaks |
| `omni power plan "Balanced"` · `omni power mode efficiency` | Switch plan / power mode |
| `omni power tweak boost_on_battery 0` · `omni power restore` | Change / undo a battery tweak |
| `omni clean` · `omni clean --run user_temp crash_dumps` | Scan / delete junk files |
| `omni speedtest [--diagnostics]` | Internet speed, latency, bufferbloat (+ ping & DNS comparison) |
| `omni ram` · `omni ram --empty standby_low` | RAM map and RAMMap-style Empty commands (admin) |
| `omni history export --range 7d -o week.csv` | Export recorded telemetry |
| `omni settings get` · `omni settings set units.temperature F` | View / change settings |

## What it reads

| Area | Source | Notes |
|---|---|---|
| CPU load, live clock, temperature | psutil + Windows PDH counters (in-process) | Live clock = base × "% Processor Performance", as in Task Manager |
| GPU | NVIDIA NVML (`nvml.dll`) | Load, temp, power vs limit, clocks, VRAM, P-state, and **why** clocks are limited |
| Battery | psutil, `root/wmi` battery classes, `powercfg /batteryreport` | Real design capacity, health %, wear, cycles, charge/discharge W, voltage |
| SSD | Storage reliability counters | Health, temperature, wear |
| Memory, disk I/O, network, processes | psutil | Process CPU % uses Task Manager's scale |
| Watts per component | Windows Energy Meter (RAPL) + NVML + battery discharge | CPU package/cores/iGPU, dGPU, whole laptop on battery |
| Every GPU | DXGI + Windows GPU counters | Intel, AMD, NVIDIA; per-engine load and memory |
| Fans | HP WMI BIOS · Dell Command \| Monitor · HP business BIOS · Lenovo Gaming Zone · Win32_Fan · LibreHardwareMonitor · HWiNFO | RPM on most PCs; Max-fan switch on HP OMEN/Victus |
| RAM map | `NtQuerySystemInformation` memory lists + counters | Standby by priority, modified, free, zeroed |

## Dashboard sections

**Monitor:** Overview (8 live gauges) · CPU (3D cylinder per thread) · Graphics · Power draw ·
Fans & sensors · Battery · Memory & storage · RAM map · Network · Processes.
**Tools:** Internet test · Power plans · Cleanup · Benchmarks (+ stress test) · History.
**App:** Settings. Light and dark themes, animations (can be turned off), works at phone width.

**Any PC:** `omni device` and the Fans & sensors page identify the machine (maker, model, line, BIOS) and shows what OMNI can read and change on it, and which maker app handles the rest.

**Fans on other PCs:** Windows has no standard fan API. Start LibreHardwareMonitor (as administrator)
or HWiNFO with *Shared Memory Support* on, and OMNI shows every fan and sensor they can read.

## Settings

Stored in `%APPDATA%\OMNI\settings.json` (validated; a bad value falls back to its default).

| Group | Settings |
|---|---|
| General | refresh interval, theme, dashboard port, open browser on start |
| Units | temperature °C/°F, data sizes metric (GB) or binary (GiB), battery Wh/mWh |
| GPU | read the GPU, poll GPU on battery (off by default so Optimus can power the GPU down) |
| History | record on/off, sample interval, retention days |
| Alerts | low battery %, charge reminder %, CPU/GPU/SSD temperature limits |
| Battery | design capacity override |
| Cleanup | minimum file age |
| Processes | protected process names |

History, the battery-report cache and power-setting backups live in the same folder.

## Safety

- **Web server:** listens on `127.0.0.1` only. Every API call needs a per-run secret token,
  and requests with a foreign `Host` header are refused (blocks other websites and DNS
  rebinding from driving the dashboard). Strict Content-Security-Policy.
- **Power tweaks** only change on-battery values, save the originals first, and
  `Restore` puts them back. **PCIe ASPM is never touched**: on some NVMe drives it makes
  the SSD drop off the bus (WHEA Event 17).
- **Cleanup** is scan-first, deletes only files older than the age setting, never follows
  links or junctions, skips files in use, and reports only what was actually deleted.
- **Process ending** refuses Windows-critical processes, OMNI itself and anything on the
  protected list.

## Development

```powershell
pip install -e ".[dev]"
pytest
```

Layout:

```
src/omni/
  sensors/      cpu, gpu (NVML), battery, system (memory/storage/network), processes
  win/          PowerShell + PDH counter helpers
  collector.py  polling loop, live buffer, history writes, alerts
  history.py    SQLite store       alerts.py   thresholds with hysteresis
  bench.py      benchmarks + stress tests       power.py    plans, mode, tweaks
  cleanup.py    scan/clean          config.py   settings      units.py   metric formatting
  web/          FastAPI app + static UI (no external CDN, works offline)
  tui.py        rich terminal dashboard         cli.py      command line
```

See [CHANGELOG.md](CHANGELOG.md) for everything that changed from ubat 1.x and
[docs/research.md](docs/research.md) for the research behind the design.

## Licence

MIT, see [LICENSE](LICENSE).
