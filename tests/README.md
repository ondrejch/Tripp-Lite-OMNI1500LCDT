# UPS Power Monitor Tests

Test suite for the Tripp Lite OMNI1500LCDT UPS Power Monitor.

Most tests run **without hardware**: discovery parses a fake sysfs tree, and HID
feature reports are faked at the `fcntl.ioctl` boundary. Only tests marked
`hardware` need the UPS plugged in, and those are skipped automatically when no
device is found.

## Prerequisites

- Python 3.9+
- `pip install -r requirements-dev.txt` (pytest, ruff)
- For hardware tests: the UPS connected via USB, with read access to its
  hidraw node (see the udev rule in the top-level README)

## Running Tests

```bash
pytest                  # everything; hardware tests skip if no UPS
pytest -m "not hardware"  # unit tests only
pytest -m hardware      # only tests that need the UPS attached
pytest -v               # verbose
```

## Test Layout

| Test File | Description | UPS Required |
|-----------|-------------|--------------|
| `test_ups_device.py` | Discovery, report decoding, error handling, reconnects | only `hardware` tests |
| `test_ups_status.py` | CLI formatting, flags, exit codes | only the end-to-end smoke test |
| `test_ups_poller.py` | Background polling thread (samples, errors, shutdown) | no |
| `test_ups_theme.py` | Threshold colours and unit-aware captions | no |
| `conftest.py` | Shared fixtures, automatic hardware-test skipping | — |

## Markers

- `@pytest.mark.hardware` — test needs the physical UPS; skipped automatically
  when `find_ups_device()` returns nothing.

## Notes

- No `sys.path` manipulation is needed: `pytest.ini` sets `pythonpath = .`.
- Discovery tests build a throwaway sysfs tree under `tmp_path` and pass it to
  `find_ups_device(sysfs_dir=...)`, so they do not depend on the real machine.
- Decoding tests monkeypatch `ups_device.fcntl.ioctl`, so the report-ID map and
  unit scaling are covered without a UPS attached.
