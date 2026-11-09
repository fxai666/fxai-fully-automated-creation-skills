# -*- coding: utf-8 -*-
"""窗口让位：让用户看得见、也点得到。

这里踩过一个真实的坑：早期用「置顶 + 缩小靠左」让人操作，结果窗口永远压在
对话窗口上面，用户根本点不到对话窗口，没法边看说明边操作。

所以规矩改成两条，分清「人要动手」和「人要说话」：

  reveal()   人要在浏览器里动手（登录 / 确认）：**取消置顶**、靠左摆放。
             窗口可见、可点，但绝不压住别的窗口——用户点一下对话窗口就能盖住它。
  minimize() 程序要跟人说话（汇报、问问题、流程停在 NeedUser）：**直接最小化**，
             把整个屏幕让给对话。
  release()  人做完了要继续自动化：还原到录制尺寸、取消置顶、激活。
"""
from __future__ import annotations

import time

from . import api

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_SHOWWINDOW = 0x0040
SWP_NOACTIVATE = 0x0010

SW_MINIMIZE = 6
SW_RESTORE = 9


def _window_rect_for_client(hwnd: int, cw: int, ch: int) -> tuple[int, int]:
    wr = api.RECT()
    api.user32.GetWindowRect(hwnd, api.ctypes.byref(wr))
    cr = api.RECT()
    api.user32.GetClientRect(hwnd, api.ctypes.byref(cr))
    dw = (wr.right - wr.left) - (cr.right - cr.left)
    dh = (wr.bottom - wr.top) - (cr.bottom - cr.top)
    return cw + dw, ch + dh


def set_topmost(hwnd: int, on: bool = True) -> None:
    api.user32.SetWindowPos(
        hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    )


def place(hwnd: int, x: int, y: int, client_w: int, client_h: int) -> tuple[int, int]:
    """把窗口摆到指定位置并设置客户区尺寸（不动页面内容，只是摆窗口）。

    最小化 / 最大化状态下直接 SetWindowPos 是无效的（尺寸会被系统忽略），
    必须先还原成普通窗口，否则会出现「说设成 1500x910、实际还是旧尺寸」。
    """
    if api.user32.IsIconic(hwnd):
        api.user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.35)
    if api.user32.IsZoomed(hwnd):
        api.user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.3)
    ww, wh = _window_rect_for_client(hwnd, client_w, client_h)
    # insertAfter 用 NOTOPMOST：浮到普通层最前，但保持可被其它窗口盖住
    api.user32.SetWindowPos(hwnd, HWND_NOTOPMOST, x, y, ww, wh, SWP_SHOWWINDOW)
    time.sleep(0.35)
    return api.client_rect(hwnd)[2:]


def reveal(hwnd: int, client_w: int = 1180, client_h: int = 860, x: int = 0, y: int = 0) -> tuple[int, int]:
    """人要动手时调用：**取消置顶** + 靠左摆放 + 留出对话区。

    绝不置顶——置顶会把对话窗口压在下面，用户连"我该干什么"都看不到。
    """
    set_topmost(hwnd, False)
    got = place(hwnd, x, y, client_w, client_h)
    api.activate_window(hwnd)
    time.sleep(0.2)
    return got


def minimize(hwnd: int) -> None:
    """程序要跟人说话时调用：最小化，把屏幕让给对话窗口。"""
    set_topmost(hwnd, False)
    api.user32.ShowWindow(hwnd, SW_MINIMIZE)
    time.sleep(0.2)


def release(hwnd: int, restore_client: tuple[int, int] | None = None, x: int = 40, y: int = 40) -> None:
    """收尾：取消置顶，恢复原尺寸（供按区域坐标点击使用）。"""
    set_topmost(hwnd, False)
    if restore_client:
        place(hwnd, x, y, restore_client[0], restore_client[1])
    api.activate_window(hwnd)
    time.sleep(0.2)
