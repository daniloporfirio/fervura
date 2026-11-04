"""Protected data folder (%ProgramData%\\Fervura) for config.json, log.txt and the autostart XML.

The program always runs as administrator, so its files must not live where a normal
(medium integrity) process could swap them for links and redirect the elevated writes,
renames and deletes. The folder is owned by Administrators or SYSTEM and has a protected
DACL (no inheritance) that gives other users read access only. Equivalent to:

    icacls "%ProgramData%\\Fervura" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F
           *S-1-5-32-544:(OI)(CI)F *S-1-5-32-545:(OI)(CI)RX

Done with the Win32 security API (ctypes) instead of icacls: the folder is created with
the final security descriptor already in place (no window with inherited permissions), and
the checks (link, owner) and the repair use one handle opened without following links.

The path itself never comes from environment variables (see system.program_data_dir), and
every folder above it is checked: no links, and the parent belongs to SYSTEM,
TrustedInstaller or Administrators, so a normal user cannot redirect it after prepare().
"""

from __future__ import annotations

import ctypes
import functools
import os
import stat
import types
from pathlib import Path

from .system import is_local_absolute, program_data_dir

APP_NAME = "Fervura"
_PROGRAM_DATA = program_data_dir()
# None when ProgramData cannot be resolved without the environment: no data folder (safe mode)
DATA_DIR: Path | None = None if _PROGRAM_DATA is None else _PROGRAM_DATA / APP_NAME

# Owner Administrators; SYSTEM and Administrators full control, Users read & execute
# (0x1200a9, the "RX" of icacls); all inherited by subfolders and files; P = protected.
DIR_SDDL = "O:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)"
TRUSTED_OWNERS = ("S-1-5-32-544", "S-1-5-18")  # Administrators, SYSTEM
# accepted owners of the folder right above DATA_DIR (C:\ProgramData belongs to SYSTEM)
PARENT_OWNERS = ("S-1-5-18",  # SYSTEM
                 "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",  # TrustedInstaller
                 "S-1-5-32-544")  # Administrators
_EMPTY_DACL_SDDL = "D:"  # a DACL with no ACEs (not a NULL DACL, which would allow everything)

_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_READ_CONTROL = 0x00020000
_WRITE_DAC = 0x00040000
_WRITE_OWNER = 0x00080000
_FILE_READ_ATTRIBUTES = 0x80
_FILE_SHARE_ALL = 0x7  # read, write, delete
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000  # needed to open a folder
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000  # open the link itself, never its target
_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x1
_DACL_SECURITY_INFORMATION = 0x4
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
_SDDL_REVISION_1 = 1
_ERROR_ALREADY_EXISTS = 183
_INVALID_HANDLE = ctypes.c_void_p(-1).value


