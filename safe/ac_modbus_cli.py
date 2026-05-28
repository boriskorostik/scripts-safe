#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import socket
import sys


DEFAULT_HOST = "172.16.0.99"
DEFAULT_PORT = 4001
DEFAULT_UNIT = 1
DEFAULT_TIMEOUT = 1.0

KNOWN_RAW_FRAMES = {
    ("temp", 22): bytes.fromhex("01 06 00 01 00 16 98 06"),
    ("reg0", 22): bytes.fromhex("01 06 00 00 00 16 09 CB"),
}


def modbus_crc(payload: bytes) -> bytes:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, byteorder="little")


def build_frame(unit: int, function: int, data: bytes) -> bytes:
    payload = bytes([unit, function]) + data
    return payload + modbus_crc(payload)


def frame_write_single_coil(unit: int, coil_addr: int, enabled: bool) -> bytes:
    value = 0xFF00 if enabled else 0x0000
    data = coil_addr.to_bytes(2, "big") + value.to_bytes(2, "big")
    return build_frame(unit, 0x05, data)


def frame_write_single_register(unit: int, register_addr: int, value: int) -> bytes:
    data = register_addr.to_bytes(2, "big") + value.to_bytes(2, "big")
    return build_frame(unit, 0x06, data)


def frame_read_registers(unit: int, function: int, register_addr: int, count: int) -> bytes:
    data = register_addr.to_bytes(2, "big") + count.to_bytes(2, "big")
    return build_frame(unit, function, data)


def frame_read_coils(unit: int, function: int, coil_addr: int, count: int) -> bytes:
    data = coil_addr.to_bytes(2, "big") + count.to_bytes(2, "big")
    return build_frame(unit, function, data)


def send_frame(host: str, port: int, frame: bytes, timeout: float) -> bytes:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(frame)
        try:
            return sock.recv(1024)
        except socket.timeout:
            return b""


