"""Tests for the UPS HID reader module."""

from __future__ import annotations

import errno
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from ups_device import (
    UPS_PID,
    UPS_VID,
    UPSDevice,
    UPSMetrics,
    UPSReadError,
    _format_device_error,
    _hid_iocgfeature,
    _u16le,
    find_ups_device,
    read_metrics_once,
)

# Feature-report payloads used by the fake ioctl: report ID -> raw value.
FAKE_REPORTS = {
    0x30: 120,  # nominal voltage, volts
    0x1E: 22,  # load, percent
    0x31: 1223,  # voltage, 0.1 V
    0x46: 13,  # current, 0.1 A
    0x47: 162,  # active power, W
    0x19: 602,  # input frequency, 0.1 Hz
    0x1C: 601,  # output frequency, 0.1 Hz
}


def _fake_ioctl(fd, request, buf, mutate=False):
    """Emulate HIDIOCGFEATURE by writing the payload into ``buf``."""
    report_id = buf[0]
    value = FAKE_REPORTS[report_id]
    buf[0] = report_id
    buf[1] = value & 0xFF
    buf[2] = (value >> 8) & 0xFF
    return 0


def _make_hidraw(
    sysfs: Path,
    name: str,
    vid: str = UPS_VID,
    pid: str = UPS_PID,
    uevent: bool = True,
) -> Path:
    """Create a fake /sys/class/hidraw/hidrawN entry."""
    device_dir = sysfs / name / "device"
    device_dir.mkdir(parents=True)
    if uevent:
        (device_dir / "uevent").write_text(
            f"HID_ID=0003:0000{vid.upper()}:0000{pid.upper()}\n"
            "HID_NAME=Tripp Lite OMNI1500LCDT\n",
            encoding="utf-8",
        )
    return device_dir


class TestU16Le:
    """Little-endian decoding of HID report payloads."""

    def test_parses_at_offset(self):
        assert _u16le(b"\x00\x10\x27") == 0x2710
        assert _u16le(b"\x00\x00\x10\x27", offset=2) == 0x2710
        assert _u16le(b"\x00\xff\xff") == 65535

    def test_short_report_raises_ups_read_error(self):
        with pytest.raises(UPSReadError, match="short HID report"):
            _u16le(b"\x00\x10")


class TestIoctlRequest:
    """The ioctl request number must match hidraw.h's HIDIOCGFEATURE."""

    @pytest.mark.skipif(sys.byteorder != "little", reason="Linux hidraw ioctls are LE")
    def test_hid_iocgfeature_is_iowr_h_7(self):
        # _IOWR('H', 0x07, 64) == 0xC0404807 on little-endian Linux.
        assert _hid_iocgfeature(64) == 0xC0404807


class TestFormatDeviceError:
    """Tests for error message formatting."""

    def test_file_not_found(self):
        msg = _format_device_error("/dev/hidraw0", FileNotFoundError())
        assert "not found" in msg
        assert "Reconnect" in msg

    def test_permission_denied_points_at_udev_not_chmod(self):
        msg = _format_device_error("/dev/hidraw0", PermissionError())
        assert "udev rule" in msg
        assert "chmod" not in msg

    def test_generic_os_error(self):
        msg = _format_device_error("/dev/hidraw0", OSError("connection reset"))
        assert "connection reset" in msg


