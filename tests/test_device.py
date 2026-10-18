from omni import device
from omni.sensors.bridges import numeric_fans


def _identify(monkeypatch, raw, battery=True):
    monkeypatch.setattr(device.win, "powershell_json", lambda script, timeout=0: raw)
    monkeypatch.setattr(device.psutil, "sensors_battery", lambda: object() if battery else None)
    return device.identify()


def test_identify_hp_omen(monkeypatch):
    d = _identify(monkeypatch, {"manufacturer": "HP", "model": "OMEN by HP Gaming Laptop 16-am0xxx",
                                "family": "103C_5335M7 HP OMEN", "chassis": [10], "bios_version": "F.12",
                                "bios_date": "2025-03-01T00:00:00"})
    assert d["vendor"] == "HP" and d["line"] == "OMEN" and d["form_factor"] == "Laptop"
    assert d["model"].startswith("OMEN") and d["bios"] == "F.12 2025-03-01"


def test_identify_lenovo_uses_friendly_name(monkeypatch):
    d = _identify(monkeypatch, {"manufacturer": "LENOVO", "model": "82JW", "product_version": "Legion 5 15ACH6H",
                                "chassis": [10]})
    assert d["vendor"] == "Lenovo" and d["model"] == "Legion 5 15ACH6H" and d["model_code"] == "82JW"
    assert d["line"] == "Legion"


def test_identify_desktop_and_unknown(monkeypatch):
    d = _identify(monkeypatch, {"manufacturer": "To Be Filled By O.E.M.", "model": "B550M", "chassis": [3], "pc_type": 1},
                  battery=False)
    assert d["form_factor"] == "Desktop" and d["vendor"] == "To" and d["line"] is None
    d = _identify(monkeypatch, None, battery=False)
    assert d["vendor"] == "Unknown" and d["model"] is None


def test_identify_asus_and_msi(monkeypatch):
    assert _identify(monkeypatch, {"manufacturer": "ASUSTeK COMPUTER INC.", "model": "ROG Strix G614JV"})["line"] == "ROG"
    d = _identify(monkeypatch, {"manufacturer": "Micro-Star International Co., Ltd.", "model": "Katana 15 B13VFK"})
    assert d["vendor"] == "MSI" and d["line"] == "Katana"


def _caps(dev, snap, admin=True, sources=()):
    return {c["area"]: c for c in device.capabilities(dev, snap, list(sources), admin)}


def test_capabilities_full_machine():
    snap = {"cpu": {"temp_c": 70, "zones": {"TZ0": 70}}, "gpu": {"available": True, "name": "RTX 5050"},
            "gpus": [{"name": "RTX 5050"}], "battery": {"present": True, "health_pct": 95, "design_source": "wmi", "cycles": 40},
            "power": {"cpu_package_w": 30}, "storage": {"disks": [{}]},
            "fans": [{"source": "HP BIOS", "rpm": 3000}], "fan_control": {"kind": "hp_max_fan"}}
    caps = _caps({"vendor": "HP", "form_factor": "Laptop"}, snap)
    assert all(c["status"] == "ok" for c in caps.values())
    assert "1 fan(s) via HP BIOS" in caps["Fan speeds"]["detail"]


def test_capabilities_bare_machine_gives_vendor_advice():
    caps = _caps({"vendor": "Dell", "form_factor": "Laptop"}, {}, admin=False, sources=["HP BIOS unavailable: x"])
    assert caps["Fan speeds"]["status"] == "needs" and "Dell Command | Monitor" in caps["Fan speeds"]["how"]
    assert caps["Fan control"]["status"] == "none" and "Alienware" in caps["Fan control"]["how"]
    assert caps["CPU temperature"]["status"] == "needs" and caps["Battery health"]["status"] == "none"
    assert caps["Administrator"]["status"] == "partly" and caps["Note"]["detail"].startswith("HP BIOS unavailable")
    asus = _caps({"vendor": "ASUS"}, {})
    assert "G-Helper" in asus["Fan control"]["how"]
    other = _caps({"vendor": "Nobody"}, {})
    assert other["Fan control"]["how"] == "Use the maker's control app"


def test_numeric_fans_from_cim_sensors():
    rows = [
        {"ElementName": "CPU Fan", "CurrentReading": 245, "BaseUnits": 19, "UnitModifier": 1},
        {"ElementName": "Video Fan", "CurrentReading": 1800, "BaseUnits": 19, "UnitModifier": 0},
        {"ElementName": "CPU Temperature", "CurrentReading": 55, "BaseUnits": 2, "UnitModifier": 0},
        {"Name": "Rear fan", "CurrentReading": None, "BaseUnits": 19},
    ]
    fans = numeric_fans(rows, "Dell Command | Monitor", "Dell")
    assert [(f["name"], f["rpm"]) for f in fans] == [("CPU Fan", 2450), ("Video Fan", 1800)]
    assert fans[0]["source"] == "Dell Command | Monitor"
