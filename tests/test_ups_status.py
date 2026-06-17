"""Tests for the CLI ups_status tool.

These tests verify the CLI behavior, including exit codes and output format.
"""

import subprocess
import sys
from pathlib import Path


def test_ups_status_returns_zero_on_success(ups):
    """ups_status.py exits with 0 when UPS available."""
    ups.require_ups()
    
    repo_dir = Path(__file__).parent.parent
    result = subprocess.run(
        [sys.executable, str(repo_dir / "ups_status.py")],
        capture_output=True,
        text=True,
        cwd=str(repo_dir),
    )
    
    assert result.returncode == 0
    assert "load_percent=" in result.stdout
    assert "active_power_watts=" in result.stdout
    assert "voltage_volts=" in result.stdout


def test_ups_status_output_format(ups):
    """ups_status.py outputs all required metrics."""
    ups.require_ups()
    
    repo_dir = Path(__file__).parent.parent
    result = subprocess.run(
        [sys.executable, str(repo_dir / "ups_status.py")],
        capture_output=True,
        text=True,
        cwd=str(repo_dir),
    )
    
    assert result.returncode == 0
    
    # Parse output
    lines = result.stdout.strip().split("\n")
    metrics = {}
    for line in lines:
        key, value = line.split("=", 1)
        metrics[key] = value
    
    assert "load_percent" in metrics
    assert "active_power_watts" in metrics
    assert "voltage_volts" in metrics
    assert "current_amps" in metrics
    assert "input_frequency_hz" in metrics
    assert "output_frequency_hz" in metrics
    assert "nominal_voltage_volts" in metrics
    
    # Check value formats
    assert int(metrics["load_percent"]) >= 0
    assert int(metrics["active_power_watts"]) >= 0
    float(metrics["voltage_volts"]) > 0
    float(metrics["current_amps"]) >= 0
    float(metrics["input_frequency_hz"]) > 0
    float(metrics["output_frequency_hz"]) > 0
    int(metrics["nominal_voltage_volts"]) > 0


def test_ups_status_physical_range(ups):
    """ups_status.py returns physically reasonable values."""
    ups.require_ups()
    
    repo_dir = Path(__file__).parent.parent
    result = subprocess.run(
        [sys.executable, str(repo_dir / "ups_status.py")],
        capture_output=True,
        text=True,
        cwd=str(repo_dir),
    )
    
    assert result.returncode == 0
    
    lines = result.stdout.strip().split("\n")
    metrics = {k: v for k, v in (l.split("=", 1) for l in lines)}
    
    # Physical sanity checks
    load = int(metrics["load_percent"])
    power = int(metrics["active_power_watts"])
    voltage = float(metrics["voltage_volts"])
    freq_in = float(metrics["input_frequency_hz"])
    freq_out = float(metrics["output_frequency_hz"])
    
    assert 0 <= load <= 100, f"Load {load}% out of range"
    assert 0 <= power <= 900, f"Power {power}W exceeds UPS capacity"
    assert 100 <= voltage <= 140, f"Voltage {voltage}V out of range"
    assert 55 <= freq_in <= 65, f"Input freq {freq_in}Hz unusual"
    assert 55 <= freq_out <= 65, f"Output freq {freq_out}Hz unusual"
