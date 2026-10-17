# Changelog

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
