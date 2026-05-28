#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import re
import paramiko
from dataclasses import dataclass
from typing import List, Tuple, Optional

# =======================
# Конфиг целей (без паролей!)
# =======================

# Пример целей. Пароль/ключ запрашивается в GUI.
TARGETS = {
    "Firewall (Ubuntu)": ("192.168.1.1", "fire"),
    "Chinatown (Hypervisor)": ("172.16.0.5", "serf"),
}

# Подсказочные списки интерфейсов (можно расширить)
EXTERNAL_IFACES = ["eno1", "eth0", "ens3", "enp1s0", "eno2"]
INTERNAL_IFACES = ["enp2s0", "eth1", "ens4", "br0", "virbr0", "virbr1"]

TAG_PREFIX = "PFWD_MGR"  # метка для комментов iptables

# =======================
# Utils
# =======================

PORT_RE = re.compile(r"^\d{1,5}(-\d{1,5})?$")

def parse_ports(text: str) -> List[str]:
    """
    Принимает: "80,443,5000-5200"
    Возвращает список токенов: ["80", "443", "5000-5200"]
    """
    items = []
    for raw in (text or "").split(","):
        t = raw.strip()
        if not t:
            continue
        if not PORT_RE.match(t):
            raise ValueError(f"Некорректный порт/диапазон: {t}")
        if "-" in t:
            a, b = t.split("-", 1)
            a_i, b_i = int(a), int(b)
            if not (1 <= a_i <= 65535 and 1 <= b_i <= 65535 and a_i <= b_i):
                raise ValueError(f"Некорректный диапазон: {t}")
        else:
            p = int(t)
            if not (1 <= p <= 65535):
                raise ValueError(f"Некорректный порт: {t}")
        items.append(t)
    return items

def shell_quote(s: str) -> str:
    # простая безопасная экранизация для наших параметров (интерфейсы/айпи/порты)
    # мы валидируем форматы, поэтому это доп. слой.
    return "'" + s.replace("'", "'\"'\"'") + "'"

def is_ipv4(ip: str) -> bool:
    m = re.match(r"^(\d{1,3}\.){3}\d{1,3}$", ip.strip())
    if not m:
        return False
    parts = ip.strip().split(".")
    return all(0 <= int(p) <= 255 for p in parts)

# =======================
# SSH wrapper
# =======================

@dataclass
class SSHConn:
    host: str
    user: str
    port: int
    password: Optional[str] = None
    key_path: Optional[str] = None

class SSHRunner:
    def __init__(self, conn: SSHConn):
        self.conn = conn
        self.client = None

    def connect(self):
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = dict(
            hostname=self.conn.host,
            port=self.conn.port,
            username=self.conn.user,
            timeout=10,
            banner_timeout=10,
            auth_timeout=10,
        )
        if self.conn.key_path:
            pkey = paramiko.RSAKey.from_private_key_file(self.conn.key_path)
            kwargs["pkey"] = pkey
        else:
            kwargs["password"] = self.conn.password or ""
        c.connect(**kwargs)
        self.client = c

    def close(self):
        if self.client:
            self.client.close()
            self.client = None

    def run(self, cmd: str, timeout: int = 30) -> Tuple[int, str, str]:
        """
        Возвращает (exit_status, stdout, stderr)
        """
        if not self.client:
            raise RuntimeError("SSH not connected")
        stdin, stdout, stderr = self.client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        rc = stdout.channel.recv_exit_status()
        return rc, out, err

# =======================
# iptables logic (idempotent + tagged)
# =======================

def rule_exists(r: SSHRunner, table: str, chain: str, rule_spec: str) -> bool:
    # iptables -C возвращает 0 если есть
    cmd = f"sudo iptables -t {table} -C {chain} {rule_spec}"
    rc, _, _ = r.run(cmd)
    return rc == 0

