"""Windows helpers: bundled file locations, PawnIO detection/installation, elevation, single instance."""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
PROJECT_DIR = Path(__file__).resolve().parent.parent

# Same rule LibreHardwareMonitor uses to decide whether PawnIO must be (re)installed.
PAWNIO_MIN_VERSION = (2, 0, 0)
MUTEX_NAME = "FervuraMutex"  # also AppMutex in the Inno Setup script


# System folders are resolved WITHOUT environment variables: the elevated process (UAC or the
# scheduled task) inherits the environment of the normal user, who can change %SystemDrive%,
# %ProgramData%, %WINDIR%, %USERNAME%... in HKCU\Environment. SHGetKnownFolderPath expands
# those variables too, so it is not used either.
PROFILE_LIST_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"


def is_local_absolute(text: str) -> bool:
    """'X:\\...' without '..' parts nor leftover '%': no UNC, relative or unexpanded paths."""
    return (re.fullmatch(r"[A-Za-z]:\\.*", text) is not None and "%" not in text
            and ".." not in text.split("\\"))


def system_windows_dir() -> Path | None:
    """Shared Windows folder (normally C:\\Windows) from GetSystemWindowsDirectoryW, which
    comes from the kernel, not from %WINDIR%/%SystemRoot%. None if unavailable."""
    if os.name != "nt":
        return None
    get = ctypes.WinDLL("kernel32", use_last_error=True).GetSystemWindowsDirectoryW
    get.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    get.restype = ctypes.c_uint
    buf = ctypes.create_unicode_buffer(32768)
    size = get(buf, len(buf))
    if size == 0 or size >= len(buf) or not is_local_absolute(buf.value):
        return None
    return Path(buf.value)


