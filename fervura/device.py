"""Finds the display's COM port and keeps the connection alive."""

from __future__ import annotations

import logging

import serial
from serial.tools import list_ports

from .protocol import BAUD_RATE, DEVICE_IDS

log = logging.getLogger(__name__)


def find_port() -> str | None:
    ports = list(list_ports.comports())
    for vid, pid in DEVICE_IDS:
        for p in ports:
            if p.vid == vid and p.pid == pid:
                return p.device
    return None


class Display:
    """Opens the port lazily and reopens it after any serial error."""

    def __init__(self, port: str | None = None):
        self.fixed_port = port
        self.ser: serial.Serial | None = None
        self._last_problem: str | None = None  # avoids repeating the same warning every second

    def _report(self, message: str) -> None:
        """Logs a connection problem once, until it changes or a connection succeeds."""
        if message != self._last_problem:
            log.warning("%s", message)
            self._last_problem = message

    @property
    def port(self) -> str | None:
        return self.ser.port if self.ser else None

    @property
    def connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def connect(self) -> bool:
        if self.connected:
            return True
        port = self.fixed_port or find_port()
        if port is None:
            ids = ", ".join(f"{vid:04X}:{pid:04X}" for vid, pid in DEVICE_IDS)
            self._report(f"display not found (VID:PID {ids})")
            return False
        try:
            self.ser = serial.Serial(
                port, BAUD_RATE,
                bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE, xonxoff=False, rtscts=False,
                write_timeout=1,
            )
        except serial.SerialException as e:
            self._report(f"could not open {port}: {e}")
            self.ser = None
            return False
        self._last_problem = None
        log.info("connected on %s", port)
        return True

    def send(self, frame: bytes) -> bool:
        if not self.connect():
            return False
        try:
            self.ser.write(frame)
            return True
        except serial.SerialException as e:
            log.warning("write failed, reconnecting: %s", e)
            self.close()
            return False

    def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            except serial.SerialException:
                pass
            self.ser = None
