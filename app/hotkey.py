import ctypes
import ctypes.wintypes as wt
import logging
import threading

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
VK_LCONTROL = 0xA2
VK_RMENU = 0xA5

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


# Qt не различает левый Ctrl и правый Alt, поэтому слушаем клавиатуру через WinAPI.
class HotkeyListener:
    def __init__(self, callback):
        self.callback = callback
        self.left_ctrl = False
        self.right_alt = False
        self.fired = False
        self.thread = None
        self.thread_id = None
        self.hook = None
        self.ready = threading.Event()
        self.proc = HOOKPROC(self._handle)

    def start(self):
        self.thread = threading.Thread(target=self._run, name="GolosHotkey", daemon=True)
        self.thread.start()
        return self.ready.wait(3)

    def stop(self):
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
        if self.thread:
            self.thread.join(timeout=2)

    def _run(self):
        kernel32.GetModuleHandleW.restype = wt.HMODULE
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HMODULE, wt.DWORD]
        user32.SetWindowsHookExW.restype = wt.HHOOK
        user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
        user32.PostThreadMessageW.restype = wt.BOOL
        self.thread_id = kernel32.GetCurrentThreadId()
        hmod = kernel32.GetModuleHandleW(None)
        self.hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self.proc, hmod, 0)
        if not self.hook:
            logging.error("keyboard hook failed: %s", kernel32.GetLastError())
        self.ready.set()
        msg = wt.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self.hook:
                user32.UnhookWindowsHookEx(self.hook)
                self.hook = None

    def _handle(self, code, wparam, lparam):
        if code == 0:
            try:
                data = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = data.vkCode
                if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if vk == VK_LCONTROL:
                        self.left_ctrl = True
                    elif vk == VK_RMENU:
                        self.right_alt = True
                    if self.left_ctrl and self.right_alt and not self.fired:
                        self.fired = True
                        self.callback()
                elif wparam in (WM_KEYUP, WM_SYSKEYUP):
                    if vk == VK_LCONTROL:
                        self.left_ctrl = False
                        self.fired = False
                    elif vk == VK_RMENU:
                        self.right_alt = False
                        self.fired = False
            except Exception:
                logging.exception("hotkey handler")
        return user32.CallNextHookEx(None, code, wparam, lparam)
