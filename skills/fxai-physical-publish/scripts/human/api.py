# -*- coding: utf-8 -*-
"""Windows 系统级底层封装：SendInput / 窗口枚举 / GDI 截屏。

只用 ctypes 与标准库，不依赖任何浏览器自动化框架，不做任何页面注入。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

# ---------------------------------------------------------------- DPI
def enable_dpi_awareness() -> None:
    """让本进程坐标与物理像素 1:1，保证缩放后点击不偏移。"""
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


# ---------------------------------------------------------------- 输入结构
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


def send_inputs(inputs: list[INPUT]) -> None:
    n = len(inputs)
    if not n:
        return
    arr = (INPUT * n)(*inputs)
    sent = user32.SendInput(n, arr, ctypes.sizeof(INPUT))
    if sent != n:
        raise OSError("SendInput 失败（%d/%d），错误码 %s" % (sent, n, ctypes.get_last_error()))


def _mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
    it = INPUT()
    it.type = INPUT_MOUSE
    it.mi = mi
    return it


def _key_input(vk: int, scan: int, flags: int) -> INPUT:
    ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    it = INPUT()
    it.type = INPUT_KEYBOARD
    it.ki = ki
    return it


# ---------------------------------------------------------------- 光标
class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def cursor_pos() -> tuple[int, int]:
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def to_absolute(x: int, y: int) -> tuple[int, int]:
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = max(1, user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    vh = max(1, user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    ax = int(round((x - vx) * 65535.0 / max(1, vw - 1)))
    ay = int(round((y - vy) * 65535.0 / max(1, vh - 1)))
    return max(0, min(65535, ax)), max(0, min(65535, ay))


def move_absolute(x: int, y: int) -> None:
    ax, ay = to_absolute(x, y)
    send_inputs([_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay)])


def mouse_button(down: bool, button: str = "left", x: int | None = None, y: int | None = None) -> None:
    if x is not None and y is not None:
        ax, ay = to_absolute(x, y)
        send_inputs([_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay)])
    table = {
        ("left", True): MOUSEEVENTF_LEFTDOWN,
        ("left", False): MOUSEEVENTF_LEFTUP,
        ("right", True): MOUSEEVENTF_RIGHTDOWN,
        ("right", False): MOUSEEVENTF_RIGHTUP,
        ("middle", True): MOUSEEVENTF_MIDDLEDOWN,
        ("middle", False): MOUSEEVENTF_MIDDLEUP,
    }
    send_inputs([_mouse_input(table[(button, down)])])


def mouse_wheel(clicks: int) -> None:
    """clicks > 0 向上，< 0 向下；每格 120。"""
    send_inputs([_mouse_input(MOUSEEVENTF_WHEEL, 0, 0, clicks * 120)])


def key_down_up(vk: int, scan: int = 0, extended: bool = False) -> None:
    flags = KEYEVENTF_EXTENDEDKEY if extended else 0
    send_inputs([_key_input(vk, scan, flags), _key_input(vk, scan, flags | KEYEVENTF_KEYUP)])


def key_press(vk: int, scan: int = 0, extended: bool = False, hold_ms: int = 0) -> None:
    flags = KEYEVENTF_EXTENDEDKEY if extended else 0
    send_inputs([_key_input(vk, scan, flags)])
    if hold_ms:
        import time as _t

        _t.sleep(hold_ms / 1000.0)
    send_inputs([_key_input(vk, scan, flags | KEYEVENTF_KEYUP)])


def unicode_char(ch: str) -> None:
    """以 KEYEVENTF_UNICODE 发送一个字符（中文等非 ASCII 走这里）。"""
    buf = ch.encode("utf-16-le")
    if len(buf) % 2:
        buf += b"\x00"
    units = [int.from_bytes(buf[i:i + 2], "little") for i in range(0, len(buf), 2)]
    for u in units:
        send_inputs([_key_input(0, u, KEYEVENTF_UNICODE), _key_input(0, u, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)])


def key_state(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def async_key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8001)


# ---------------------------------------------------------------- 窗口
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
# cmd_env 会读 LOGPIXELSX；不声明签名时 64 位 HDC 被截成 c_int，触发 OverflowError
gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.GetDeviceCaps.restype = ctypes.c_int


def window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def process_exe(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wintypes.DWORD(2048)
        buf = ctypes.create_unicode_buffer(2048)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def list_windows(visible_only: bool = True) -> list[dict]:
    out: list[dict] = []

    def cb(hwnd, _lparam):
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        title = window_title(hwnd)
        if visible_only and not title:
            return True
        r = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        cr = RECT()
        user32.GetClientRect(hwnd, ctypes.byref(cr))
        pid = window_pid(hwnd)
        out.append(
            {
                "hwnd": int(hwnd),
                "title": title,
                "class": window_class(hwnd),
                "pid": pid,
                "exe": process_exe(pid),
                "window_rect": [r.left, r.top, r.right - r.left, r.bottom - r.top],
                "client_size": [cr.right - cr.left, cr.bottom - cr.top],
                "foreground": int(hwnd) == int(user32.GetForegroundWindow()),
                "minimized": bool(user32.IsIconic(hwnd)),
            }
        )
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out


def client_origin(hwnd: int) -> tuple[int, int]:
    pt = POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def client_rect(hwnd: int) -> tuple[int, int, int, int]:
    """返回客户区在屏幕上的绝对矩形 (x, y, w, h)。"""
    cr = RECT()
    user32.GetClientRect(hwnd, ctypes.byref(cr))
    ox, oy = client_origin(hwnd)
    return ox, oy, cr.right - cr.left, cr.bottom - cr.top


def foreground_window() -> int:
    return int(user32.GetForegroundWindow())


def activate_window(hwnd: int) -> bool:
    """把窗口切到前台。SetForegroundWindow 有前台锁，逐级降级重试。"""
    import time as _t

    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        _t.sleep(0.35)
    if foreground_window() == hwnd:
        return True
    for _ in range(6):
        user32.SetForegroundWindow(hwnd)
        _t.sleep(0.18)
        if foreground_window() == hwnd:
            return True
        # 借用前台线程输入队列，绕过前台锁
        fg = user32.GetForegroundWindow()
        tid_fg = user32.GetWindowThreadProcessId(fg, None)
        tid_me = kernel32.GetCurrentThreadId()
        attached = False
        if tid_fg and tid_fg != tid_me:
            attached = bool(user32.AttachThreadInput(tid_me, tid_fg, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            _t.sleep(0.15)
        finally:
            if attached:
                user32.AttachThreadInput(tid_me, tid_fg, False)
        if foreground_window() == hwnd:
            return True
    return foreground_window() == hwnd


def resize_client(hwnd: int, width: int, height: int, move: bool = True) -> tuple[int, int]:
    """把客户区精确调整为 width x height（窗口边框由系统算），并移到主屏居中略偏左上。"""
    import time as _t

    wr = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    cr = RECT()
    user32.GetClientRect(hwnd, ctypes.byref(cr))
    dw = (wr.right - wr.left) - (cr.right - cr.left)
    dh = (wr.bottom - wr.top) - (cr.bottom - cr.top)

    if user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        _t.sleep(0.3)

    x = 40 if move else wr.left
    y = 40 if move else wr.top
    SWP_NOZORDER = 0x0004
    SWP_SHOWWINDOW = 0x0040
    user32.SetWindowPos(hwnd, 0, x, y, width + dw, height + dh, SWP_NOZORDER | SWP_SHOWWINDOW)
    _t.sleep(0.35)
    _x, _y, w, h = client_rect(hwnd)
    return w, h


def close_window(hwnd: int) -> None:
    WM_CLOSE = 0x0010
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


# ---------------------------------------------------------------- GDI 截屏
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


SRCCOPY = 0x00CC0020


def grab(x: int, y: int, w: int, h: int) -> tuple[int, int, bytes]:
    """抓屏，返回 (w, h, BGRA 原始字节)。"""
    if w <= 0 or h <= 0:
        raise ValueError("截屏区域无效：%sx%s" % (w, h))
    hdc_screen = user32.GetDC(0)
    if not hdc_screen:
        raise OSError("GetDC 失败")
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    old = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        if not gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y, SRCCOPY):
            raise OSError("BitBlt 失败")
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h  # 负值 = 自上而下
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0  # BI_RGB
        bi.bmiHeader.biSizeImage = w * h * 4
        buf = ctypes.create_string_buffer(w * h * 4)
        n = gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bi), 0)
        if n == 0:
            raise OSError("GetDIBits 失败")
        return w, h, buf.raw
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)


def pixel_of(data: bytes, w: int, x: int, y: int) -> tuple[int, int, int]:
    i = (y * w + x) * 4
    b, g, r = data[i], data[i + 1], data[i + 2]
    return r, g, b


# ---------------------------------------------------------------- 环境可用性
def is_locked() -> bool:
    """工作站是否处于锁定/登录界面。

    锁屏后系统把输入桌面切换给 Winlogon，普通进程既无法 `SendInput`
    （返回错误码 5，拒绝访问），也无法 `BitBlt` 截屏（直接失败）。
    所有「点了没反应」「截图全黑/失败」的怪象，第一步都该先查这个。
    """
    d = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if d:
        user32.CloseDesktop(d)
        return False
    return True


def assert_ready(action: str = "执行") -> None:
    """动手前的环境自检：锁屏、显示器关闭时直接报错停下，不得空转。"""
    if is_locked():
        raise RuntimeError(
            "工作站已锁定，无法%s：锁屏状态下 SendInput 会被拒绝、截屏会失败。"
            "请先解锁桌面再继续。" % action
        )
