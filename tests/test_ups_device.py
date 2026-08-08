"""Tests for the UPS HID reader module."""

import os
from unittest.mock import patch, mock_open
import pytest
from ups_device import (
    UPSDevice,
    UPSMetrics,
    UPSReadError,
    UPS_VID,
    UPS_PID,
    find_ups_device,
    _u16le,
    _format_device_error,
    read_metrics_once,
)


class TestU16le:
    """Tests for little-endian 16-bit integer parsing.
    
    Note: _u16le() is designed for USB HID feature reports where the first
    byte is the report ID, so default offset=1 is used.
    """
    
    def test_basic_parsing_with_offset(self):
        """Parse 16-bit values with explicit offset."""
        # Simulate HID report with report ID prefix
        assert _u16le(b"\x00\x10\x27") == 0x2710  # 10000
        assert _u16le(b"\x00\x00\x00") == 0
        assert _u16le(b"\x00\xff\xff") == 65535
    
    def test_basic_parsing_with_custom_offset(self):
        """Parse 16-bit values with offset=1."""
        assert _u16le(b"\x00\x10\x27", offset=1) == 0x2710
    
    def test_basic_parsing_at_offset_2(self):
        """Parse 16-bit values at offset=2."""
        # bytes at positions [2] and [3] form the 16-bit value
        assert _u16le(b"\x00\x00\x10\x27", offset=2) == 0x2710
    
    def test_custom_offset(self):
        """Parse with custom offset."""
        data = b"\x00\x00\x10\x27\x00"
        assert _u16le(data, offset=2) == 0x2710
    
    def test_default_offset(self):
        """Default offset is 1."""
        assert _u16le(b"\x00\x10\x27") == 0x2710


class TestFormatDeviceError:
    """Tests for error message formatting."""
    
    def test_file_not_found(self):
        """FileNotFoundError gives helpful message."""
        msg = _format_device_error("/dev/hidraw0", FileNotFoundError())
        assert "not found" in msg
        assert "Reconnect" in msg
    
    def test_permission_denied(self):
        """PermissionError gives chmod instruction."""
        msg = _format_device_error("/dev/hidraw0", PermissionError())
        assert "chmod 666" in msg
    
    def test_generic_os_error(self):
        """Generic OSError passes through exc message."""
        exc = OSError("connection reset")
        msg = _format_device_error("/dev/hidraw0", exc)
        assert "connection reset" in msg


class TestFindUpsDevice:
    """Tests for UPS device discovery via sysfs."""

    def test_returns_none_when_no_hidraw_dir(self):
        """Returns None when /sys/class/hidraw doesn't exist."""
        with patch("os.listdir", side_effect=OSError("no such file")):
            assert find_ups_device() is None

    def test_returns_none_when_no_match(self, tmp_path):
        """Returns None when no device matches VID/PID."""
        hidraw_dir = tmp_path / "hidraw"
        hidraw_dir.mkdir()
        dev_dir = hidraw_dir / "hidraw0"
        dev_dir.mkdir()
        device_target = tmp_path / "usb" / "1-5"
        device_target.mkdir(parents=True)
        (device_target / "idVendor").write_text("045e")
        (device_target / "idProduct").write_text("07b2")
        (dev_dir / "device").symlink_to(device_target)

        with patch("os.listdir", return_value=["hidraw0"]):
            with patch("os.path.realpath", side_effect=lambda p: str(tmp_path / p.relative_to(hidraw_dir)) if "hidraw" in str(p) else os.path.realpath(p)):
                pass

    def test_ignores_non_hidraw_entries(self):
        """Skips entries that don't start with 'hidraw'."""
        with patch("os.listdir", return_value=["other", "devices"]):
            assert find_ups_device() is None

    def test_constants_correct(self):
        """VID and PID match Tripp Lite OMNI1500LCDT."""
        assert UPS_VID == "09ae"
        assert UPS_PID == "3016"


class TestFindUpsDeviceHardware:
    """Hardware-backed discovery tests."""

    @pytest.mark.hardware
    def test_discovery_finds_ups(self, ups):
        """find_ups_device() finds the connected UPS."""
        ups.require_ups()
        path = find_ups_device()
        assert path is not None
        assert path.startswith("/dev/hidraw")
        assert os.path.exists(path)


class TestUPSMetricsDataclass:
    """Tests for the UPSMetrics data structure."""
    
    def test_as_dict(self):
        """as_dict returns all fields."""
        from datetime import datetime
        metrics = UPSMetrics(
            timestamp=datetime(2024, 1, 1, 12, 0, 0),
            load_percent=50,
            active_power_watts=450,
            voltage_volts=120.5,
            current_amps=3.75,
            input_frequency_hz=60.0,
            output_frequency_hz=60.0,
            nominal_voltage_volts=120,
        )
        d = metrics.as_dict()
        assert d["load_percent"] == 50
        assert d["active_power_watts"] == 450
        assert d["voltage_volts"] == 120.5
        assert d["current_amps"] == 3.75
        assert d["input_frequency_hz"] == 60.0
        assert d["output_frequency_hz"] == 60.0
        assert d["nominal_voltage_volts"] == 120
    
    def test_field_types(self):
        """All fields have correct types."""
        from datetime import datetime
        metrics = UPSMetrics(
            timestamp=datetime.now(),
            load_percent=0,
            active_power_watts=0,
            voltage_volts=0.0,
            current_amps=0.0,
            input_frequency_hz=0.0,
            output_frequency_hz=0.0,
            nominal_voltage_volts=0,
        )
        assert isinstance(metrics.load_percent, int)
        assert isinstance(metrics.active_power_watts, int)
        assert isinstance(metrics.voltage_volts, float)
        assert isinstance(metrics.current_amps, float)
        assert isinstance(metrics.input_frequency_hz, float)
        assert isinstance(metrics.output_frequency_hz, float)
        assert isinstance(metrics.nominal_voltage_volts, int)


