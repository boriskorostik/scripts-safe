 #!/bin/bash
# Конвертер ~/.ssh/config -> ~/.config/mc/hotlist
# Работает с Host, HostName, Port, User и формирует удобный список для mc

SSH_CONFIG="$HOME/.ssh/config"
MC_HOTLIST="$HOME/.config/mc/hotlist"

mkdir -p "$(dirname "$MC_HOTLIST")"

# Чистим старый hotlist
echo "# Midnight Commander Hotlist" > "$MC_HOTLIST"

current_host=""
current_hostname=""
current_user=""
current_port=""

while IFS= read -r line; do
    case "$line" in
        Host\ *)
            # если уже есть предыдущий хост - записываем его
            if [[ -n "$current_host" && -n "$current_hostname" ]]; then
                portpart=""
                if [[ -n "$current_port" ]]; then
                    portpart=":$current_port"
                fi
                userpart=""
                if [[ -n "$current_user" ]]; then
                    userpart="$current_user@"
                fi
                echo "ENTRY \"$current_host\" -> sh://$userpart$current_hostname$portpart" >> "$MC_HOTLIST"
            fi
            # начинаем новый хост
            current_host=$(echo "$line" | awk '{print $2}')
            current_hostname=""
            current_user=""
            current_port=""
            ;;
        HostName\ *)
            current_hostname=$(echo "$line" | awk '{print $2}')
            ;;
        User\ *)
            current_user=$(echo "$line" | awk '{print $2}')
            ;;
        Port\ *)
            current_port=$(echo "$line" | awk '{print $2}')
            ;;
    esac
done < "$SSH_CONFIG"

# последний хост
if [[ -n "$current_host" && -n "$current_hostname" ]]; then
    portpart=""
    if [[ -n "$current_port" ]]; then
        portpart=":$current_port"
    fi
    userpart=""
    if [[ -n "$current_user" ]]; then
        userpart="$current_user@"
    fi
    echo "ENTRY \"$current_host\" -> sh://$userpart$current_hostname$portpart" >> "$MC_HOTLIST"
fi

echo "Hotlist успешно сгенерирован: $MC_HOTLIST"

