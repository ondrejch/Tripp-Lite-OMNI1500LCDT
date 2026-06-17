# UPS Power Monitor Tests

Hardware-backed tests for the Tripp Lite OMNI1500LCDT UPS Power Monitor.

## Prerequisites

- Physical Tripp Lite OMNI1500LCDT UPS connected via USB
- `/dev/hidraw0` accessible (run `sudo chmod 666 /dev/hidraw0` if needed)
- pytest installed (`pip install pytest`)

## Running Tests

### All tests (requires UPS connected)
```bash
cd /home/o/tmp/Tripp-Lite-OMNI1500LCDT
pytest
```

### Only unit tests (no UPS required)
```bash
pytest -m "not hardware"
```

### Verbose output
```bash
pytest -v
```

### With coverage
```bash
pytest --cov=ups_device --cov=ups_status -v
```

## Test Categories

| Test File | Description | UPS Required |
|-----------|-------------|--------------|
| `test_ups_device.py` | Core HID reader logic | Yes (hardware marked) |
| `test_ups_status.py` | CLI tool behavior | Yes |
| `conftest.py` | Shared fixtures | N/A |

## Markers

- `@pytest.mark.hardware` - Tests requiring physical UPS connection

Skip hardware tests:
```bash
pytest -m "not hardware"
```

## Sample Test Output

```
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3
collected 15 items

tests/test_ups_device.py::TestU16le::test_basic_parsing PASSED
tests/test_ups_device.py::TestU16le::test_custom_offset PASSED
tests/test_ups_device.py::TestU16le::test_default_offset PASSED
tests/test_ups_device.py::TestFormatDeviceError::test_file_not_found PASSED
tests/test_ups_device.py::TestFormatDeviceError::test_permission_denied PASSED
tests/test_ups_device.py::TestFormatDeviceError::test_generic_os_error PASSED
tests/test_ups_device.py::TestUPSMetricsDataclass::test_as_dict PASSED
tests/test_ups_device.py::TestUPSMetricsDataclass::test_field_types PASSED
tests/test_ups_device.py::TestUPSDeviceHardware::test_device_opens_and_closes PASSED
tests/test_ups_device.py::TestUPSDeviceHardware::test_device_context_manager PASSED
tests/test_ups_device.py::TestUPSDeviceHardware::test_read_metrics_returns_valid_data PASSED
tests/test_ups_device.py::TestUPSDeviceHardware::test_read_metrics_consistency PASSED
tests/test_ups_device.py::TestUPSDeviceHardware::test_permissions_error_handling SKIPPED
tests/test_ups_status.py::test_ups_status_returns_zero_on_success PASSED
tests/test_ups_status.py::test_ups_status_output_format PASSED
tests/test_ups_status.py::test_ups_status_physical_range PASSED

======================== 14 passed, 1 skipped in 0.15s =========================
```
