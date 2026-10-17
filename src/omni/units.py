"""Metric unit formatting shared by the CLI, TUI and reports.

Internal values are always SI base-derived: degrees Celsius, watts, milliwatt-hours
(as reported by Windows), bytes and bytes per second.
"""

SI_PREFIXES = ["B", "kB", "MB", "GB", "TB", "PB"]
IEC_PREFIXES = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]


def kelvin_tenths_to_c(value: float) -> float:
    """ACPI reports temperature in tenths of a kelvin."""
    return value / 10.0 - 273.15


def temperature(celsius, unit: str = "C", digits: int = 0) -> str:
    if celsius is None:
        return "—"
    if unit == "F":
        return f"{celsius * 9 / 5 + 32:.{digits}f} °F"
    return f"{celsius:.{digits}f} °C"


def data_size(num_bytes, system: str = "si", digits: int = 1) -> str:
    if num_bytes is None:
        return "—"
    base, prefixes = (1024, IEC_PREFIXES) if system == "iec" else (1000, SI_PREFIXES)
    value = float(num_bytes)
    for prefix in prefixes:
        if abs(value) < base or prefix == prefixes[-1]:
            return f"{value:.0f} {prefix}" if prefix == "B" else f"{value:.{digits}f} {prefix}"
        value /= base
    return f"{value:.{digits}f} {prefixes[-1]}"


def data_rate(bytes_per_second, system: str = "si") -> str:
    if bytes_per_second is None:
        return "—"
    return data_size(bytes_per_second, system) + "/s"


def energy(milliwatt_hours, unit: str = "Wh") -> str:
    if milliwatt_hours is None:
        return "—"
    if unit == "mWh":
        return f"{milliwatt_hours:,.0f} mWh"
    return f"{milliwatt_hours / 1000:.1f} Wh"


def power(watts, digits: int = 1) -> str:
    return "—" if watts is None else f"{watts:.{digits}f} W"


def frequency(mhz) -> str:
    if mhz is None:
        return "—"
    return f"{mhz / 1000:.2f} GHz" if mhz >= 1000 else f"{mhz:.0f} MHz"


def percent(value, digits: int = 0) -> str:
    return "—" if value is None else f"{value:.{digits}f} %"


def duration(seconds) -> str:
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"
