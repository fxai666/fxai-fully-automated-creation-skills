# -*- coding: utf-8 -*-
"""真实浏览器窗口管理：找窗口、开窗口、把客户区调到固定尺寸。

刻意做的事：不带任何自动化开关（无 remote-debugging、无 automation 标记），
用的是用户自己那个已经登录过的 Chrome，不接管 profile、不读写 cookie。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

from . import api, regions

# 上次用的是哪个窗口，记在实例目录里：下次优先复用同一个，不新开窗口
STATE_PATH = os.path.join(regions.ROOT, "config", "window.json")

CANDIDATES = [
    os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe"),
]


def chrome_path() -> str | None:
    for p in CANDIDATES:
        if p and os.path.isfile(p):
            return p
    w = shutil.which("chrome") or shutil.which("msedge")
    return w


def chrome_windows(min_width: int = 0) -> list[dict]:
    """列出浏览器窗口。

    Chrome 会把「权限气泡」「下载气泡」「选择文件对话框」等也做成 Chrome_WidgetWin 类窗口，
    它们尺寸很小，必须能按宽度过滤掉，否则会被误当成主窗口来截图/点击。
    """
    out = []
    for w in api.list_windows():
        exe = os.path.basename(w["exe"]).lower()
        if exe in ("chrome.exe", "msedge.exe") and w["client_size"][0] >= min_width:
            out.append(w)
    return out


def main_windows(min_width: int = 600, include_minimized: bool = True) -> list[dict]:
    """浏览器主窗口。

    最小化的窗口客户区会被系统压到很小（几十像素），不可按宽度直接排除，
    否则「人把窗口最小化了」就会被误判成「没有浏览器窗口」。
    """
    wins = []
    for w in chrome_windows():
        cls = w["class"]
        if cls != "Chrome_WidgetWin_1":
            continue  # 排除 Chrome_WidgetWin_0 / IME / crashpad 等辅助窗口
        if w["minimized"]:
            if include_minimized and w["title"]:
                wins.append(w)
            continue
        if w["client_size"][0] >= min_width:
            wins.append(w)
    wins.sort(key=lambda w: (w["minimized"], -(w["client_size"][0] * w["client_size"][1])))
    return wins


def _read_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _write_state(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass


def remember(hwnd: int, title: str = "") -> None:
    """记下当前用的窗口，下次优先复用它。"""
    d = _read_state()
    try:
        _x, _y, cw, ch = api.client_rect(hwnd)
    except Exception:
        cw = ch = 0
    d["browser"] = {
        "hwnd": int(hwnd),
        "title": title or api.window_title(hwnd),
        "client": [cw, ch],
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write_state(d)


def ensure_window(url: str = "about:blank", exe: str = "chrome.exe") -> tuple[int, bool]:
    """拿一个可用的浏览器窗口——**能复用就绝不开新的**。

    挑选顺序：
      1. 上次用过的那个（`config/window.json` 里记着）
      2. 现有主窗口里客户区最大的那个
      3. 一个都没有，才启动浏览器

    窗口一旦开起来就一直用：同一个窗口、同一个标签里导航，不关窗口、不新开窗口、
    不堆标签页。真人不会每做一件事就开一个窗口，多出来的窗口本身就很显眼。
    """
    want = (exe or "chrome.exe").lower()
    wins = [w for w in main_windows() if os.path.basename(w["exe"]).lower() == want]
    last = int((_read_state().get("browser") or {}).get("hwnd") or 0)
    if last:
        for w in wins:
            if w["hwnd"] == last:
                return w["hwnd"], True
    if wins:
        return wins[0]["hwnd"], True   # main_windows 已按「未最小化优先、面积大优先」排好
    return launch(url), False


# --hide-crash-restore-bubble：只隐藏「要恢复页面吗」这个气泡，不是自动化开关，
# 不碰 CDP、不碰调试端口、不改 UA。作用纯粹是去掉盖在页面上、挡住点击的界面噪音
# （上次 Chrome 被强杀 / 关机重启后必然出现），等价于用户在设置里关掉它。
LAUNCH_FLAGS = ["--hide-crash-restore-bubble"]


def launch(url: str = "about:blank", wait_s: float = 12.0) -> int:
    """像用户手动双击图标一样打开浏览器（不带任何自动化参数）。"""
    exe = chrome_path()
    if not exe:
        raise RuntimeError("找不到 Chrome / Edge 可执行文件")
    before = {w["hwnd"] for w in chrome_windows()}
    subprocess.Popen([exe, *LAUNCH_FLAGS, url], close_fds=True)
    t0 = time.time()
    while time.time() - t0 < wait_s:
        for w in chrome_windows():
            if w["hwnd"] not in before and not w["minimized"] and w["client_size"][0] > 200:
                return w["hwnd"]
        time.sleep(0.35)
    # 复用已有窗口（可能只是新开了标签页）
    wins = chrome_windows()
    if wins:
        wins.sort(key=lambda w: -w["client_size"][0] * w["client_size"][1])
        return wins[0]["hwnd"]
    raise RuntimeError("浏览器窗口没有出现")


def pick_window(title_contains: str = "", hwnd: int | None = None) -> dict:
    wins = chrome_windows()
    if hwnd:
        for w in wins:
            if w["hwnd"] == hwnd:
                return w
        raise RuntimeError("指定的窗口不存在：%s" % hwnd)
    if title_contains:
        hits = [w for w in wins if title_contains in w["title"]]
        if hits:
            return hits[0]
    if not wins:
        raise RuntimeError("当前没有浏览器窗口")
    wins.sort(key=lambda w: -w["client_size"][0] * w["client_size"][1])
    return wins[0]


def fit(hwnd: int, width: int = 1500, height: int = 900) -> tuple[int, int]:
    """把客户区调到固定尺寸——区域坐标才稳定。窗口位置由系统摆放，不动站点内容。"""
    api.activate_window(hwnd)
    time.sleep(0.2)
    return api.resize_client(hwnd, width, height, move=True)


def describe(w: dict) -> str:
    return "#%-10s %-8s %4dx%-4d %s" % (
        w["hwnd"],
        os.path.basename(w["exe"]),
        w["client_size"][0],
        w["client_size"][1],
        (w["title"][:70] + ("…" if len(w["title"]) > 70 else "")),
    )
