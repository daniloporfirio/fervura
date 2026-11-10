"""Opens a URL in the user's normal (not elevated) session, through the desktop shell.

Fervura always runs elevated. ShellExecute, os.startfile or webbrowser called from here would
start the browser elevated too, and so would "explorer.exe <url>" (it inherits this token).
Instead the desktop's Explorer, which runs without elevation, is asked to run it, over COM:

    ShellWindows -> FindWindowSW(SWC_DESKTOP) -> IServiceProvider::QueryService
    (SID_STopLevelBrowser) -> IShellBrowser::QueryActiveShellView -> IShellView::GetItemObject
    (SVGIO_BACKGROUND) -> ShellFolderView.Application -> IShellDispatch2::ShellExecute(url)

Only ctypes, no new dependency. If the desktop shell is missing or is itself elevated, nothing
is opened and the caller gets an error (the window then offers "Copiar link").
"""

from __future__ import annotations

import ctypes
import re
import threading
import uuid
from typing import Callable

# only plain https addresses; no spaces, quotes or shell metacharacters
_SAFE_URL = re.compile(r"https://[A-Za-z0-9._~/+:?=&#-]+")
MAX_URL_LENGTH = 2048


def is_safe_url(url) -> bool:
    return isinstance(url, str) and len(url) <= MAX_URL_LENGTH and _SAFE_URL.fullmatch(url) is not None


class _GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]


def _guid(text: str) -> _GUID:
    return _GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


CLSID_ShellWindows = "9BA05972-F6A8-11CF-A442-00A0C90A8F39"
IID_IShellWindows = "85CB6900-4D95-11CF-960C-0080C7F4EE85"
IID_IServiceProvider = "6D5140C1-7436-11CE-8034-00AA006009FA"
SID_STopLevelBrowser = "4C96BE40-915C-11CF-99D3-00AA004AE837"
IID_IShellBrowser = "000214E2-0000-0000-C000-000000000046"
IID_IDispatch = "00020400-0000-0000-C000-000000000046"
IID_IShellDispatch2 = "A4C6892C-3BA9-11D2-9DEA-00C04FB16162"
IID_NULL = "00000000-0000-0000-0000-000000000000"

CLSCTX_LOCAL_SERVER = 0x4  # never in-process: the shell object must live in Explorer
COINIT_APARTMENTTHREADED = 0x2
CSIDL_DESKTOP = 0
SWC_DESKTOP = 8
SWFO_NEEDDISPATCH = 1
SVGIO_BACKGROUND = 0
DISPATCH_METHOD, DISPATCH_PROPERTYGET = 1, 2
LOCALE_USER_DEFAULT = 0x0400
VT_EMPTY, VT_I4, VT_BSTR, VT_DISPATCH = 0, 3, 8, 9
SW_SHOWNORMAL = 1

# vtable slots
_QUERY_INTERFACE, _RELEASE = 0, 2
_GET_IDS_OF_NAMES, _INVOKE = 5, 6  # IDispatch
_FIND_WINDOW_SW = 15  # IShellWindows
_QUERY_SERVICE = 3  # IServiceProvider
_QUERY_ACTIVE_SHELL_VIEW = 15  # IShellBrowser
_GET_ITEM_OBJECT = 15  # IShellView


class _VariantValue(ctypes.Union):
    _fields_ = [("lVal", ctypes.c_long), ("bstrVal", ctypes.c_void_p),
                ("pdispVal", ctypes.c_void_p), ("_pad", ctypes.c_void_p * 2)]


class _VARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("_r1", ctypes.c_ushort), ("_r2", ctypes.c_ushort),
                ("_r3", ctypes.c_ushort), ("value", _VariantValue)]


class _DISPPARAMS(ctypes.Structure):
    _fields_ = [("rgvarg", ctypes.POINTER(_VARIANT)), ("rgdispidNamedArgs", ctypes.POINTER(ctypes.c_long)),
                ("cArgs", ctypes.c_uint), ("cNamedArgs", ctypes.c_uint)]


def _call(ptr: ctypes.c_void_p, slot: int, argtypes: tuple, *args, restype=ctypes.HRESULT):
    """Calls method `slot` of the COM interface `ptr` (HRESULT failures raise OSError)."""
    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    method = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[slot])
    return method(ptr, *args)


def _release(ptr: ctypes.c_void_p) -> None:
    if ptr:
        _call(ptr, _RELEASE, (), restype=ctypes.c_ulong)


def _query_interface(ptr: ctypes.c_void_p, iid: str) -> ctypes.c_void_p:
    out = ctypes.c_void_p()
    _call(ptr, _QUERY_INTERFACE, (ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)),
          ctypes.byref(_guid(iid)), ctypes.byref(out))
    return out


def _dispid(disp: ctypes.c_void_p, name: str) -> int:
    names = (ctypes.c_wchar_p * 1)(name)
    dispid = ctypes.c_long()
    _call(disp, _GET_IDS_OF_NAMES,
          (ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_uint, ctypes.c_ulong,
           ctypes.POINTER(ctypes.c_long)),
          ctypes.byref(_guid(IID_NULL)), names, 1, LOCALE_USER_DEFAULT, ctypes.byref(dispid))
    return dispid.value


def _invoke(disp: ctypes.c_void_p, name: str, flags: int, args: list, result: _VARIANT) -> None:
    """IDispatch::Invoke by name; `args` in the normal order (reversed here, as COM wants)."""
    array = (_VARIANT * max(1, len(args)))(*reversed(args))
    params = _DISPPARAMS(array if args else None, None, len(args), 0)
    _call(disp, _INVOKE,
          (ctypes.c_long, ctypes.POINTER(_GUID), ctypes.c_ulong, ctypes.c_ushort,
           ctypes.POINTER(_DISPPARAMS), ctypes.POINTER(_VARIANT), ctypes.c_void_p,
           ctypes.POINTER(ctypes.c_uint)),
          _dispid(disp, name), ctypes.byref(_guid(IID_NULL)), LOCALE_USER_DEFAULT, flags,
          ctypes.byref(params), ctypes.byref(result), None, None)