def add_rule(r: SSHRunner, table: str, chain: str, rule_spec: str, insert: bool = False) -> None:
    op = "-I" if insert else "-A"
    cmd = f"sudo iptables -t {table} {op} {chain} {rule_spec}"
    rc, out, err = r.run(cmd)
    if rc != 0:
        raise RuntimeError(f"Не удалось добавить правило: {cmd}\n{out}\n{err}")

def del_rule(r: SSHRunner, table: str, chain: str, rule_spec: str) -> None:
    cmd = f"sudo iptables -t {table} -D {chain} {rule_spec}"
    rc, out, err = r.run(cmd)
    # удаление может не найти правило -> не критично
    # но если другая ошибка — покажем
    if rc != 0 and "No chain/target/match" not in err and "Bad rule" not in err:
        # "Bad rule" бывает при отсутствии точного совпадения, можно игнорировать
        pass

def ensure_ip_forwarding(r: SSHRunner) -> str:
    log = []
    rc, out, err = r.run("sudo sysctl -n net.ipv4.ip_forward")
    if rc == 0:
        log.append(f"net.ipv4.ip_forward currently: {out.strip()}")
    # включим
    rc, out, err = r.run("sudo sysctl -w net.ipv4.ip_forward=1")
    log.append(out.strip() if out.strip() else err.strip())
    # закрепим
    r.run("sudo sh -c \"echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-ipforward.conf\"")
    r.run("sudo sysctl --system >/dev/null 2>&1 || true")
    return "\n".join([x for x in log if x])

def apply_port_forward(
    r: SSHRunner,
    wan_if: str,
    lan_if: str,
    dst_ip: str,
    tcp_ports: List[str],
    udp_ports: List[str],
    enable_masq: bool,
    masq_src_cidr: Optional[str],
    tag_id: str,
) -> str:
    """
    Делает:
    - DNAT: WAN tcp/udp dport -> dst_ip:same
    - FORWARD allow: WAN->LAN to dst_ip dport
    - FORWARD allow: LAN->WAN established handled conntrack (но добавим обратное правило по src ip, если хочешь)
    - optional MASQUERADE: для src-сети в интернет
    """
    if not is_ipv4(dst_ip):
        raise ValueError("dst_ip должен быть IPv4")

    log = []
    log.append(ensure_ip_forwarding(r))

    # Базовые "stateful" правила (если их нет)
    # 1) established/related forward accept
    state_rule = "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"
    if not rule_exists(r, "filter", "FORWARD", state_rule):
        add_rule(r, "filter", "FORWARD", state_rule, insert=True)
        log.append("Added FORWARD established/related ACCEPT")

    # 2) (опционально) разрешить ICMP в forward можно не добавлять

    # DNAT + FORWARD per port token
    def add_dnat_and_forward(proto: str, port_token: str):
        comment = f"-m comment --comment {shell_quote(f'{TAG_PREFIX}:{tag_id}:{proto}:{port_token}:{dst_ip}')}"
        # DNAT only on WAN interface
        dnat_spec = f"-i {wan_if} -p {proto} --dport {port_token} {comment} -j DNAT --to-destination {dst_ip}"
        if not rule_exists(r, "nat", "PREROUTING", dnat_spec):
            add_rule(r, "nat", "PREROUTING", dnat_spec)
            log.append(f"DNAT {proto} {port_token} -> {dst_ip}")

        # Forward allow WAN->LAN to dst_ip port
        fwd_in_spec = f"-i {wan_if} -o {lan_if} -p {proto} -d {dst_ip} --dport {port_token} {comment} -j ACCEPT"
        if not rule_exists(r, "filter", "FORWARD", fwd_in_spec):
            add_rule(r, "filter", "FORWARD", fwd_in_spec)
            log.append(f"FORWARD allow {proto} {port_token} WAN->{dst_ip}")

        # Обратное правило обычно не нужно из-за conntrack, но если у тебя где-то DROP без ctstate — можно оставить:
        # fwd_out_spec = f"-i {lan_if} -o {wan_if} -p {proto} -s {dst_ip} --sport {port_token} {comment} -j ACCEPT"
        # if not rule_exists(r, "filter", "FORWARD", fwd_out_spec):
        #     add_rule(r, "filter", "FORWARD", fwd_out_spec)

    for p in tcp_ports:
        add_dnat_and_forward("tcp", p)
    for p in udp_ports:
        add_dnat_and_forward("udp", p)

    # MASQUERADE (обычно один раз на сеть, не на порт)
    if enable_masq:
        if not masq_src_cidr:
            raise ValueError("Для MASQUERADE нужно указать src CIDR (например 172.16.0.0/20)")
        # Очень важно: masquerade only out WAN
        masq_comment = f"-m comment --comment {shell_quote(f'{TAG_PREFIX}:{tag_id}:MASQ:{masq_src_cidr}')}"
        masq_spec = f"-s {masq_src_cidr} -o {wan_if} {masq_comment} -j MASQUERADE"
        if not rule_exists(r, "nat", "POSTROUTING", masq_spec):
            add_rule(r, "nat", "POSTROUTING", masq_spec)
            log.append(f"MASQUERADE {masq_src_cidr} -> {wan_if}")

    return "\n".join([x for x in log if x.strip()])