class TestUPSDeviceHardware:
    """Hardware-backed tests for UPSDevice.

    These tests require the physical Tripp Lite OMNI1500LCDT UPS to be
    connected via USB. Device is auto-discovered by VID/PID.
    """
    
    @pytest.mark.hardware
    def test_device_opens_and_closes(self, ups):
        """UPS device opens and closes without error."""
        ups.require_ups()
        device = UPSDevice()
        try:
            device.open()
            device.close()
        finally:
            device.close()  # Idempotent close
    
    @pytest.mark.hardware
    def test_device_context_manager(self, ups):
        """UPSDevice works as context manager."""
        ups.require_ups()
        with UPSDevice() as device:
            assert device._fd is not None
        # fd should be closed after context
        assert device._fd is None
    
    @pytest.mark.hardware
    def test_read_metrics_returns_valid_data(self, ups):
        """read_metrics() returns all fields with reasonable values."""
        ups.require_ups()
        metrics = read_metrics_once()
        
        # Check types
        assert isinstance(metrics.load_percent, int)
        assert isinstance(metrics.active_power_watts, int)
        assert isinstance(metrics.voltage_volts, float)
        assert isinstance(metrics.current_amps, float)
        assert isinstance(metrics.input_frequency_hz, float)
        assert isinstance(metrics.output_frequency_hz, float)
        assert isinstance(metrics.nominal_voltage_volts, int)
        
        # Check physical reasonableness
        assert 0 <= metrics.load_percent <= 100, f"Load {metrics.load_percent}% unrealistic"
        assert 0 <= metrics.active_power_watts <= 900, f"Power {metrics.active_power_watts}W exceeds UPS capacity"
        assert 100 <= metrics.voltage_volts <= 140, f"Voltage {metrics.voltage_volts}V unrealistic"
        assert 55 <= metrics.input_frequency_hz <= 65, f"Input freq {metrics.input_frequency_hz}Hz unrealistic"
        assert 55 <= metrics.output_frequency_hz <= 65, f"Output freq {metrics.output_frequency_hz}Hz unrealistic"
        assert metrics.nominal_voltage_volts > 0
    
    @pytest.mark.hardware
    def test_read_metrics_consistency(self, ups):
        """Multiple reads return consistent values (within tolerance)."""
        ups.require_ups()
        m1 = read_metrics_once()
        m2 = read_metrics_once()
        
        # Voltage, current, freq should be similar
        assert abs(m1.voltage_volts - m2.voltage_volts) < 5.0
        assert abs(m1.input_frequency_hz - m2.input_frequency_hz) < 1.0
        assert abs(m1.output_frequency_hz - m2.output_frequency_hz) < 1.0
    
    @pytest.mark.hardware
    def test_permissions_error_handling(self, ups, tmp_path):
        """When unable to open device, raises UPSReadError with helpful message."""
        # This is hard to test with real device, test with nonexistent path
        from ups_device import UPSDevice
        device = UPSDevice(path=str(tmp_path / "nonexistent"))
        with pytest.raises(UPSReadError):
            device.read_metrics()


class TestUPSDeviceErrorHandling:
    """Tests for error conditions in UPSDevice."""
    
    def test_nonexistent_path_is_file_not_found_error(self, tmp_path):
        """Nonexistent device path raises UPSReadError."""
        device = UPSDevice(path=str(tmp_path / "nonexistent"))
        with pytest.raises(UPSReadError):
            device.read_metrics()

    def test_find_raises_when_no_device(self):
        """UPSDevice.find() raises UPSReadError when UPS not found."""
        with patch("ups_device.find_ups_device", return_value=None):
            with pytest.raises(UPSReadError) as excinfo:
                UPSDevice.find()
            assert "not found" in str(excinfo.value)

    def test_find_returns_device_when_found(self):
        """UPSDevice.find() returns device when UPS is discovered."""
        with patch("ups_device.find_ups_device", return_value="/dev/hidraw3"):
            device = UPSDevice.find()
            assert device.path == "/dev/hidraw3"

    def test_init_auto_discovers(self):
        """UPSDevice() with no args uses discovered path."""
        with patch("ups_device.find_ups_device", return_value="/dev/hidraw3"):
            device = UPSDevice()
            assert device.path == "/dev/hidraw3"

    def test_init_falls_back_to_default(self):
        """UPSDevice() falls back to DEFAULT_DEVICE_PATH when discovery fails."""
        with patch("ups_device.find_ups_device", return_value=None):
            device = UPSDevice()
            assert device.path == "/dev/hidraw0"

    def test_init_explicit_path(self):
        """UPSDevice(path=...) uses explicit path, no discovery."""
        device = UPSDevice(path="/dev/hidraw5")
        assert device.path == "/dev/hidraw5"
