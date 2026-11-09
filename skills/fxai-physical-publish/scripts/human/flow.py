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

from . import api, browser, clipboard, keyboard, mouse, regions, screen, timing, ui

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

    # ------------------------------------------------------------ 系统文件对话框
    def wait_file_dialog(self, timeout_s: float = 12.0) -> int:
        """等目标程序的系统 Open 对话框出现（真实窗口，不是网页元素）。

        注意：Chrome 的文件对话框由独立子进程弹出，pid 与浏览器主窗口不同，
        所以按「程序名 + 窗口类 #32770」匹配，不按 pid。
        """
        t0 = time.time()
        exe_name = os.path.basename((self.site_data.get("window", {}) or {}).get("exe", "chrome.exe")).lower()
        dialogs = ("#32770", "Alternate Modal Top Most")
        while time.time() - t0 < timeout_s:
            self.check_abort()
            for w in api.list_windows():
                if w["class"] not in dialogs:
                    continue
                if os.path.basename(w["exe"]).lower() != exe_name:
                    continue
                if w["client_size"][0] < 200:
                    continue
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
        # 文件名输入框默认有焦点：清空后逐字敲入完整路径，再回车（真人做法）
        keyboard.combo("ctrl", "a")
        time.sleep(0.15)
        api.key_press(keyboard.VK["backspace"], hold_ms=30)
        time.sleep(0.2)
        keyboard.type_text(path, self.rnd, wpm=(70, 110))
        time.sleep(0.35)
        api.key_press(keyboard.VK["enter"], hold_ms=40)
        # 等对话框消失
        t0 = time.time()
        while time.time() - t0 < 25:
            alive = any(w["hwnd"] == dlg for w in api.list_windows())
            if not alive:
                break
            time.sleep(0.4)
        time.sleep(settle_s)
        if self.hwnd and not api.activate_window(self.hwnd):
            self.log("warn", msg="文件对话框关闭后窗口未回到前台")

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
            self.log("open", url=url, wait_ms=wait)

        elif op in ("click", "rightclick"):
            if "region" in st:
                rect = self.rect_of(st["region"])
            else:
                cx, cy = self.client()[0], self.client()[1]
                rect = (cx + st["at"][0], cy + st["at"][1], 2, 2)
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

        elif op == "type":
            text = self.fill(st.get("text", ""))
            if st.get("clear"):
                keyboard.select_all_then_replace(text, self.rnd)
            else:
                keyboard.type_text(text, self.rnd, wpm=tuple(st.get("wpm", (34, 70))))
            self.log("type", chars=len(text), preview=text[:24])

        elif op == "paste":
            text = self.fill(st.get("text", ""))
            old = clipboard.get_text()
            if not clipboard.set_text(text):
                raise RuntimeError("写剪贴板失败")
            time.sleep(0.12)
            keyboard.combo("ctrl", "v")
            time.sleep(0.35)
            if old:
                clipboard.set_text(old)
            self.log("paste", chars=len(text), preview=text[:24])

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
                mouse.scroll(int(st["clicks"]), rnd=self.rnd)
            self.log("scroll", clicks=st["clicks"])

        elif op == "file":
            self.handle_file_step(st["path"], float(st.get("timeout_s", 12)))
            self.log("file", path=self.fill(st["path"]))

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
        # 与下一步之间要不要停、停多久：看两步在做什么，不看步骤序号
        self.settle_to(nxt, op, st)

    def run(self, steps: list[dict]) -> dict:
        self.log("run_start", site=self.site, steps=len(steps), vars=self.vars, run_dir=self.run_dir)
        ok = 0
        try:
            for i, st in enumerate(steps, 1):
                nxt = steps[i] if i < len(steps) else None
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
            self.log("run_done", site=self.site, ok=ok, **self.tk.report())
            return {"ok": True, "steps_done": ok, "run_dir": self.run_dir,
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
