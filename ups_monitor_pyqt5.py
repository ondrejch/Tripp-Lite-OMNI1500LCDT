#!/usr/bin/env python3
"""PyQt5 dashboard for continuous OMNI1500LCDT UPS monitoring."""

from __future__ import annotations

import time
from collections import deque

import pyqtgraph as pg
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

from ups_device import UPSMetrics
from ups_poller import BackgroundPoller
from ups_theme import (
    BLUE,
    CARD_BG,
    CARD_BORDER,
    GREEN,
    PANEL_BG,
    RED,
    TEXT_MUTED,
    TEXT_PRIMARY,
    WINDOW_BG,
    capacity_caption,
    format_local_time,
    severity_color,
)

UI_TICK_MS = 200
STALE_AFTER_S = 3.0
HISTORY_POINTS = 300


def _font(size: int, bold: bool = False) -> QFont:
    font = QFont()
    font.setPointSize(size)
    if bold:
        font.setBold(True)
    return font


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
        title_label.setFont(_font(10))
        title_label.setStyleSheet(f"color: {TEXT_MUTED};")

        self.value_label.setFont(_font(20, bold=True))
        self.value_label.setStyleSheet(f"color: {TEXT_PRIMARY};")

        layout.addWidget(title_label)
        layout.addWidget(self.value_label)
        if unit:
            unit_label = QLabel(unit)
            unit_label.setFont(_font(10))
            unit_label.setStyleSheet(f"color: {TEXT_MUTED};")
            layout.addWidget(unit_label)

        self.setStyleSheet(
            f"QFrame {{ background-color: {CARD_BG}; border: 1px solid {CARD_BORDER};"
            " border-radius: 10px; }}"
        )

    def set_value(self, value: str, color: str = TEXT_PRIMARY) -> None:
        self.value_label.setText(value)
        self.value_label.setStyleSheet(f"color: {color};")


