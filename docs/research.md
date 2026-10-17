# Research notes (October 2026)

What existing tools do, what the Windows APIs actually provide on the target laptop
(HP OMEN 16-am0xxx, i7-14650HX, RTX 5050 Laptop GPU), and how OMNI 2.0 uses that.

## Comparable tools

| Tool | What it does well | Lesson for OMNI |
|---|---|---|
| [G-Helper](https://github.com/seerge/g-helper) (ASUS) | Single small exe, no background services. Silent/Balanced/Turbo modes, GPU modes, battery charge limit re-applied at login because the BIOS resets it | Keep it light; re-apply settings that firmware resets |
| [OmenMon](https://github.com/n-elia/OmenMon) (HP OMEN) | Talks to the embedded controller and the HP WMI BIOS for temperatures and fan control, replacing Omen Hub | Fan control is model-specific (see below). OMNI leaves it out rather than poking EC registers |
| [Battery-Health-Checker](https://github.com/LotfiBoukhemerra/Battery-Health-Checker), [BatteryMaster](https://batterymaster.weero.net/) | Design vs full-charge capacity, wear, live charge/discharge power, alerts | Show health as full ÷ design, track it over time |
| HWiNFO, [LibreHardwareMonitor](https://github.com/topics/battery-monitor) | Deep sensor coverage | OMNI focuses on laptop-relevant sensors and explains them |

HP OMEN fan control is board- and BIOS-specific. Linux `hp-wmi` patches still add board
IDs one by one ([Phoronix](https://www.phoronix.com/news/HP-Victus-S-Linux-Fan-Control)),
and fans read 0 RPM on some 16-inch boards
([kernel bug report](https://lkml.iu.edu/2602.3/05799.html)). Fan control was therefore
left out of 2.0.

## Battery

- Health = full charge capacity ÷ design capacity. Above roughly 80 % after a year or two
  is normal ageing
  ([How-To Geek](https://www.howtogeek.com/217010/how-to-generate-a-battery-health-report-on-windows-8-or-windows-10/),
  [Tom's Hardware](https://www.tomshardware.com/how-to/battery-health-report-windows)).
- `powercfg /batteryreport` lists design capacity, full charge capacity and cycle count.
- On this laptop `root/wmi BatteryStaticData` is **empty**, so the design capacity is
  read from `powercfg /batteryreport /xml` (83,028 mWh) and cached for a day.
- The other `root/wmi` classes work:
  - `BatteryFullChargedCapacity`: 80,492 mWh
  - `BatteryCycleCount`: 32
  - `BatteryStatus`: remaining capacity, charge/discharge rate in mW, voltage

## Power

- Classic power plans: `PowerGetActiveScheme`, plus `PowerRead/WriteDCValueIndex`
  ([Microsoft docs](https://learn.microsoft.com/en-us/windows/win32/api/powersetting/nf-powersetting-powersetactivescheme)).
- `PERFBOOSTMODE` is hidden. `powercfg /query` rejects it, but the API reads and writes it.
- The Windows 11 power mode slider is an overlay scheme, set with
  `PowerSetActiveOverlayScheme` and read with `PowerGetEffectiveOverlayScheme`
  ([PowerShell Gallery example](https://www.powershellgallery.com/packages/ARC/0.0.1/Content/Public%5COS%5CPower%5CSet-PowerMode.ps1)).
  The GUIDs:
  - best efficiency: `961cc777-…`
  - balanced: all zeros
  - best performance: `ded574b5-…`
- Windows only honours the slider on the Balanced plan
  ([Windows Central](https://windowscentral.com/how-change-power-modes-performance-or-battery-windows-11)).
- **PCIe ASPM** is deliberately not touched. On this laptop it was the cause of NVMe drops
  (WHEA Event 17).

## GPU (NVML)

- NVML reports clock-event reasons as a bitmask
  ([NVIDIA NVML reference](https://docs.nvidia.com/deploy/nvml-api/group__nvmlClocksEventReasons.html)).
  The bits used:
  - 0x1 idle
  - 0x4 software power cap
  - 0x8 hardware slowdown
  - 0x20 software thermal
  - 0x40 hardware thermal
  - 0x80 power brake
- Newer drivers name these "ClocksEvent" and deprecate "ClocksThrottle". OMNI tries both
  names.
- The power-cap bit is set even at idle, so limits are only reported above 20 % load.
- Every NVML or `nvidia-smi` query wakes a sleeping Optimus dGPU. OMNI therefore skips GPU
  polling on battery by default.

## CPU

- psutil's `cpu_freq()` on Windows returns the base clock (2,200 MHz here). The live clock
  is base × `\Processor Information(_Total)\% Processor Performance`, read through PDH
  in-process.
- Temperature: `\Thermal Zone Information(*)\High Precision Temperature`, in tenths of a
  kelvin. This laptop exposes one ACPI zone (TZ01).
- The i7-14650HX has 16 cores (8 performance + 8 efficiency) and 24 threads. Multi-core
  scaling over single-core is well below 24× because of efficiency cores, hyper-threading
  and the all-core clock drop. OMNI measured 6.1×.

## Measured on this laptop (OMNI 2.0)

| Test | Result |
|---|---|
| CPU | 8,598 single / 52,544 multi (k iterations/s), 6.1× scaling |
| Memory | 18.1 GB/s copy (read + write), 7.0 GB/s write |
| SSD (Samsung MZVL81T0HELB, 1 TB NVMe) | 2.4 GB/s sequential read, 750 MB/s write (unbuffered, QD1), 15.9k IOPS random 4K, 0.06 ms |
| GPU | 12.8 TFLOPS FP32 |
| GPU stress (12 s) | 66 W, 67 °C, no limits hit |
| Battery | 96.9 % health, 32 cycles |
