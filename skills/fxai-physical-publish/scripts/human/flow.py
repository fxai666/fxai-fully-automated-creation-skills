# -*- coding: utf-8 -*-
"""流程执行器：把「步骤表」翻译成真实鼠标键盘动作。

步骤全部是真人能做的动作：切窗口、点区域、敲字、粘贴、滚动、等画面稳定、
处理系统文件对话框、人工确认闸门。没有任何一步会去碰页面内部。
"""
from __future__ import annotations

import json
import os
import random
import threading
import time

from . import api, browser, clipboard, keyboard, mouse, probe, regions, screen, timing, ui

VK_ESCAPE = 0x1B
VK_F9 = 0x78

_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUMAN_CFG = os.path.join(regions.ROOT, "config", "human.json")


def load_human_cfg() -> dict:
    try:
        with open(HUMAN_CFG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def preflight(site: str, client_rect: tuple[int, int, int, int] | None = None) -> dict:
    """开工前置诊断：锁屏、客户区原点/尺寸、以及会吞掉鼠标点击的独立顶层确认窗。

    2026-11-11 实测：Chrome「离开此网站？」是独立顶层窗口，窗口截图看不见，
    但会吃掉所有点击，表现为「键盘/滚轮有效、点击全失效、截图零变化」。
    """
    wins = api.list_windows()
    suspects = ("离开此网站", "要离开", "Leave site", "重新加载", "还原页面", "Reload", "此网站", "确定要")
    blockers = [
        {"hwnd": w["hwnd"], "title": w["title"], "class": w["class"], "exe": os.path.basename(w["exe"])}
        for w in wins
        if w["class"] == "Chrome_WidgetWin_1"
        and any(s.lower() in w["title"].lower() for s in suspects)
    ]
    fg = api.foreground_window()
    rep = {
        "locked": api.is_locked(),
        "client": list(client_rect) if client_rect else None,
        "origin": [client_rect[0], client_rect[1]] if client_rect else None,
        "foreground": fg,
        "windows": len(wins),
        "blockers": blockers,
    }
    if client_rect:
        try:
            rec = regions.load(site).get("client")
            if rec:
                rep["client_expected"] = list(rec)
                rep["client_deviation"] = round(max(
                    abs(client_rect[2] - rec[0]) / max(1, rec[0]),
                    abs(client_rect[3] - rec[1]) / max(1, rec[1])), 4)
        except Exception:
            pass
    return rep


def selection_ratio(rect: tuple[int, int, int, int], step: int = 5) -> float:
    """整块画面里「选中高亮蓝」的像素占比。

    Chrome 在全选整页时会把大片文字铺上高亮蓝（#3297FD 一系）；正常页面里
    只有少量链接/主按钮是蓝的，占比远低于阈值。用它当「刚才那次 Ctrl+A
    有没有误伤整页」的客观判据，比看截图猜可靠。
    """
    x, y, w, h = [int(v) for v in rect]
    if w <= 0 or h <= 0:
        return 0.0
    gw, gh, data = api.grab(x, y, w, h)   # BGRA
    hit = tot = 0
    for yy in range(0, gh, step):
        base = yy * gw * 4
        for xx in range(0, gw, step):
            i = base + xx * 4
            b, g, r = data[i], data[i + 1], data[i + 2]
            tot += 1
            if b > 150 and (b - r) > 55 and (b - g) > 25:
                hit += 1
    return hit / max(1, tot)


MODAL_TITLES = ("离开此网站", "要离开", "Leave site", "重新加载", "还原页面",
                "Reload", "此网站", "确定要", "Changes you made")


def dismiss_modals(hwnd: int | None = None, log=None, max_round: int = 2) -> list[dict]:
    """把 Chrome 的独立确认窗（「离开此网站？」「要重新加载吗？」）当正常操作点掉。

    这类窗是 Chrome 的**另一个顶层窗口**，不在页面截图里，却会吞掉所有点击。
    旧做法是停下来当阻塞，实测没必要——它就是个普通弹窗，点右边那个
    主按钮（离开 / 重新加载 / 确定）即可，跟真人操作一模一样。
    """
    done: list[dict] = []
    for _ in range(max_round):
        dlg = None
        for w in api.list_windows():
            if w["class"] not in ("Chrome_WidgetWin_1", "Chrome_WidgetWin_2", "#32770"):
                continue
            t = (w["title"] or "")
            if any(s.lower() in t.lower() for s in MODAL_TITLES):
                if hwnd and w["hwnd"] == hwnd:
                    continue
                dlg = w
                break
        if not dlg:
            break
        api.activate_window(dlg["hwnd"])
        time.sleep(0.35)
        dl, dt, dw, dh = dlg["window_rect"]
        # 主按钮在右下角：距右 52px、距底 26px 左右，是所有 Chrome 弹窗的固定位置
        px, py = dl + dw - 52, dt + dh - 26
        mouse.click(px, py, rnd=random.Random(), button="left")
        time.sleep(0.5)
        alive = any(w["hwnd"] == dlg["hwnd"] for w in api.list_windows())
        rec = {"hwnd": dlg["hwnd"], "title": dlg["title"][:40],
               "clicked": [px, py], "closed": not alive}
        if not alive:
            done.append(rec)
            if log:
                log("modal_dismissed", **rec)
            if hwnd:
                api.activate_window(hwnd)
                time.sleep(0.25)
            continue
        # 右侧没点掉就退回键盘：Enter → Tab+Enter → Shift+Tab+Enter（总有一个是「确定」）
        for plan in ("enter", "tab_enter", "shift_tab_enter"):
            if plan == "enter":
                api.key_press(keyboard.VK["enter"], hold_ms=35)
            elif plan == "tab_enter":
                api.key_press(keyboard.VK["tab"], hold_ms=35)
                time.sleep(0.12)
                api.key_press(keyboard.VK["enter"], hold_ms=35)
            else:
                keyboard.combo("shift", "tab")
                time.sleep(0.12)
                api.key_press(keyboard.VK["enter"], hold_ms=35)
            time.sleep(0.45)
            if not any(w["hwnd"] == dlg["hwnd"] for w in api.list_windows()):
                rec["closed"] = True
                rec["via"] = plan
                done.append(rec)
                if log:
                    log("modal_dismissed", **rec)
                break
        else:
            rec["closed"] = False
            done.append(rec)
            if log:
                log("modal_stuck", **rec)
    return done


class Aborted(RuntimeError):
    pass


class NeedUser(RuntimeError):
    """流程停在需要用户亲手操作的地方（登录 / 确认）。不阻塞，交给上层去问人。"""

    def __init__(self, what: str, detail: str = "") -> None:
        super().__init__("%s：%s" % (what, detail) if detail else what)
        self.what = what
        self.detail = detail


class Watcher:
    """后台看门狗：长按 ESC 立即中止当前流程。"""

    def __init__(self) -> None:
        self.stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self) -> None:
        held = 0
        while not self.stop.is_set():
            if api.key_state(VK_ESCAPE):
                held += 1
                if held >= 4:
                    self.stop.set()
                    return
            else:
                held = 0
            time.sleep(0.06)

    @property
    def aborted(self) -> bool:
        return self.stop.is_set()


