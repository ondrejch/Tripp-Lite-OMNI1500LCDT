#!/usr/bin/env python3
"""Direct USB HID access for the Tripp Lite OMNI1500LCDT UPS."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import fcntl
import os

DEFAULT_DEVICE_PATH = "/dev/hidraw0"

UPS_VID = "09ae"
UPS_PID = "3016"


def _find_usb_vid_pid(device_path: str) -> tuple[str, str] | None:
    """Walk up sysfs from a device path to find idVendor/idProduct.

    Returns ``(vid, pid)`` as lowercase hex strings, or ``None`` if not found.
    """
    current = device_path
    root = os.path.sep
    while current != root:
        vid_file = os.path.join(current, "idVendor")
        pid_file = os.path.join(current, "idProduct")
        if os.path.isfile(vid_file) and os.path.isfile(pid_file):
            try:
                with open(vid_file) as f:
                    vid = f.read().strip().lower()
                with open(pid_file) as f:
                    pid = f.read().strip().lower()
                return (vid, pid)
            except OSError:
                return None
        current = os.path.dirname(current)
    return None


def find_ups_device() -> str | None:
    """Scan /sys/class/hidraw/ for the Tripp Lite UPS by VID/PID.

    Returns the matching ``/dev/hidrawN`` path, or ``None`` if not found.
    """
    hidraw_dir = "/sys/class/hidraw"
    try:
        entries = os.listdir(hidraw_dir)
    except OSError:
        return None

    for name in sorted(entries):
        if not name.startswith("hidraw"):
            continue
        device_link = os.path.join(hidraw_dir, name, "device")
        try:
            device_path = os.path.realpath(device_link)
        except OSError:
            continue

        result = _find_usb_vid_pid(device_path)
        if result is None:
            continue

        vid, pid = result
        if vid == UPS_VID and pid == UPS_PID:
            return f"/dev/{name}"

    return None


# Linux hidraw ioctl constants.
_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS = 2
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS
_IOC_WRITE = 1
_IOC_READ = 2


class UPSReadError(RuntimeError):
    """Raised when the UPS cannot be read via its hidraw interface."""


@dataclass(slots=True)
class UPSMetrics:
    """Single UPS sample in host-friendly units."""

    timestamp: datetime
    load_percent: int
    active_power_watts: int
    voltage_volts: float
    current_amps: float
    input_frequency_hz: float
    output_frequency_hz: float
    nominal_voltage_volts: int

    def as_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "load_percent": self.load_percent,
            "active_power_watts": self.active_power_watts,
            "voltage_volts": self.voltage_volts,
            "current_amps": self.current_amps,
            "input_frequency_hz": self.input_frequency_hz,
            "output_frequency_hz": self.output_frequency_hz,
            "nominal_voltage_volts": self.nominal_voltage_volts,
        }


def _ioc(direction: int, type_: str, nr: int, size: int) -> int:
    return (
        (direction << _IOC_DIRSHIFT)
        | (ord(type_) << _IOC_TYPESHIFT)
        | (nr << _IOC_NRSHIFT)
        | (size << _IOC_SIZESHIFT)
    )


def _hid_iocgfeature(length: int) -> int:
    return _ioc(_IOC_READ | _IOC_WRITE, "H", 0x07, length)


def _u16le(data: bytes, offset: int = 1) -> int:
    return data[offset] | (data[offset + 1] << 8)


def _format_device_error(path: str, exc: OSError) -> str:
    if isinstance(exc, FileNotFoundError):
        return f"{path} not found. Reconnect the UPS USB cable."
    if isinstance(exc, PermissionError):
        return f"Cannot open {path}. Run: sudo chmod 666 {path}"
    return f"Unable to read {path}: {exc}"


class UPSDevice:
    """Read metrics from the UPS using cached hidraw file descriptor access."""

    REPORT_CONFIG_VOLTAGE = 0x30
    REPORT_LOAD_PERCENT = 0x1E
    REPORT_VOLTAGE = 0x31
    REPORT_CURRENT = 0x46
    REPORT_ACTIVE_POWER = 0x47
    REPORT_INPUT_FREQUENCY = 0x19
    REPORT_OUTPUT_FREQUENCY = 0x1C

    def __init__(self, path: str | None = None):
        self.path = path or find_ups_device() or DEFAULT_DEVICE_PATH
        self._fd: int | None = None

    @classmethod
    def find(cls) -> "UPSDevice":
        """Create a UPSDevice pointing to the auto-discovered UPS."""
        path = find_ups_device()
        if path is None:
            raise UPSReadError(
                f"Tripp Lite UPS (VID={UPS_VID}, PID={UPS_PID}) not found. "
                "Check USB connection."
            )
        return cls(path=path)

    def __enter__(self) -> "UPSDevice":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        if self._fd is not None:
            return
        try:
            self._fd = os.open(self.path, os.O_RDWR)
        except OSError as exc:
            raise UPSReadError(_format_device_error(self.path, exc)) from exc

    def close(self) -> None:
        if self._fd is None:
            return
        try:
            os.close(self._fd)
        finally:
            self._fd = None

    def _read_feature(self, report_id: int) -> bytes:
        self.open()
        assert self._fd is not None
        buf = bytearray([report_id]) + bytearray(63)
        try:
            fcntl.ioctl(self._fd, _hid_iocgfeature(len(buf)), buf, True)
        except OSError as exc:
            self.close()
            raise UPSReadError(_format_device_error(self.path, exc)) from exc
        return bytes(buf)

    def read_metrics(self) -> UPSMetrics:
        """Return a single UPS sample in engineering units."""
        config_voltage = self._read_feature(self.REPORT_CONFIG_VOLTAGE)[1]
        load_percent = self._read_feature(self.REPORT_LOAD_PERCENT)[1]
        voltage_tenths = _u16le(self._read_feature(self.REPORT_VOLTAGE))
        current_tenths = _u16le(self._read_feature(self.REPORT_CURRENT))
        active_power_watts = _u16le(self._read_feature(self.REPORT_ACTIVE_POWER))
        input_freq_tenths = _u16le(self._read_feature(self.REPORT_INPUT_FREQUENCY))
        output_freq_tenths = _u16le(self._read_feature(self.REPORT_OUTPUT_FREQUENCY))

        return UPSMetrics(
            timestamp=datetime.now(),
            load_percent=load_percent,
            active_power_watts=active_power_watts,
            voltage_volts=voltage_tenths / 10.0,
            current_amps=current_tenths / 10.0,
            input_frequency_hz=input_freq_tenths / 10.0,
            output_frequency_hz=output_freq_tenths / 10.0,
            nominal_voltage_volts=config_voltage,
        )


def read_metrics_once(path: str | None = None) -> UPSMetrics:
    with UPSDevice(path) as device:
        return device.read_metrics()
