"""Shared palette, thresholds and formatting helpers for the UPS GUIs.

Kept free of tkinter/Qt imports so it can be unit tested headlessly.
"""

from __future__ import annotations

from datetime import datetime

from ups_device import UPS_CAPACITY_VA, UPS_CAPACITY_WATTS, UPSMetrics

WINDOW_BG = "#0e121a"
CARD_BG = "#171c28"
CARD_BORDER = "#232b3a"
PANEL_BG = "#101620"
TEXT_PRIMARY = "#f5f7fb"
TEXT_MUTED = "#8e99ad"
GREEN = "#39d98a"
AMBER = "#ffb84d"
RED = "#ff5c7a"
BLUE = "#4cb3ff"

LOAD_AMBER_PERCENT = 50
LOAD_RED_PERCENT = 80

CAPACITY_LABEL = f"{UPS_CAPACITY_VA} VA / {UPS_CAPACITY_WATTS} W"

# Real power at which the load warning colour starts (80 % of 900 W).
POWER_WARN_WATTS = round(UPS_CAPACITY_WATTS * LOAD_RED_PERCENT / 100)

# Type scale, in points, shared by both frontends. Kept to four sizes so the
# dashboards read as one design instead of a pile of labels.
FONT_HERO = 54
FONT_SECONDARY = 32
FONT_VALUE = 18
FONT_LABEL = 10

# Unit text next to a value is drawn at this fraction of the value size.
UNIT_RATIO = 0.5


def power_axis_range(peak_watts: float) -> tuple[float, float]:
    """Zero-based chart range with headroom, never shorter than the rating.

    Auto-ranging the power chart from its minimum value makes a quiet UPS look
    busy and hides how much of the 900 W budget is in use, so the axis always
    starts at zero and always shows the nameplate rating.
    """
    top = max(float(UPS_CAPACITY_WATTS), float(peak_watts)) * 1.1
    return (0.0, round(top, 1))


def severity_color(load_percent: int) -> str:
    """Map a UPS load percentage to a traffic-light colour."""
    if load_percent >= LOAD_RED_PERCENT:
        return RED
    if load_percent >= LOAD_AMBER_PERCENT:
        return AMBER
    return GREEN


def capacity_caption(metrics: UPSMetrics) -> str:
    """Describe the load in the same units the percentage refers to.

    ``PercentLoad`` is relative to the apparent-power rating, so the VA figure
    and the percentage agree; the real-power figure is shown alongside.
    """
    return (
        f"{metrics.apparent_power_va:.0f} VA of {CAPACITY_LABEL}"
        f" · {metrics.active_power_watts} W real"
    )


def format_local_time(timestamp: datetime) -> str:
    """Render a (UTC-aware) sample timestamp as local wall-clock time."""
    if timestamp.tzinfo is None:
        return timestamp.strftime("%H:%M:%S")
    return timestamp.astimezone().strftime("%H:%M:%S")
