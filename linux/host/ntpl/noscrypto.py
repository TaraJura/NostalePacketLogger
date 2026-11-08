"""NosTale login/world packet cipher.

Ported from NosBazar's ``backend/src/noscrypto.{h,cpp}`` (MIT License,
Copyright (c) 2026 NosBazar contributors). Only the wire transforms are
ported; the Python types and the surrounding session code are ours.

The login channel is a simple byte transform. The world channel packs runs
of characters from a 16-entry table into nibbles and XORs the result with a
session-derived key. ``pack``/``unpack`` are used in opposite directions with
different tables: the client packs outgoing packets with the encryption
table; the server's packets unpack with the decryption table.
"""

from __future__ import annotations

#: Chars the client packs into nibbles when sending to the server.
ENCRYPTION_TABLE = bytes(
    (
        0x00, 0x20, 0x2D, 0x2E,
        0x30, 0x31, 0x32, 0x33,
        0x34, 0x35, 0x36, 0x37,
        0x38, 0x39, 0xFF, 0x00,
    )
)
#: Chars the server's packets unpack with (index 14 is the newline).
DECRYPTION_TABLE = bytes(
    (
        0x00, 0x20, 0x2D, 0x2E,
        0x30, 0x31, 0x32, 0x33,
        0x34, 0x35, 0x36, 0x37,
        0x38, 0x39, 0x0A, 0x00,
    )
)


def _mask(packet: bytes, table: bytes) -> list[bool]:
    """True for each byte that can be packed; stops at a NUL byte."""
    mask: list[bool] = []
    for value in packet:
        if value == 0x00:
            break
        mask.append(value in table)
    return mask


def _run_length(mask: list[bool], start: int, value: bool) -> int:
    length = 0
    while start + length < len(mask) and mask[start + length] is value:
        length += 1
    return length


def pack(packet: bytes, table: bytes = ENCRYPTION_TABLE) -> bytes:
    """Pack a client packet (nibble runs + literal runs), terminated by 0xFF."""
    packet = bytes(packet)
    mask = _mask(packet, table)
    output = bytearray()
    position = 0
    while position < len(mask):
        run = _run_length(mask, position, False)
        index = 0
        while index < run and position < len(mask):
            if index % 0x7E == 0:
                output.append(min(run - index, 0x7E))
            output.append(packet[position] ^ 0xFF)
            position += 1
            index += 1
        run = _run_length(mask, position, True)
        index = 0
        while index < run and position < len(mask):
            if index % 0x7E == 0:
                output.append(min(run - index, 0x7E) | 0x80)
            value = table.index(packet[position])
            if index % 2 == 0:
                output.append((value << 4) & 0xFF)
            else:
                output[-1] |= value
            position += 1
            index += 1
    output.append(0xFF)
    return bytes(output)


def unpack(packet: bytes, table: bytes = DECRYPTION_TABLE) -> bytes:
    """Unpack a server packet (opposite of :func:`pack`)."""
    packet = bytes(packet)
    output = bytearray()
    position = 0
    size = len(packet)
    while position < size:
        header = packet[position]
        if header == 0xFF:
            break
        length = header & 0x7F
        packed = bool(header & 0x80)
        position += 1
        if packed:
            iterations = (length + 1) // 2
            for _ in range(iterations):
                if position >= size:
                    break
                two = packet[position]
                position += 1
                output.append(table[two >> 4])
                right = two & 0x0F
                if right == 0:
                    break
                output.append(table[right])
        else:
            for _ in range(length):
                if position >= size:
                    break
                output.append(packet[position] ^ 0xFF)
                position += 1
    return bytes(output)


def login_encrypt(packet: bytes) -> bytes:
    """Client -> login server: newline-terminated XOR/shift transform."""
    data = bytes(packet)
    if not data or data[-1] != 0x0A:
        data += b"\n"
    return bytes((((value ^ 0xC3) + 0x0F) & 0xFF) for value in data)


def login_decrypt(packet: bytes) -> bytes:
    """Login server -> client. Note: not the inverse of :func:`login_encrypt`."""
    return bytes((value - 0x0F) & 0xFF for value in packet)


def world_xor(packet: bytes, session: int, first: bool = False) -> bytes:
    """Session-keyed byte transform used on the world channel."""
    style = -1 if first else (session >> 6) & 3
    key = session & 0xFF
    output = bytearray()
    for value in packet:
        if style == 0:
            transformed = (value + key + 0x40) & 0xFF
        elif style == 1:
            transformed = (value - key - 0x40) & 0xFF
        elif style == 2:
            transformed = ((value ^ 0xC3) + key + 0x40) & 0xFF
        elif style == 3:
            transformed = ((value ^ 0xC3) - key - 0x40) & 0xFF
        else:
            transformed = (value + 0x0F) & 0xFF
        output.append(transformed)
    return bytes(output)


def world_encrypt(packet: bytes, session: int, first: bool = False) -> bytes:
    """Client -> world server: pack with the encryption table, then XOR."""
    return world_xor(pack(packet, ENCRYPTION_TABLE), session, first)


def world_decrypt(packet: bytes) -> bytes:
    """World server -> client: unpack with the decryption table."""
    return unpack(packet, DECRYPTION_TABLE)
