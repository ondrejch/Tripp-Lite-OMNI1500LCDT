#!/usr/bin/env python3
"""Direct USB HID access for the Tripp Lite OMNI1500LCDT UPS."""

from __future__ import annotations

import errno
import fcntl
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

DEFAULT_DEVICE_PATH = "/dev/hidraw0"
HIDRAW_SYSFS_DIR = "/sys/class/hidraw"

UPS_VID = "09ae"
UPS_PID = "3016"

# Nameplate ratings of the OMNI1500LCDT. ``PercentLoad`` reported by the UPS is
# relative to the apparent-power (VA) rating, while ``ActivePower`` is real (W).
UPS_CAPACITY_VA = 1500
UPS_CAPACITY_WATTS = 900

# Size of a HID feature report buffer, including the leading report ID byte.
REPORT_SIZE = 64

# errno values that mean the device node we were using no longer exists.
_RECONNECT_ERRNOS = frozenset({errno.ENOENT, errno.ENODEV, errno.ENXIO})
# Transient errors worth retrying without dropping the file descriptor.
_RETRY_ERRNOS = frozenset({errno.EINTR, errno.EAGAIN, errno.EBUSY})
_READ_ATTEMPTS = 3

_HID_ID_RE = re.compile(r"^HID_ID=[0-9A-Fa-f]+:([0-9A-Fa-f]{8}):([0-9A-Fa-f]{8})$")


class UPSReadError(RuntimeError):
    """Raised when the UPS cannot be read via its hidraw interface."""


def _read_uevent_ids(device_dir: str) -> tuple[str, str] | None:
    """Return ``(vid, pid)`` from a HID device's sysfs ``uevent`` file.

    ``uevent`` contains a single ``HID_ID=bus:vid:pid`` line, which is far
    cheaper to read than walking up the sysfs tree for idVendor/idProduct.
    """
    try:
        with open(os.path.join(device_dir, "uevent"), encoding="utf-8") as handle:
            for line in handle:
                match = _HID_ID_RE.match(line.strip())
                if match:
                    vid, pid = match.groups()
                    return (vid[-4:].lower(), pid[-4:].lower())
    except OSError:
        return None
    return None


def _find_usb_vid_pid(device_path: str) -> tuple[str, str] | None:
    """Walk up sysfs from a device path to find idVendor/idProduct.

    Fallback for kernels without a readable ``uevent`` file. Returns
    ``(vid, pid)`` as lowercase hex strings, or ``None`` if not found.
    """
    current = device_path
    root = os.path.sep
    while True:
        vid_file = os.path.join(current, "idVendor")
        pid_file = os.path.join(current, "idProduct")
        if os.path.isfile(vid_file) and os.path.isfile(pid_file):
            try:
                with open(vid_file, encoding="utf-8") as handle:
                    vid = handle.read().strip().lower()
                with open(pid_file, encoding="utf-8") as handle:
                    pid = handle.read().strip().lower()
            except OSError:
                return None
            return (vid, pid)

        parent = os.path.dirname(current)
        if parent == current or current == root:
            return None
        current = parent


def _hidraw_sort_key(name: str) -> tuple[int, str]:
    """Sort hidraw2 before hidraw10 instead of lexicographically."""
    try:
        return (int(name.removeprefix("hidraw")), name)
    except ValueError:
        return (0, name)


def find_ups_device(sysfs_dir: str | None = None) -> str | None:
    """Scan sysfs for the Tripp Lite UPS by VID/PID.

    Returns the matching ``/dev/hidrawN`` path, or ``None`` if not found.
    ``sysfs_dir`` exists so tests can point at a fake sysfs tree.
    """
    sysfs_dir = sysfs_dir or HIDRAW_SYSFS_DIR
    try:
        entries = os.listdir(sysfs_dir)
    except OSError:
        return None

    matches = []
    for name in sorted(entries, key=_hidraw_sort_key):
        if not name.startswith("hidraw"):
            continue
        device_dir = os.path.join(sysfs_dir, name, "device")
        ids = _read_uevent_ids(device_dir)
        if ids is None:
            ids = _find_usb_vid_pid(os.path.realpath(device_dir))
        if ids == (UPS_VID, UPS_PID):
            matches.append(name)

    if not matches:
        return None
    return f"/dev/{matches[0]}"


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


