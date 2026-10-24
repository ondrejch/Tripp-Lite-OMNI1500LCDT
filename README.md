# Tripp Lite OMNI1500LCDT UPS Power Monitor

A Python toolkit to read real-time power consumption data directly from a Tripp Lite OMNI1500LCDT UPS via its USB HID interface, without requiring Network UPS Tools (NUT). Includes a reusable HID reader, a command-line tool, a simple GUI, and an advanced dashboard.

## UPS Specifications

| Spec | Value |
|------|-------|
| Model | Tripp Lite OMNI1500LCDT |
| Capacity | 1500 VA / 900 W |
| Input | 120VAC @ 60Hz (nominal) |
| Output | 120VAC @ 60Hz (nominal) |
| Battery | Internal sealed lead-acid (12V) |
| USB Interface | HID-compliant (VID: 0x09AE, PID: 0x3016) |

## Available Tools

| Tool | Description | Dependencies |
|------|-------------|--------------|
| `ups_device.py` | Shared direct-HID reader used by all apps | Python 3.9+ |
| `ups_theme.py` | Shared palette/thresholds/formatting for the GUIs | none |
| `ups_poller.py` | Background polling thread shared by the GUIs | none |
| `ups_status.py` | Command-line power reader (text or JSON) | Python 3.9+ |
| `ups_monitor_tk.py` | Simple GUI dashboard | Python 3.9+ (tkinter) |
| `ups_monitor_pyqt5.py` | Advanced dashboard with live charts | PyQt5, pyqtgraph |

## Requirements

- Linux with `hidraw` support
- **Python 3.9+** (uses `datetime.timezone`, `str.removeprefix`, `X | None` annotations)
- UPS connected via USB

## Setup

### 1. Connect the UPS via USB

### 2. Verify the device

```bash
lsusb | grep -i tripp
# Should show: Bus 001 Device XX: ID 09ae:3016 Tripp Lite OMNI1500LCDT
```

### 3. Grant access with a udev rule (recommended)

Do **not** `chmod 666` the device node: hidraw is writable, so world-writable
access lets any local user send arbitrary HID reports to the UPS (including
shutdown commands that cut power to this machine). Also, the `/dev/hidrawN`
index changes when the UPS is replugged, so a `chmod` does not survive.

```bash
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="09ae", ATTR{idProduct}=="3016", MODE="0660", GROUP="plugdev", TAG+="uaccess"' \
  | sudo tee /etc/udev/rules.d/99-tripp-lite-ups.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

`TAG+="uaccess"` grants the locally logged-in user access without adding them
to `plugdev`; use whichever fits your system. Replug the USB cable afterwards.

A throwaway alternative for a single session is `sudo chmod 600 /dev/hidrawN`
when your user owns the node.

### 4. Make scripts executable (optional)

```bash
chmod +x *.py
```

### For the PyQt5 dashboard

```bash
# Usually present on Ubuntu desktops:
sudo apt install python3-pyqt5 python3-pyqtgraph
# or, in a virtualenv:
pip install pyqt5 pyqtgraph
```

### Building a Standalone Executable (Optional)

`ups_monitor_pyqt5.py` can be frozen into a single executable for machines of
the same architecture that do not have Python or Qt installed, using PyInstaller:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pyinstaller pyqt5 pyqtgraph
pyinstaller --onefile --windowed --name ups_monitor_pyqt5 ups_monitor_pyqt5.py
./dist/ups_monitor_pyqt5
```

The frozen binary still needs read access to the hidraw node — install the udev
rule above.

---

## Usage

### Option 1: Command line (`ups_status.py`)

```bash
python3 ups_status.py                 # one sample, key=value lines
python3 ups_status.py --json          # one sample as JSON
python3 ups_status.py --interval 2    # stream every 2 s (Ctrl-C to stop)
python3 ups_status.py --interval 1 --count 60 > ups.log
python3 ups_status.py --device /dev/hidraw3
```

**Sample output:**

```
timestamp=2026-10-23T14:22:31.418+00:00
load_percent=22
active_power_watts=162
voltage_volts=122.3
current_amps=1.3
input_frequency_hz=60.2
output_frequency_hz=60.1
nominal_voltage_volts=120
```