def _profile_list_program_data() -> str | None:
    """Raw (unexpanded) value `ProgramData` of HKLM\\...\\ProfileList; only administrators
    can change it. Normally '%SystemDrive%\\ProgramData'."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, PROFILE_LIST_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            value, kind = winreg.QueryValueEx(k, "ProgramData")  # never expanded by winreg
    except (ImportError, OSError):
        return None
    if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or not isinstance(value, str):
        return None
    return value


def expand_system_drive(text: str, drive: str) -> str | None:
    """Replaces %SystemDrive% (any case) by `drive`; None if any other %variable% is left."""
    result = re.sub("%systemdrive%", lambda _: drive, text.strip(), flags=re.IGNORECASE)
    return None if "%" in result else result


def program_data_dir() -> Path | None:
    """ProgramData folder (normally C:\\ProgramData): the ProfileList value with %SystemDrive%
    replaced by the drive of the Windows folder. None if it cannot be resolved this way
    (no fallback to %ProgramData%: the caller then works without the data folder)."""
    windows = system_windows_dir()
    value = _profile_list_program_data()
    if windows is None or value is None:
        return None
    path = expand_system_drive(value, windows.drive)
    return Path(path) if path is not None and is_local_absolute(path) else None


def fonts_dir() -> Path | None:
    """Windows fonts folder (normally C:\\Windows\\Fonts); None if unavailable."""
    windows = system_windows_dir()
    return None if windows is None else windows / "Fonts"


def current_user() -> str:
    """'DOMAIN\\user' of this process's token (GetUserNameExW, NameSamCompatible), the same
    form as %USERDOMAIN%\\%USERNAME% but not taken from the environment."""
    if os.name != "nt":
        raise OSError("current_user needs Windows")
    get = ctypes.WinDLL("secur32", use_last_error=True).GetUserNameExW
    get.argtypes = [ctypes.c_int, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
    get.restype = ctypes.c_ubyte  # BOOLEAN
    NAME_SAM_COMPATIBLE = 2
    size = ctypes.c_ulong(1024)
    buf = ctypes.create_unicode_buffer(size.value)
    if not get(NAME_SAM_COMPATIBLE, buf, ctypes.byref(size)) or "\\" not in buf.value:
        raise OSError(f"não foi possível obter o usuário atual: {ctypes.WinError(ctypes.get_last_error())}")
    return buf.value


def lhm_dir() -> Path:
    """Folder with LibreHardwareMonitorLib.dll and its dependencies."""
    if FROZEN:
        return Path(sys._MEIPASS) / "lhm"
    return PROJECT_DIR / "build" / "vendor" / "lhm"


def pawnio_setup_path() -> Path:
    if FROZEN:  # installed by the Inno Setup script next to the program
        return Path(sys.executable).parent / "third_party" / "PawnIO" / "PawnIO_setup.exe"
    return PROJECT_DIR / "build" / "vendor" / "pawnio" / "PawnIO_setup.exe"


def pawnio_version() -> tuple[int, ...] | None:
    """Installed PawnIO version from its uninstall entry, or None if absent."""
    try:
        import winreg
    except ImportError:
        return None
    key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO"
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_READ | view) as k:
                text = winreg.QueryValueEx(k, "DisplayVersion")[0]
            return parse_version(text)
        except (OSError, ValueError):
            continue
    return None


MAX_VERSION_DIGITS = 9  # per part; longer numbers are not a real version


def parse_version(text) -> tuple[int, int, int] | None:
    """'2' -> (2, 0, 0), '2.0.0-beta' -> (2, 0, 0); None without a leading number.
    Each part contributes its numeric prefix; parsing stops after a part with a suffix.
    Only ASCII digits count (not '٢' or '２'); a part with too many digits gives None."""
    parts: list[int] = []
    for part in str(text).strip().split(".")[:3]:
        match = re.match(r"[0-9]+", part.strip())
        if match is None:
            break
        if len(match.group()) > MAX_VERSION_DIGITS:
            return None
        parts.append(int(match.group()))
        if match.end() != len(part.strip()):
            break
    if not parts:
        return None
    return tuple(parts + [0] * (3 - len(parts)))


def pawnio_ok() -> bool:
    v = pawnio_version()
    return v is not None and v >= PAWNIO_MIN_VERSION


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except AttributeError:  # not Windows
        return False


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong), ("fMask", ctypes.c_ulong), ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p), ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p), ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int), ("hInstApp", ctypes.c_void_p), ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_ulong), ("hIconOrMonitor", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


def run_elevated(exe: Path | str, params: str = "", wait: bool = True) -> int | None:
    """Starts `exe` through the UAC prompt. Returns the exit code when waiting,
    or None if the user declined the prompt."""
    SEE_MASK_NOCLOSEPROCESS = 0x40
    info = _ShellExecuteInfo(cbSize=ctypes.sizeof(_ShellExecuteInfo),
                             fMask=SEE_MASK_NOCLOSEPROCESS, lpVerb="runas",
                             lpFile=str(exe), lpParameters=params, nShow=1)
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        return None
    if not wait:
        ctypes.windll.kernel32.CloseHandle(info.hProcess)
        return 0
    ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(info.hProcess)
    return code.value


def install_pawnio() -> bool:
    """Runs the bundled official PawnIO installer (UAC prompt if needed)."""
    setup = pawnio_setup_path()
    if not setup.exists():
        raise FileNotFoundError(setup)
    if is_admin():
        subprocess.run([str(setup), "-install"], check=False)
    else:
        run_elevated(setup, "-install")
    return pawnio_ok()


def self_command() -> tuple[str, list[str]]:
    """Executable and arguments that start this program again."""
    if FROZEN:
        return sys.executable, []
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return str(pythonw if pythonw.exists() else sys.executable), [str(PROJECT_DIR / "iniciar.pyw")]


def restart_as_admin(extra_args: list[str]) -> bool:
    exe, args = self_command()
    params = subprocess.list2cmdline(args + extra_args)
    return run_elevated(exe, params, wait=False) is not None


class SingleInstance:
    """Named mutex; Inno Setup uses the same name to detect the running program."""

    def __init__(self):
        self.handle = None
        self.already_running = False
        if os.name != "nt":
            return
        ERROR_ALREADY_EXISTS = 183
        self.handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        self.already_running = ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS

    def release(self) -> None:
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None