class Runner:
    def __init__(self, site: str, variables: dict | None = None, run_dir: str | None = None, verbose: bool = True,
                 human_override: dict | None = None) -> None:
        self.site = site
        self.vars = dict(variables or {})
        self.run_dir = run_dir or os.path.join(
            regions.ROOT, "runs", time.strftime("%Y%m%d-%H%M%S")
        )
        os.makedirs(os.path.join(self.run_dir, "shots"), exist_ok=True)
        self.log_path = os.path.join(self.run_dir, "log.jsonl")
        self.verbose = verbose
        self.rnd = random.Random()
        self.watcher = Watcher()
        self.hwnd: int | None = None
        self.site_data = regions.load(site)
        # 真人节奏：全局配置 + 站点级覆盖
        cfg = load_human_cfg()
        cfg.update((self.site_data.get("humanize") or {}))
        cfg.update(human_override or {})
        self.tk = timing.TimeKeeper(cfg, self.rnd, verbose=False)
        # 本次运行里已经点过的区域：第一次要找，之后是熟路
        self._seen: set[str] = set()
        # 用户 2026-11-16 口径：进页面先判有没有上次没发完的草稿。
        # 有 → 点「继续编辑」在原稿上接着做，防止重复上传；后续带
        # skip_if_resumed 的步骤（点上传入口、选文件）自动跳过。
        self.resumed = False
        # 发布是否已确认成功：关窗闸门读它，没确认成功不许关浏览器。
        self.publish_ok = False
        self.publish_unverified = False

    # ------------------------------------------------------------ 输出
    def log(self, event: str, **kw) -> None:
        rec = {"ts": time.strftime("%H:%M:%S"), "event": event}
        rec.update(kw)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if self.verbose:
            extra = " ".join("%s=%s" % (k, v) for k, v in kw.items() if k not in ("shot",))
            print("[%s] %-14s %s" % (rec["ts"], event, extra), flush=True)

    def shot(self, name: str) -> str:
        if not self.hwnd:
            return ""
        p = os.path.join(self.run_dir, "shots", "%s.png" % name)
        screen.shot_window(self.hwnd, p)
        return p

    def check_abort(self) -> None:
        if self.watcher.aborted:
            raise Aborted("用户长按 ESC 中止")

    # ------------------------------------------------------------ 变量
    def fill(self, text: str) -> str:
        out = text
        for k, v in self.vars.items():
            out = out.replace("{%s}" % k, str(v))
        return out

    # ------------------------------------------------------------ 窗口
    def resolve_window(self) -> int:
        """定下这次要操作的窗口：能复用就复用，绝不新开。

        多个浏览器窗口时按站点配置的标题偏好挑；没配或挑不到，就回到
        「上次用的那个 / 客户区最大的那个」，保证同一台机器上始终是同一个窗口。
        """
        win_cfg = self.site_data.get("window", {}) or {}
        want_exe = (win_cfg.get("exe") or "chrome.exe").lower()
        want_title = win_cfg.get("title_contains", "")

        wins = [w for w in browser.main_windows()
                if os.path.basename(w["exe"]).lower() == want_exe]
        if want_title:
            hit = [w for w in wins if want_title in w["title"]]
            if hit:
                self.log("window_pick", mode="title", hwnd=hit[0]["hwnd"], title=hit[0]["title"][:40])
                browser.remember(hit[0]["hwnd"], hit[0]["title"])
                return hit[0]["hwnd"]

        hwnd, reused = browser.ensure_window("about:blank", want_exe)
        self.log("window_pick", mode="reuse" if reused else "new", hwnd=hwnd)
        browser.remember(hwnd)
        return hwnd

    def focus(self) -> tuple[int, int, int, int]:
        hwnd = self.hwnd or self.resolve_window()
        if not api.activate_window(hwnd):
            raise RuntimeError("无法把浏览器窗口切到前台（可能被系统弹窗挡住）")
        self.hwnd = hwnd
        time.sleep(0.2)
        # 画布尺寸自愈：需要用户操作时窗口会被缩小让位，回来后必须还原，
        # 否则按区域录的坐标全部失效。
        rec = self.site_data.get("client")
        if rec and rec[0] and rec[1]:
            _x, _y, cw, ch = api.client_rect(hwnd)
            if abs(cw - rec[0]) > 3 or abs(ch - rec[1]) > 3:
                got = api.resize_client(hwnd, int(rec[0]), int(rec[1]))
                self.log("canvas_restore", want=list(rec), got=list(got))
                time.sleep(0.3)
        browser.remember(hwnd)
        return api.client_rect(hwnd)

    def client(self) -> tuple[int, int, int, int]:
        if not self.hwnd:
            return self.focus()
        return api.client_rect(self.hwnd)

    def rect_of(self, name: str) -> tuple[int, int, int, int]:
        return regions.resolve(self.site, name, self.client())

    def _click_target(self, st: dict) -> tuple[int, int]:
        """fill 步骤用：先把光标放进输入框，再粘贴。"""
        if st.get("region"):
            rect = self.rect_of(st["region"])
        elif st.get("anchor") == "bottom":
            cx, cy, cw, ch = self.client()
            rect = (cx + st["at"][0], cy + ch - st["at"][1], st.get("w", 2), st.get("h", 2))
        else:
            cx, cy = self.client()[0], self.client()[1]
            rect = (cx + st["at"][0], cy + st["at"][1], 2, 2)
        px, py = mouse.random_point_in(rect, self.rnd)
        api.activate_window(self.hwnd) if self.hwnd and api.foreground_window() != self.hwnd else None
        mouse.click(px, py, rnd=self.rnd)
        return px, py

    # ------------------------------------------------------------ 安全清空
    def guard_no_page_select(self, before: float | None = None) -> bool:
        """检查有没有「误选整页」。返回 True 表示干净。

        2026-11-11 抖音实测：输入框没拿到焦点时按 Ctrl+A，Chrome 把整页文字
        都选中（左侧导航和右侧预览全变蓝），接着一粘贴就覆盖掉整页。
        判据用客观像素占比，不靠肉眼看截图。
        """
        cx, cy, cw, ch = self.client()
        ratio = selection_ratio((cx, cy, cw, ch))
        if before is not None and ratio > max(0.22, before + 0.15):
            self.log("page_select_detected", ratio=round(ratio, 3), before=round(before, 3))
            return False
        return ratio <= 0.22

    def drag_select(self, rect: tuple[int, int, int, int], multi: bool = False,
                    to_end: bool = True) -> None:
        """在框内拖选文字。不发 Ctrl+A，焦点跑偏也不会波及整页。"""
        x, y, w, h = [int(v) for v in rect]
        w = max(w, 12)
        h = max(h, 12)
        api.activate_window(self.hwnd) if self.hwnd and api.foreground_window() != self.hwnd else None
        if multi and h > 56:
            mouse.drag(x + w - 6, y + h - 10, x + 4, y + 8, self.rnd)   # 底右 → 顶左
        else:
            mid = y + h // 2
            mouse.drag(x + w - 6, mid, x + 4, mid, self.rnd)            # 右 → 左
        time.sleep(0.12)
        if to_end:
            keyboard.combo("shift", "end")
            time.sleep(0.1)

    def clear_field(self, rect: tuple[int, int, int, int], multi: bool = False) -> None:
        """在输入框内部清空内容：拖选 + Shift+End + Delete，全程不发 Ctrl+A。"""
        self.drag_select(rect, multi=multi)
        api.key_press(keyboard.VK["delete"], hold_ms=30)
        time.sleep(0.15)

    # ------------------------------------------------------------ 系统文件对话框
    def wait_file_dialog(self, timeout_s: float = 12.0) -> int:
        """等目标程序的系统 Open 对话框出现（真实窗口，不是网页元素）。

        注意：Chrome 的文件对话框由独立子进程弹出，pid 与浏览器主窗口不同，
        所以按「程序名 + 窗口类 #32770」匹配，不按 pid。
        """
        t0 = time.time()
        exe_name = os.path.basename((self.site_data.get("window", {}) or {}).get("exe", "chrome.exe")).lower()
        dialogs = ("#32770", "Alternate Modal Top Most")
        # 2026-11-20 实测：Win11 下文件选择器有时不是 #32770（现代选择器 / 由网络位置
        # 命名空间托管），按类名匹配会「等不到」而其实窗口已经弹出来了，后面全被它挡住。
        # 补一条按标题兜底：只要是浏览器进程、宽 ≥200、标题叫「打开/Open/选择文件」就当对话框。
        titles = ("打开", "Open", "选择文件", "Select File")
        while time.time() - t0 < timeout_s:
            self.check_abort()
            for w in api.list_windows():
                if os.path.basename(w["exe"]).lower() != exe_name:
                    continue
                if w["client_size"][0] < 200:
                    continue
                if w["class"] in dialogs or (w.get("title") or "").strip() in titles:
                    return w["hwnd"]
            time.sleep(0.35)
        raise RuntimeError("等不到系统文件对话框（点击上传后应弹出「打开」窗口）")

    def handle_file_step(self, path: str, timeout_s: float = 12.0, settle_s: float = 1.2) -> None:
        path = os.path.abspath(self.fill(path))
        if not os.path.exists(path):
            raise FileNotFoundError("待上传文件不存在：%s" % path)
        dlg = self.wait_file_dialog(timeout_s)
        api.activate_window(dlg)
        time.sleep(0.45)
        base = os.path.basename(path)
        ok = False
        for attempt in (1, 2, 3):
            # 焦点必须先落到「文件名」框（Alt+N 是它的加速键）。焦点若停在文件列表上，
            # Ctrl+A 选中的是文件、Ctrl+V 也贴不进去，框里会留着对话框「上次选择」预填的
            # `"文件1" "文件2"` 两个带引号的名字——2026-11-16 02:49 实测就是这个现象。
            keyboard.combo("alt", "n")
            time.sleep(0.25)
            keyboard.combo("ctrl", "a")
            time.sleep(0.15)
            api.key_press(keyboard.VK["backspace"], hold_ms=30)
            time.sleep(0.2)
            if attempt == 1 and hasattr(clipboard, "set_files"):
                # 优先按「资源管理器复制文件」的格式放剪贴板：对话框会自己定位并选中该文件
                clipboard.set_files([path])
            else:
                # 退路：直接贴完整路径文本
                clipboard.set_text(path)
            keyboard.combo("ctrl", "v")
            time.sleep(0.8)
            got = " | ".join(self._dlg_edit_texts(dlg))
            self.log("file_dialog_check", attempt=attempt, box=got[:180])
            if base in got or path in got:
                ok = True
                break
        if not ok:
            self.log("warn", msg="文件名框未确认到目标文件，按当前内容回车：%s" % base)
        api.key_press(keyboard.VK["enter"], hold_ms=40)
        # 等对话框消失（消失 = 路径已被接受）
        t0 = time.time()
        while time.time() - t0 < 25:
            alive = any(w["hwnd"] == dlg for w in api.list_windows())
            if not alive:
                break
            time.sleep(0.4)
        # 用完清空，别把路径或旧文件列表留在剪贴板里污染下一次
        try:
            clipboard.clear()
        except Exception:
            pass
        time.sleep(settle_s)
        if self.hwnd and not api.activate_window(self.hwnd):
            self.log("warn", msg="文件对话框关闭后窗口未回到前台")

    def _dlg_edit_texts(self, dlg: int) -> list:
        """读系统文件对话框里所有 Edit 控件的文本，用来确认文件名框到底填了什么。"""
        import ctypes
        out = []
        try:
            u = api.user32
            CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            u.EnumChildWindows.argtypes = [ctypes.c_void_p, CB, ctypes.c_void_p]
            u.EnumChildWindows.restype = ctypes.c_bool

            def _cb(hwnd, _lp):
                try:
                    cls = ctypes.create_unicode_buffer(64)
                    u.GetClassNameW(ctypes.c_void_p(hwnd), cls, 64)
                    if cls.value == "Edit":
                        n = u.GetWindowTextLengthW(ctypes.c_void_p(hwnd))
                        if n and n > 0:
                            b = ctypes.create_unicode_buffer(n + 1)
                            u.GetWindowTextW(ctypes.c_void_p(hwnd), b, n + 1)
                            out.append(b.value)
                except Exception:
                    pass
                return True

            u.EnumChildWindows(ctypes.c_void_p(dlg), CB(_cb), 0)
        except Exception:
            pass
        return out

    # ------------------------------------------------------------ 节奏
    def approach(self, name: str) -> None:
        """点击前的准备动作。

        第一次点某个区域：视线要在页面上扫一遍才落得到目标上，停得久一点；
        之后同一次流程里再点它是熟路，抬眼看一眼就下手。
        """
        if name and name not in self._seen:
            self._seen.add(name)
            secs = self.tk.search()
            self.log("search", region=name, s=round(secs, 2))
        else:
            secs = self.tk.glance()
            self.log("glance", region=name, s=round(secs, 2))

    def settle_to(self, nxt: dict | None, op: str, st: dict) -> None:
        """两步之间该不该等、等多久——由动作语义决定，不是随机数。

        连续动作用户（连填几个框、连点几下同一块）几乎不停；
        只有「页面刚打开」「第一次找控件」「点完等界面反应」才真停。
        """
        if st.get("no_pause"):
            self.log("settle", mode="skip", s=0)
            return
        cur_tag = timing.tag_of(op)
        nxt_tag = timing.tag_of((nxt or {}).get("do"))
        secs = self.tk.bridge(cur_tag, nxt_tag, st, nxt or {})
        self.log("settle", cur=cur_tag, nxt=nxt_tag, s=round(secs, 2))

    # ------------------------------------------------------------ 步骤
    def step(self, st: dict, idx: int, nxt: dict | None = None) -> None:
        self.check_abort()
        op = st.get("do")
        t0 = time.time()

        if op == "focus":
            self.focus()
            self.log("focus", hwnd=self.hwnd, client=list(self.client()))

        elif op in ("goto", "open"):
            self.focus()
            url = self.fill(st.get("url", "") or "")
            if not url:
                # 默认从站点配置读发布页地址（第一次分析后存进配置，之后只读不猜）
                key = st.get("url_key", "upload_url")
                url = (self.site_data.get(key) or "").strip()
                if not url:
                    raise RuntimeError("站点 %s 配置里没有 %s，先把发布页地址写进 %s.json" % (
                        self.site, key, regions._path(self.site)))
            keyboard.combo("ctrl", "l")
            time.sleep(self.rnd.uniform(0.18, 0.42))
            # 地址靠粘贴，不逐字敲（真人也是复制粘贴）
            old = clipboard.get_text()
            if not clipboard.set_text(url):
                raise RuntimeError("写剪贴板失败，无法粘贴地址")
            time.sleep(self.rnd.uniform(0.1, 0.25))
            keyboard.combo("ctrl", "v")
            time.sleep(self.rnd.uniform(0.25, 0.55))
            api.key_press(keyboard.VK["enter"], hold_ms=40)
            if old:
                try:
                    clipboard.set_text(old)
                except Exception:
                    pass
            wait = st.get("wait_ms", 3000)
            time.sleep(wait / 1000.0)
            # 表单有未保存内容时 Chrome 会弹「离开此网站？」——这是正常一步，直接点掉
            gone = dismiss_modals(self.hwnd, log=self.log)
            if gone:
                self.log("open_modal_dismissed", n=len(gone))
                time.sleep(wait / 1000.0)
            # 🔴 2026-11-20 实测：Ctrl+L 粘贴地址后回车，焦点常留在地址栏，
            # 之后所有点击/输入要么落进联想下拉、要么把内容打进地址栏。
            # 所以导航后要按一下 Escape 把下拉收掉、焦点交回页面。
            # 🔴 但 Escape 在页面「还在加载」时会中止 Chrome 的加载，留下全白页
            # （百家号 2026-11-20 01:54 就是这么白掉的）。必须先等画面稳定再按。
            stable = False
            try:
                stable = screen.wait_stable_win(self.hwnd, None, stable_ms=700, timeout_ms=25000)
            except Exception as e:
                self.log("wait_stable_err", err=str(e))
            api.key_press(keyboard.VK["escape"], hold_ms=30)
            time.sleep(0.25)
            self.log("defocus", why="页面稳定后交还焦点", stable=stable)
            self.log("open", url=url, wait_ms=wait)

        elif op in ("click", "rightclick"):
            if "region" in st:
                rect = self.rect_of(st["region"])
            elif st.get("anchor") == "bottom":
                # 相对视口底边：at=[x, 距底边像素]，下方增删行不影响
                cx, cy, cw, ch = self.client()
                rect = (cx + st["at"][0], cy + ch - st["at"][1], st.get("w", 2), st.get("h", 2))
            else:
                cx, cy = self.client()[0], self.client()[1]
                rect = (cx + st["at"][0], cy + st["at"][1], 2, 2)

            # 点击前局部校验：只截这一小块确认目标还在；没命中按 on_miss 重定位/停下。
            # 不带 probe 的步骤行为与旧版完全一致，零回归。
            if st.get("probe") and "region" in st:
                g = probe.guard(
                    self.site, st["region"], self.client(),
                    on_miss=st.get("on_miss", "relocate") if isinstance(st.get("probe"), bool)
                    else (st["probe"].get("on_miss", "relocate") if isinstance(st["probe"], dict) else "relocate"),
                    out_dir=os.path.join(self.run_dir, "shots"),
                )
                self.log("probe", region=st["region"], action=g.get("action"), ok=g.get("ok"),
                         shot=os.path.basename(str(g.get("detail", ""))))
                if not g.get("ok"):
                    raise RuntimeError(
                        "区域 %s 局部校验未命中（%s），拒绝盲点。现场截图：%s"
                        % (st["region"], g.get("action"), g.get("detail"))
                    )
                rect = tuple(g["rect"])
                cfg = regions.get_region(self.site, st["region"]).get("probe") or {}
                if cfg.get("color"):
                    self.log("probe_cfg", expect_text=cfg.get("text", ""), color=cfg.get("color", ""))

            px, py = mouse.random_point_in(rect, self.rnd)
            api.activate_window(self.hwnd) if self.hwnd and api.foreground_window() != self.hwnd else None
            self.approach(st.get("region", ""))
            btn = st.get("button") or ("right" if op == "rightclick" else "left")
            mouse.click(px, py, rnd=self.rnd, button=btn)
            if "region" in st:
                regions.bump(self.site, st["region"], True)
            self.log(op, region=st.get("region", ""), at=[px, py], button=btn)

        elif op == "dblclick":
            rect = self.rect_of(st["region"])
            px, py = mouse.random_point_in(rect, self.rnd)
            self.approach(st["region"])
            mouse.double_click(px, py, self.rnd)
            self.log("dblclick", region=st["region"], at=[px, py])

        elif op == "drag":
            # 用真实鼠标拖选文本，替代 Ctrl+A（抖音复合编辑框会把 Ctrl+A 变成整页全选）
            cx, cy = self.client()[0], self.client()[1]
            if st.get("region") and ("dx" in st or "dy" in st):
                # 拖控件（缩放滑块 / 把手）：从区域中心按下，按 dx,dy 拖到位。
                # 偏移量按当前/录制客户区宽度等比缩放，视口换尺寸也能对准。
                rx, ry, rw, rh = self.rect_of(st["region"])
                mx, my = rx + rw // 2, ry + rh // 2
                reg = (regions.get_region(self.site, st["region"]) or {}).get("client")                     or (self.site_data.get("client") or [0, 0])
                sx = (self.client()[2] / float(reg[0])) if reg and reg[0] else 1.0
                x0, y0 = mx, my
                x1 = mx + int(round(int(st.get("dx", 0)) * sx))
                y1 = my + int(st.get("dy", 0))
                api.activate_window(self.hwnd) if self.hwnd and api.foreground_window() != self.hwnd else None
                mouse.drag(x0, y0, x1, y1, self.rnd)
                self.log("drag", region=st.get("region", ""), frm=[x0 - cx, y0 - cy],
                         to=[x1 - cx, y1 - cy])
            elif st.get("region"):
                rx, ry, rw, rh = self.rect_of(st["region"])
                x0, y0 = rx + 4, ry + rh // 2
                x1, y1 = rx + rw - 4, ry + rh // 2
            else:
                a, b = st["from"], st["to"]
                x0, y0, x1, y1 = cx + a[0], cy + a[1], cx + b[0], cy + b[1]
            api.activate_window(self.hwnd) if self.hwnd and api.foreground_window() != self.hwnd else None
            mouse.drag(x0, y0, x1, y1, self.rnd)
            self.log("drag", frm=[x0 - cx, y0 - cy], to=[x1 - cx, y1 - cy])

        elif op == "select":
            # 只选中、不删除：给「先选整行再粘贴替换」用，永不发 Ctrl+A
            if st.get("region"):
                rect = self.rect_of(st["region"])
            else:
                cx, cy = self.client()[0], self.client()[1]
                a = st.get("at", [0, 0])
                rect = (cx + a[0], cy + a[1], int(st.get("w", 240)), int(st.get("h", 32)))
            self.drag_select(rect, multi=bool(st.get("multi")),
                             to_end=bool(st.get("to_end", True)))
            self.log("select", region=st.get("region", ""), via="drag")

        elif op in ("type", "paste", "fill"):
            text = self.fill(st.get("text", ""))
            # 用户 2026-11-11 口径：标题 / 描述 / 话题这类表单文本一律复制粘贴，
            # 不逐字敲——中文逐字会被 IME 吞、长文本还慢。只有显式 literal:true 才逐字。
            if op == "fill" and (st.get("region") or st.get("at")):
                self._click_target(st)
            if st.get("clear"):
                rect = None
                if st.get("region"):
                    rect = self.rect_of(st["region"])
                elif st.get("at"):
                    cx, cy = self.client()[0], self.client()[1]
                    rect = (cx + st["at"][0], cy + st["at"][1],
                            int(st.get("w", 240)), int(st.get("h", 32)))
                # clear_mode: drag（默认，框内拖选）| all（整框全选，带误选整页检测）
                mode = st.get("clear_mode") or ("drag" if rect else "all")
                if rect and mode != "all":
                    # 有框就按框内拖选清空——绝不发 Ctrl+A（抖音会整页全选）
                    self.clear_field(rect, multi=bool(st.get("multi")))
                    self.log("clear", region=st.get("region", ""), via="drag")
                else:
                    # 整框全选：光标已在框内时 Ctrl+A 才会被编辑器接住；
                    # 按完立刻用像素占比验有没有误选整页，误选就取消并退回拖选
                    base = 0.0
                    try:
                        cx0, cy0, cw0, ch0 = self.client()
                        base = selection_ratio((cx0, cy0, cw0, ch0))
                    except Exception:
                        pass
                    keyboard.combo("ctrl", "a")
                    time.sleep(0.14)
                    if not self.guard_no_page_select(before=base):
                        mouse.click(self.client()[0] + 4, self.client()[1] + 4, rnd=self.rnd)
                        time.sleep(0.12)
                        cur = self.client()
                        fall = rect or (cur[0] + 4, cur[1] + int(st.get("y", 200)),
                                        cur[2] - 8, int(st.get("h", 32)))
                        self.clear_field(fall, multi=bool(st.get("multi")))
                        self.log("clear", via="drag_fallback")
                    else:
                        self.log("clear", region=st.get("region", ""), via="ctrl_a")
                        api.key_press(keyboard.VK["delete"], hold_ms=30)
                        time.sleep(0.16)
            if st.get("literal"):
                keyboard.type_text(text, self.rnd, wpm=tuple(st.get("wpm", (34, 70))))
                self.log(op, chars=len(text), via="keystrokes", preview=text[:24])
            else:
                old = clipboard.get_text()
                if not clipboard.set_text(text):
                    raise RuntimeError("写剪贴板失败")
                time.sleep(0.12)
                keyboard.combo("ctrl", "v")
                time.sleep(0.35)
                if old:
                    try:
                        clipboard.set_text(old)
                    except Exception:
                        pass
                self.log(op, chars=len(text), via="clipboard", preview=text[:24])

        elif op == "resume_gate":
            # 用户 2026-11-16 口径：进页面先判有没有上次没发完的草稿。
            # 有 → 点「继续编辑」在原稿上接着做，防止重复上传；
            # 无 → 什么都不做，后面的上传步骤照常走。
            # 判据是提示条区域那一小块模板，只截局部，不整屏扫。
            dg = (self.site_data.get("draft_guard") or {})
            pr = dg.get("probe_region")
            if not pr:
                self.log("resume_gate_skip", why="站点未配 draft_guard.probe_region")
            else:
                g = probe.guard(self.site, pr, self.client(),
                                on_miss=dg.get("on_miss", "learn"),
                                out_dir=os.path.join(self.run_dir, "shots"))
                act = g.get("action")
                # 第一次没有模板时会学一次（action=learned）；站点若明确声明
                # 「首次学完就当命中」，就按有草稿处理，防止第一次又白传一遍。
                hit = (act == "hit") or (act == "learned" and bool(dg.get("first_run_assume")))
                self.log("resume_gate", region=pr, action=act, hit=hit)
                if hit:
                    cr = dg.get("continue_region")
                    if not cr:
                        self.log("resume_hit_no_region", region=pr)
                    else:
                        r = self.rect_of(cr)
                        px, py = mouse.random_point_in(r, self.rnd)
                        if self.hwnd and api.foreground_window() != self.hwnd:
                            api.activate_window(self.hwnd)
                        mouse.click(px, py, rnd=self.rnd)
                        self.resumed = True
                        self.log("resume_continue", region=cr, at=[px, py])
                        time.sleep(float(dg.get("wait_ms", 2800)) / 1000.0)
                else:
                    self.log("resume_none", region=pr)

        elif op == "confirm_success":
            # 用户 2026-11-16 口径：点发布后必须在页面上确认成功了才允许关窗。
            # 站点配了 success_check.region（成功提示条 / 稿件管理页特征元素）就做
            # 局部模板校验；没配则退化为「页面已变 + 稳定」并标记 unverified，
            # 由 close 闸门要求人工看图，不冒充成功。
            sc = (self.site_data.get("success_check") or {})
            reg = sc.get("region")
            if reg:
                g = probe.guard(self.site, reg, self.client(),
                                on_miss=sc.get("on_miss", "warn"),
                                out_dir=os.path.join(self.run_dir, "shots"))
                self.publish_ok = bool(g.get("ok"))
                self.log("confirm_success", region=reg, ok=self.publish_ok,
                         action=g.get("action"))
                if not self.publish_ok and st.get("required", True):
                    raise RuntimeError(
                        "发布后没在页面上确认到成功标志（区域 %s 未命中，%s）。"
                        "按铁律 36 不许当成功、不许关窗。现场截图：%s"
                        % (reg, g.get("action"), g.get("detail")))
            else:
                self.publish_ok = True
                self.publish_unverified = True
                self.log("confirm_success", region="", ok=True, unverified=True,
                         why="站点未配 success_check.region，只做了页面变化判定")

        elif op == "dismiss_resume":
            # 残留草稿预清理（用户 2026-11-16 口径）：上次没发完，进投稿页会弹
            # 「继续编辑 / 恢复草稿」。先放弃并删掉那一条，再走本次全新发布，
            # 否则上一条的标题、简介、封面会被带进新作品。
            dg = (self.site_data.get("draft_guard") or {})
            if not dg.get("probe_region"):
                self.log("draft_guard_skip", why="站点未配 draft_guard，跳过预清理")
            else:
                g = probe.guard(self.site, dg["probe_region"], self.client(),
                                on_miss="warn",
                                out_dir=os.path.join(self.run_dir, "shots"))
                if not g.get("ok"):
                    self.log("draft_none", region=dg["probe_region"], action=g.get("action"))
                else:
                    for key in ("discard_region", "confirm_region"):
                        name = dg.get(key)
                        if not name:
                            continue
                        r = self.rect_of(name)
                        px, py = mouse.random_point_in(r, self.rnd)
                        if self.hwnd and api.foreground_window() != self.hwnd:
                            api.activate_window(self.hwnd)
                        mouse.click(px, py, rnd=self.rnd)
                        self.log("draft_click", region=name, at=[px, py])
                        time.sleep(float(dg.get("wait_ms", 900)) / 1000.0)
                    self.log("draft_discarded", region=dg["probe_region"])

        elif op == "crop_fit":
            # 封面裁剪框调整（用户 2026-11-16 口径）：平台给了裁剪框，就把图拖到
            # 设计稿的重要文案全部落在框内。偏移优先取步骤内联，其次取站点 cover.crop_adjust。
            ca = ((self.site_data.get("cover") or {}).get("crop_adjust") or {})
            box = st.get("box") or ca.get("box")
            dx = st.get("dx", (ca.get("drag") or [0, 0])[0])
            dy = st.get("dy", (ca.get("drag") or [0, 0])[1])
            zoom = st.get("zoom", ca.get("zoom"))
            if not box:
                self.log("crop_fit_skip", why="未配裁剪框区域")
            else:
                rx, ry, rw, rh = self.rect_of(box)
                mx, my = rx + rw // 2, ry + rh // 2
                if zoom:
                    mouse.scroll(int(zoom), mx, my, self.rnd)
                    time.sleep(0.45)
                if dx or dy:
                    mouse.drag(mx, my, mx + int(dx), my + int(dy), self.rnd)
                self.log("crop_fit", box=box, drag=[int(dx), int(dy)], zoom=zoom)

        elif op == "probe":
            name = st.get("region", "")
            if st.get("learn"):
                rect = self.rect_of(name)
                info = probe.learn(self.site, name, rect, st.get("color", ""),
                                   st.get("text", ""), int(st.get("pad", 12)),
                                   os.path.join(self.run_dir, "shots", "probe_%s.png" % name))
                self.log("probe_learn", region=name, shot=os.path.basename(info["png"]))
            else:
                g = probe.guard(self.site, name, self.client(),
                                on_miss=st.get("on_miss", "abort"),
                                out_dir=os.path.join(self.run_dir, "shots"))
                self.log("probe", region=name, action=g.get("action"), ok=g.get("ok"))
                if not g.get("ok") and st.get("required", True):
                    raise RuntimeError("区域 %s 局部校验未命中：%s" % (name, g.get("action")))

        elif op == "preflight":
            rep = preflight(self.site, self.client() if self.hwnd else None)
            self.log("preflight", locked=rep["locked"], client=rep["client"],
                     blockers=len(rep["blockers"]), foreground=rep["foreground"])
            for b in rep["blockers"]:
                self.log("preflight_blocker", title=b["title"][:48], cls=b["class"])
            # 用户 2026-11-11 口径：这类确认窗就是个普通弹窗，点「确定/离开」就行，
            # 不当阻塞停下。默认自动点掉，只有显式 dismiss:false 才保持原样。
            if rep["blockers"] and st.get("dismiss", True):
                gone = dismiss_modals(self.hwnd, log=self.log)
                self.log("preflight_dismiss", n=len(gone),
                         closed=sum(1 for g in gone if g.get("closed")))
                rep = preflight(self.site, self.client() if self.hwnd else None)
            if rep["blockers"] and st.get("strict", False):
                raise RuntimeError("存在会吞点击的顶层确认窗且没点掉：%s"
                                   % ", ".join(b["title"][:24] for b in rep["blockers"]))

        elif op == "modal":
            # 手动点掉 Chrome 独立确认窗（「离开此网站？」「要重新加载吗？」）
            gone = dismiss_modals(self.hwnd, log=self.log)
            self.log("modal", n=len(gone), closed=sum(1 for g in gone if g.get("closed")))

        elif op == "key":
            keyboard.press(st["key"], int(st.get("times", 1)))
            self.log("key", key=st["key"], times=st.get("times", 1))

        elif op == "combo":
            keyboard.combo(*st["keys"])
            self.log("combo", keys=st["keys"])

        elif op == "wait":
            ms = int(st.get("ms", 800))
            time.sleep(ms / 1000.0)
            self.log("wait", ms=ms)

        elif op in ("wait_stable", "wait_change"):
            rect = self.rect_of(st["region"]) if st.get("region") else self.client()
            if op == "wait_change":
                base = screen.signature_rect(rect)
                ok = screen.wait_change(
                    rect, base,
                    timeout_ms=int(st.get("timeout_ms", 25000)),
                    stable_ms=int(st.get("stable_ms", 600)),
                )
            else:
                # 用窗口自身渲染判稳定：窗口被别的程序盖住时 BitBlt 会一直「不变」，
                # 误判成已加载完，接着就点早了。
                cx, cy = self.client()[0], self.client()[1]
                rel = (rect[0] - cx, rect[1] - cy, rect[2], rect[3])
                if self.hwnd:
                    ok = screen.wait_stable_win(
                        self.hwnd, rel,
                        stable_ms=int(st.get("stable_ms", 800)),
                        timeout_ms=int(st.get("timeout_ms", 25000)),
                    )
                else:
                    ok = screen.wait_stable(
                        rect,
                        stable_ms=int(st.get("stable_ms", 800)),
                        timeout_ms=int(st.get("timeout_ms", 25000)),
                    )
            self.log(op, region=st.get("region", "client"), ok=ok)
            if not ok and st.get("required", False):
                p = self.shot("fail_%02d_%s" % (idx, op))
                raise RuntimeError("画面在 %s 内未%s（截图 %s）" % (st.get("timeout_ms", 25000), "变化" if op == "wait_change" else "稳定", p))

        elif op == "scroll":
            rect = self.rect_of(st["region"]) if st.get("region") else None
            if rect:
                px, py = mouse.random_point_in(rect, self.rnd)
                mouse.scroll(int(st["clicks"]), px, py, self.rnd)
            else:
                # 滚轮事件发给**指针所在的那个窗口区域**。未指定坐标时按「鼠标现在在哪」投递，
                # 可是文件对话框一关，光标常常停在浏览器左导航、
                # 或者干脆还在浏览器外面，滚轮就滚不动正文——页面停在顶部，后面
                # 所有按 y 定位的点击全打在别处（2026-11-16 B站实测整页没动）。
                # 统一滚在客户区中心：那里一定是正文区，与光标历史无关。
                cx, cy, cw, ch = self.client()
                mouse.scroll(int(st["clicks"]), cx + cw // 2, cy + ch // 2, self.rnd)
            self.log("scroll", clicks=st["clicks"], at=st.get("region") or "client_center")

        elif op == "file":
            self.handle_file_step(st["path"], float(st.get("timeout_s", 12)))
            self.log("file", path=self.fill(st["path"]))

        elif op == "wait_file":
            # 逐步确认闸门（用户 2026-11-16 02:28 口径：每一步必须截图、确认过后再操作）。
            # 本环境里浏览器是命令的子进程，命令一结束窗口就没了；所以要让「命令别结束」，
            # 每步做完截图后停在这里，等调用方看过截图、放下放行文件，再走下一步。
            # 等待期间浏览器活着、页面状态原地保留，不重开、不重传。
            path = self.fill(st["path"])
            timeout_s = float(st.get("timeout_s", 900))
            t0 = time.time()
            self.log("wait_file_begin", path=path, timeout_s=timeout_s)
            while time.time() - t0 < timeout_s:
                self.check_abort()
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                    self.log("wait_file_go", path=path, waited=round(time.time() - t0, 1))
                    break
                time.sleep(0.4)
            else:
                raise RuntimeError("等放行超时 %ss：%s" % (timeout_s, path))

        elif op == "pause":
            kind = st.get("kind", "auto")
            if kind == "read":
                secs = self.tk.reading_pause()
            elif kind == "hesitate":
                secs = self.tk.hesitation()
            elif kind == "think":
                secs = self.tk.think()
            else:
                secs = self.tk.delay(st.get("min_s"), st.get("max_s"))
                self.tk.wait(secs)
            self.log("pause", kind=kind, seconds=round(secs, 1), total_s=round(self.tk.spent, 1))

        elif op == "login_gate":
            # 只读窗口标题（操作系统层面），不看页面内容、不读 cookie
            expect = st.get("expect") or []
            bad = st.get("bad") or ["登录", "Login", "Sign in", "注册"]
            title = api.window_title(self.hwnd) if self.hwnd else ""
            need = (bool(expect) and not any(k in title for k in expect)) or any(k in title for k in bad)
            if not need:
                self.log("login_ok", title=title[:60])
            else:
                # 需要人出手：先把浏览器摆到看得见的位置（不置顶，别压住对话窗口）
                got = ui.reveal(self.hwnd)
                self.log("reveal_for_user", client=list(got), title=title[:50])
                if not st.get("block", False):
                    # 要把控制权交回对话问人 → 最小化，别挡着用户看消息
                    ui.minimize(self.hwnd)
                    self.log("minimize_for_talk", reason="need_login")
                    raise NeedUser("需要登录", "请在浏览器窗口里完成登录（当前标题：%s）" % title[:40])
                print("\n>>> 需要登录：当前标题「%s」" % title[:50], flush=True)
                print(">>> 浏览器已摆到屏幕左侧、未置顶。需要看说明就点对话窗口，它会盖在上面。", flush=True)
                print(">>> 登录完成后按 F9 继续（长按 ESC 中止）。", flush=True)
                tv = time.time()
                while True:
                    self.check_abort()
                    if api.async_key_down(VK_F9):
                        break
                    if time.time() - tv > float(st.get("timeout_s", 600)):
                        raise RuntimeError("等待登录超时")
                    time.sleep(0.15)
                ui.release(self.hwnd)
                self.focus()
                time.sleep(0.8)
                self.tk.reading_pause()
                self.log("login_ok", title=api.window_title(self.hwnd)[:60])

        elif op == "gate":
            msg = st.get("prompt", "请人工处理后继续")
            self.log("gate", prompt=msg)
            got = ui.reveal(self.hwnd)
            self.log("reveal_for_user", client=list(got))
            if not st.get("block", False):
                ui.minimize(self.hwnd)
                self.log("minimize_for_talk", reason="need_user")
                raise NeedUser("需要你操作", msg)
            print("\n>>> 需要你手动操作：%s" % msg, flush=True)
            print(">>> 浏览器已摆到屏幕左侧、未置顶；要看说明就点对话窗口，它会盖在上面。", flush=True)
            print(">>> 处理完后按 F9 继续；长按 ESC 中止。\n", flush=True)
            t0 = time.time()
            while True:
                self.check_abort()
                if api.async_key_down(VK_F9):
                    break
                if time.time() - t0 > float(st.get("timeout_s", 900)):
                    raise RuntimeError("人工确认超时（%ss）" % st.get("timeout_s", 900))
                time.sleep(0.12)
            ui.release(self.hwnd)
            self.focus()
            time.sleep(0.5)
            self.log("gate_done", waited_s=round(time.time() - t0, 1))

        elif op == "shot":
            p = self.shot(st.get("name", "step%02d" % idx))
            self.log("shot", file=os.path.basename(p))

        elif op == "note":
            self.log("note", text=st.get("text", ""))

        else:
            raise ValueError("未知步骤类型：%r" % op)

        self.log("step_ok", idx=idx, op=op, ms=int((time.time() - t0) * 1000))
        # 与下一步之间是否停、停多久：看两步在做什么，不看步骤序号
        self.settle_to(nxt, op, st)

    def run(self, steps: list[dict]) -> dict:
        self.log("run_start", site=self.site, steps=len(steps), vars=self.vars, run_dir=self.run_dir)
        ok = 0
        try:
            for i, st in enumerate(steps, 1):
                nxt = steps[i] if i < len(steps) else None
                # 草稿已恢复 → 上传入口、选文件这些步骤整体跳过，防止重复上传
                if st.get("skip_if_resumed") and self.resumed:
                    self.log("skip_resumed", idx=i, op=st.get("do"))
                    ok += 1
                    continue
                if st.get("optional"):
                    try:
                        self.step(st, i, nxt)
                        ok += 1
                    except Exception as e:
                        self.log("optional_skip", idx=i, op=st.get("do"), err=str(e)[:160])
                    continue
                self.step(st, i, nxt)
                ok += 1
            # 带 terminal 的步骤（发布按钮）就是本站终点：点完不许再截图、不许再收尾。
            # 铁律 22/27 与 06 册第 2 次提炼都要求「点发布即收工」。
            if not any(bool(s.get("terminal")) for s in steps):
                self.shot("final")
            self.log("run_done", site=self.site, ok=ok, resumed=self.resumed,
                     publish_ok=self.publish_ok, **self.tk.report())
            return {"ok": True, "steps_done": ok, "run_dir": self.run_dir,
                    "resumed": self.resumed, "publish_ok": self.publish_ok,
                    "publish_unverified": self.publish_unverified,
                    "human_pauses": self.tk.count, "paused_s": round(self.tk.spent, 1),
                    "gap_s": round(self.tk.gap_spent, 1), "thoughts": self.tk.thoughts}
        except Aborted as e:
            self.log("aborted", err=str(e))
            return {"ok": False, "aborted": True, "steps_done": ok, "run_dir": self.run_dir}
        except NeedUser as e:
            self.log("need_user", what=e.what, detail=e.detail)
            return {"ok": False, "need_user": e.what, "detail": e.detail,
                    "steps_done": ok, "run_dir": self.run_dir}
        except Exception as e:
            p = self.shot("error") if self.hwnd else ""
            self.log("run_failed", err=str(e)[:400], step=ok + 1, shot=p)
            return {"ok": False, "error": str(e), "steps_done": ok, "run_dir": self.run_dir, "shot": p}
        finally:
            self.watcher.stop.set()