class TestFindUpsDevice:
    """Discovery of the UPS hidraw node via a fake sysfs tree."""

    def test_returns_none_when_no_sysfs_dir(self, tmp_path):
        assert find_ups_device(str(tmp_path / "missing")) is None

    def test_finds_device_by_uevent_ids(self, tmp_path):
        _make_hidraw(tmp_path, "hidraw3")
        assert find_ups_device(str(tmp_path)) == "/dev/hidraw3"

    def test_ignores_other_vid_pid(self, tmp_path):
        _make_hidraw(tmp_path, "hidraw0", vid="045e", pid="07b2")
        assert find_ups_device(str(tmp_path)) is None

    def test_ignores_non_hidraw_entries(self, tmp_path):
        (tmp_path / "devices").mkdir()
        _make_hidraw(tmp_path, "hidraw1")
        assert find_ups_device(str(tmp_path)) == "/dev/hidraw1"

    def test_sorts_numerically(self, tmp_path):
        _make_hidraw(tmp_path, "hidraw10")
        _make_hidraw(tmp_path, "hidraw2")
        assert find_ups_device(str(tmp_path)) == "/dev/hidraw2"

    def test_falls_back_to_idvendor_walk_when_uevent_missing(self, tmp_path):
        device_dir = _make_hidraw(tmp_path, "hidraw0", uevent=False)
        usb_device = tmp_path / "devices" / "usb1" / "1-1" / "1-1:1.0"
        usb_device.mkdir(parents=True)
        (usb_device.parent / "idVendor").write_text(f"{UPS_VID}\n", encoding="utf-8")
        (usb_device.parent / "idProduct").write_text(f"{UPS_PID}\n", encoding="utf-8")
        device_dir.rmdir()
        device_dir.symlink_to(os.path.relpath(usb_device, device_dir.parent))

        assert find_ups_device(str(tmp_path)) == "/dev/hidraw0"

    def test_returns_none_when_uevent_has_no_hid_id(self, tmp_path):
        device_dir = _make_hidraw(tmp_path, "hidraw0")
        (device_dir / "uevent").write_text("HID_NAME=Tripp Lite\n", encoding="utf-8")
        assert find_ups_device(str(tmp_path)) is None

    def test_constants_correct(self):
        assert UPS_VID == "09ae"
        assert UPS_PID == "3016"


class TestReadMetricsDecode:
    """Decoding of feature reports into engineering units (no hardware)."""

    def test_scaling_and_units(self, monkeypatch):
        monkeypatch.setattr("ups_device.fcntl.ioctl", _fake_ioctl)
        metrics = read_metrics_once("/dev/null")

        assert metrics.load_percent == 22
        assert metrics.active_power_watts == 162
        assert metrics.voltage_volts == pytest.approx(122.3)
        assert metrics.current_amps == pytest.approx(1.3)
        assert metrics.input_frequency_hz == pytest.approx(60.2)
        assert metrics.output_frequency_hz == pytest.approx(60.1)
        assert metrics.nominal_voltage_volts == 120

    def test_timestamp_is_utc_aware(self, monkeypatch):
        monkeypatch.setattr("ups_device.fcntl.ioctl", _fake_ioctl)
        metrics = read_metrics_once("/dev/null")
        assert metrics.timestamp.tzinfo is timezone.utc
        assert abs((datetime.now(timezone.utc) - metrics.timestamp).total_seconds()) < 5

    def test_retries_transient_ioctl_errors(self, monkeypatch):
        calls = {"n": 0}

        def flaky_ioctl(fd, request, buf, mutate=False):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(errno.EAGAIN, "try again")
            return _fake_ioctl(fd, request, buf, mutate)

        monkeypatch.setattr("ups_device.fcntl.ioctl", flaky_ioctl)
        monkeypatch.setattr("ups_device.time.sleep", lambda _seconds: None)
        assert read_metrics_once("/dev/null").load_percent == 22
        assert calls["n"] > 7  # first report was retried


class TestUPSMetricsDataclass:
    """Tests for the UPSMetrics data structure."""

    @pytest.fixture
    def metrics(self) -> UPSMetrics:
        return UPSMetrics(
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            load_percent=50,
            active_power_watts=450,
            voltage_volts=120.5,
            current_amps=3.75,
            input_frequency_hz=60.0,
            output_frequency_hz=60.0,
            nominal_voltage_volts=120,
        )

    def test_as_dict_is_json_friendly(self, metrics):
        data = metrics.as_dict()
        assert data["load_percent"] == 50
        assert data["active_power_watts"] == 450
        assert data["voltage_volts"] == 120.5
        assert data["input_frequency_hz"] == 60.0
        assert datetime.fromisoformat(data["timestamp"]) == metrics.timestamp

    def test_apparent_power_is_volts_times_amps(self, metrics):
        assert metrics.apparent_power_va == pytest.approx(120.5 * 3.75)
        assert metrics.as_dict()["apparent_power_va"] == pytest.approx(451.9)