def delete_tagged_rules(r: SSHRunner, tag_id: str) -> str:
    """
    Удаляет правила по комментарию PFWD_MGR:tag_id:...
    Делает поиск через iptables-save и удаляет точными -D (самый надежный способ).
    """
    log = []
    rc, out, err = r.run("sudo iptables-save")
    if rc != 0:
        raise RuntimeError(f"iptables-save error:\n{err}")

    # соберём строки правил с нашим тэгом
    lines = [ln for ln in out.splitlines() if f'{TAG_PREFIX}:{tag_id}:' in ln]
    if not lines:
        return "Tagged rules not found (nothing to delete)."

    # iptables-save формат:
    # -A CHAIN ... -m comment --comment "PFWD_MGR:tag:..." ...
    # Нам нужно удалить их через iptables -t <table> -D <chain> <spec>
    # Определяем table по секциям *nat/*filter.
    current_table = None
    for ln in out.splitlines():
        if ln.startswith("*"):
            current_table = ln[1:].strip()
        if f'{TAG_PREFIX}:{tag_id}:' not in ln:
            continue
        if not ln.startswith("-A "):
            continue
        # парсим chain + spec
        parts = ln.split()
        chain = parts[1]
        spec = " ".join(parts[2:])
        # удаляем
        del_rule(r, current_table or "filter", chain, spec)
        log.append(f"Deleted from {current_table}:{chain} -> {spec}")

    return "\n".join(log)

def show_rules_text(r: SSHRunner) -> str:
    cmds = [
        "sudo iptables -S",
        "sudo iptables -t nat -S",
        "sudo iptables -L FORWARD -n -v --line-numbers",
        "sudo iptables -t nat -L PREROUTING -n -v --line-numbers",
        "sudo iptables -t nat -L POSTROUTING -n -v --line-numbers",
    ]
    out_all = []
    for c in cmds:
        rc, out, err = r.run(c, timeout=60)
        out_all.append(f"$ {c}\n{out}")
        if err.strip():
            out_all.append(f"stderr:\n{err}\n")
    return "\n".join(out_all)

