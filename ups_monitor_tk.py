#!/usr/bin/env python3
"""Simple tkinter dashboard for continuous OMNI1500LCDT power monitoring."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import font as tkfont

from ups_device import UPSMetrics
from ups_poller import BackgroundPoller
from ups_theme import (
    BLUE,
    CARD_BG,
    CARD_BORDER,
    GREEN,
    RED,
    TEXT_MUTED,
    TEXT_PRIMARY,
    WINDOW_BG,
    capacity_caption,
    format_local_time,
    severity_color,
)

LOAD_BAR_WIDTH = 360
UI_TICK_MS = 200
STALE_AFTER_S = 3.0


def _shorten(message: str, limit: int = 64) -> str:
    message = " ".join(message.split())
    return message if len(message) <= limit else message[: limit - 1] + "…"


class UPSMonitorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Tripp Lite OMNI1500LCDT Monitor")
        self.geometry("500x470")
        self.resizable(False, False)
        self.configure(bg=WINDOW_BG)

        self.poller = BackgroundPoller(interval_s=1.0)
        self.last_metrics: UPSMetrics | None = None
        self.last_sample_monotonic: float | None = None
        self.tick_id: str | None = None

        family = tkfont.nametofont("TkDefaultFont").actual("family")
        self.big_font = tkfont.Font(family=family, size=48, weight="bold")
        self.value_font = tkfont.Font(family=family, size=15, weight="bold")
        self.label_font = tkfont.Font(family=family, size=11)
        self.caption_font = tkfont.Font(family=family, size=10)

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.poller.start()
        self.check_poller()

    def _card(self, parent: tk.Misc) -> tk.Frame:
        return tk.Frame(
            parent, bg=CARD_BG, highlightbackground=CARD_BORDER, highlightthickness=1
        )

    def _build_metric(self, parent: tk.Misc, title: str, row: int, column: int) -> tk.Label:
        title_label = tk.Label(parent, text=title, font=self.caption_font, bg=CARD_BG, fg=TEXT_MUTED)
        title_label.grid(row=row, column=column, sticky="w", padx=16, pady=(8, 0))
        value_label = tk.Label(parent, text="--", font=self.value_font, bg=CARD_BG, fg=TEXT_PRIMARY)
        value_label.grid(row=row + 1, column=column, sticky="w", padx=16, pady=(2, 10))
        return value_label

    def _build_ui(self) -> None:
        container = tk.Frame(self, bg=WINDOW_BG)
        container.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)

        tk.Label(
            container,
            text="Tripp Lite OMNI1500LCDT",
            font=tkfont.Font(family=self.big_font.actual("family"), size=19, weight="bold"),
            bg=WINDOW_BG,
            fg=TEXT_PRIMARY,
        ).pack(anchor="w")

        tk.Label(
            container,
            text="Continuous UPS output power and load monitor",
            font=self.caption_font,
            bg=WINDOW_BG,
            fg=TEXT_MUTED,
        ).pack(anchor="w", pady=(2, 14))

        hero = self._card(container)
        hero.pack(fill=tk.X)

        hero_top = tk.Frame(hero, bg=CARD_BG)
        hero_top.pack(fill=tk.X, padx=18, pady=(16, 4))
        tk.Label(
            hero_top, text="CURRENT DRAW", font=self.caption_font, bg=CARD_BG, fg=TEXT_MUTED
        ).pack(anchor="w")

        self.watts_value = tk.Label(hero, text="--", font=self.big_font, bg=CARD_BG, fg=GREEN)
        self.watts_value.pack(anchor="w", padx=18)

        tk.Label(hero, text="watts", font=self.label_font, bg=CARD_BG, fg=TEXT_MUTED).pack(
            anchor="w", padx=18, pady=(0, 12)
        )

        load_card = self._card(container)
        load_card.pack(fill=tk.X, pady=(14, 0))

        load_header = tk.Frame(load_card, bg=CARD_BG)
        load_header.pack(fill=tk.X, padx=18, pady=(14, 6))
        tk.Label(
            load_header, text="UPS LOAD", font=self.caption_font, bg=CARD_BG, fg=TEXT_MUTED
        ).pack(side=tk.LEFT)
        self.load_value = tk.Label(load_header, text="--%", font=self.value_font, bg=CARD_BG, fg=BLUE)
        self.load_value.pack(side=tk.RIGHT)

        self.load_canvas = tk.Canvas(
            load_card, width=LOAD_BAR_WIDTH, height=18, bg=CARD_BG, highlightthickness=0
        )
        self.load_canvas.pack(padx=18, pady=(0, 8), anchor="w")
        self.load_canvas.create_rectangle(0, 0, LOAD_BAR_WIDTH, 18, fill="#0d1018", outline="")
        self.load_bar = self.load_canvas.create_rectangle(0, 0, 0, 18, fill=GREEN, outline="")

        self.load_caption = tk.Label(
            load_card, text="Waiting for data", font=self.caption_font, bg=CARD_BG, fg=TEXT_MUTED
        )
        self.load_caption.pack(anchor="w", padx=18, pady=(0, 14))

        metrics = self._card(container)
        metrics.pack(fill=tk.BOTH, expand=True, pady=(14, 0))
        metrics.grid_columnconfigure(0, weight=1)
        metrics.grid_columnconfigure(1, weight=1)

        self.voltage_value = self._build_metric(metrics, "Output Voltage", 0, 0)
        self.current_value = self._build_metric(metrics, "Output Current", 0, 1)
        self.input_freq_value = self._build_metric(metrics, "Input Frequency", 2, 0)
        self.output_freq_value = self._build_metric(metrics, "Output Frequency", 2, 1)
        self.nominal_voltage_value = self._build_metric(metrics, "Nominal Voltage", 4, 0)
        self.last_update_value = self._build_metric(metrics, "Last Update", 4, 1)

        footer = tk.Frame(container, bg=WINDOW_BG)
        footer.pack(fill=tk.X, pady=(14, 0))
        self.status_dot = tk.Canvas(footer, width=14, height=14, bg=WINDOW_BG, highlightthickness=0)
        self.status_dot.pack(side=tk.LEFT)
        self.status_circle = self.status_dot.create_oval(1, 1, 13, 13, fill=RED, outline="")
        self.status_label = tk.Label(
            footer,
            text="Connecting to UPS…",
            font=self.caption_font,
            bg=WINDOW_BG,
            fg=TEXT_MUTED,
            anchor="w",
        )
        self.status_label.pack(side=tk.LEFT, padx=(8, 0), fill=tk.X, expand=True)

    def render_metrics(self, metrics: UPSMetrics) -> None:
        color = severity_color(metrics.load_percent)
        self.last_metrics = metrics
        self.last_sample_monotonic = time.monotonic()

        self.watts_value.config(text=str(metrics.active_power_watts), fg=color)
        self.load_value.config(text=f"{metrics.load_percent}%", fg=color)
        self.load_caption.config(text=capacity_caption(metrics))

        bar_width = int((metrics.load_percent / 100) * LOAD_BAR_WIDTH)
        self.load_canvas.coords(self.load_bar, 0, 0, bar_width, 18)
        self.load_canvas.itemconfig(self.load_bar, fill=color)

        self.voltage_value.config(text=f"{metrics.voltage_volts:.1f} V")
        self.current_value.config(text=f"{metrics.current_amps:.1f} A")
        self.input_freq_value.config(text=f"{metrics.input_frequency_hz:.1f} Hz")
        self.output_freq_value.config(text=f"{metrics.output_frequency_hz:.1f} Hz")
        self.nominal_voltage_value.config(text=f"{metrics.nominal_voltage_volts} V")
        self.last_update_value.config(text=format_local_time(metrics.timestamp))

        self.status_dot.itemconfig(self.status_circle, fill=GREEN)
        self.status_label.config(text="Online · updating every second", fg=TEXT_MUTED)

    def render_error(self, message: str) -> None:
        self.status_dot.itemconfig(self.status_circle, fill=RED)
        self.status_label.config(text=_shorten(message), fg=RED)
        if self.last_metrics is None:
            self.watts_value.config(text="--", fg=RED)
            self.load_value.config(text="--%", fg=RED)
            self.load_caption.config(text="No UPS data yet")

    def render_stale(self, age_s: float) -> None:
        self.status_dot.itemconfig(self.status_circle, fill=RED)
        self.status_label.config(text=f"No data for {age_s:.0f}s — showing last sample", fg=RED)

    def check_poller(self) -> None:
        """Drain poller events on the Tk event loop; never block here."""
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
            self.render_stale(time.monotonic() - self.last_sample_monotonic)

        self.tick_id = self.after(UI_TICK_MS, self.check_poller)

    def on_close(self) -> None:
        if self.tick_id is not None:
            self.after_cancel(self.tick_id)
            self.tick_id = None
        self.poller.stop()
        self.destroy()


def main() -> None:
    app = UPSMonitorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
