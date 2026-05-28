#!/usr/bin/env python3
import os

# пути
ssh_config = os.path.expanduser("~/.ssh/config")

# определяем профиль MC
mc_profile = os.environ.get("MC_PROFILE")
if not mc_profile:
    # если переменная пуста, используем ~/.mc
    mc_profile = os.path.expanduser("~/.mc")

hotlist_file = os.path.join(mc_profile, "hotlist")

# читаем ~/.ssh/config
hosts = []
current_host = None
entry = {}

with open(ssh_config, "r") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if line.lower().startswith("host "):
            if current_host and entry.get("HostName"):
                hosts.append((current_host, entry.copy()))
            entry = {}
            current_host = line.split(maxsplit=1)[1]

        elif line.lower().startswith("hostname "):
            entry["HostName"] = line.split(maxsplit=1)[1]
        elif line.lower().startswith("user "):
            entry["User"] = line.split(maxsplit=1)[1]
        elif line.lower().startswith("port "):
            entry["Port"] = line.split(maxsplit=1)[1]

# последний хост
if current_host and entry.get("HostName"):
    hosts.append((current_host, entry.copy()))

# создаём папку профиля, если нет
os.makedirs(mc_profile, exist_ok=True)

# записываем hotlist
with open(hotlist_file, "w") as f:
    f.write("# Midnight Commander Hotlist\n")
    for alias, cfg in hosts:
        hostname = cfg.get("HostName", "")
        user = cfg.get("User", os.getenv("USER"))
        port = cfg.get("Port", "22")
        f.write(f"ENTRY ssh://{user}@{hostname}:{port} {alias}\n")

print(f"Готово! Записано {len(hosts)} хостов в {hotlist_file}")
print(f"MC должен увидеть их после перезапуска.")
