#!/usr/bin/env python3
"""PyQt5 dashboard for continuous OMNI1500LCDT UPS monitoring."""

from __future__ import annotations

import sys
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

from ups_device import UPS_CAPACITY_WATTS, UPSMetrics
from ups_poller import BackgroundPoller
from ups_theme import (
    AMBER,
    BLUE,
    CARD_BG,
    CARD_BORDER,
    FONT_HERO,
    FONT_LABEL,
    FONT_SECONDARY,
    FONT_VALUE,
    GREEN,
    PANEL_BG,
    POWER_WARN_WATTS,
    RED,
    TEXT_MUTED,
    TEXT_PRIMARY,
    UNIT_RATIO,
    WINDOW_BG,
    capacity_caption,
    format_local_time,
    power_axis_range,
    severity_color,
)

UI_TICK_MS = 200
STALE_AFTER_S = 3.0
HISTORY_POINTS = 300


def _font(size: int, bold: bool = False) -> QFont:
    font = QFont()
    font.setPointSize(size)
    font.setBold(bold)
    return font


def _numeric_font(size: int) -> QFont:
    """Monospace digits so readouts do not shift as values change width."""
    font = QFont("DejaVu Sans Mono")
    font.setStyleHint(QFont.Monospace)
    font.setPointSize(size)
    font.setBold(True)
    return font


def _with_unit(value: str, unit: str, size: int, tight: bool = False) -> str:
    """Inline a smaller, muted unit after a value (QLabel rich text)."""
    unit_pt = max(8, round(size * UNIT_RATIO))
    gap = "" if tight else " "
    return (
        f'{value}<span style="font-size:{unit_pt}pt; font-weight:600;'
        f' color:{TEXT_MUTED};">{gap}{unit}</span>'
    )


