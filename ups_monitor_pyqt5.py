#!/usr/bin/env python3
"""PyQt5 dashboard for continuous OMNI1500LCDT UPS monitoring."""

from __future__ import annotations

from collections import deque
from datetime import datetime
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)
import pyqtgraph as pg

from ups_device import UPSDevice, UPSReadError

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


def severity_color(load_percent: int) -> str:
    if load_percent >= 80:
        return RED
    if load_percent >= 50:
        return AMBER
    return GREEN


class MetricCard(QFrame):
    def __init__(self, title: str, unit: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.value_label = QLabel("--")
        self._build_ui(title, unit)

    def _build_ui(self, title: str, unit: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setFont(QFont("Helvetica", 10))
        title_label.setStyleSheet(f"color: {TEXT_MUTED};")

        self.value_label.setFont(QFont("Helvetica", 20, QFont.Bold))
        self.value_label.setStyleSheet(f"color: {TEXT_PRIMARY};")

        layout.addWidget(title_label)
        layout.addWidget(self.value_label)
        if unit:
            unit_label = QLabel(unit)
            unit_label.setFont(QFont("Helvetica", 10))
            unit_label.setStyleSheet(f"color: {TEXT_MUTED};")
            layout.addWidget(unit_label)

        self.setStyleSheet(
            f"QFrame {{ background-color: {CARD_BG}; border: 1px solid {CARD_BORDER}; border-radius: 10px; }}"
        )

    def set_value(self, value: str, color: str = TEXT_PRIMARY) -> None:
        self.value_label.setText(value)
        self.value_label.setStyleSheet(f"color: {color};")


class UPSDashboard(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.device = UPSDevice()
        self.poll_interval_ms = 1000
        self.history_minutes = deque(maxlen=300)
        self.history_watts = deque(maxlen=300)
        self.history_load = deque(maxlen=300)
        self.start_monotonic: float | None = None
        self.last_update: datetime | None = None

        self.setWindowTitle("Tripp Lite OMNI1500LCDT Monitor")
        self.resize(980, 760)

        self._build_ui()

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_once)
        self.poll_timer.start(self.poll_interval_ms)
        self.poll_once()

    def _build_ui(self) -> None:
        pg.setConfigOption("background", WINDOW_BG)
        pg.setConfigOption("foreground", TEXT_PRIMARY)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        title = QLabel("Tripp Lite OMNI1500LCDT")
        title.setFont(QFont("Helvetica", 20, QFont.Bold))
        title.setStyleSheet(f"color: {TEXT_PRIMARY};")
        subtitle = QLabel("Continuous UPS output monitoring over direct USB HID")
        subtitle.setFont(QFont("Helvetica", 10))
        subtitle.setStyleSheet(f"color: {TEXT_MUTED};")
        root.addWidget(title)
        root.addWidget(subtitle)

        hero_row = QHBoxLayout()
        hero_row.setSpacing(14)

        power_group = self._group_box("CURRENT DRAW")
        power_layout = QVBoxLayout(power_group)
        power_layout.setContentsMargins(18, 16, 18, 16)
        self.power_value = QLabel("--")
        self.power_value.setFont(QFont("Helvetica", 64, QFont.Bold))
        self.power_value.setStyleSheet(f"color: {GREEN};")
        self.power_value.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        power_layout.addWidget(self.power_value)
        power_caption = QLabel("watts")
        power_caption.setFont(QFont("Helvetica", 12))
        power_caption.setStyleSheet(f"color: {TEXT_MUTED};")
        power_layout.addWidget(power_caption)
        hero_row.addWidget(power_group, 1)

        load_group = self._group_box("UPS LOAD")
        load_layout = QVBoxLayout(load_group)
        load_layout.setContentsMargins(18, 16, 18, 16)
        self.load_value = QLabel("--%")
        self.load_value.setFont(QFont("Helvetica", 42, QFont.Bold))
        self.load_value.setStyleSheet(f"color: {BLUE};")
        load_layout.addWidget(self.load_value)
        self.load_bar = QProgressBar()
        self.load_bar.setRange(0, 100)
        self.load_bar.setTextVisible(False)
        self.load_bar.setFixedHeight(14)
        self.load_bar.setStyleSheet(
            f"QProgressBar {{ border: 0; border-radius: 7px; background-color: {PANEL_BG}; }}"
            f"QProgressBar::chunk {{ border-radius: 7px; background-color: {GREEN}; }}"
        )
        load_layout.addWidget(self.load_bar)
        self.load_caption = QLabel("0 W of 900 W capacity")
        self.load_caption.setStyleSheet(f"color: {TEXT_MUTED};")
        load_layout.addWidget(self.load_caption)
        hero_row.addWidget(load_group, 1)

        root.addLayout(hero_row)

        charts_group = self._group_box("LIVE HISTORY")
        charts_layout = QVBoxLayout(charts_group)
        charts_layout.setContentsMargins(12, 16, 12, 12)
        charts_layout.setSpacing(10)

        self.power_plot = pg.PlotWidget()
        self.power_plot.setMinimumHeight(240)
        self.power_plot.showGrid(x=True, y=True, alpha=0.18)
        self.power_plot.setLabel("left", "Power", units="W")
        self.power_plot.setLabel("bottom", "Time", units="min")
        self.power_plot.getAxis("left").setWidth(52)
        self.power_plot.getAxis("bottom").setHeight(34)
        self.power_plot.setMenuEnabled(False)
        self.power_curve = self.power_plot.plot(pen=pg.mkPen(GREEN, width=3))

        self.load_plot = pg.PlotWidget()
        self.load_plot.setMinimumHeight(170)
        self.load_plot.showGrid(x=True, y=True, alpha=0.18)
        self.load_plot.setLabel("left", "Load", units="%")
        self.load_plot.setLabel("bottom", "Time", units="min")
        self.load_plot.getAxis("left").setWidth(52)
        self.load_plot.getAxis("bottom").setHeight(34)
        self.load_plot.setMenuEnabled(False)
        self.load_plot.setYRange(0, 100, padding=0.05)
        self.load_curve = self.load_plot.plot(pen=pg.mkPen(BLUE, width=2))
        self.load_plot.setXLink(self.power_plot)

        charts_layout.addWidget(self.power_plot)
        charts_layout.addWidget(self.load_plot)
        root.addWidget(charts_group, 1)

        metrics_group = self._group_box("DETAILS")
        metrics_layout = QGridLayout(metrics_group)
        metrics_layout.setContentsMargins(12, 16, 12, 12)
        metrics_layout.setHorizontalSpacing(12)
        metrics_layout.setVerticalSpacing(12)

        self.voltage_card = MetricCard("Output Voltage", "V")
        self.current_card = MetricCard("Output Current", "A")
        self.in_freq_card = MetricCard("Input Frequency", "Hz")
        self.out_freq_card = MetricCard("Output Frequency", "Hz")
        self.nominal_card = MetricCard("Nominal Voltage", "V")
        self.update_card = MetricCard("Last Update", "clock")

        cards = [
            self.voltage_card,
            self.current_card,
            self.in_freq_card,
            self.out_freq_card,
            self.nominal_card,
            self.update_card,
        ]
        for index, card in enumerate(cards):
            metrics_layout.addWidget(card, index // 3, index % 3)

        root.addWidget(metrics_group)

        self.statusBar().setStyleSheet(f"QStatusBar {{ background-color: {WINDOW_BG}; color: {TEXT_MUTED}; }}")
        self.statusBar().showMessage("Waiting for UPS data...")

        self.setStyleSheet(
            f"QMainWindow {{ background-color: {WINDOW_BG}; }}"
            f"QGroupBox {{ background-color: {CARD_BG}; border: 1px solid {CARD_BORDER}; border-radius: 12px; margin-top: 12px; font-weight: bold; color: {TEXT_PRIMARY}; }}"
            "QGroupBox::title { left: 12px; top: -6px; padding: 0 4px; }"
        )

    def _group_box(self, title: str) -> QGroupBox:
        box = QGroupBox(title)
        return box

    def poll_once(self) -> None:
        try:
            metrics = self.device.read_metrics()
        except UPSReadError as exc:
            self.statusBar().showMessage(str(exc))
            return

        self.last_update = metrics.timestamp
        color = severity_color(metrics.load_percent)

        self.power_value.setText(str(metrics.active_power_watts))
        self.power_value.setStyleSheet(f"color: {color};")
        self.load_value.setText(f"{metrics.load_percent}%")
        self.load_value.setStyleSheet(f"color: {color};")
        self.load_bar.setValue(metrics.load_percent)
        self.load_bar.setStyleSheet(
            f"QProgressBar {{ border: 0; border-radius: 7px; background-color: {PANEL_BG}; }}"
            f"QProgressBar::chunk {{ border-radius: 7px; background-color: {color}; }}"
        )
        self.load_caption.setText(f"{metrics.active_power_watts} W of 900 W capacity")

        self.voltage_card.set_value(f"{metrics.voltage_volts:.1f}")
        self.current_card.set_value(f"{metrics.current_amps:.1f}")
        self.in_freq_card.set_value(f"{metrics.input_frequency_hz:.1f}")
        self.out_freq_card.set_value(f"{metrics.output_frequency_hz:.1f}")
        self.nominal_card.set_value(str(metrics.nominal_voltage_volts))
        self.update_card.set_value(metrics.timestamp.strftime("%H:%M:%S"), TEXT_MUTED)

        now = time.monotonic()
        if self.start_monotonic is None:
            self.start_monotonic = now
        elapsed_minutes = (now - self.start_monotonic) / 60.0
        self.history_minutes.append(elapsed_minutes)
        self.history_watts.append(metrics.active_power_watts)
        self.history_load.append(metrics.load_percent)
        self._refresh_plots()

        self.statusBar().showMessage(
            f"Live | {metrics.active_power_watts} W | {metrics.load_percent}% load | {metrics.voltage_volts:.1f} V"
        )

    def _refresh_plots(self) -> None:
        if not self.history_minutes:
            return
        x = list(self.history_minutes)
        self.power_curve.setData(x, list(self.history_watts))
        self.load_curve.setData(x, list(self.history_load))

        last_x = x[-1]
        min_x = max(0.0, last_x - 5.0)
        self.power_plot.setXRange(min_x, max(5.0, last_x + 0.05), padding=0)
        self.load_plot.setXRange(min_x, max(5.0, last_x + 0.05), padding=0)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.poll_timer.stop()
        self.device.close()
        super().closeEvent(event)


def main() -> None:
    app = QApplication([])
    app.setStyle("Fusion")
    window = UPSDashboard()
    window.show()
    app.exec_()


if __name__ == "__main__":
    main()
