#!/usr/bin/env python3
"""PyQt5 dashboard for continuous OMNI1500LCDT UPS monitoring."""

from __future__ import annotations

import argparse
import signal
import sys
import time
from collections import deque

import pyqtgraph as pg
from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PyQt5.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeySequence,
    QPainter,
    QPen,
)
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QShortcut,
    QSizePolicy,
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
    load_arc,
    power_axis_range,
    severity_color,
    severity_label,
)

UI_TICK_MS = 200
STALE_AFTER_S = 3.0
HISTORY_POINTS = 300
NORMAL_SIZE = QSize(980, 760)
COMPACT_SIZE = QSize(700, 400)


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


class LoadGauge(QWidget):
    """Ring gauge showing the load percentage as a clockwise arc."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._percent: int | None = None
        self._color = BLUE
        self.setMinimumSize(160, 160)
        self.setMaximumHeight(210)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_load(self, percent: int | None, color: str = BLUE) -> None:
        self._percent = percent
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        side = float(min(self.width(), self.height()))
        thickness = max(10.0, round(side * 0.08))
        margin = thickness / 2.0 + 2.0
        rect = QRectF(
            (self.width() - side) / 2.0 + margin,
            (self.height() - side) / 2.0 + margin,
            side - 2.0 * margin,
            side - 2.0 * margin,
        )

        painter.setPen(QPen(QColor(CARD_BORDER), thickness, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, 90 * 16, -360 * 16)

        if self._percent is not None:
            start, span = load_arc(self._percent)
            painter.setPen(QPen(QColor(self._color), thickness, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(rect, int(start * 16), int(span * 16))

        value_font = _numeric_font(max(16, round(side * 0.20)))
        unit_font = _font(max(9, round(side * 0.10)), bold=True)
        fm_value = QFontMetricsF(value_font)
        fm_unit = QFontMetricsF(unit_font)

        value_text = "--" if self._percent is None else str(self._percent)
        unit_text = "" if self._percent is None else "%"
        total = fm_value.width(value_text) + fm_unit.width(unit_text)
        baseline = (
            rect.center().y()
            + (fm_value.ascent() - fm_value.descent()) / 2.0
        )
        x = rect.center().x() - total / 2.0

        painter.setFont(value_font)
        painter.setPen(QPen(QColor(self._color if self._percent is not None else TEXT_MUTED)))
        painter.drawText(QPointF(x, baseline), value_text)
        if unit_text:
            painter.setFont(unit_font)
            painter.setPen(QPen(QColor(TEXT_MUTED)))
            painter.drawText(QPointF(x + fm_value.width(value_text), baseline), unit_text)


class StatusChip(QLabel):
    """Small rounded text badge for the load severity band."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFont(_font(FONT_LABEL, bold=True))
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(22)
        self.set_state("--", TEXT_MUTED)

    def set_state(self, text: str, color: str) -> None:
        self.setText(text.upper())
        self.setStyleSheet(
            f"color: {color}; border: 1px solid {color}; border-radius: 10px;"
            " padding: 2px 9px; background: transparent;"
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
    def __init__(self, compact: bool = False) -> None:
        super().__init__()
        self.poller = BackgroundPoller(interval_s=1.0)
        self.history_minutes: deque[float] = deque(maxlen=HISTORY_POINTS)
        self.history_watts: deque[int] = deque(maxlen=HISTORY_POINTS)
        self.history_load: deque[int] = deque(maxlen=HISTORY_POINTS)
        self.start_monotonic: float | None = None
        self.last_metrics: UPSMetrics | None = None
        self.last_sample_monotonic: float | None = None
        self._compact = False
        self._normal_size = QSize(NORMAL_SIZE)

        self.setWindowTitle("Tripp Lite OMNI1500LCDT Monitor")
        self.resize(NORMAL_SIZE)
        self._build_ui()

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.check_poller)
        self.poll_timer.start(UI_TICK_MS)

        self.poller.start()
        if compact:
            self.compact_button.setChecked(True)

    # -- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        pg.setConfigOptions(antialias=True, background=WINDOW_BG, foreground=TEXT_PRIMARY)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("Tripp Lite OMNI1500LCDT")
        title.setFont(_font(20, bold=True))
        title.setStyleSheet(f"color: {TEXT_PRIMARY};")
        self.subtitle = QLabel("Continuous UPS output monitoring over direct USB HID")
        self.subtitle.setFont(_font(FONT_LABEL))
        self.subtitle.setStyleSheet(f"color: {TEXT_MUTED};")
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        header.addLayout(titles)
        header.addStretch(1)

        self.compact_button = QPushButton("Compact")
        self.compact_button.setObjectName("compactButton")
        self.compact_button.setCheckable(True)
        self.compact_button.setCursor(Qt.PointingHandCursor)
        self.compact_button.setToolTip("Compact always-on-top mode (Ctrl+M)")
        self.compact_button.toggled.connect(self._apply_compact)
        header.addWidget(self.compact_button, 0, Qt.AlignTop)
        root.addLayout(header)

        hero_row = QHBoxLayout()
        hero_row.setSpacing(12)

        power_group = QGroupBox("CURRENT DRAW")
        power_layout = QVBoxLayout(power_group)
        power_layout.setContentsMargins(16, 12, 16, 12)
        power_layout.setSpacing(2)
        power_layout.addStretch(1)
        self.power_value = QLabel("--")
        self.power_value.setFont(_numeric_font(FONT_HERO))
        self.power_value.setTextFormat(Qt.RichText)
        self.power_value.setStyleSheet(f"color: {TEXT_PRIMARY};")
        self.power_value.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        power_layout.addWidget(self.power_value)
        power_caption = QLabel(f"Real power · {UPS_CAPACITY_WATTS} W continuous rating")
        power_caption.setFont(_font(FONT_LABEL))
        power_caption.setStyleSheet(f"color: {TEXT_MUTED};")
        power_layout.addWidget(power_caption)
        power_layout.addStretch(1)
        hero_row.addWidget(power_group, 1)

        load_group = QGroupBox("UPS LOAD")
        load_layout = QVBoxLayout(load_group)
        load_layout.setContentsMargins(16, 8, 16, 12)
        load_layout.setSpacing(2)

        chip_row = QHBoxLayout()
        chip_row.addStretch(1)
        self.severity_chip = StatusChip()
        chip_row.addWidget(self.severity_chip)
        load_layout.addLayout(chip_row)

        self.load_gauge = LoadGauge()
        load_layout.addWidget(self.load_gauge, 1)
        self.load_caption = QLabel("Waiting for data")
        self.load_caption.setFont(_font(FONT_LABEL))
        self.load_caption.setStyleSheet(f"color: {TEXT_MUTED};")
        self.load_caption.setAlignment(Qt.AlignCenter)
        load_layout.addWidget(self.load_caption)
        hero_row.addWidget(load_group, 1)

        root.addLayout(hero_row)

        self.charts_group = QGroupBox("LIVE HISTORY")
        charts_layout = QVBoxLayout(self.charts_group)
        charts_layout.setContentsMargins(12, 14, 12, 10)
        charts_layout.setSpacing(4)

        self.power_plot = self._make_plot("Power", "W", 190, show_time_label=False)
        self.power_plot.setYRange(0, UPS_CAPACITY_WATTS * 1.1, padding=0)
        self.power_curve = self.power_plot.plot(
            pen=pg.mkPen(GREEN, width=2),
            fillLevel=0,
            brush=pg.mkBrush(57, 217, 138, 24),
        )
        self._add_threshold(self.power_plot, POWER_WARN_WATTS, AMBER)
        self._add_threshold(self.power_plot, UPS_CAPACITY_WATTS, RED)

        self.load_plot = self._make_plot("Load", "%", 120, show_time_label=True)
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
        root.addWidget(self.charts_group, 1)

        self.metrics_group = QGroupBox("DETAILS")
        metrics_layout = QGridLayout(self.metrics_group)
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

        root.addWidget(self.metrics_group)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        self.status_pill = QFrame()
        self.status_pill.setObjectName("statusPill")
        pill_layout = QHBoxLayout(self.status_pill)
        pill_layout.setContentsMargins(10, 3, 12, 3)
        pill_layout.setSpacing(6)
        self.status_dot = QLabel("●")
        self.status_dot.setFont(_font(FONT_LABEL, bold=True))
        self.status_dot.setStyleSheet(f"color: {GREEN};")
        self.status_text = QLabel("Waiting for UPS data…")
        self.status_text.setFont(_font(FONT_LABEL))
        self.status_text.setStyleSheet(f"color: {TEXT_MUTED};")
        pill_layout.addWidget(self.status_dot)
        pill_layout.addWidget(self.status_text)
        footer.addWidget(self.status_pill)
        footer.addStretch(1)
        root.addLayout(footer)

        self._set_status("Waiting for UPS data…", AMBER)

        self._compact_shortcut = QShortcut(QKeySequence("Ctrl+M"), self)
        self._compact_shortcut.activated.connect(self.compact_button.toggle)

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
            f"QFrame#statusPill {{ background-color: {PANEL_BG};"
            f" border: 1px solid {CARD_BORDER}; border-radius: 11px; }}"
            f"QPushButton#compactButton {{ background-color: {PANEL_BG};"
            f" color: {TEXT_MUTED}; border: 1px solid {CARD_BORDER};"
            " border-radius: 8px; padding: 5px 12px; font-weight: 600; }"
            f"QPushButton#compactButton:hover {{ color: {TEXT_PRIMARY};"
            f" border-color: {TEXT_MUTED}; }}"
            f"QPushButton#compactButton:checked {{ color: {TEXT_PRIMARY};"
            f" border-color: {BLUE}; }}"
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
        left_axis.setWidth(64)
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

    def _set_status(self, text: str, color: str = GREEN) -> None:
        """Update the footer status pill (dot colour plus message)."""
        self.status_dot.setStyleSheet(f"color: {color};")
        self.status_text.setText(text)

    def _apply_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        was_visible = self.isVisible()
        self._compact = compact
        for widget in (self.subtitle, self.charts_group, self.metrics_group):
            widget.setVisible(not compact)

        flags = self.windowFlags()
        if compact:
            self._normal_size = self.size()
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.resize(COMPACT_SIZE if compact else self._normal_size)
        if was_visible:
            self.show()

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
            self._set_status(
                f"No data for {time.monotonic() - self.last_sample_monotonic:.0f}s "
                "— showing last sample",
                AMBER,
            )

    def render_metrics(self, metrics: UPSMetrics) -> None:
        self.last_metrics = metrics
        self.last_sample_monotonic = time.monotonic()
        color = severity_color(metrics.load_percent)

        # Real power is not the warning metric: keep it neutral and let the
        # gauge and severity chip carry the warning colour.
        self.power_value.setText(_with_unit(str(metrics.active_power_watts), "W", FONT_HERO))
        self.load_gauge.set_load(metrics.load_percent, color)
        self.severity_chip.set_state(severity_label(metrics.load_percent), color)
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

        self._set_status(
            f"Live · {metrics.active_power_watts} W · {metrics.load_percent}% load · "
            f"{metrics.voltage_volts:.1f} V · {metrics.apparent_power_va:.0f} VA",
            GREEN,
        )

    def render_error(self, message: str) -> None:
        self._set_status(message, RED)
        if self.last_metrics is None:
            self.power_value.setText("--")
            self.load_gauge.set_load(None, RED)
            self.severity_chip.set_state("Offline", RED)
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
    parser = argparse.ArgumentParser(description="PyQt5 dashboard for the OMNI1500LCDT UPS")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="start in compact always-on-top mode (toggle with Ctrl+M)",
    )
    args = parser.parse_args()

    # Must be set before the QApplication exists.
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = UPSDashboard(compact=args.compact)

    # Qt's event loop blocks in C++ and only comes back to the interpreter when
    # an event invokes Python, so a default SIGINT would surface as a
    # KeyboardInterrupt in the middle of a timer callback. A periodic no-op
    # timer gives the interpreter a chance to run the handler below, which
    # shuts down through the normal close path and joins the poller thread.
    def handle_signal(_signum: int, _frame: object) -> None:
        window.close()
        app.quit()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    signal_timer = QTimer(window)
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(200)

    window.show()
    app.exec_()


if __name__ == "__main__":
    main()
