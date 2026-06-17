"""Shared pytest fixtures for UPS Power Monitor tests."""

import os
import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "hardware: tests requiring physical UPS")


def pytest_collection_modifyitems(config, items):
    """Skip hardware tests if UPS not available."""
    skip_hardware = pytest.mark.skip(reason="UPS device not available")
    
    # Check if UPS is available before marking tests to skip
    ups_available = os.path.exists("/dev/hidraw0")
    
    if not ups_available:
        for item in items:
            if "hardware" in item.keywords:
                item.add_marker(skip_hardware)


@pytest.fixture(scope="module")
def ups_device_path():
    """Path to the UPS hidraw device."""
    return "/dev/hidraw0"


@pytest.fixture(scope="module")
def ups_available(ups_device_path):
    """Check if UPS device is available."""
    return os.path.exists(ups_device_path)


class UPSHardwareSkipper:
    """Helper to skip tests when UPS not available."""
    
    def __init__(self, ups_available: bool, ups_device_path: str):
        self.ups_available = ups_available
        self.ups_device_path = ups_device_path
    
    def require_ups(self):
        """Raise skip exception if UPS not available."""
        if not self.ups_available:
            pytest.skip(
                f"UPS device {self.ups_device_path} not available. "
                "Connect the Tripp Lite OMNI1500LCDT via USB and ensure /dev/hidraw0 exists."
            )


@pytest.fixture
def ups(ups_available, ups_device_path):
    """Provide UPS availability check for hardware tests."""
    return UPSHardwareSkipper(ups_available, ups_device_path)
