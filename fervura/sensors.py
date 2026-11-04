"""Hardware readings for Windows.

  - Temperatures and GPU usage/clock/memory: LibreHardwareMonitorLib, loaded
    in-process through pythonnet on the .NET Framework that ships with
    Windows. CPU temperature needs the PawnIO driver.
  - CPU usage/clock and RAM usage: psutil.
Anything a source cannot provide stays None.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Preferred sensor names, first match wins; otherwise the first nonzero sensor of that type.
CPU_TEMP_NAMES = ("package", "tctl", "tdie", "core max", "core average")
GPU_PREFERENCE = ("GpuNvidia", "GpuAmd", "GpuIntel")  # discrete cards first


@dataclass
class Snapshot:
    cpu_name: str = ""
    gpu_name: str = ""
    cpu_temp: float | None = None       # °C
    gpu_temp: float | None = None       # °C
    cpu_usage: float | None = None      # %
    cpu_clock: float | None = None      # MHz
    ram_usage: float | None = None      # %
    gpu_usage: float | None = None      # %
    gpu_clock: float | None = None      # MHz
    gpu_mem_used: float | None = None   # MB
    gpu_mem_total: float | None = None  # MB


class LhmReader:
    def __init__(self, lib_dir: Path):
        if not (lib_dir / "LibreHardwareMonitorLib.dll").exists():
            raise FileNotFoundError(lib_dir / "LibreHardwareMonitorLib.dll")
        # pythonnet resolves dependent assemblies from sys.path entries.
        sys.path.append(str(lib_dir))
        from pythonnet import load
        load("netfx")
        import clr
        clr.AddReference("LibreHardwareMonitorLib")
        from LibreHardwareMonitor.Hardware import Computer

        self.computer = Computer()
        self.computer.IsCpuEnabled = True
        self.computer.IsGpuEnabled = True
        self.computer.Open()
        self._logged = False

    def read(self) -> dict:
        cpus, gpus = [], []
        for hw in self.computer.Hardware:
            kind = str(hw.HardwareType)
            if kind == "Cpu":
                cpus.append((hw.Name, _sensors(hw)))
            elif kind in GPU_PREFERENCE:
                gpus.append((GPU_PREFERENCE.index(kind), hw.Name, _sensors(hw)))
        if not self._logged:
            self._logged = True
            for name, sensors in cpus + [(n, s) for _, n, s in gpus]:
                temps = [f"{s['name']}={s['value']}" for s in sensors if s["type"] == "Temperature"]
                log.info("hardware %s: temperaturas %s", name, temps or "nenhuma")

        out: dict = {}
        if cpus:
            name, sensors = cpus[0]
            out["cpu_name"] = name
            out["cpu_temp"] = _pick(sensors, "Temperature", CPU_TEMP_NAMES)
        if gpus:
            _, name, sensors = min(gpus, key=lambda g: g[0])
            out["gpu_name"] = name
            out["gpu_temp"] = _pick(sensors, "Temperature", ("gpu core", "edge"))
            out["gpu_usage"] = _pick(sensors, "Load", ("gpu core", "d3d 3d"))
            out["gpu_clock"] = _pick(sensors, "Clock", ("gpu core",))
            out["gpu_mem_used"] = _pick(sensors, "SmallData", ("gpu memory used", "d3d dedicated memory used"))
            out["gpu_mem_total"] = _pick(sensors, "SmallData", ("gpu memory total",))
        return out

    def close(self) -> None:
        self.computer.Close()


def _sensors(hw) -> list[dict]:
    hw.Update()
    items = list(hw.Sensors)
    for sub in hw.SubHardware:
        sub.Update()
        items.extend(sub.Sensors)
    return [{"type": str(s.SensorType), "name": str(s.Name),
             "value": None if s.Value is None else float(s.Value)} for s in items]


def _pick(sensors: list[dict], sensor_type: str, preferred_names) -> float | None:
    candidates = [s for s in sensors if s["type"] == sensor_type and s["value"] is not None]
    if sensor_type == "Temperature":
        # Without the needed access LHM may report 0 instead of no value.
        candidates = [s for s in candidates if s["value"] > 0]
    for name in preferred_names:
        for s in candidates:
            if name in s["name"].lower():
                return s["value"]
    nonzero = [s for s in candidates if s["value"] > 0]
    return nonzero[0]["value"] if nonzero else None


class SensorHub:
    def __init__(self, lib_dir: Path):
        self.lhm = LhmReader(lib_dir)
        self.ps = None
        try:
            import psutil
            self.ps = psutil
            psutil.cpu_percent()  # first call primes the counter
        except ImportError as e:
            log.info("psutil unavailable: %s", e)

    def read(self) -> Snapshot:
        s = Snapshot()
        if self.ps is not None:
            s.cpu_usage = self.ps.cpu_percent()
            s.ram_usage = self.ps.virtual_memory().percent
            freq = self.ps.cpu_freq()
            s.cpu_clock = freq.current if freq and freq.current else None
        for key, value in self.lhm.read().items():
            if value is not None:
                setattr(s, key, value)
        return s

    def close(self) -> None:
        self.lhm.close()