class MetricCard(QFrame):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("metricCard")
        self._build_ui(title)

    def _build_ui(self, title: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)

        title_label = QLabel(title)
        title_label.setFont(_font(FONT_LABEL))
        title_label.setStyleSheet(f"color: {TEXT_MUTED};")

        self.value_label = QLabel("--")
        self.value_label.setFont(_numeric_font(FONT_VALUE))
        self.value_label.setTextFormat(Qt.RichText)
        self.value_label.setStyleSheet(f"color: {TEXT_PRIMARY};")

        layout.addWidget(title_label)
        layout.addWidget(self.value_label)

    def set_value(self, value: str, unit: str = "", color: str = TEXT_PRIMARY) -> None:
        self.value_label.setText(_with_unit(value, unit, FONT_VALUE) if unit else value)
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

    # -- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        pg.setConfigOptions(antialias=True, background=WINDOW_BG, foreground=TEXT_PRIMARY)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("Tripp Lite OMNI1500LCDT")
        title.setFont(_font(20, bold=True))
        title.setStyleSheet(f"color: {TEXT_PRIMARY};")
        subtitle = QLabel("Continuous UPS output monitoring over direct USB HID")
        subtitle.setFont(_font(FONT_LABEL))
        subtitle.setStyleSheet(f"color: {TEXT_MUTED};")
        root.addWidget(title)
        root.addWidget(subtitle)

        hero_row = QHBoxLayout()
        hero_row.setSpacing(12)

        power_group = QGroupBox("CURRENT DRAW")
        power_layout = QVBoxLayout(power_group)
        power_layout.setContentsMargins(16, 12, 16, 12)
        power_layout.setSpacing(0)
        self.power_value = QLabel("--")
        self.power_value.setFont(_numeric_font(FONT_HERO))
        self.power_value.setTextFormat(Qt.RichText)
        self.power_value.setStyleSheet(f"color: {TEXT_PRIMARY};")
        self.power_value.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        power_layout.addWidget(self.power_value)
        hero_row.addWidget(power_group, 1)

        load_group = QGroupBox("UPS LOAD")
        load_layout = QVBoxLayout(load_group)
        load_layout.setContentsMargins(16, 12, 16, 12)
        load_layout.setSpacing(6)
        self.load_value = QLabel("--%")
        self.load_value.setFont(_numeric_font(FONT_SECONDARY))
        self.load_value.setTextFormat(Qt.RichText)
        self.load_value.setStyleSheet(f"color: {BLUE};")
        load_layout.addWidget(self.load_value)
        self.load_bar = QProgressBar()
        self.load_bar.setRange(0, 100)
        self.load_bar.setTextVisible(False)
        self.load_bar.setFixedHeight(12)
        self._style_progress_bar(BLUE)
        load_layout.addWidget(self.load_bar)
        self.load_caption = QLabel("Waiting for data")
        self.load_caption.setFont(_font(FONT_LABEL))
        self.load_caption.setStyleSheet(f"color: {TEXT_MUTED};")
        load_layout.addWidget(self.load_caption)
        hero_row.addWidget(load_group, 1)

        root.addLayout(hero_row)

        charts_group = QGroupBox("LIVE HISTORY")
        charts_layout = QVBoxLayout(charts_group)
        charts_layout.setContentsMargins(12, 14, 12, 10)
        charts_layout.setSpacing(4)

        self.power_plot = self._make_plot("Power", "W", 240, show_time_label=False)
        self.power_plot.setYRange(0, UPS_CAPACITY_WATTS * 1.1, padding=0)
        self.power_curve = self.power_plot.plot(
            pen=pg.mkPen(GREEN, width=2),
            fillLevel=0,
            brush=pg.mkBrush(57, 217, 138, 24),
        )
        self._add_threshold(self.power_plot, POWER_WARN_WATTS, AMBER)
        self._add_threshold(self.power_plot, UPS_CAPACITY_WATTS, RED)

        self.load_plot = self._make_plot("Load", "%", 150, show_time_label=True)
        self.load_plot.setYRange(0, 100, padding=0)
        self.load_curve = self.load_plot.plot(
            pen=pg.mkPen(BLUE, width=2),
            fillLevel=0,
            brush=pg.mkBrush(76, 179, 255, 18),
        )
        self.load_plot.setXLink(self.power_plot)

        charts_layout.addWidget(self.power_plot)
        charts_layout.addWidget(self.load_plot)

        hint = QLabel(
            f'<span style="color:{AMBER};">—</span> {POWER_WARN_WATTS} W warning'
            f'  ·  <span style="color:{RED};">—</span> {UPS_CAPACITY_WATTS} W rating'
        )
        hint.setFont(_font(FONT_LABEL))
        hint.setStyleSheet(f"color: {TEXT_MUTED};")
        hint.setAlignment(Qt.AlignRight)
        charts_layout.addWidget(hint)
        root.addWidget(charts_group, 1)

        metrics_group = QGroupBox("DETAILS")
        metrics_layout = QGridLayout(metrics_group)
        metrics_layout.setContentsMargins(12, 14, 12, 12)
        metrics_layout.setHorizontalSpacing(12)
        metrics_layout.setVerticalSpacing(12)

        self.voltage_card = MetricCard("Output Voltage")
        self.current_card = MetricCard("Output Current")
        self.in_freq_card = MetricCard("Input Frequency")
        self.out_freq_card = MetricCard("Output Frequency")
        self.nominal_card = MetricCard("Nominal Voltage")
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
            f" border-radius: 12px; margin-top: 12px; font-weight: bold;"
            f" color: {TEXT_PRIMARY}; }}"
            "QGroupBox::title { left: 12px; top: -6px; padding: 0 4px; }"
            # QLabel is a QFrame subclass: without this every label inside a
            # card picks up the card's border/background and the UI turns into
            # a stack of nested boxes.
            "QLabel { background: transparent; border: none; }"
            f"QFrame#metricCard {{ background-color: {PANEL_BG};"
            f" border: 1px solid {CARD_BORDER}; border-radius: 10px; }}"
        )

    def _make_plot(
        self, left_label: str, units: str, height: int, show_time_label: bool
    ) -> pg.PlotWidget:
        plot = pg.PlotWidget()
        plot.setMinimumHeight(height)
        plot.showGrid(x=True, y=True, alpha=0.08)
        plot.setLabel("left", left_label, units=units)
        if show_time_label:
            plot.setLabel("bottom", "Time", units="min")
        plot.hideAxis("right")
        plot.hideAxis("top")
        if not show_time_label:
            # The load chart below is X-linked and carries the shared time axis.
            plot.hideAxis("bottom")
        left_axis = plot.getAxis("left")
        left_axis.setWidth(48)
        left_axis.setPen(pg.mkPen(CARD_BORDER))
        left_axis.setTextPen(pg.mkPen(TEXT_MUTED))
        if show_time_label:
            bottom_axis = plot.getAxis("bottom")
            bottom_axis.setHeight(42)
            bottom_axis.setPen(pg.mkPen(CARD_BORDER))
            bottom_axis.setTextPen(pg.mkPen(TEXT_MUTED))
        plot.setMenuEnabled(False)
        plot.setMouseEnabled(x=False, y=False)
        plot.setClipToView(True)
        plot.setDownsampling(auto=True, mode="peak")
        return plot

    @staticmethod
    def _add_threshold(plot: pg.PlotWidget, watts: int, color: str) -> None:
        plot.addItem(
            pg.InfiniteLine(
                pos=watts,
                angle=0,
                movable=False,
                pen=pg.mkPen(color, width=1, style=Qt.DashLine),
            )
        )

    def _style_progress_bar(self, color: str) -> None:
        self.load_bar.setStyleSheet(
            f"QProgressBar {{ border: 0; border-radius: 6px; background-color: {PANEL_BG}; }}"
            f"QProgressBar::chunk {{ border-radius: 6px; background-color: {color}; }}"
        )

    # -- data -------------------------------------------------------------

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

        # Real power is not the warning metric: keep it neutral and let the
        # load percentage and bar carry the severity colour.
        self.power_value.setText(_with_unit(str(metrics.active_power_watts), "W", FONT_HERO))
        self.load_value.setText(_with_unit(str(metrics.load_percent), "%", FONT_SECONDARY, tight=True))
        self.load_value.setStyleSheet(f"color: {color};")
        self.load_bar.setValue(metrics.load_percent)
        self._style_progress_bar(color)
        self.load_caption.setText(capacity_caption(metrics))

        self.voltage_card.set_value(f"{metrics.voltage_volts:.1f}", "V")
        self.current_card.set_value(f"{metrics.current_amps:.1f}", "A")
        self.in_freq_card.set_value(f"{metrics.input_frequency_hz:.1f}", "Hz")
        self.out_freq_card.set_value(f"{metrics.output_frequency_hz:.1f}", "Hz")
        self.nominal_card.set_value(str(metrics.nominal_voltage_volts), "V")
        self.update_card.set_value(format_local_time(metrics.timestamp), color=TEXT_MUTED)

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
        _, top = power_axis_range(max(self.history_watts))
        self.power_plot.setYRange(0, top, padding=0)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.poll_timer.stop()
        self.poller.stop()
        super().closeEvent(event)


def main() -> None:
    # Must be set before the QApplication exists.
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = UPSDashboard()
    window.show()
    app.exec_()


if __name__ == "__main__":
    main()
