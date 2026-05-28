#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# M-Bus TCP Scanner — ЖИВОЙ ВЫВОД + СТОП РАБОТАЕТ + ВСЁ ЧИСТО

import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import queue
import re
from datetime import datetime

output_queue = queue.Queue()
scan_process = None
stop_flag = threading.Event()

def reader_thread(pipe):
    """Отдельный поток — читает вывод построчно и сразу кидает в очередь"""
    try:
        for line in iter(pipe.readline, ''):
            if stop_flag.is_set():
                break
            if line:
                output_queue.put(line.rstrip())
    finally:
        pipe.close()

def run_scan(cmd_list):
    global scan_process
    stop_flag.clear()

    ip = ip_entry.get().strip()
    port = port_entry.get().strip()
    if not ip or not port.isdigit():
        messagebox.showerror("Ошибка", "Проверьте IP и порт")
        return

    # Подставляем IP и порт
    cmd = [arg.replace("IP", ip).replace("PORT", port) for arg in cmd_list]

    output_text.delete(1.0, tk.END)
    tree.delete(*tree.get_children())
    for btn in [btn_full, btn_fast, btn_simple]:
        btn.config(state="disabled")
    btn_stop.config(state="normal")
    save_button.config(state="disabled")

    output_text.insert(tk.END, f"Запуск: {' '.join(cmd)}\n\n")

    try:
        scan_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )

        # ←←← КЛЮЧЕВОЕ: отдельный поток читает вывод мгновенно
        threading.Thread(target=reader_thread, args=(scan_process.stdout,), daemon=True).start()

        scan_process.wait()  # ждём завершения
    except FileNotFoundError:
        output_queue.put("ОШИБКА: mbus-tcp-scan-secondary не найдена в системе!")
        output_queue.put("Установите: sudo apt install mbus-utils")
    except Exception as e:
        output_queue.put(f"Ошибка запуска: {e}")
    finally:
        if not stop_flag.is_set():
            output_queue.put("__SCAN_DONE__")

# 1. ВСЯ ШИНА
def full_scan():
    threading.Thread(target=run_scan, args=(["mbus-tcp-scan-secondary", "IP", "PORT", "FFFFFFFFFFFFFFFF"],), daemon=True).start()

# 2. По маске
def fast_scan():
    mask = mask_entry.get().strip().upper()
    if len(mask) != 16 or not re.fullmatch(r"[0-9A-F]{16}", mask):
        messagebox.showerror("Ошибка", "Маска должна быть 16 символов (0-9, A-F)")
        return
    threading.Thread(target=run_scan, args=(["mbus-tcp-scan-secondary", "IP", "PORT", mask],), daemon=True).start()

# 3. ПРОСТО СКАНИРОВАНИЕ без маски — ТО ЧТО ТЫ ПРОСИЛ!
def simple_scan():
    threading.Thread(target=run_scan, args=(["mbus-tcp-scan-secondary", "IP", "PORT"],), daemon=True).start()

# СТОП — теперь работает идеально
def stop_scan():
    global scan_process
    stop_flag.set()
    if scan_process and scan_process.poll() is None:
        try:
            scan_process.kill()
            scan_process.wait(timeout=2)
        except:
            pass
    output_queue.put("\nСКАНИРОВАНИЕ ПРЕРВАНО ПОЛЬЗОВАТЕЛЕМ\n")
    finalize_scan()

def finalize_scan():
    for btn in [btn_full, btn_fast, btn_simple]:
        btn.config(state="normal")
    btn_stop.config(state="disabled")
    save_button.config(state="normal")
    parse_and_fill()

def check_output():
    try:
        while True:
            line = output_queue.get_nowait()
            if line == "__SCAN_DONE__":
                finalize_scan()
                output_text.insert(tk.END, "\nГОТОВО! Сканирование завершено.\n")
                output_text.see(tk.END)
                return

            output_text.insert(tk.END, line + "\n")
            output_text.see(tk.END)

            if "Found a device" in line or "secondary address" in line:
                parse_and_fill()

    except queue.Empty:
        pass
    root.after(50, check_output)