# =======================
# GUI
# =======================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Firewall / Port Forward Manager (Ubuntu)")
        self.geometry("920x560")

        self.target_var = tk.StringVar(value=list(TARGETS.keys())[0])
        self.ssh_port_var = tk.StringVar(value="22")
        self.auth_mode_var = tk.StringVar(value="password")  # password | key
        self.password_var = tk.StringVar(value="")
        self.key_path_var = tk.StringVar(value="")

        self.wan_if_var = tk.StringVar(value=EXTERNAL_IFACES[0])
        self.lan_if_var = tk.StringVar(value=INTERNAL_IFACES[0])

        self.dst_ip_var = tk.StringVar(value="192.168.1.2")
        self.tcp_ports_var = tk.StringVar(value="58322,22102")
        self.udp_ports_var = tk.StringVar(value="5000-5200")

        self.enable_masq_var = tk.BooleanVar(value=False)
        self.masq_src_var = tk.StringVar(value="172.16.0.0/20")

        self.tag_id_var = tk.StringVar(value="main")

        self._build()

    def _build(self):
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        row = 0
        ttk.Label(frm, text="Target:").grid(row=row, column=0, sticky="w")
        ttk.OptionMenu(frm, self.target_var, self.target_var.get(), *TARGETS.keys()).grid(row=row, column=1, sticky="we")
        ttk.Label(frm, text="SSH port:").grid(row=row, column=2, sticky="w")
        ttk.Entry(frm, textvariable=self.ssh_port_var, width=8).grid(row=row, column=3, sticky="w")
        row += 1

        ttk.Label(frm, text="Auth:").grid(row=row, column=0, sticky="w")
        ttk.Radiobutton(frm, text="Password", variable=self.auth_mode_var, value="password").grid(row=row, column=1, sticky="w")
        ttk.Radiobutton(frm, text="SSH Key (RSA)", variable=self.auth_mode_var, value="key").grid(row=row, column=1, sticky="e")
        row += 1

        ttk.Label(frm, text="Password:").grid(row=row, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.password_var, show="*").grid(row=row, column=1, sticky="we")
        ttk.Label(frm, text="Key path:").grid(row=row, column=2, sticky="w")
        ttk.Entry(frm, textvariable=self.key_path_var).grid(row=row, column=3, sticky="we")
        row += 1

        ttk.Separator(frm).grid(row=row, column=0, columnspan=4, sticky="we", pady=8)
        row += 1

        ttk.Label(frm, text="WAN iface:").grid(row=row, column=0, sticky="w")
        ttk.OptionMenu(frm, self.wan_if_var, self.wan_if_var.get(), *EXTERNAL_IFACES).grid(row=row, column=1, sticky="we")
        ttk.Label(frm, text="LAN iface:").grid(row=row, column=2, sticky="w")
        ttk.OptionMenu(frm, self.lan_if_var, self.lan_if_var.get(), *INTERNAL_IFACES).grid(row=row, column=3, sticky="we")
        row += 1

        ttk.Label(frm, text="DNAT destination IP:").grid(row=row, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.dst_ip_var).grid(row=row, column=1, sticky="we")
        ttk.Label(frm, text="Tag ID (group):").grid(row=row, column=2, sticky="w")
        ttk.Entry(frm, textvariable=self.tag_id_var).grid(row=row, column=3, sticky="we")
        row += 1

        ttk.Label(frm, text="TCP ports:").grid(row=row, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.tcp_ports_var).grid(row=row, column=1, sticky="we")
        ttk.Label(frm, text="UDP ports:").grid(row=row, column=2, sticky="w")
        ttk.Entry(frm, textvariable=self.udp_ports_var).grid(row=row, column=3, sticky="we")
        row += 1

        ttk.Checkbutton(frm, text="Enable MASQUERADE (SNAT) for src CIDR", variable=self.enable_masq_var).grid(row=row, column=0, columnspan=2, sticky="w")
        ttk.Label(frm, text="src CIDR:").grid(row=row, column=2, sticky="w")
        ttk.Entry(frm, textvariable=self.masq_src_var).grid(row=row, column=3, sticky="we")
        row += 1

        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=4, sticky="we", pady=10)
        ttk.Button(btns, text="Apply (idempotent)", command=self._apply_async).pack(side=tk.LEFT, padx=5)
        ttk.Button(btns, text="Delete tagged rules", command=self._delete_async).pack(side=tk.LEFT, padx=5)
        ttk.Button(btns, text="Show current rules", command=self._show_async).pack(side=tk.LEFT, padx=5)
        row += 1

        self.output = scrolledtext.ScrolledText(frm, height=18)
        self.output.grid(row=row, column=0, columnspan=4, sticky="nsew")
        frm.rowconfigure(row, weight=1)
        frm.columnconfigure(1, weight=1)
        frm.columnconfigure(3, weight=1)

        self._log("Ready.\n")

    def _log(self, text: str):
        self.output.insert(tk.END, text + ("\n" if not text.endswith("\n") else ""))
        self.output.see(tk.END)

    def _get_conn(self) -> SSHConn:
        name = self.target_var.get()
        host, user = TARGETS[name]
        port = int(self.ssh_port_var.get().strip() or "22")
        if self.auth_mode_var.get() == "key":
            key = self.key_path_var.get().strip()
            if not key:
                raise ValueError("Укажи путь к приватному ключу")
            return SSHConn(host=host, user=user, port=port, key_path=key)
        else:
            pwd = self.password_var.get()
            if not pwd:
                raise ValueError("Введи пароль (или переключись на SSH key)")
            return SSHConn(host=host, user=user, port=port, password=pwd)

    def _apply_async(self):
        t = threading.Thread(target=self._apply, daemon=True)
        t.start()

    def _delete_async(self):
        t = threading.Thread(target=self._delete, daemon=True)
        t.start()

    def _show_async(self):
        t = threading.Thread(target=self._show, daemon=True)
        t.start()

    def _apply(self):
        try:
            conn = self._get_conn()
            wan = self.wan_if_var.get().strip()
            lan = self.lan_if_var.get().strip()
            dst_ip = self.dst_ip_var.get().strip()
            tag_id = self.tag_id_var.get().strip() or "main"

            tcp = parse_ports(self.tcp_ports_var.get())
            udp = parse_ports(self.udp_ports_var.get())

            enable_masq = bool(self.enable_masq_var.get())
            masq_src = self.masq_src_var.get().strip() if enable_masq else None

            self._log(f"\n== APPLY to {conn.host} ({conn.user}) ==\n")
            r = SSHRunner(conn)
            r.connect()
            res = apply_port_forward(
                r=r,
                wan_if=wan,
                lan_if=lan,
                dst_ip=dst_ip,
                tcp_ports=tcp,
                udp_ports=udp,
                enable_masq=enable_masq,
                masq_src_cidr=masq_src,
                tag_id=tag_id,
            )
            r.close()
            self._log(res or "Done.")
            messagebox.showinfo("OK", "Правила применены (idempotent).")
        except Exception as e:
            self._log(f"ERROR: {e}\n")
            messagebox.showerror("Ошибка", str(e))

    def _delete(self):
        try:
            conn = self._get_conn()
            tag_id = self.tag_id_var.get().strip() or "main"
            self._log(f"\n== DELETE tagged rules '{tag_id}' on {conn.host} ==\n")
            r = SSHRunner(conn)
            r.connect()
            res = delete_tagged_rules(r, tag_id=tag_id)
            r.close()
            self._log(res or "Done.")
            messagebox.showinfo("OK", "Попытка удаления завершена.")
        except Exception as e:
            self._log(f"ERROR: {e}\n")
            messagebox.showerror("Ошибка", str(e))

    def _show(self):
        try:
            conn = self._get_conn()
            self._log(f"\n== SHOW rules on {conn.host} ==\n")
            r = SSHRunner(conn)
            r.connect()
            res = show_rules_text(r)
            r.close()

            win = tk.Toplevel(self)
            win.title(f"Rules: {conn.host}")
            txt = scrolledtext.ScrolledText(win, width=120, height=40)
            txt.pack(fill=tk.BOTH, expand=True)
            txt.insert(tk.END, res)
            txt.config(state=tk.DISABLED)
        except Exception as e:
            self._log(f"ERROR: {e}\n")
            messagebox.showerror("Ошибка", str(e))

if __name__ == "__main__":
    App().mainloop()