class UPSDashboard(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.poller = BackgroundPoller(interval_s=1.0)
        self.history_minutes: deque[float] = deque(maxlen=HISTORY_POINTS)
        self.history_watts: deque[int] = deque(maxlen=HISTORY_POINTS)
        self.history_load: deque[int] = deque(maxlen=HISTORY_POINTS)
        self.start_monotonic: float | None = None
        self.last_metrics: UPSMetrics | None = None
        self.last_sample_monotonic: float | None = None

        self.setWindowTitle("Tripp Lite OMNI1500LCDT Monitor")
        self.resize(980, 760)
        self._build_ui()

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.check_poller)
        self.poll_timer.start(UI_TICK_MS)

        self.poller.start()

    def _build_ui(self) -> None:
        pg.setConfigOption("background", WINDOW_BG)
        pg.setConfigOption("foreground", TEXT_PRIMARY)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        title = QLabel("Tripp Lite OMNI1500LCDT")
        title.setFont(_font(20, bold=True))
        title.setStyleSheet(f"color: {TEXT_PRIMARY};")
        subtitle = QLabel("Continuous UPS output monitoring over direct USB HID")
        subtitle.setFont(_font(10))
        subtitle.setStyleSheet(f"color: {TEXT_MUTED};")
        root.addWidget(title)
        root.addWidget(subtitle)

        hero_row = QHBoxLayout()
        hero_row.setSpacing(14)

        power_group = QGroupBox("CURRENT DRAW")
        power_layout = QVBoxLayout(power_group)
        power_layout.setContentsMargins(18, 16, 18, 16)
        self.power_value = QLabel("--")
        self.power_value.setFont(_font(64, bold=True))
        self.power_value.setStyleSheet(f"color: {GREEN};")
        self.power_value.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        power_layout.addWidget(self.power_value)
        power_caption = QLabel("watts")
        power_caption.setFont(_font(12))
        power_caption.setStyleSheet(f"color: {TEXT_MUTED};")
        power_layout.addWidget(power_caption)
        hero_row.addWidget(power_group, 1)

        load_group = QGroupBox("UPS LOAD")
        load_layout = QVBoxLayout(load_group)
        load_layout.setContentsMargins(18, 16, 18, 16)
        self.load_value = QLabel("--%")
        self.load_value.setFont(_font(42, bold=True))
        self.load_value.setStyleSheet(f"color: {BLUE};")
        load_layout.addWidget(self.load_value)
        self.load_bar = QProgressBar()
        self.load_bar.setRange(0, 100)
        self.load_bar.setTextVisible(False)
        self.load_bar.setFixedHeight(14)
        self._style_progress_bar(GREEN)
        load_layout.addWidget(self.load_bar)
        self.load_caption = QLabel("Waiting for data")
        self.load_caption.setStyleSheet(f"color: {TEXT_MUTED};")
        load_layout.addWidget(self.load_caption)
        hero_row.addWidget(load_group, 1)

        root.addLayout(hero_row)

        charts_group = QGroupBox("LIVE HISTORY")
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

        metrics_group = QGroupBox("DETAILS")
        metrics_layout = QGridLayout(metrics_group)
        metrics_layout.setContentsMargins(12, 16, 12, 12)
        metrics_layout.setHorizontalSpacing(12)
        metrics_layout.setVerticalSpacing(12)

        self.voltage_card = MetricCard("Output Voltage", "V")
        self.current_card = MetricCard("Output Current", "A")
        self.in_freq_card = MetricCard("Input Frequency", "Hz")
        self.out_freq_card = MetricCard("Output Frequency", "Hz")
        self.nominal_card = MetricCard("Nominal Voltage", "V")
        self.update_card = MetricCard("Last Update")

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

        self.statusBar().setStyleSheet(
            f"QStatusBar {{ background-color: {WINDOW_BG}; color: {TEXT_MUTED}; }}"
        )
        self.statusBar().showMessage("Waiting for UPS data…")

        self.setStyleSheet(
            f"QMainWindow {{ background-color: {WINDOW_BG}; }}"
            f"QGroupBox {{ background-color: {CARD_BG}; border: 1px solid {CARD_BORDER};"
            f" border-radius: 12px; margin-top: 12px; font-weight: bold; color: {TEXT_PRIMARY}; }}"
            "QGroupBox::title { left: 12px; top: -6px; padding: 0 4px; }"
        )

    def _style_progress_bar(self, color: str) -> None:
        self.load_bar.setStyleSheet(
            f"QProgressBar {{ border: 0; border-radius: 7px; background-color: {PANEL_BG}; }}"
            f"QProgressBar::chunk {{ border-radius: 7px; background-color: {color}; }}"
        )

    def check_poller(self) -> None:
        """Drain poller events on the GUI thread; never block here."""
        events = self.poller.drain()
        for event in events:
            if event.metrics is not None:
                self.render_metrics(event.metrics)
            elif event.error is not None:
                self.render_error(event.error)

        if (
            not events
            and self.last_sample_monotonic is not None
            and time.monotonic() - self.last_sample_monotonic > STALE_AFTER_S
        ):
            self.statusBar().showMessage(
                f"No data for {time.monotonic() - self.last_sample_monotonic:.0f}s "
                "— showing last sample"
            )

    def render_metrics(self, metrics: UPSMetrics) -> None:
        self.last_metrics = metrics
        self.last_sample_monotonic = time.monotonic()
        color = severity_color(metrics.load_percent)

        self.power_value.setText(str(metrics.active_power_watts))
        self.power_value.setStyleSheet(f"color: {color};")
        self.load_value.setText(f"{metrics.load_percent}%")
        self.load_value.setStyleSheet(f"color: {color};")
        self.load_bar.setValue(metrics.load_percent)
        self._style_progress_bar(color)
        self.load_caption.setText(capacity_caption(metrics))

        self.voltage_card.set_value(f"{metrics.voltage_volts:.1f}")
        self.current_card.set_value(f"{metrics.current_amps:.1f}")
        self.in_freq_card.set_value(f"{metrics.input_frequency_hz:.1f}")
        self.out_freq_card.set_value(f"{metrics.output_frequency_hz:.1f}")
        self.nominal_card.set_value(str(metrics.nominal_voltage_volts))
        self.update_card.set_value(format_local_time(metrics.timestamp), TEXT_MUTED)

        now = time.monotonic()
        if self.start_monotonic is None:
            self.start_monotonic = now
        self.history_minutes.append((now - self.start_monotonic) / 60.0)
        self.history_watts.append(metrics.active_power_watts)
        self.history_load.append(metrics.load_percent)
        self._refresh_plots()

        self.statusBar().showMessage(
            f"Live · {metrics.active_power_watts} W · {metrics.load_percent}% load · "
            f"{metrics.voltage_volts:.1f} V · {metrics.apparent_power_va:.0f} VA"
        )

    def render_error(self, message: str) -> None:
        self.statusBar().showMessage(message)
        if self.last_metrics is None:
            self.power_value.setText("--")
            self.power_value.setStyleSheet(f"color: {RED};")
            self.load_value.setText("--%")
            self.load_value.setStyleSheet(f"color: {RED};")
            self.load_caption.setText("No UPS data yet")

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
        self.poller.stop()
        super().closeEvent(event)


def main() -> None:
    app = QApplication([])
    app.setStyle("Fusion")
    window = UPSDashboard()
    window.show()
    app.exec_()


if __name__ == "__main__":
    main()
