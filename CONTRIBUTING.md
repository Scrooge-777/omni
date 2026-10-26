# Contributing

Thanks for helping. OMNI is a Windows laptop monitor, so the most useful contributions are fixes and support for more laptop models.

## Set up

```powershell
git clone https://github.com/Scrooge-777/omni
cd omni
pip install -e ".[dev]"
pytest -q
omni web
```

Python 3.11 or newer. The tests use a fake collector, so they run on any machine without special hardware.

## Adding support for your laptop

1. Run `omni device` and open an issue with the **My laptop is not fully supported** template.
2. To add a fan or sensor source yourself, look at `src/omni/sensors/bridges.py` and `src/omni/device.py`. Each source is a small provider that returns readings or says why it cannot.
3. Never make up a number. If a sensor is not available the UI says so.

## Pull requests

- Keep each pull request focused on one change.
- Add or update a test for new behavior.
- Run `pytest -q` before opening it. CI runs the tests on Python 3.11, 3.13 and 3.14.
- Update `CHANGELOG.md` for anything users will notice.
- Dashboard code (`src/omni/web/static`) has no build step and no outside dependencies. Keep it that way, and keep colors as role tokens in `app.css`.

## Safety rules for code that changes the system

Anything that changes Windows settings, deletes files or ends processes must preview first, save the original value, offer a way back, and refuse protected targets. See `power.py`, `cleanup.py` and `sensors/processes.py` for the pattern.
