#!/usr/bin/env python3
"""Simple tkinter dashboard for continuous OMNI1500LCDT power monitoring."""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

from ups_device import UPSDevice, UPSMetrics, UPSReadError

WINDOW_BG = "#10131c"
CARD_BG = "#171c28"
CARD_BORDER = "#252c3b"
TEXT_MUTED = "#8e99ad"
TEXT_PRIMARY = "#f5f7fb"
GREEN = "#39d98a"
AMBER = "#ffb84d"
RED = "#ff5c7a"
BLUE = "#4cb3ff"
LOAD_BAR_WIDTH = 360


def severity_color(load_percent: int) -> str:
    if load_percent >= 80:
        return RED
    if load_percent >= 50:
        return AMBER
    return GREEN


class UPSMonitorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Tripp Lite OMNI1500LCDT Monitor")
        self.geometry("500x470")
        self.resizable(False, False)
        self.configure(bg=WINDOW_BG)

        self.device = UPSDevice()
        self.after_id: str | None = None
        self.poll_interval_ms = 1000
        self.last_metrics: UPSMetrics | None = None

        self.big_font = tkfont.Font(family="Helvetica", size=48, weight="bold")
        self.value_font = tkfont.Font(family="Helvetica", size=15, weight="bold")
        self.label_font = tkfont.Font(family="Helvetica", size=11)
        self.caption_font = tkfont.Font(family="Helvetica", size=10)

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.poll_once()

    def _card(self, parent: tk.Misc) -> tk.Frame:
        frame = tk.Frame(parent, bg=CARD_BG, highlightbackground=CARD_BORDER, highlightthickness=1)
        return frame

    def _build_metric(self, parent: tk.Misc, title: str, row: int, column: int) -> tk.Label:
        title_label = tk.Label(parent, text=title, font=self.caption_font, bg=CARD_BG, fg=TEXT_MUTED)
        title_label.grid(row=row, column=column, sticky="w", padx=16, pady=(8, 0))
        value_label = tk.Label(parent, text="--", font=self.value_font, bg=CARD_BG, fg=TEXT_PRIMARY)
        value_label.grid(row=row + 1, column=column, sticky="w", padx=16, pady=(2, 10))
        return value_label

    def _build_ui(self) -> None:
        container = tk.Frame(self, bg=WINDOW_BG)
        container.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)

        title = tk.Label(
            container,
            text="Tripp Lite OMNI1500LCDT",
            font=tkfont.Font(family="Helvetica", size=19, weight="bold"),
            bg=WINDOW_BG,
            fg=TEXT_PRIMARY,
        )
        title.pack(anchor="w")

        subtitle = tk.Label(
            container,
            text="Continuous UPS output power and load monitor",
            font=self.caption_font,
            bg=WINDOW_BG,
            fg=TEXT_MUTED,
        )
        subtitle.pack(anchor="w", pady=(2, 14))

        hero = self._card(container)
        hero.pack(fill=tk.X)

        hero_top = tk.Frame(hero, bg=CARD_BG)
        hero_top.pack(fill=tk.X, padx=18, pady=(16, 4))
        tk.Label(hero_top, text="CURRENT DRAW", font=self.caption_font, bg=CARD_BG, fg=TEXT_MUTED).pack(anchor="w")

        self.watts_value = tk.Label(hero, text="--", font=self.big_font, bg=CARD_BG, fg=GREEN)
        self.watts_value.pack(anchor="w", padx=18)

        self.watts_caption = tk.Label(hero, text="watts", font=self.label_font, bg=CARD_BG, fg=TEXT_MUTED)
        self.watts_caption.pack(anchor="w", padx=18, pady=(0, 12))

        load_card = self._card(container)
        load_card.pack(fill=tk.X, pady=(14, 0))

        load_header = tk.Frame(load_card, bg=CARD_BG)
        load_header.pack(fill=tk.X, padx=18, pady=(14, 6))
        tk.Label(load_header, text="UPS LOAD", font=self.caption_font, bg=CARD_BG, fg=TEXT_MUTED).pack(side=tk.LEFT)
        self.load_value = tk.Label(load_header, text="--%", font=self.value_font, bg=CARD_BG, fg=BLUE)
        self.load_value.pack(side=tk.RIGHT)

        self.load_canvas = tk.Canvas(load_card, width=LOAD_BAR_WIDTH, height=18, bg=CARD_BG, highlightthickness=0)
        self.load_canvas.pack(padx=18, pady=(0, 8), anchor="w")
        self.load_canvas.create_rectangle(0, 0, LOAD_BAR_WIDTH, 18, fill="#0d1018", outline="")
        self.load_bar = self.load_canvas.create_rectangle(0, 0, 0, 18, fill=GREEN, outline="")

        self.load_caption = tk.Label(load_card, text="0 of 900 W capacity", font=self.caption_font, bg=CARD_BG, fg=TEXT_MUTED)
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
        self.status_label = tk.Label(footer, text="Connecting to UPS...", font=self.caption_font, bg=WINDOW_BG, fg=TEXT_MUTED)
        self.status_label.pack(side=tk.LEFT, padx=(8, 0))

    def render_metrics(self, metrics: UPSMetrics) -> None:
        color = severity_color(metrics.load_percent)
        self.last_metrics = metrics

        self.watts_value.config(text=str(metrics.active_power_watts), fg=color)
        self.load_value.config(text=f"{metrics.load_percent}%", fg=color)
        self.load_caption.config(text=f"{metrics.active_power_watts} W of 900 W capacity")

        bar_width = int((metrics.load_percent / 100) * LOAD_BAR_WIDTH)
        self.load_canvas.coords(self.load_bar, 0, 0, bar_width, 18)
        self.load_canvas.itemconfig(self.load_bar, fill=color)

        self.voltage_value.config(text=f"{metrics.voltage_volts:.1f} V")
        self.current_value.config(text=f"{metrics.current_amps:.1f} A")
        self.input_freq_value.config(text=f"{metrics.input_frequency_hz:.1f} Hz")
        self.output_freq_value.config(text=f"{metrics.output_frequency_hz:.1f} Hz")
        self.nominal_voltage_value.config(text=f"{metrics.nominal_voltage_volts} V")
        self.last_update_value.config(text=metrics.timestamp.strftime("%H:%M:%S"))

        self.status_dot.itemconfig(self.status_circle, fill=GREEN)
        self.status_label.config(text="Online and updating every second", fg=TEXT_MUTED)

    def render_error(self, message: str) -> None:
        self.status_dot.itemconfig(self.status_circle, fill=RED)
        self.status_label.config(text=message, fg=RED)
        if self.last_metrics is None:
            self.watts_value.config(text="--", fg=RED)
            self.load_value.config(text="--%", fg=RED)
            self.load_caption.config(text="No UPS data yet")

    def poll_once(self) -> None:
        try:
            self.render_metrics(self.device.read_metrics())
        except UPSReadError as exc:
            self.render_error(str(exc))

        self.after_id = self.after(self.poll_interval_ms, self.poll_once)

    def on_close(self) -> None:
        if self.after_id is not None:
            self.after_cancel(self.after_id)
        self.device.close()
        self.destroy()


def main() -> None:
    app = UPSMonitorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
