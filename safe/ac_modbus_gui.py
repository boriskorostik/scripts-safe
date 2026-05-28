#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from ac_modbus_cli import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    DEFAULT_UNIT,
    KNOWN_RAW_FRAMES,
    frame_write_single_coil,
    frame_write_single_register,
    hex_bytes,
    parse_hex_string,
    send_frame,
)


WINDOW_BG = "#eef3f8"
CARD_BG = "#ffffff"
LOG_BG = "#0f1720"
LOG_FG = "#d7e2f0"
ACCENT = "#1f6feb"
ACCENT_ACTIVE = "#195cc0"
MUTED = "#5b6b7f"


class AirconControlApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AC Modbus Control")
        self.root.geometry("860x690")
        self.root.minsize(760, 600)
        self.root.configure(bg=WINDOW_BG)

        self.host_var = tk.StringVar(value=DEFAULT_HOST)
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        self.unit_var = tk.StringVar(value=str(DEFAULT_UNIT))
        self.timeout_var = tk.StringVar(value=str(DEFAULT_TIMEOUT))
        self.temp_var = tk.StringVar(value="22")
        self.reg0_var = tk.StringVar(value="22")
        self.raw_var = tk.StringVar(value="01 05 00 00 FF 00 8C 3A")
        self.status_var = tk.StringVar(value="Готово к отправке команд.")
        self.busy = False

        self._configure_style()
        self._build_ui()

    def _configure_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Card.TFrame", background=CARD_BG)
        style.configure("Card.TLabelframe", background=CARD_BG, borderwidth=0)
        style.configure("Card.TLabelframe.Label", background=CARD_BG, foreground="#122033", font=("DejaVu Sans", 10, "bold"))
        style.configure("Header.TLabel", background=WINDOW_BG, foreground="#102033", font=("DejaVu Sans", 17, "bold"))
        style.configure("Sub.TLabel", background=WINDOW_BG, foreground=MUTED, font=("DejaVu Sans", 10))
        style.configure("Field.TLabel", background=CARD_BG, foreground="#233548", font=("DejaVu Sans", 10))
        style.configure("Status.TLabel", background=WINDOW_BG, foreground="#203246", font=("DejaVu Sans", 10, "bold"))
        style.configure("TEntry", padding=6, fieldbackground="#f8fbff")
        style.configure("TButton", padding=(10, 7), font=("DejaVu Sans", 10))
        style.map("Accent.TButton", background=[("active", ACCENT_ACTIVE), ("!disabled", ACCENT)], foreground=[("!disabled", "#ffffff")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", borderwidth=0, focusthickness=0)

    def _build_ui(self):
        outer = tk.Frame(self.root, bg=WINDOW_BG)
        outer.pack(fill="both", expand=True, padx=16, pady=16)

        ttk.Label(outer, text="Управление кондиционером по Modbus", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Проверенные команды вынесены в отдельные кнопки. Все отправленные кадры и ответы видны в логе ниже.",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(4, 14))

        top_card = ttk.Frame(outer, style="Card.TFrame", padding=14)
        top_card.pack(fill="x")

        for col in range(4):
            top_card.columnconfigure(col, weight=1)

        fields = [
            ("IP / Host", self.host_var),
            ("TCP Port", self.port_var),
            ("Unit ID", self.unit_var),
            ("Timeout", self.timeout_var),
        ]
        for idx, (label, variable) in enumerate(fields):
            ttk.Label(top_card, text=label, style="Field.TLabel").grid(row=0, column=idx, sticky="w", padx=(0, 10))
            ttk.Entry(top_card, textvariable=variable).grid(row=1, column=idx, sticky="ew", padx=(0, 10), pady=(4, 0))

        quick_card = ttk.LabelFrame(outer, text="Быстрые действия", style="Card.TLabelframe", padding=14)
        quick_card.pack(fill="x", pady=(14, 0))
        for col in range(4):
            quick_card.columnconfigure(col, weight=1)

        ttk.Button(quick_card, text="Включить", style="Accent.TButton", command=self.command_on).grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        ttk.Button(quick_card, text="Выключить", command=self.command_off).grid(row=0, column=1, sticky="ew", padx=6, pady=6)
        ttk.Button(quick_card, text="Команда temp=22", command=lambda: self.command_temp(preset=22)).grid(row=0, column=2, sticky="ew", padx=6, pady=6)
        ttk.Button(quick_card, text="Команда reg0=22", command=lambda: self.command_reg0(preset=22)).grid(row=0, column=3, sticky="ew", padx=6, pady=6)

        value_card = ttk.LabelFrame(outer, text="Экспериментальные значения", style="Card.TLabelframe", padding=14)
        value_card.pack(fill="x", pady=(14, 0))
        for col in range(4):
            value_card.columnconfigure(col, weight=1)

        ttk.Label(value_card, text="register 0x0001", style="Field.TLabel").grid(row=0, column=0, sticky="w", padx=6)
        ttk.Entry(value_card, textvariable=self.temp_var).grid(row=1, column=0, sticky="ew", padx=6, pady=(4, 0))
        ttk.Button(value_card, text="Отправить temp", command=self.command_temp).grid(row=1, column=1, sticky="ew", padx=6, pady=(4, 0))

        ttk.Label(value_card, text="register 0x0000", style="Field.TLabel").grid(row=0, column=2, sticky="w", padx=6)
        ttk.Entry(value_card, textvariable=self.reg0_var).grid(row=1, column=2, sticky="ew", padx=6, pady=(4, 0))
        ttk.Button(value_card, text="Отправить reg0", command=self.command_reg0).grid(row=1, column=3, sticky="ew", padx=6, pady=(4, 0))

        raw_card = ttk.LabelFrame(outer, text="Raw frame", style="Card.TLabelframe", padding=14)
        raw_card.pack(fill="x", pady=(14, 0))
        raw_card.columnconfigure(0, weight=1)
        raw_card.columnconfigure(1, weight=0)
        ttk.Entry(raw_card, textvariable=self.raw_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(raw_card, text="Отправить raw", command=self.command_raw).grid(row=0, column=1, sticky="ew")

        status_row = tk.Frame(outer, bg=WINDOW_BG)
        status_row.pack(fill="x", pady=(12, 6))
        ttk.Label(status_row, textvariable=self.status_var, style="Status.TLabel").pack(side="left", anchor="w")
        ttk.Button(status_row, text="Очистить лог", command=self.clear_log).pack(side="right")

        log_card = ttk.LabelFrame(outer, text="Лог", style="Card.TLabelframe", padding=10)
        log_card.pack(fill="both", expand=True)

        self.log = ScrolledText(
            log_card,
            height=24,
            wrap="word",
            font=("DejaVu Sans Mono", 10),
            bg=LOG_BG,
            fg=LOG_FG,
            insertbackground="#ffffff",
            relief="flat",
            padx=12,
            pady=12,
        )
        self.log.pack(fill="both", expand=True)
        self.log.insert("end", "Готово. Можно отправлять команды.\n")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._set_status("Лог очищен.")

    def _append_log(self, text: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{timestamp}] {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_status(self, text: str):
        self.status_var.set(text)

    def _set_busy(self, value: bool):
        self.busy = value
        if value:
            self.root.configure(cursor="watch")
        else:
            self.root.configure(cursor="")

    def _connection_params(self):
        host = self.host_var.get().strip()
        if not host:
            raise ValueError("Укажи IP или hostname устройства.")
        try:
            port = int(self.port_var.get().strip())
            unit = int(self.unit_var.get().strip())
            timeout = float(self.timeout_var.get().strip())
        except ValueError as exc:
            raise ValueError("Port, Unit ID и Timeout должны быть числами.") from exc
        return host, port, unit, timeout

    def _run_frame(self, title: str, frame: bytes):
        if self.busy:
            messagebox.showinfo("Команда выполняется", "Сейчас уже идёт отправка. Дождись завершения.")
            return

        try:
            host, port, unit, timeout = self._connection_params()
        except ValueError as exc:
            messagebox.showerror("Ошибка параметров", str(exc))
            return

        self._set_busy(True)
        self._set_status(f"Отправка: {title}")
        self._append_log(f"{title}")
        self._append_log(f"Host: {host}:{port}  Unit: {unit}  Timeout: {timeout}")
        self._append_log(f"TX:   {hex_bytes(frame)}")

        thread = threading.Thread(
            target=self._send_worker,
            args=(host, port, timeout, title, frame),
            daemon=True,
        )
        thread.start()

    def _send_worker(self, host: str, port: int, timeout: float, title: str, frame: bytes):
        try:
            response = send_frame(host, port, frame, timeout)
            self.root.after(0, self._finish_success, title, response)
        except (OSError, socket.error) as exc:
            self.root.after(0, self._finish_error, title, str(exc))
        except Exception as exc:
            self.root.after(0, self._finish_error, title, str(exc))

    def _finish_success(self, title: str, response: bytes):
        if response:
            self._append_log(f"RX:   {hex_bytes(response)}")
        else:
            self._append_log("RX:   <нет ответа>")
        self._append_log("")
        self._set_status(f"Готово: {title}")
        self._set_busy(False)

    def _finish_error(self, title: str, error_text: str):
        self._append_log(f"Ошибка: {error_text}")
        self._append_log("")
        self._set_status(f"Ошибка: {title}")
        self._set_busy(False)
        messagebox.showerror("Ошибка отправки", error_text)

    def command_on(self):
        try:
            _, _, unit, _ = self._connection_params()
        except ValueError as exc:
            messagebox.showerror("Ошибка параметров", str(exc))
            return
        frame = frame_write_single_coil(unit, 0x0000, True)
        self._run_frame("Включение", frame)

    def command_off(self):
        try:
            _, _, unit, _ = self._connection_params()
        except ValueError as exc:
            messagebox.showerror("Ошибка параметров", str(exc))
            return
        frame = frame_write_single_coil(unit, 0x0000, False)
        self._run_frame("Выключение", frame)

    def command_temp(self, preset: int | None = None):
        try:
            _, _, unit, _ = self._connection_params()
            value = preset if preset is not None else int(self.temp_var.get().strip())
        except ValueError:
            messagebox.showerror("Ошибка значения", "Для temp нужно целое число.")
            return

        if not 0 <= value <= 0xFFFF:
            messagebox.showerror("Ошибка значения", "temp должен быть в диапазоне 0..65535.")
            return

        frame = KNOWN_RAW_FRAMES.get(("temp", value))
        if frame is None:
            frame = frame_write_single_register(unit, 0x0001, value)
            self._append_log("Примечание: для этого temp используется стандартный Modbus CRC.")
        self._run_frame(f"Команда temp -> register 0x0001 = {value}", frame)

    def command_reg0(self, preset: int | None = None):
        try:
            _, _, unit, _ = self._connection_params()
            value = preset if preset is not None else int(self.reg0_var.get().strip())
        except ValueError:
            messagebox.showerror("Ошибка значения", "Для reg0 нужно целое число.")
            return

        if not 0 <= value <= 0xFFFF:
            messagebox.showerror("Ошибка значения", "reg0 должен быть в диапазоне 0..65535.")
            return

        frame = KNOWN_RAW_FRAMES.get(("reg0", value))
        if frame is None:
            frame = frame_write_single_register(unit, 0x0000, value)
            self._append_log("Примечание: для этого reg0 используется стандартный Modbus CRC.")
        self._run_frame(f"Команда reg0 -> register 0x0000 = {value}", frame)

    def command_raw(self):
        try:
            frame = parse_hex_string(self.raw_var.get().strip())
        except ValueError as exc:
            messagebox.showerror("Ошибка raw frame", str(exc))
            return
        self._run_frame("Raw frame", frame)


def main():
    root = tk.Tk()
    app = AirconControlApp(root)
    root.mainloop()
    return app


if __name__ == "__main__":
    main()
