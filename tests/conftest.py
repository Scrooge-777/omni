import pytest


@pytest.fixture(autouse=True)
def omni_home(tmp_path, monkeypatch):
    """Every test gets its own settings/history folder, never the real %APPDATA%\\OMNI."""
    monkeypatch.setenv("OMNI_HOME", str(tmp_path / "omni-home"))
    return tmp_path / "omni-home"