def hex_bytes(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def parse_hex_string(raw: str) -> bytes:
    cleaned = raw.replace("\\x", " ").replace(",", " ").replace("0x", " ")
    parts = cleaned.split()
    if not parts:
        raise ValueError("Пустая hex-строка")
    return bytes(int(part, 16) for part in parts)


def command_on(args):
    frame = frame_write_single_coil(args.unit, 0x0000, True)
    return run_command(args, frame, "Включение")


def command_off(args):
    frame = frame_write_single_coil(args.unit, 0x0000, False)
    return run_command(args, frame, "Выключение")


def command_temp(args):
    if not 0 <= args.value <= 0xFFFF:
        raise ValueError("Температура/значение должно быть в диапазоне 0..65535")
    frame = KNOWN_RAW_FRAMES.get(("temp", args.value))
    if frame is None:
        frame = frame_write_single_register(args.unit, 0x0001, args.value)
        print("Предупреждение: для этого значения используется стандартный расчёт Modbus CRC.", file=sys.stderr)
    return run_command(args, frame, f"Установка register 0x0001 = {args.value}")


def command_reg0(args):
    if not 0 <= args.value <= 0xFFFF:
        raise ValueError("Значение должно быть в диапазоне 0..65535")
    frame = KNOWN_RAW_FRAMES.get(("reg0", args.value))
    if frame is None:
        frame = frame_write_single_register(args.unit, 0x0000, args.value)
        print("Предупреждение: для этого значения используется стандартный расчёт Modbus CRC.", file=sys.stderr)
    return run_command(args, frame, f"Установка register 0x0000 = {args.value}")


def command_raw(args):
    frame = parse_hex_string(args.hex_data)
    return run_command(args, frame, "Raw frame", prebuilt=True)


def command_read_holding(args):
    frame = frame_read_registers(args.unit, 0x03, args.address, args.count)
    return run_command(args, frame, f"Чтение holding registers c 0x{args.address:04X}, count={args.count}", prebuilt=True)


def command_read_input(args):
    frame = frame_read_registers(args.unit, 0x04, args.address, args.count)
    return run_command(args, frame, f"Чтение input registers c 0x{args.address:04X}, count={args.count}", prebuilt=True)


def command_read_coils(args):
    frame = frame_read_coils(args.unit, 0x01, args.address, args.count)
    return run_command(args, frame, f"Чтение coils c 0x{args.address:04X}, count={args.count}", prebuilt=True)


def command_read_discrete(args):
    frame = frame_read_coils(args.unit, 0x02, args.address, args.count)
    return run_command(args, frame, f"Чтение discrete inputs c 0x{args.address:04X}, count={args.count}", prebuilt=True)


def run_command(args, frame: bytes, title: str, prebuilt: bool = False):
    print(f"{title}")
    print(f"Host: {args.host}:{args.port}")
    print(f"TX:   {hex_bytes(frame)}")

    if args.dry_run:
        print("Dry-run: пакет не отправлялся")
        return 0

    response = send_frame(args.host, args.port, frame, args.timeout)
    if response:
        print(f"RX:   {hex_bytes(response)}")
    else:
        print("RX:   <нет ответа>")

    if not prebuilt and args.verify_echo and response and response != frame:
        print("Примечание: устройство ответило нестандартно, но команда могла выполниться.", file=sys.stderr)

    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Управление кондиционером по Modbus RTU-over-TCP."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"IP устройства, по умолчанию {DEFAULT_HOST}")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int, help=f"TCP порт, по умолчанию {DEFAULT_PORT}")
    parser.add_argument("--unit", default=DEFAULT_UNIT, type=int, help=f"Modbus unit id, по умолчанию {DEFAULT_UNIT}")
    parser.add_argument("--timeout", default=DEFAULT_TIMEOUT, type=float, help=f"Таймаут сокета, по умолчанию {DEFAULT_TIMEOUT}")
    parser.add_argument("--dry-run", action="store_true", help="Только показать пакет, не отправлять")
    parser.add_argument("--no-verify-echo", dest="verify_echo", action="store_false", help="Не сравнивать echo-ответ с запросом")
    parser.set_defaults(verify_echo=True)

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("on", help="Включить кондиционер").set_defaults(func=command_on)
    subparsers.add_parser("off", help="Выключить кондиционер").set_defaults(func=command_off)

    temp_parser = subparsers.add_parser("temp", help="Записать значение в register 0x0001")
    temp_parser.add_argument("value", type=int, help="Значение register 0x0001, например 22")
    temp_parser.set_defaults(func=command_temp)

    reg0_parser = subparsers.add_parser("reg0", help="Записать значение в register 0x0000")
    reg0_parser.add_argument("value", type=int, help="Значение register 0x0000, например 22")
    reg0_parser.set_defaults(func=command_reg0)

    raw_parser = subparsers.add_parser("raw", help="Отправить raw hex frame")
    raw_parser.add_argument("hex_data", help=r"Например: '01 05 00 00 FF 00 8C 3A' или '\\x01\\x05...'")
    raw_parser.set_defaults(func=command_raw)

    read_holding_parser = subparsers.add_parser("read-holding", help="Читать holding registers (функция 0x03)")
    read_holding_parser.add_argument("address", type=lambda x: int(x, 0), help="Адрес регистра, например 1 или 0x0001")
    read_holding_parser.add_argument("count", type=int, nargs="?", default=1, help="Сколько регистров читать")
    read_holding_parser.set_defaults(func=command_read_holding)

    read_input_parser = subparsers.add_parser("read-input", help="Читать input registers (функция 0x04)")
    read_input_parser.add_argument("address", type=lambda x: int(x, 0), help="Адрес регистра, например 1 или 0x0001")
    read_input_parser.add_argument("count", type=int, nargs="?", default=1, help="Сколько регистров читать")
    read_input_parser.set_defaults(func=command_read_input)

    read_coils_parser = subparsers.add_parser("read-coils", help="Читать coils (функция 0x01)")
    read_coils_parser.add_argument("address", type=lambda x: int(x, 0), help="Адрес coil")
    read_coils_parser.add_argument("count", type=int, nargs="?", default=1, help="Сколько читать")
    read_coils_parser.set_defaults(func=command_read_coils)

    read_discrete_parser = subparsers.add_parser("read-discrete", help="Читать discrete inputs (функция 0x02)")
    read_discrete_parser.add_argument("address", type=lambda x: int(x, 0), help="Адрес discrete input")
    read_discrete_parser.add_argument("count", type=int, nargs="?", default=1, help="Сколько читать")
    read_discrete_parser.set_defaults(func=command_read_discrete)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
