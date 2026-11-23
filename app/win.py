import ctypes
import ctypes.wintypes as wt
import os
import sys
import time
import winreg
from pathlib import Path

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi
gdi32 = ctypes.windll.gdi32

kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetWindowPos.restype = wt.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
dwmapi.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
HWND_TOPMOST = wt.HWND(-1)
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
SWP_FRAMECHANGED = 0x0020
SW_SHOWNOACTIVATE = 4
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_V = 0x56
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Golos"

_mutex = None


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", ctypes.c_ulong), ("data", INPUTUNION)]


def project_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resource_dir():
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return project_dir()


def asset_path(name):
    candidates = [
        project_dir() / "assets" / name,
        resource_dir() / "assets" / name,
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[-1]


def models_dir():
    candidates = [
        project_dir() / "models",
        resource_dir() / "models",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def single_instance():
    global _mutex
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    kernel32.CreateMutexW.restype = wt.HANDLE
    handle = kernel32.CreateMutexW(None, False, "Local\\GolosDictation")
    error = kernel32.GetLastError()
    if error == 183:
        if handle:
            kernel32.CloseHandle(handle)
        return False
    _mutex = handle
    return True


# Acrylic в Windows красит весь прямоугольник окна, а нам нужна плашка.
# Поэтому окно просто обрезаем скруглённым регионом, а фон размываем сами.
def configure_window(hwnd):
    exstyle = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    exstyle |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    exstyle &= ~WS_EX_TRANSPARENT
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, exstyle)
    enabled = ctypes.c_int(1)
    dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
    policy = ctypes.c_int(2)
    dwmapi.DwmSetWindowAttribute(hwnd, 2, ctypes.byref(policy), ctypes.sizeof(policy))
    user32.SetWindowPos(
        hwnd,
        HWND_TOPMOST,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_FRAMECHANGED,
    )


def show_without_activation(hwnd):
    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def apply_round_region(hwnd, width, height, radius, scale=1.0):
    width = max(1, int(width * scale))
    height = max(1, int(height * scale))
    radius = max(1, int(radius * scale))
    gdi32.CreateRoundRectRgn.restype = wt.HRGN
    region = gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius * 2, radius * 2)
    if not region:
        return
    user32.SetWindowRgn.argtypes = [wt.HWND, wt.HRGN, wt.BOOL]
    user32.SetWindowRgn.restype = ctypes.c_int
    if not user32.SetWindowRgn(hwnd, region, True):
        gdi32.DeleteObject(region)


def modifiers_down():
    keys = (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN)
    return any(user32.GetAsyncKeyState(key) & 0x8000 for key in keys)


# Вставляем через буфер и Ctrl+V, иначе кириллица ломается в половине программ.
def _key_event(vk, up=False):
    item = INPUT()
    item.type = 1
    item.ki.wVk = vk
    item.ki.wScan = 0
    item.ki.dwFlags = 0x0002 if up else 0
    item.ki.time = 0
    item.ki.dwExtraInfo = 0
    return item


def send_paste():
    user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = ctypes.c_uint
    size = ctypes.sizeof(INPUT)
    ctrl_down = (INPUT * 1)(_key_event(VK_CONTROL))
    v_down = (INPUT * 1)(_key_event(VK_V))
    v_up = (INPUT * 1)(_key_event(VK_V, True))
    ctrl_up = (INPUT * 1)(_key_event(VK_CONTROL, True))
    try:
        user32.SendInput(1, ctrl_down, size)
        time.sleep(0.015)
        user32.SendInput(1, v_down, size)
        time.sleep(0.015)
        user32.SendInput(1, v_up, size)
    finally:
        time.sleep(0.01)
        user32.SendInput(1, ctrl_up, size)


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --autostart'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    script = project_dir() / "app" / "main.py"
    return f'"{pythonw}" "{script}" --autostart'


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
            winreg.QueryValueEx(key, RUN_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_autostart(enabled):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def open_path(path):
    try:
        os.startfile(str(path))
    except Exception:
        pass
