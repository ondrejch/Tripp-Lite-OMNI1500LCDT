"""Tests for the shared GUI theme/formatting helpers."""

from __future__ import annotations

from datetime import datetime, timezone

import ups_theme
from ups_device import UPSMetrics


def _metrics(load: int = 10, watts: int = 90) -> UPSMetrics:
    return UPSMetrics(
        timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        load_percent=load,
        active_power_watts=watts,
        voltage_volts=120.0,
        current_amps=0.75,
        input_frequency_hz=60.0,
        output_frequency_hz=60.0,
        nominal_voltage_volts=120,
    )


def test_severity_thresholds():
    assert ups_theme.severity_color(0) == ups_theme.GREEN
    assert ups_theme.severity_color(ups_theme.LOAD_AMBER_PERCENT - 1) == ups_theme.GREEN
    assert ups_theme.severity_color(ups_theme.LOAD_AMBER_PERCENT) == ups_theme.AMBER
    assert ups_theme.severity_color(ups_theme.LOAD_RED_PERCENT - 1) == ups_theme.AMBER
    assert ups_theme.severity_color(ups_theme.LOAD_RED_PERCENT) == ups_theme.RED


def test_capacity_caption_uses_matching_units():
    caption = ups_theme.capacity_caption(_metrics(load=10, watts=90))
    # 120 V * 0.75 A = 90 VA, matching the 10 % of 1500 VA load reading.
    assert "90 VA of 1500 VA" in caption
    assert "90 W real" in caption


def test_load_warning_watts_matches_threshold():
    assert ups_theme.POWER_WARN_WATTS == 720


def test_power_axis_range_keeps_zero_baseline_and_rating():
    assert ups_theme.power_axis_range(0) == (0.0, 990.0)
    assert ups_theme.power_axis_range(300) == (0.0, 990.0)
    assert ups_theme.power_axis_range(1000) == (0.0, 1100.0)


def test_format_local_time_handles_naive_and_aware():
    naive = datetime(2024, 1, 1, 12, 0, 0)
    assert ups_theme.format_local_time(naive) == "12:00:00"

    aware = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert ups_theme.format_local_time(aware) == aware.astimezone().strftime("%H:%M:%S")
