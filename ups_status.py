#!/usr/bin/env python3
"""Read UPS samples from a Tripp Lite OMNI1500LCDT over hidraw.

Examples:
    ups_status.py                     # one sample, key=value lines
    ups_status.py --json              # one sample as JSON
    ups_status.py --interval 2        # stream a sample every 2 seconds
    ups_status.py --device /dev/hidraw3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence

from ups_device import UPSMetrics, UPSReadError, read_metrics_once

_FIELDS = (
    "load_percent",
    "active_power_watts",
    "voltage_volts",
    "current_amps",
    "input_frequency_hz",
    "output_frequency_hz",
    "nominal_voltage_volts",
)


def format_metrics(metrics: UPSMetrics, as_json: bool = False) -> str:
    """Render one sample. Text output stays one ``key=value`` per line."""
    if as_json:
        return json.dumps(metrics.as_dict())
    lines = [f"timestamp={metrics.timestamp.isoformat()}"]
    for field in _FIELDS:
        value = getattr(metrics, field)
        if isinstance(value, float):
            value = f"{value:.1f}"
        lines.append(f"{field}={value}")
    return "\n".join(lines)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--device",
        metavar="PATH",
        help="hidraw device to read (default: auto-discover by VID/PID)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit one JSON object per sample"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="keep sampling every SECONDS (default: print one sample and exit)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        metavar="N",
        help="stop after N samples when --interval is used (default: forever)",
    )
    return parser.parse_args(argv)


def _print_once(args: argparse.Namespace) -> None:
    print(format_metrics(read_metrics_once(args.device), args.json), flush=True)


def _stream(args: argparse.Namespace) -> None:
    emitted = 0
    while True:
        _print_once(args)
        emitted += 1
        if args.count and emitted >= args.count:
            return
        time.sleep(args.interval)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.interval < 0:
        print("--interval must be non-negative", file=sys.stderr)
        return 2

    try:
        if args.interval > 0:
            _stream(args)
        else:
            _print_once(args)
    except UPSReadError as exc:
        print(exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    except BrokenPipeError:
        # `ups_status.py | head` closes stdout; exit quietly like other tools.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
