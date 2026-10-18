# Changelog

## 2.3.0 — 2026-10-18: works on any PC, Internet test fixed

- **Your device:** the Fans & sensors page now starts by identifying the PC:
  - It shows the maker, model, product line (OMEN, Legion, ROG, XPS…), laptop or desktop, and the BIOS version.
  - A checklist then shows what OMNI can read and change on this machine: works, partly, needs setup, or not available.
  - When something needs setup, it says what to do for that maker, e.g. Dell Command | Monitor for Dell fans.
  - It lists what each maker lets you change (fan modes, charge limit, performance mode) and in which app.
  - A fold-out shows every fan source OMNI checked and what each one found.
- **More fan sources:**
  - Dell Command | Monitor (Dell, Alienware).
  - HP business BIOS sensors (EliteBook, ProBook, ZBook).
  - Lenovo Legion / LOQ Gaming Zone (experimental).
  - Windows Win32_Fan.
  - These are tried only when the HP OMEN BIOS isn't present. Settings → Sensors can turn them off.
- **Internet test fixed:**
  - Cloudflare limits how many tests a connection can run (HTTP 429, for about an hour). When that happens, OMNI now switches to the nearest of ten download mirrors (Linode, Hetzner, OVH) and says so under the result.
  - The switch works both before the test and in the middle of a download.
  - The old saved server address counts as "auto", so existing settings get the fallback too.
  - A test that was already running is followed instead of showing "already running".

## 2.2.0 — 2026-10-18: continuous charts and a cleaner UI

- **Continuous live charts:**
  - Every live chart now shows a fixed time window that scrolls smoothly, as in Task Manager. Before, the x-axis stretched from when the page was opened, so charts squeezed together the longer OMNI ran.
  - A window switch in the top bar picks **1 / 2 / 5 / 15 minutes**, and the choice is remembered.
  - Time labels sit on round clock times and move with the data, and single-series charts get a soft area fill.
  - The live buffer holds 15 minutes.
- **Icons:**
  - A new inline SVG icon set for every section, the theme button (system / dark / light) and the top-bar stat chips.
  - Every page title has an icon badge.
- **Layout:**
  - Live stat chips (CPU, temperature, watts, fan) in the top bar.
  - At medium widths the sidebar collapses to an icon rail.
  - The RAM map's process list shows the top 10 with a working-set bar, plus "Show all".
  - A link to the GitHub repo sits at the foot of the sidebar.

## 2.1.0 — 2026-10-18: watts, fans, every GPU, RAM map, Internet test, new UI

### New views
- **Power draw:**
  - Live watts per component: CPU package, CPU cores, integrated GPU, the rest of the CPU, and the dedicated GPU.
  - On battery it also shows the whole laptop's draw.
  - Comes with a breakdown donut, a watts-over-time chart and energy used this session (Wh).
  - CPU figures come from Windows' Energy Meter counters (Intel/AMD RAPL), with no driver needed.
- **Fans & sensors:**
  - Spinning fan cards whose speed follows the real RPM, plus a fan-speed chart and a table of every sensor.
  - **HP OMEN / Victus:** fan RPM, BIOS temperature and a Max-fan switch, through the HP WMI BIOS (the calls OmenMon uses).
  - **Any other laptop or desktop:** bridges to **LibreHardwareMonitor / OpenHardwareMonitor** (WMI) and **HWiNFO** (shared memory).
- **Graphics:** every GPU (integrated and dedicated, Intel/AMD/NVIDIA) from Windows' GPU counters, with per-engine load and dedicated/shared memory. Reading these doesn't wake a sleeping Optimus GPU.
- **RAM map** (after Sysinternals RAMMap):
  - Physical memory as in use / modified / standby / free / zeroed, from `NtQuerySystemInformation(SystemMemoryListInformation)`.
  - What "in use" is made of, and the standby list by priority 0–7.
  - Commit and page faults, plus the top processes by working set with a per-process **Trim**.
  - RAMMap's **Empty** commands (working sets, modified list, standby, low-priority standby, system working set), each showing before → after (needs administrator).
- **Internet test:**
  - Speedometer with latency, jitter, download, upload, latency under load and a **bufferbloat grade**, against Cloudflare's nearest server. You choose the parts, the number of streams and the duration.
  - TCP ping and packet loss to Cloudflare, Google and Quad9.
  - **DNS comparison** of your current DNS against 1.1.1.1, 8.8.8.8 and 9.9.9.9, with every server queried the same way.
  - **Wi-Fi details:** signal, RSSI, band, channel, standard, link speed and security.
  - Past results are shown as a trend.

### CPU
- **Every thread** is drawn as a 3D cylinder showing its load, grouped into performance and efficiency cores on hybrid CPUs.
- **Live clocks per thread:** E-cores show speed relative to their own base clock, because Windows only reports the P-core base clock (the E-core figure would otherwise be wrong, e.g. "5 GHz").
- **Package, core and iGPU watts** in a gauge and a chart.

### UI / UX
- **Overview:** 8 animated radial gauges (CPU load, temperature, watts, GPU load, temperature, memory, fans, battery).
- **Animations:**
  - number tweens and smooth gauge/bar transitions
  - view transitions and card entrance animations
  - a liquid battery cell with a wave while charging, and a pulsing live indicator
  - A **Settings → Animations** switch turns them off, and Windows' reduced-motion setting is respected.
- **Layout:**
  - grouped sidebar with icons
  - live stats in the top bar
  - toggle switches
  - axis steps that avoid labels like "3.8k"
  - back/forward and `#view` links work
- New settings groups: Display, Sensors & fans, Internet test.

