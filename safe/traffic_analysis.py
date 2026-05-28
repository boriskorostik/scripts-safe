import requests
import matplotlib.pyplot as plt
from collections import defaultdict

def fetch_data(url):
    """
    Загружает данные с указанного URL и возвращает их в виде списка строк.
    """
    response = requests.get(url)
    response.raise_for_status()  # Проверка на успешность запроса
    data = response.text.splitlines()
    return data

def process_line(line):
    """
    Обрабатывает строку данных, возвращая IP и объем трафика.
    Если строка содержит некорректные данные, возвращает None.
    """
    parts = line.split(',')

    # Проверяем, что данные корректны
    if len(parts) < 5:
        print(f"Некорректный формат строки (недостаточно данных): {line}")
        return None

    try:
        ip = parts[0]
        data_volume = int(parts[4])  # Объем трафика
        return ip, data_volume
    except ValueError:
        print(f"Ошибка преобразования трафика в int: {line}")
        return None  # Пропускаем строку, если не удается преобразовать объем трафика в число

def analyze_and_plot(url):
    """
    Загружает данные, анализирует объем трафика по IP и строит график.
    """
    # Словарь для хранения объемов трафика по IP-адресам
    traffic_data = defaultdict(int)

    # Загружаем и обрабатываем данные
    data = fetch_data(url)
    for line in data:
        result = process_line(line)
        if result:
            ip, data_volume = result
            traffic_data[ip] += data_volume

    # Проверяем, есть ли данные для построения графика
    if not traffic_data:
        print("Нет корректных данных для построения графика.")
        return

    # Построение графика
    ips = list(traffic_data.keys())
    volumes = list(traffic_data.values())

    plt.figure(figsize=(10, 6))
    plt.bar(ips, volumes, color='skyblue')
    plt.xlabel("IP-адреса")
    plt.ylabel("Объем трафика")
    plt.title("Объем трафика по IP-адресам")
    plt.xticks(rotation=45, ha='right')
    plt.tight_layout()
    plt.show()

# URL для получения данных
url = "http://31.134.131.254/accounting/ip.cgi"

# Запуск анализа и построение графика
analyze_and_plot(url)
