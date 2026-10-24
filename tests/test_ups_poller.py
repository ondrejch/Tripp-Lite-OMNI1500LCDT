"""Tests for the background polling thread."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from ups_device import UPSMetrics, UPSReadError
from ups_poller import BackgroundPoller, PollEvent


def _sample(load: int = 10) -> UPSMetrics:
    return UPSMetrics(
        timestamp=datetime.now(timezone.utc),
        load_percent=load,
        active_power_watts=90,
        voltage_volts=120.0,
        current_amps=0.8,
        input_frequency_hz=60.0,
        output_frequency_hz=60.0,
        nominal_voltage_volts=120,
    )


class FakeDevice:
    """Replays a scripted list of results; raises scripted exceptions."""

    def __init__(self, results: list[object]) -> None:
        self.results = results
        self.closed = False

    def read_metrics(self) -> UPSMetrics:
        result = self.results.pop(0) if self.results else _sample()
        if isinstance(result, Exception):
            raise result
        return result

    def close(self) -> None:
        self.closed = True


def _collect(poller: BackgroundPoller, count: int, timeout: float = 2.0) -> list[PollEvent]:
    events: list[PollEvent] = []
    deadline = time.monotonic() + timeout
    while len(events) < count and time.monotonic() < deadline:
        events.extend(poller.drain())
        time.sleep(0.01)
    return events


def test_emits_samples_in_order():
    device = FakeDevice([_sample(11), _sample(22)])
    poller = BackgroundPoller(interval_s=0.01, device_factory=lambda: device)
    poller.start()
    try:
        events = _collect(poller, 2)
    finally:
        poller.stop()

    loads = [event.metrics.load_percent for event in events if event.ok]
    assert loads[:2] == [11, 22]
    assert all(event.error is None for event in events if event.ok)


def test_device_is_replaced_after_read_error():
    device = FakeDevice([UPSReadError("UPS gone"), _sample(33)])
    created = []

    def factory():
        created.append(device)
        return device

    poller = BackgroundPoller(interval_s=0.01, device_factory=factory)
    poller.start()
    try:
        events = _collect(poller, 2)
    finally:
        poller.stop()

    errors = [event.error for event in events if not event.ok]
    assert errors and "UPS gone" in errors[0]
    assert any(event.ok and event.metrics.load_percent == 33 for event in events)
    assert device.closed  # dropped on error, closed again on shutdown


def test_unexpected_exception_does_not_kill_the_thread():
    device = FakeDevice([RuntimeError("boom"), _sample(44)])
    poller = BackgroundPoller(interval_s=0.01, device_factory=lambda: device)
    poller.start()
    try:
        events = _collect(poller, 2)
    finally:
        poller.stop()

    assert any(event.error and "Unexpected error" in event.error for event in events)
    assert any(event.ok and event.metrics.load_percent == 44 for event in events)


def test_device_factory_failure_is_reported_and_retried():
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise UPSReadError("not found")
        return FakeDevice([_sample(55)])

    poller = BackgroundPoller(interval_s=0.01, device_factory=factory)
    poller.start()
    try:
        events = _collect(poller, 2)
    finally:
        poller.stop()

    assert any(event.error and "not found" in event.error for event in events)
    assert any(event.ok and event.metrics.load_percent == 55 for event in events)


def test_stop_is_idempotent_and_closes_device():
    device = FakeDevice([])
    poller = BackgroundPoller(interval_s=0.01, device_factory=lambda: device)
    poller.start()
    _collect(poller, 1)
    poller.stop()
    poller.stop()
    assert device.closed