Every sample carries an ISO-8601 UTC timestamp, so logs can be concatenated
from several runs. `--json` emits one object per line (JSON Lines), convenient
for `jq`, Prometheus textfile collectors, MQTT bridges, and so on. Exit codes:
`0` success, `1` UPS read error, `2` bad usage.

### Option 2: Simple GUI (`ups_monitor_tk.py`)

```bash
python3 ups_monitor_tk.py
```

Large wattage display, load percentage bar, voltage/current/frequency readings,
colour-coded load warnings with a severity chip, and a status dot that turns red
(with the reason) when the UPS stops answering.

### Option 3: Advanced dashboard (`ups_monitor_pyqt5.py`)

```bash
python3 ups_monitor_pyqt5.py
python3 ups_monitor_pyqt5.py --compact   # ring gauge only, always on top
```

Live power and load history charts (last 5 minutes), a ring gauge for load,
large numeric displays, detailed metric cards, a severity chip, a status pill,
and colour-coded load warnings. `Ctrl+M` toggles compact mode, which hides the
charts and details and keeps the window on top for a small always-visible
readout.

Both GUIs poll in a **worker thread** (`ups_poller.py`) and only drain a queue
from the GUI event loop, so a slow or unresponsive UPS cannot freeze the window.
Reads recover automatically after a replug: the device is re-discovered by
VID/PID whenever the open `/dev/hidrawN` node disappears.

---

## Metrics

| Field | Description |
|-------|-------------|
| `timestamp` | Sample time, ISO-8601 UTC |
| `load_percent` | Load as a percentage of the **apparent** power rating (1500 VA) |
| `active_power_watts` | Real power consumption (W, 900 W rating) |
| `voltage_volts` | Output voltage |
| `current_amps` | Output current |
| `apparent_power_va` | `voltage_volts × current_amps` (what `load_percent` refers to) |
| `input_frequency_hz` | Input AC frequency |
| `output_frequency_hz` | Output AC frequency |
| `nominal_voltage_volts` | Configured nominal voltage |

`load_percent` and `active_power_watts` use different scales (VA vs W), which is
why the GUIs show both, e.g. `162 VA of 1500 VA / 900 W · 162 W real`.

## HID Report IDs

| ID | Purpose | Units |
|----|---------|-------|
| 0x30 | ConfigVoltage | Volts (integer) |
| 0x1E | PercentLoad | Percentage (0-100), relative to VA rating |
| 0x31 | Voltage | Decivolts (0.1 V) |
| 0x46 | Current | Deciamps (0.1 A) |
| 0x47 | ActivePower | Watts |
| 0x19 | Input Frequency | Decihertz (0.1 Hz) |
| 0x1C | Output Frequency | Decihertz (0.1 Hz) |

The scaling factors above were verified against this model. Per the HID Power
Device specification each field carries its own unit exponent; a different
Tripp Lite model may report e.g. ActivePower in 0.1 W. If values look 10× off,
check the report descriptor before changing the parser.

---

## Troubleshooting

### Permission denied

Install the udev rule from [Setup](#3-grant-access-with-a-udev-rule-recommended)
and replug the UPS. The error message names the device node that could not be
opened.

### Device not found

```bash
lsusb | grep -i tripp
ls -la /dev/hidraw*
cat /sys/class/hidraw/hidraw*/device/uevent
```

The reader auto-discovers the UPS by VID/PID (`09ae:3016`), so a `hidrawN`
number change after replug is handled. If nothing matches, the UPS is not
enumerated — replug the USB cable.

### GUI won't start

```bash
python3 -c "import tkinter; print('OK')"
python3 -c "import PyQt5, pyqtgraph; print('OK')"
```

### Tests

```bash
python3 -m pytest                 # unit tests only (hardware tests skip)
python3 -m pytest -m hardware     # only tests that need the UPS attached
```

---

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
python3 -m pytest
```

GitHub Actions runs the lint and test suite on Python 3.9–3.13; tests marked
`hardware` are skipped automatically when no UPS is present.

---

## Screenshots

**Simple GUI (tkinter):**

![Simple GUI (tkinter)](.pics/screenshot-tk.png)

**Advanced Dashboard (PyQt5):**

![Advanced Dashboard (PyQt5)](.pics/screenshot-pyqt5.png)

---

## License

GPL-3.0 — see [LICENSE](LICENSE).