def _window_process_elevated(hwnd: int) -> bool:
    """Whether the process owning `hwnd` runs elevated; True when it cannot be told."""
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    PROCESS_QUERY_LIMITED_INFORMATION, TOKEN_QUERY, TOKEN_ELEVATION = 0x1000, 0x0008, 20

    pid = wintypes.DWORD()
    if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
        return True
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        return True
    try:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
            return True
        try:
            elevated, size = wintypes.DWORD(), wintypes.DWORD()
            if not advapi32.GetTokenInformation(token, TOKEN_ELEVATION, ctypes.byref(elevated),
                                                ctypes.sizeof(elevated), ctypes.byref(size)):
                return True
            return bool(elevated.value)
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(process)


def _bstr_variant(text: str) -> _VARIANT:
    oleaut32 = ctypes.WinDLL("oleaut32")
    oleaut32.SysAllocString.argtypes = [ctypes.c_wchar_p]
    oleaut32.SysAllocString.restype = ctypes.c_void_p
    v = _VARIANT(vt=VT_BSTR)
    v.value.bstrVal = oleaut32.SysAllocString(text)
    if not v.value.bstrVal:
        raise MemoryError("SysAllocString")
    return v


def shell_execute_unelevated(file: str, args: str = "", show: int = SW_SHOWNORMAL) -> None:
    """Asks the desktop Explorer to ShellExecute `file`. Raises OSError on any failure, and
    when the desktop shell is elevated (the program would then start elevated as well).
    Must run on a thread without COM initialized in another mode (a fresh thread is fine)."""
    ole32 = ctypes.oledll.ole32
    oleaut32 = ctypes.WinDLL("oleaut32")
    oleaut32.VariantClear.argtypes = [ctypes.POINTER(_VARIANT)]
    ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    windows, desktop, provider, browser, view, folder_view, shell = (
        ctypes.c_void_p() for _ in range(7))
    application = _VARIANT()
    arguments: list[_VARIANT] = []
    try:
        ole32.CoCreateInstance(ctypes.byref(_guid(CLSID_ShellWindows)), None, CLSCTX_LOCAL_SERVER,
                               ctypes.byref(_guid(IID_IShellWindows)), ctypes.byref(windows))
        location = _VARIANT(vt=VT_I4)
        location.value.lVal = CSIDL_DESKTOP
        empty = _VARIANT(vt=VT_EMPTY)
        hwnd = ctypes.c_long()
        _call(windows, _FIND_WINDOW_SW,
              (ctypes.POINTER(_VARIANT), ctypes.POINTER(_VARIANT), ctypes.c_int,
               ctypes.POINTER(ctypes.c_long), ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
              ctypes.byref(location), ctypes.byref(empty), SWC_DESKTOP, ctypes.byref(hwnd),
              SWFO_NEEDDISPATCH, ctypes.byref(desktop))
        if not desktop or not hwnd.value:
            raise OSError("área de trabalho do Windows (Explorer) não encontrada")
        if _window_process_elevated(hwnd.value & 0xFFFFFFFFFFFFFFFF):  # HWNDs are sign-extended
            raise OSError("o Explorer da área de trabalho está elevado; o navegador também ficaria")
        provider = _query_interface(desktop, IID_IServiceProvider)
        _call(provider, _QUERY_SERVICE,
              (ctypes.POINTER(_GUID), ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)),
              ctypes.byref(_guid(SID_STopLevelBrowser)), ctypes.byref(_guid(IID_IShellBrowser)),
              ctypes.byref(browser))
        _call(browser, _QUERY_ACTIVE_SHELL_VIEW, (ctypes.POINTER(ctypes.c_void_p),),
              ctypes.byref(view))
        _call(view, _GET_ITEM_OBJECT,
              (ctypes.c_uint, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)),
              SVGIO_BACKGROUND, ctypes.byref(_guid(IID_IDispatch)), ctypes.byref(folder_view))
        _invoke(folder_view, "Application", DISPATCH_PROPERTYGET, [], application)
        if application.vt != VT_DISPATCH or not application.value.pdispVal:
            raise OSError("Shell.Application indisponível")
        shell = _query_interface(ctypes.c_void_p(application.value.pdispVal), IID_IShellDispatch2)
        show_variant = _VARIANT(vt=VT_I4)
        show_variant.value.lVal = show
        arguments = [_bstr_variant(file), _bstr_variant(args), _bstr_variant(""),
                     _bstr_variant("open"), show_variant]
        _invoke(shell, "ShellExecute", DISPATCH_METHOD, arguments, _VARIANT())
    finally:
        for v in arguments:
            oleaut32.VariantClear(ctypes.byref(v))
        for ptr in (shell, folder_view, view, browser, provider, desktop, windows):
            _release(ptr)
        oleaut32.VariantClear(ctypes.byref(application))
        ole32.CoUninitialize()


def open_url(url: str, on_done: Callable[[str | None], None]) -> None:
    """Opens `url` (plain https only) in the user's normal browser, on a short background
    thread so a busy Explorer never freezes the window. Then calls on_done(None), or
    on_done(<error>) if nothing was opened; on_done runs on that thread."""
    def work() -> None:
        try:
            if not is_safe_url(url):
                raise ValueError("endereço recusado")
            shell_execute_unelevated(url)
        except Exception as e:
            on_done(str(e) or type(e).__name__)
        else:
            on_done(None)

    threading.Thread(target=work, daemon=True, name="open-url").start()
