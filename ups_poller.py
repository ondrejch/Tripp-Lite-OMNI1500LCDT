"""Background polling thread shared by the GUI frontends.

The hidraw ioctls are blocking calls: if the UPS stops answering (battery
mode, firmware hiccup, cable pulled mid-read) a read in the GUI thread freezes
the whole window. This runs the device in a worker thread and hands samples to
the GUI through a queue that the UI drains from its own event loop.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

from ups_device import UPSDevice, UPSMetrics, UPSReadError


@dataclass(frozen=True)
class PollEvent:
    """One poll result: either a sample or an error message."""

    metrics: UPSMetrics | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.metrics is not None


class BackgroundPoller:
    """Poll a :class:`UPSDevice` at a fixed rate on its own thread."""

    def __init__(
        self,
        interval_s: float = 1.0,
        device_factory: Callable[[], UPSDevice] = UPSDevice,
    ) -> None:
        self.interval_s = interval_s
        self._device_factory = device_factory
        self._events: queue.SimpleQueue[PollEvent] = queue.SimpleQueue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="ups-poller", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            self._thread = None

    def drain(self) -> list[PollEvent]:
        """Return every event produced since the last call (non-blocking)."""
        events = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    def _run(self) -> None:
        device: UPSDevice | None = None
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                if device is None:
                    device = self._device_factory()
                metrics = device.read_metrics()
            except UPSReadError as exc:
                # Drop the device so the next round re-discovers it; the UPS
                # gets a new /dev/hidrawN node when it is replugged.
                if device is not None:
                    device.close()
                device = None
                self._events.put(PollEvent(error=str(exc)))
            except Exception as exc:  # noqa: BLE001 - keep the thread alive
                if device is not None:
                    device.close()
                device = None
                self._events.put(PollEvent(error=f"Unexpected error: {exc}"))
            else:
                self._events.put(PollEvent(metrics=metrics))

            elapsed = time.monotonic() - started
            self._stop.wait(max(0.0, self.interval_s - elapsed))

        if device is not None:
            device.close()