@dataclass(frozen=True)
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

    @property
    def apparent_power_va(self) -> float:
        """Apparent power, which is what ``load_percent`` is relative to."""
        return self.voltage_volts * self.current_amps

    def as_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "load_percent": self.load_percent,
            "active_power_watts": self.active_power_watts,
            "voltage_volts": self.voltage_volts,
            "current_amps": self.current_amps,
            "apparent_power_va": round(self.apparent_power_va, 1),
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
    """Decode a little-endian 16-bit value from a HID report.

    ``offset`` defaults to 1 because byte 0 of a feature report is the ID.
    """
    if len(data) < offset + 2:
        raise UPSReadError(
            f"short HID report: {len(data)} bytes, need {offset + 2} for a 16-bit value"
        )
    return data[offset] | (data[offset + 1] << 8)


def _format_device_error(path: str, exc: OSError) -> str:
    if isinstance(exc, FileNotFoundError):
        return f"{path} not found. Reconnect the UPS USB cable."
    if isinstance(exc, PermissionError):
        return (
            f"Cannot open {path}. Install the udev rule for the UPS "
            "(see README, 'Permission denied'), then replug the USB cable."
        )
    return f"Unable to read {path}: {exc}"


def _looks_disconnected(exc: BaseException) -> bool:
    cause = exc.__cause__
    return isinstance(cause, OSError) and cause.errno in _RECONNECT_ERRNOS


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
        self._explicit_path = path
        self.path = path or self._discover()
        self._fd: int | None = None

    @classmethod
    def find(cls) -> UPSDevice:
        """Create a UPSDevice pointing to the auto-discovered UPS."""
        return cls()

    @staticmethod
    def _discover() -> str:
        path = find_ups_device()
        if path is None:
            raise UPSReadError(
                f"Tripp Lite UPS (VID={UPS_VID}, PID={UPS_PID}) not found. "
                "Check the USB connection, or pass an explicit device path."
            )
        return path

    def __enter__(self) -> UPSDevice:
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        if self._fd is not None:
            return
        try:
            self._fd = os.open(self.path, os.O_RDONLY)
        except OSError as exc:
            raise UPSReadError(_format_device_error(self.path, exc)) from exc

    def close(self) -> None:
        if self._fd is None:
            return
        try:
            os.close(self._fd)
        finally:
            self._fd = None

    def rediscover(self) -> bool:
        """Re-run discovery after a replug.

        Only meaningful for auto-discovered devices: an explicitly requested
        path is never silently replaced. Returns ``True`` on success.
        """
        if self._explicit_path is not None:
            return False
        self.close()
        try:
            self.path = self._discover()
        except UPSReadError:
            return False
        return True

    def _read_feature(self, report_id: int) -> bytes:
        self.open()
        fd = self._fd
        if fd is None:  # pragma: no cover - open() either sets it or raises
            raise UPSReadError("UPS device is not open")

        buf = bytearray([report_id]) + bytearray(REPORT_SIZE - 1)
        request = _hid_iocgfeature(len(buf))
        for attempt in range(_READ_ATTEMPTS):
            try:
                fcntl.ioctl(fd, request, buf, True)
                return bytes(buf)
            except OSError as exc:
                if exc.errno in _RETRY_ERRNOS and attempt + 1 < _READ_ATTEMPTS:
                    time.sleep(0.02 * (attempt + 1))
                    continue
                self.close()
                raise UPSReadError(_format_device_error(self.path, exc)) from exc
        raise UPSReadError(f"Could not read report 0x{report_id:02x}")  # pragma: no cover

    def read_metrics(self) -> UPSMetrics:
        """Return a single UPS sample in engineering units."""
        try:
            return self._read_metrics()
        except UPSReadError as exc:
            if self._explicit_path is not None or not _looks_disconnected(exc):
                raise
            if not self.rediscover():
                raise
            return self._read_metrics()

    def _read_metrics(self) -> UPSMetrics:
        config_voltage = self._read_feature(self.REPORT_CONFIG_VOLTAGE)[1]
        load_percent = self._read_feature(self.REPORT_LOAD_PERCENT)[1]
        voltage_tenths = _u16le(self._read_feature(self.REPORT_VOLTAGE))
        current_tenths = _u16le(self._read_feature(self.REPORT_CURRENT))
        active_power_watts = _u16le(self._read_feature(self.REPORT_ACTIVE_POWER))
        input_freq_tenths = _u16le(self._read_feature(self.REPORT_INPUT_FREQUENCY))
        output_freq_tenths = _u16le(self._read_feature(self.REPORT_OUTPUT_FREQUENCY))

        return UPSMetrics(
            timestamp=datetime.now(timezone.utc),
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