def is_safe_file(path: Path) -> bool:
    """True if `path` does not exist or is a plain file: not a link (reparse point) and
    without other hard links, so opening, writing or renaming it touches only this file."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    return stat.S_ISREG(st.st_mode) and st.st_nlink <= 1


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("nLength", ctypes.c_ulong), ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", ctypes.c_int)]


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]


class _FileInfo(ctypes.Structure):  # BY_HANDLE_FILE_INFORMATION
    _fields_ = [("dwFileAttributes", ctypes.c_ulong), ("ftCreationTime", _FileTime),
                ("ftLastAccessTime", _FileTime), ("ftLastWriteTime", _FileTime),
                ("dwVolumeSerialNumber", ctypes.c_ulong), ("nFileSizeHigh", ctypes.c_ulong),
                ("nFileSizeLow", ctypes.c_ulong), ("nNumberOfLinks", ctypes.c_ulong),
                ("nFileIndexHigh", ctypes.c_ulong), ("nFileIndexLow", ctypes.c_ulong)]


@functools.cache
def _api() -> types.SimpleNamespace:
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    p, pp = ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)

    def fn(dll, name, restype, *argtypes):
        f = getattr(dll, name)
        f.restype, f.argtypes = restype, argtypes
        return f

    return types.SimpleNamespace(
        CreateFileW=fn(k32, "CreateFileW", p, wintypes.LPCWSTR, wintypes.DWORD,
                       wintypes.DWORD, p, wintypes.DWORD, wintypes.DWORD, p),
        CloseHandle=fn(k32, "CloseHandle", wintypes.BOOL, p),
        CreateDirectoryW=fn(k32, "CreateDirectoryW", wintypes.BOOL, wintypes.LPCWSTR, p),
        GetFileInformationByHandle=fn(k32, "GetFileInformationByHandle", wintypes.BOOL, p,
                                      ctypes.POINTER(_FileInfo)),
        LocalFree=fn(k32, "LocalFree", p, p),
        StringToSD=fn(adv, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                      wintypes.BOOL, wintypes.LPCWSTR, wintypes.DWORD, pp, p),
        SDToString=fn(adv, "ConvertSecurityDescriptorToStringSecurityDescriptorW",
                      wintypes.BOOL, p, wintypes.DWORD, wintypes.DWORD, pp, p),
        SidToString=fn(adv, "ConvertSidToStringSidW", wintypes.BOOL, p, pp),
        GetSDDacl=fn(adv, "GetSecurityDescriptorDacl", wintypes.BOOL, p,
                     ctypes.POINTER(wintypes.BOOL), pp, ctypes.POINTER(wintypes.BOOL)),
        GetSDOwner=fn(adv, "GetSecurityDescriptorOwner", wintypes.BOOL, p, pp,
                      ctypes.POINTER(wintypes.BOOL)),
        GetSecurityInfo=fn(adv, "GetSecurityInfo", wintypes.DWORD, p, ctypes.c_int,
                           wintypes.DWORD, pp, pp, pp, pp, pp),
        SetSecurityInfo=fn(adv, "SetSecurityInfo", wintypes.DWORD, p, ctypes.c_int,
                           wintypes.DWORD, p, p, p, p),
        SetKernelObjectSecurity=fn(adv, "SetKernelObjectSecurity", wintypes.BOOL, p,
                                   wintypes.DWORD, p),
    )


def _last_error() -> OSError:
    return ctypes.WinError(ctypes.get_last_error())


def _open(path: Path, access: int):
    """Handle to `path` (folder or file); a link is opened itself, not followed."""
    h = _api().CreateFileW(str(path), access, _FILE_SHARE_ALL, None, _OPEN_EXISTING,
                           _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT, None)
    if not h or h == _INVALID_HANDLE:
        raise _last_error()
    return h


def _wide_string(api, ptr: ctypes.c_void_p) -> str:
    try:
        return ctypes.wstring_at(ptr.value)
    finally:
        api.LocalFree(ptr)


def _security(h) -> tuple[str, str]:
    """(owner SID, DACL in SDDL) of an open handle."""
    api = _api()
    owner, sd = ctypes.c_void_p(), ctypes.c_void_p()
    err = api.GetSecurityInfo(h, _SE_FILE_OBJECT,
                              _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
                              ctypes.byref(owner), None, None, None, ctypes.byref(sd))
    if err:
        raise ctypes.WinError(err)
    try:
        text = ctypes.c_void_p()
        if not api.SidToString(owner, ctypes.byref(text)):
            raise _last_error()
        owner_sid = _wide_string(api, text)
        text = ctypes.c_void_p()
        if not api.SDToString(sd, _SDDL_REVISION_1, _DACL_SECURITY_INFORMATION,
                              ctypes.byref(text), None):
            raise _last_error()
        return owner_sid, _wide_string(api, text)
    finally:
        api.LocalFree(sd)


def security_info(path: Path) -> tuple[str, str]:
    """(owner SID, DACL in SDDL) of `path`, without following links. For diagnostics and tests."""
    h = _open(path, _READ_CONTROL | _FILE_READ_ATTRIBUTES)
    try:
        return _security(h)
    finally:
        _api().CloseHandle(h)


def _file_info(h) -> _FileInfo:
    info = _FileInfo()
    if not _api().GetFileInformationByHandle(h, ctypes.byref(info)):
        raise _last_error()
    return info


def _check_ancestors(path: Path, parent_owners: tuple[str, ...]) -> str | None:
    """Every folder above `path` must be a real folder (no junction or symlink anywhere in the
    chain), and the one right above must belong to `parent_owners`, so a normal user can
    neither redirect nor rename it after the checks. Returns why not, or None."""
    for folder in reversed(path.parents):  # C:\ first, then down to the parent
        is_parent = folder == path.parent
        h = _open(folder, _FILE_READ_ATTRIBUTES | (_READ_CONTROL if is_parent else 0))
        try:
            attributes = _file_info(h).dwFileAttributes
            if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                return f"{folder} (above {path}) is a link (reparse point)"
            if not attributes & _FILE_ATTRIBUTE_DIRECTORY:
                return f"{folder} (above {path}) is not a folder"
            if is_parent:
                owner, _ = _security(h)
                if owner not in parent_owners:
                    return (f"parent folder {folder} is owned by {owner}, "
                            "not by SYSTEM, TrustedInstaller or Administrators")
        finally:
            _api().CloseHandle(h)
    return None


def _sd_dacl(sd) -> ctypes.c_void_p | None:
    present, defaulted, dacl = ctypes.c_int(), ctypes.c_int(), ctypes.c_void_p()
    if not _api().GetSDDacl(sd, ctypes.byref(present), ctypes.byref(dacl),
                            ctypes.byref(defaulted)) or not present.value or not dacl.value:
        return None
    return dacl


def _sd_owner(sd) -> ctypes.c_void_p | None:
    owner, defaulted = ctypes.c_void_p(), ctypes.c_int()
    if not _api().GetSDOwner(sd, ctypes.byref(owner), ctypes.byref(defaulted)) or not owner.value:
        return None
    return owner


def _reset_files(path: Path, owner) -> str | None:
    """Files already in the folder (config.json, log.txt and rotations, a leftover task XML)
    keep the owner and explicit ACEs they had before the repair, e.g. Everyone full control.
    Each plain file gets `owner` (when not None) and only the permissions inherited from the
    folder. Links, hard-linked files (their permissions are shared with the other name) and
    subfolders are left alone: the program refuses to use them (is_safe_file) and never
    enters subfolders. Returns why a file could not be reset, or None."""
    api = _api()
    empty_sd = ctypes.c_void_p()
    if not api.StringToSD(_EMPTY_DACL_SDDL, _SDDL_REVISION_1, ctypes.byref(empty_sd), None):
        return f"invalid security descriptor: {_last_error()}"
    try:
        empty = _sd_dacl(empty_sd)
        if empty is None:
            return "empty security descriptor without DACL"
        flags = _DACL_SECURITY_INFORMATION | _UNPROTECTED_DACL_SECURITY_INFORMATION
        access = _READ_CONTROL | _WRITE_DAC | _FILE_READ_ATTRIBUTES
        if owner is not None:
            flags |= _OWNER_SECURITY_INFORMATION
            access |= _WRITE_OWNER
        for name in sorted(os.listdir(path)):
            child = path / name
            # look first with the least access, so links and odd entries are just skipped
            h = _open(child, _FILE_READ_ATTRIBUTES)
            try:
                info = _file_info(h)
            finally:
                api.CloseHandle(h)
            if not _is_plain(info):
                continue
            h = _open(child, access)
            try:
                if not _is_plain(_file_info(h)):  # checked again on the handle that is changed
                    continue
                error = api.SetSecurityInfo(h, _SE_FILE_OBJECT, flags, owner, None, empty, None)
                if error:
                    return f"could not reset the permissions of {child}: {ctypes.WinError(error)}"
            finally:
                api.CloseHandle(h)
    finally:
        api.LocalFree(empty_sd)
    return None


def _is_plain(info: _FileInfo) -> bool:
    """A plain file: not a folder, not a link, no other hard links."""
    return (not info.dwFileAttributes & (_FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT)
            and info.nNumberOfLinks <= 1)


def prepare(path: Path | None = None, sddl: str = DIR_SDDL,
            owners: tuple[str, ...] = TRUSTED_OWNERS,
            parent_owners: tuple[str, ...] = PARENT_OWNERS) -> str | None:
    """Creates the data folder (default DATA_DIR) with the protected DACL, or checks and
    repairs an existing one. Returns None when the folder is safe to use, otherwise the
    reason it is not.

    Refused (never deleted, moved or followed): no resolvable path, a path that is not
    'X:\\...', a link (junction/symlink) at the folder or at any folder above it, a parent
    whose owner is not in `parent_owners`, something that is not a folder, or a folder whose
    owner is not in `owners` (e.g. created beforehand by a normal user). Without
    administrator rights the folder cannot be created or repaired, which is also refused.
    Files already inside get the folder's owner and inherited permissions (_reset_files)."""
    if os.name != "nt":
        return "the protected data folder needs Windows"
    if path is None:
        path = DATA_DIR
    if path is None:
        return "ProgramData folder could not be resolved without environment variables"
    if not is_local_absolute(str(path)):
        return f"{path} is not a local absolute path"
    api = _api()
    sd = ctypes.c_void_p()
    if not api.StringToSD(sddl, _SDDL_REVISION_1, ctypes.byref(sd), None):
        return f"invalid security descriptor: {_last_error()}"
    try:
        problem = _check_ancestors(path, parent_owners)
        if problem:
            return problem
        if not os.path.lexists(path):
            sa = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), sd, 0)
            if not api.CreateDirectoryW(str(path), ctypes.byref(sa)):
                error = ctypes.get_last_error()
                if error != _ERROR_ALREADY_EXISTS:  # created meanwhile: checked below
                    return f"could not create {path}: {ctypes.WinError(error)}"
        h = _open(path, _READ_CONTROL | _WRITE_DAC | _FILE_READ_ATTRIBUTES)
        try:
            info = _file_info(h)
            if info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                return f"{path} is a link (reparse point)"
            if not info.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY:
                return f"{path} is not a folder"
            owner, _ = _security(h)
            if owner not in owners:
                return f"{path} is owned by {owner}, not by Administrators or SYSTEM"
            if _sd_dacl(sd) is None:
                return "security descriptor without DACL"
            # SetKernelObjectSecurity, not SetSecurityInfo: it does not propagate the new
            # inherited ACEs to what is inside, which would also rewrite the permissions of a
            # file elsewhere hard-linked into the folder. Plain files are reset below.
            if not api.SetKernelObjectSecurity(
                    h, _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION, sd):
                return f"could not protect {path}: {_last_error()}"
        finally:
            api.CloseHandle(h)
        return _reset_files(path, _sd_owner(sd))
    except OSError as e:
        return f"could not check {path}: {e}"
    finally:
        api.LocalFree(sd)
    return None
