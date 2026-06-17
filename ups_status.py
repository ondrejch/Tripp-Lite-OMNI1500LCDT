#!/usr/bin/env python3
"""Read one UPS sample from a Tripp Lite OMNI1500LCDT over hidraw."""

from __future__ import annotations

import sys

from ups_device import UPSReadError, read_metrics_once


def main() -> int:
    try:
        metrics = read_metrics_once()
    except UPSReadError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"load_percent={metrics.load_percent}")
    print(f"active_power_watts={metrics.active_power_watts}")
    print(f"voltage_volts={metrics.voltage_volts:.1f}")
    print(f"current_amps={metrics.current_amps:.1f}")
    print(f"input_frequency_hz={metrics.input_frequency_hz:.1f}")
    print(f"output_frequency_hz={metrics.output_frequency_hz:.1f}")
    print(f"nominal_voltage_volts={metrics.nominal_voltage_volts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
