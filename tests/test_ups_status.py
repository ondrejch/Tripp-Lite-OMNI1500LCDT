"""Tests for the CLI ups_status tool.

The device is monkeypatched, so these tests run without hardware. The text
output format is a stable interface used by shell scripts; it is asserted here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import ups_status
from ups_device import UPSMetrics, UPSReadError


@pytest.fixture
def metrics() -> UPSMetrics:
    return UPSMetrics(
        timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        load_percent=22,
        active_power_watts=162,
        voltage_volts=122.3,
        current_amps=1.3,
        input_frequency_hz=60.2,
        output_frequency_hz=60.1,
        nominal_voltage_volts=120,
    )


class TestFormatMetrics:
    def test_text_output_is_key_value_lines(self, metrics):
        lines = ups_status.format_metrics(metrics).splitlines()
        parsed = dict(line.split("=", 1) for line in lines)

        assert parsed["load_percent"] == "22"
        assert parsed["active_power_watts"] == "162"
        assert parsed["voltage_volts"] == "122.3"
        assert parsed["current_amps"] == "1.3"
        assert parsed["input_frequency_hz"] == "60.2"
        assert parsed["output_frequency_hz"] == "60.1"
        assert parsed["nominal_voltage_volts"] == "120"
        assert datetime.fromisoformat(parsed["timestamp"]) == metrics.timestamp

    def test_json_output_is_one_object(self, metrics):
        payload = json.loads(ups_status.format_metrics(metrics, as_json=True))
        assert payload["load_percent"] == 22
        assert payload["apparent_power_va"] == pytest.approx(159.0)
        assert payload["timestamp"] == metrics.timestamp.isoformat()


class TestMain:
    def test_exit_zero_and_output(self, monkeypatch, capsys, metrics):
        monkeypatch.setattr(ups_status, "read_metrics_once", lambda path=None: metrics)
        assert ups_status.main([]) == 0
        assert "load_percent=22" in capsys.readouterr().out

    def test_json_flag(self, monkeypatch, capsys, metrics):
        monkeypatch.setattr(ups_status, "read_metrics_once", lambda path=None: metrics)
        assert ups_status.main(["--json"]) == 0
        assert json.loads(capsys.readouterr().out)["voltage_volts"] == 122.3

    def test_device_flag_is_passed_through(self, monkeypatch, capsys, metrics):
        seen = {}
        monkeypatch.setattr(
            ups_status,
            "read_metrics_once",
            lambda path=None: seen.update(path=path) or metrics,
        )
        assert ups_status.main(["--device", "/dev/hidraw5"]) == 0
        assert seen["path"] == "/dev/hidraw5"

    def test_read_error_exits_one(self, monkeypatch, capsys):
        def boom(path=None):
            raise UPSReadError("UPS not found")

        monkeypatch.setattr(ups_status, "read_metrics_once", boom)
        assert ups_status.main([]) == 1
        assert "UPS not found" in capsys.readouterr().err

    def test_bad_interval_exits_two(self, capsys):
        assert ups_status.main(["--interval", "-1"]) == 2
        assert "non-negative" in capsys.readouterr().err

    def test_count_stops_stream(self, monkeypatch, capsys, metrics):
        monkeypatch.setattr(ups_status, "read_metrics_once", lambda path=None: metrics)
        monkeypatch.setattr(ups_status.time, "sleep", lambda _s: None)
        assert ups_status.main(["--interval", "0.01", "--count", "3"]) == 0
        assert capsys.readouterr().out.count("load_percent=") == 3


@pytest.mark.hardware
def test_ups_status_cli_against_hardware(ups):
    """Smoke-test the real CLI end to end against the UPS."""
    ups.require_ups()
    repo_dir = Path(__file__).parent.parent
    result = subprocess.run(
        [sys.executable, str(repo_dir / "ups_status.py"), "--json"],
        capture_output=True,
        text=True,
        cwd=str(repo_dir),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert 0 <= payload["load_percent"] <= 100
    assert 100 <= payload["voltage_volts"] <= 140