### Commands
- `omni speedtest [--parts …] [--diagnostics]` and `omni ram [--empty standby_low|…]`.

### Quality
- 43 pytest tests. The speed test runs against a local fake server, so tests use no Internet data.
- The history database upgrades itself with the new columns.

## 2.0.0 — 2026-10-17: full rewrite

OMNI 2.0 replaces ubat / OMNI 1.x (PowerShell scripts + a 2,177-line Python TUI + a
separate web server) with one Python package. Every module was rebuilt; this lists
what each one gained.

### Architecture
- One sensor engine (`Collector`) shared by the web dashboard, terminal dashboard and CLI.
  1.x had three separate copies of the sensor code that disagreed with each other.
- Slow Windows queries (battery details, SSD health) run on their own thread, so live
  readings update every second without stalls.
- Settings page and validated `settings.json` (1.x had no settings).
- Metric units throughout, with °F / GiB / mWh as options.

### Sensors
- **CPU:** the live clock now comes from the Windows PDH counters. psutil only reports the
  fixed 2.2 GHz base clock on Windows. Temperatures are read in-process instead of
  starting a PowerShell process every second.
- **GPU:** NVML directly (no `nvidia-smi` subprocesses). Adds the power limit, VRAM,
  P-state and the reason clocks are limited. The always-on "power cap" bit is ignored at
  idle. Garbage readings from a GPU entering sleep are discarded, and a sleeping GPU is
  labelled "Sleeping".
- **GPU on battery:** NVML is no longer polled on battery by default, so an Optimus GPU
  can power down. 1.x kept waking it.
- **Battery:**
  - The design capacity is read from Windows' battery report. 1.x hardcoded 83,028 mWh,
    so wear % was wrong on every other laptop.
  - Adds cycles, voltage, charge/discharge watts, time to full, and a daily capacity
    history.
  - Health grades (Excellent / Good / Fair / Worn) replace 1.x's "Grade B" for anything
    worse than 10 % wear.
- **No fake fallbacks:** 1.x showed "RTX 5050, 43 °C, 18.2 W", "HP OMEN" and "80 %" when
  detection failed. 2.0 shows that the value is unavailable.
- **SSD:** health, temperature and wear from Windows' storage reliability counters.

### Benchmarks & stress tests
- **CPU multi-core:**
  - Now runs in separate processes. 1.x used Python threads, which the GIL limits to about
    one core, so its "multi-thread" score (3,653) was *lower* than single-thread (5,300).
  - A start barrier makes every worker run for the same time.
  - Measured on the i7-14650HX: 6.1× single-core.
- **Memory:** native memcpy/memset (18 GB/s copy). 1.x measured 4.7 GB/s through
  Python-level copies.
- **Disk:**
  - True unbuffered, write-through I/O on aligned buffers.
  - 1.x's 4K figure of 143k IOPS was served from the Windows file cache. The real figure
    is about 16k IOPS at QD1, 0.06 ms.
  - Adds a sequential write test and p99 latency.
- **GPU:**
  - Reports measured FP32 throughput (12.8 TFLOPS on the RTX 5050 Laptop GPU).
  - Every CUDA call is checked, and the GPU context, memory and module are released
    afterwards (1.x leaked the context).
  - Load is measured, not faked: 1.x reported "100 %" whenever the clock was above
    1.5 GHz.
- **Stress tests:**
  - Can be stopped at any time.
  - Report peak temperatures and power, CPU clock at the start vs the end, and GPU
    limit reasons.
  - Findings are based on the measurements.
- Results are saved and shown as history.

### Power
- **Turbo Boost on battery:** read and written through the Windows power API, which works
  even though Windows hides the setting from `powercfg /query`.
- **Reversible tweaks:** original values are backed up before the first change, and
  **Restore** puts them back (verified on hardware).
- **ASPM:** 1.x set PCIe ASPM to maximum power saving. That can make NVMe drives drop out
  (WHEA Event 17), so 2.0 never changes it.
- **Plan switching** checks that the plan exists and confirms that Windows applied it. 1.x
  reported success without checking.
- **Windows 11 "Power mode" slider:** best efficiency / balanced / best performance.

### Cleanup
- **Scan first:** sizes are shown per category before anything is deleted. 1.x deleted
  all of %TEMP% straight away.
- **Age limit:** only files older than the configured age are deleted (24 h by default).
- **Links:** symlinks and junctions are never followed.
- **Honest totals:** "freed" only counts files that were really deleted. 1.x counted
  locked files that it failed to delete.
- **Windows Update cache:** no longer deleted while the update service may be using it.
  That job is left to Windows Storage Sense.

### Web dashboard
- **New UI:**
  - Sections: Overview, CPU, GPU, Battery, Memory & storage, Network, Processes, Power,
    Cleanup, Benchmarks, History and Settings.
  - Live charts with a crosshair tooltip.
  - Light and dark themes, and works at phone width.
  - No external CDN, so it works offline.
- **Live updates:** pushed over a WebSocket instead of polling.
- **History:** 1 h to 30 days (SQLite, downsampled), plus CSV export.
- **Security:**
  - In 1.x, any website you visited could switch your power plan through
    `/api/power-profile`.
  - 2.0 requires a per-run token on every API call, checks the Host header (blocking DNS
    rebinding) and sends a strict CSP.

### Alerts
- Thresholds with hysteresis, so they don't flicker: CPU/GPU/SSD temperature, low battery,
  and a charge reminder. The reminder only fires while charging, so firmware that already
  holds the charge at 80 % doesn't trigger it.

### Quality
- 24 unit/API tests (pytest) and GitHub Actions CI on Windows.
- MIT `LICENSE` file. 1.x's README said MIT but had no licence file.
