"""Settings (%ProgramData%\\Fervura\\config.json, per machine) and Windows autostart."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from . import datadir

log = logging.getLogger(__name__)

APP_NAME = datadir.APP_NAME
SOURCES = {"cpu": "Temperatura da CPU", "gpu": "Temperatura da GPU", "max": "Maior das duas"}
DEFAULT_SOURCE = "cpu"
# config.json keys: "source" (one of SOURCES) and "check_updates" (bool, off by default:
# without it the program never touches the network)
MAX_CONFIG_BYTES = 64 * 1024  # the real file has a few bytes

# None without a data folder (ProgramData not resolvable); only used when storage_ready
CONFIG_FILE: Path | None = None if datadir.DATA_DIR is None else datadir.DATA_DIR / "config.json"
# Where older versions kept it (per user, writable without elevation). Only read, to migrate.
LEGACY_CONFIG_FILE = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME / "config.json"

# Set by main() once datadir.prepare() confirms the folder is protected. While False,
# config.json is neither read nor written and autostart cannot be enabled (no XML file).
storage_ready = False


def _read_config(path: Path) -> dict | None:
    """The JSON object stored in `path`; None if missing, unsafe, too big or invalid."""
    if not datadir.is_safe_file(path):
        log.warning("ignoring %s: link or hard link", path)
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            return None
        # utf-8-sig: files saved by Notepad and similar editors may start with a BOM
        data = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError, RecursionError):  # RecursionError: deeply nested JSON
        return None
    return data if isinstance(data, dict) else None  # valid JSON, but not an object


def _valid_source(value) -> str | None:
    return value if isinstance(value, str) and value in SOURCES else None


def _read_source(path: Path) -> str | None:
    """The "source" stored in `path`; None if missing, unsafe, too big or invalid."""
    data = _read_config(path)
    return None if data is None else _valid_source(data.get("source"))


def _known_keys(data: dict) -> dict:
    """Exactly the known keys, each validated; anything invalid gets its default.
    check_updates is a strict bool: only JSON true turns it on (not 1, "true"...)."""
    return {"source": _valid_source(data.get("source")) or DEFAULT_SOURCE,
            "check_updates": data.get("check_updates") is True}


def load() -> dict:
    """Only the known keys; anything else in the file is dropped."""
    data = _read_config(CONFIG_FILE) if storage_ready else None
    return _known_keys(data or {})


def save(data: dict) -> None:
    """Raises OSError if the file cannot be written (e.g. folder deleted meanwhile)."""
    if not storage_ready:
        log.warning("settings not saved: data folder unavailable")
        return
    if not datadir.is_safe_file(CONFIG_FILE):
        log.warning("settings not saved: %s is a link or hard link", CONFIG_FILE)
        return
    CONFIG_FILE.write_text(json.dumps(_known_keys(data), indent=2), encoding="utf-8")


def migrate_legacy() -> None:
    """First run after the move to %ProgramData%: copy "source" from the old per-user file.
    Nothing in %APPDATA% is deleted or renamed (an elevated delete or rename in a folder the
    user controls could be redirected elsewhere), so the old files stay there."""
    if not storage_ready or os.path.lexists(CONFIG_FILE):
        return
    source = _read_source(LEGACY_CONFIG_FILE)
    if source is not None:
        try:
            save({"source": source})
        except OSError as e:
            log.warning("settings not migrated: %s", e)
            return
        log.info("settings migrated from %s", LEGACY_CONFIG_FILE)


# Elevated programs cannot start from the Run registry key (Windows blocks UAC at logon),
# so autostart is a scheduled task with the highest privileges, triggered at this user's logon.
TASK_NAME = APP_NAME
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: no console flash from the windowed app

_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Inicia o Fervura no logon, com privilégios de administrador.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
      <Delay>PT10S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True,
                          creationflags=_NO_WINDOW)


def autostart_enabled() -> bool:
    try:
        return _schtasks("/Query", "/TN", TASK_NAME).returncode == 0
    except OSError:  # not Windows
        return False


def set_autostart(enabled: bool) -> None:
    """Needs administrator rights, which the program always has."""
    if not enabled:
        if autostart_enabled():
            result = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
            if result.returncode != 0:
                raise OSError(result.stderr.strip() or result.stdout.strip())
        return

    from xml.sax.saxutils import escape

    from .system import current_user, self_command
    exe, args = self_command()
    user = current_user()  # from the process token, not %USERDOMAIN%\%USERNAME%
    xml = _TASK_XML.format(user=escape(user), command=escape(exe),
                           arguments=escape(subprocess.list2cmdline([*args, "--minimized"])))
    # inside the protected folder: no other process can swap the file before schtasks reads it
    if not storage_ready:
        raise OSError(f"pasta de dados protegida indisponível ({datadir.DATA_DIR}); veja o log")
    xml_file = CONFIG_FILE.parent / "autostart-task.xml"
    if not datadir.is_safe_file(xml_file):
        raise OSError(f"arquivo inseguro (link): {xml_file}")
    xml_file.unlink(missing_ok=True)  # leftover of an interrupted run (plain file, checked)
    with open(xml_file, "x", encoding="utf-16") as f:  # schtasks requires UTF-16
        f.write(xml)
    try:
        result = _schtasks("/Create", "/TN", TASK_NAME, "/XML", str(xml_file), "/F")
    finally:
        xml_file.unlink(missing_ok=True)
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or result.stdout.strip())
