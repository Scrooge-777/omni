## What changed and why

## Checklist
- [ ] `pytest -q` passes
- [ ] New behavior has a test (sensor code can use a fake collector, see `tests/test_web.py`)
- [ ] Values come from the hardware: nothing is made up, and a missing sensor says "not available"
- [ ] `CHANGELOG.md` is updated for user-visible changes