def parse_and_fill():
    raw = output_text.get("1.0", tk.END)
    devices = re.findall(r"secondary address ([0-9A-F]{16})", raw, re.IGNORECASE)
    unique = sorted({d[:8].upper() for d in devices})
    tree.delete(*tree.get_children())
    for addr in unique:
        tree.insert("", "end", values=[addr])
    root.title(f"M-Bus Scanner • Найдено: {len(unique)} приборов")

def save_list():
    items = tree.get_children()
    if not items:
        messagebox.showwarning("Пусто", "Список пуст")
        return
    ip = ip_entry.get().strip().replace(".", "_")
    name = f"M-Bus_{ip}_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.csv"
    path = filedialog.asksaveasfilename(initialfile=name, defaultextension=".csv")
    if not path: return
    with open(path, "w", encoding="utf-8") as f:
        f.write("№;Адрес (8 симв.)\n")
        for i, item in enumerate(items, 1):
            f.write(f"{i};{tree.item(item)['values'][0]}\n")
    messagebox.showinfo("Готово", f"Сохранено {len(items)} приборов\n{path}")

# GUI
root = tk.Tk()
root.title("M-Bus Scanner — Живой вывод + СТОП работает")
root.geometry("1100x860")

frame = ttk.Frame(root, padding=20)
frame.pack(fill="x")

ttk.Label(frame, text="IP шлюза:", font=("", 11)).grid(row=0, column=0, sticky="w", pady=8)
ip_entry = ttk.Entry(frame, width=25, font=("", 11))
ip_entry.grid(row=0, column=1, padx=10, pady=8)
ip_entry.insert(0, "31.134.131.254")

ttk.Label(frame, text="Порт:", font=("", 11)).grid(row=1, column=0, sticky="w", pady=8)
port_entry = ttk.Entry(frame, width=12, font=("", 11))
port_entry.grid(row=1, column=1, padx=10, pady=8, sticky="w")
port_entry.insert(0, "4003")

ttk.Separator(frame, orient="horizontal").grid(row=2, columnspan=3, sticky="ew", pady=20)

btns = ttk.Frame(frame)
btns.grid(row=3, columnspan=3, pady=15)

btn_full = ttk.Button(btns, text="1. ВСЯ ШИНА (быстро)", command=full_scan)
btn_full.pack(side="left", padx=12)

btn_fast = ttk.Button(btns, text="2. По маске", command=fast_scan)
btn_fast.pack(side="left", padx=12)

btn_simple = ttk.Button(btns, text="3. ПРОСТО СКАНИРОВАНИЕ", command=simple_scan)
btn_simple.pack(side="left", padx=12)

btn_stop = ttk.Button(btns, text="СТОП", command=stop_scan, state="disabled")
btn_stop.pack(side="left", padx=12)

save_button = ttk.Button(btns, text="Сохранить", command=save_list, state="disabled")
save_button.pack(side="left", padx=12)

ttk.Label(frame, text="Маска (16 символов):", font=("", 10)).grid(row=4, column=0, sticky="w", pady=10)
mask_entry = ttk.Entry(frame, width=40, font=("", 10))
mask_entry.grid(row=4, column=1, padx=10, pady=10)
mask_entry.insert(0, "23101261FFFFFFFF")

# Таблица и лог
tframe = ttk.LabelFrame(root, text=" Найденные приборы ", padding=10)
tframe.pack(fill="both", expand=True, padx=20, pady=10)
tree = ttk.Treeview(tframe, columns=("addr",), show="headings", height=18)
tree.heading("addr", text="Адрес (8 симв.)")
tree.column("addr", anchor="center", width=160)
tree.pack(fill="both", expand=True)

lframe = ttk.LabelFrame(root, text=" Живой вывод (обновляется мгновенно) ", padding=10)
lframe.pack(fill="both", expand=True, padx=20, pady=10)
output_text = tk.Text(lframe, font=("Consolas", 10), wrap="none")
sb1 = ttk.Scrollbar(lframe, command=output_text.yview)
sb2 = ttk.Scrollbar(lframe, orient="horizontal", command=output_text.xview)
output_text.config(yscrollcommand=sb1.set, xscrollcommand=sb2.set)
output_text.pack(side="left", fill="both", expand=True)
sb1.pack(side="right", fill="y")
sb2.pack(side="bottom", fill="x")

root.after(50, check_output)
root.mainloop()