class TestUPSDeviceErrors:
    """Error handling and lifecycle without hardware."""

    def test_nonexistent_path_raises_ups_read_error(self, tmp_path):
        device = UPSDevice(path=str(tmp_path / "nonexistent"))
        with pytest.raises(UPSReadError, match="not found"):
            device.read_metrics()

    def test_device_is_opened_read_only(self, monkeypatch, tmp_path):
        calls = {}

        def fake_open(path, flags, *args, **kwargs):
            calls["flags"] = flags
            raise OSError(errno.EACCES, "denied")

        monkeypatch.setattr("ups_device.os.open", fake_open)
        device = UPSDevice(path=str(tmp_path / "dev"))
        with pytest.raises(UPSReadError):
            device.open()
        assert calls["flags"] == os.O_RDONLY

    def test_constructor_raises_when_discovery_fails(self, monkeypatch):
        monkeypatch.setattr("ups_device.find_ups_device", lambda *a, **k: None)
        with pytest.raises(UPSReadError, match="not found"):
            UPSDevice()

    def test_find_returns_discovered_device(self, monkeypatch):
        monkeypatch.setattr("ups_device.find_ups_device", lambda *a, **k: "/dev/hidraw3")
        assert UPSDevice.find().path == "/dev/hidraw3"

    def test_explicit_path_is_not_rediscovered(self, tmp_path):
        device = UPSDevice(path=str(tmp_path / "dev"))
        assert device.rediscover() is False
        assert device.path == str(tmp_path / "dev")

    def test_auto_device_reconnects_after_replug(self, monkeypatch):
        found = iter(["/dev/hidraw3", "/dev/hidraw7"])
        monkeypatch.setattr("ups_device.find_ups_device", lambda *a, **k: next(found))
        device = UPSDevice()
        assert device.path == "/dev/hidraw3"

        sample = UPSMetrics(
            timestamp=datetime.now(timezone.utc),
            load_percent=10,
            active_power_watts=90,
            voltage_volts=120.0,
            current_amps=0.8,
            input_frequency_hz=60.0,
            output_frequency_hz=60.0,
            nominal_voltage_volts=120,
        )
        attempts = {"n": 0}

        def flaky_read():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise UPSReadError("/dev/hidraw3 not found") from OSError(errno.ENODEV, "gone")
            return sample

        monkeypatch.setattr(device, "_read_metrics", flaky_read)
        assert device.read_metrics() is sample
        assert device.path == "/dev/hidraw7"
        assert attempts["n"] == 2

    def test_close_is_idempotent(self, tmp_path):
        device = UPSDevice(path=str(tmp_path / "dev"))
        device.close()
        device.close()

    def test_context_manager_closes_fd(self, tmp_path):
        device = UPSDevice(path=str(tmp_path / "dev"))
        with patch("ups_device.os.open", return_value=42), patch("ups_device.os.close"):
            with device as opened:
                assert opened is device
                assert device._fd == 42
        assert device._fd is None


class TestUPSDeviceHardware:
    """Hardware-backed tests for UPSDevice."""

    @pytest.mark.hardware
    def test_discovery_finds_ups(self, ups):
        ups.require_ups()
        path = find_ups_device()
        assert path is not None
        assert path.startswith("/dev/hidraw")
        assert os.path.exists(path)

    @pytest.mark.hardware
    def test_open_and_close(self, ups):
        ups.require_ups()
        device = UPSDevice()
        device.open()
        device.close()
        assert device._fd is None

    @pytest.mark.hardware
    def test_read_metrics_returns_plausible_data(self, ups):
        ups.require_ups()
        metrics = read_metrics_once()

        assert 0 <= metrics.load_percent <= 100
        assert 0 <= metrics.active_power_watts <= 900
        assert 100 <= metrics.voltage_volts <= 140
        assert 55 <= metrics.input_frequency_hz <= 65
        assert 55 <= metrics.output_frequency_hz <= 65
        assert metrics.nominal_voltage_volts > 0

    @pytest.mark.hardware
    def test_reads_are_consistent(self, ups):
        ups.require_ups()
        first = read_metrics_once()
        second = read_metrics_once()
        assert abs(first.voltage_volts - second.voltage_volts) < 5.0
        assert abs(first.input_frequency_hz - second.input_frequency_hz) < 1.0
        assert abs(first.output_frequency_hz - second.output_frequency_hz) < 1.0
