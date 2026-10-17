import json

import pytest

from omni import units
from omni.config import DEFAULTS, Settings, SettingsError


def test_metric_formatting():
    assert units.temperature(45.4) == "45 °C"
    assert units.temperature(100, "F") == "212 °F"
    assert units.data_size(1_500_000) == "1.5 MB"
    assert units.data_size(1024 ** 3, "iec") == "1.0 GiB"
    assert units.data_rate(2_400_000_000) == "2.4 GB/s"
    assert units.energy(83028) == "83.0 Wh"
    assert units.energy(83028, "mWh") == "83,028 mWh"
    assert units.frequency(4561) == "4.56 GHz"
    assert units.frequency(800) == "800 MHz"
    assert units.duration(3 * 3600 + 5 * 60) == "3 h 05 min"
    assert units.kelvin_tenths_to_c(3232) == pytest.approx(50.05)
    assert units.temperature(None) == "—"


def test_settings_defaults_and_persistence(omni_home):
    s = Settings()
    assert s.as_dict() == DEFAULTS
    s.update({"units": {"temperature": "F"}, "general": {"refresh_interval_s": "2"}})
    again = Settings()
    assert again.get("units", "temperature") == "F"
    assert again.get("general", "refresh_interval_s") == 2.0


def test_settings_validation_is_atomic(omni_home):
    s = Settings()
    with pytest.raises(SettingsError):
        s.update({"units": {"temperature": "F"}, "alerts": {"cpu_temp_c": 500}})
    assert s.get("units", "temperature") == "C"  # nothing applied when one value is bad
    with pytest.raises(SettingsError):
        s.update({"nope": {"x": 1}})
    with pytest.raises(SettingsError):
        s.update({"gpu": {"enabled": "maybe"}})


def test_corrupt_or_hand_edited_file_falls_back(omni_home):
    omni_home.mkdir(parents=True, exist_ok=True)
    (omni_home / "settings.json").write_text(json.dumps({
        "units": {"temperature": "K", "data": "iec"}, "general": "broken", "alerts": {"cpu_temp_c": 9999},
    }))
    s = Settings()
    assert s.get("units", "temperature") == "C"
    assert s.get("units", "data") == "iec"
    assert s.get("alerts", "cpu_temp_c") == DEFAULTS["alerts"]["cpu_temp_c"]


def test_protected_processes(omni_home):
    s = Settings()
    s.update({"processes": {"protected": "Foo.exe, bar.EXE ,"}})
    assert s.protected_processes() == {"foo.exe", "bar.exe"}
