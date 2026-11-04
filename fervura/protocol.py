"""Serial protocol of the two-digit temperature display (see specification.md, section 3).

Frame (23 bytes, big-endian multi-byte fields):
    0      't' (0x74)            header, not obfuscated
    1      show Fahrenheit 0/1   not obfuscated
    2      cpu temperature       u8
    3-4    cpu fan speed         u16
    5      cpu utilization       u8
    6-7    cpu frequency         u16
    8      gpu utilization       u8
    9-10   gpu frequency         u16
    11-14  gpu memory used       u32
    15-18  gpu memory total      u32
    19-20  gpu fan speed         u16
    21     gpu temperature       u8
    22     ram usage             u8
Bytes 2..22 are XORed with 0x02.

Tested on the device (2026-10-15): only the cpu temperature (byte 2) is
shown on the display, as a plain number. The other fields and the
Fahrenheit flag had no visible effect.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, fields

BAUD_RATE = 1_000_000
# Device USB IDs, tried in this order.
DEVICE_IDS = ((0x1F3A, 0x0008), (0x1A86, 0x484A))

HEADER = 0x74
XOR_KEY = 0x02
FRAME_SIZE = 23

_BODY = struct.Struct(">BHBHBHIIHBB")


@dataclass
class Readings:
    cpu_temp: int = 0       # °C
    cpu_fan: int = 0        # RPM
    cpu_usage: int = 0      # %
    cpu_clock: int = 0      # MHz (assumed)
    gpu_usage: int = 0      # %
    gpu_clock: int = 0      # MHz (assumed)
    gpu_mem_used: int = 0   # MB (assumed)
    gpu_mem_total: int = 0  # MB (assumed)
    gpu_fan: int = 0        # RPM
    gpu_temp: int = 0       # °C
    ram_usage: int = 0      # %


def _clamp(value: float, width: int) -> int:
    return max(0, min(int(round(value)), (1 << (8 * width)) - 1))


def build_frame(r: Readings, show_fahrenheit: bool = False) -> bytes:
    body = _BODY.pack(
        _clamp(r.cpu_temp, 1),
        _clamp(r.cpu_fan, 2),
        _clamp(r.cpu_usage, 1),
        _clamp(r.cpu_clock, 2),
        _clamp(r.gpu_usage, 1),
        _clamp(r.gpu_clock, 2),
        _clamp(r.gpu_mem_used, 4),
        _clamp(r.gpu_mem_total, 4),
        _clamp(r.gpu_fan, 2),
        _clamp(r.gpu_temp, 1),
        _clamp(r.ram_usage, 1),
    )
    return bytes([HEADER, 1 if show_fahrenheit else 0]) + bytes(b ^ XOR_KEY for b in body)


def parse_frame(frame: bytes) -> tuple[Readings, bool]:
    """Inverse of build_frame; handy for checking captured traffic."""
    if len(frame) != FRAME_SIZE or frame[0] != HEADER:
        raise ValueError("not a display frame")
    values = _BODY.unpack(bytes(b ^ XOR_KEY for b in frame[2:]))
    return Readings(*values), frame[1] == 1


assert _BODY.size == FRAME_SIZE - 2
assert len(fields(Readings)) == len(_BODY.format) - 1
