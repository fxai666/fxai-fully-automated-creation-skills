# -*- coding: utf-8 -*-
"""真人节奏引擎（语义驱动版）。

停顿不是「每步之后随机抽一个数」——那是机器的做法。真人只在这几种情况下停：

  要看  —— 页面刚变、控件刚出现、内容得读一眼
  要找  —— 目标在哪，手得先过去
  要想  —— 这里要做决定（填什么、发不发）

其余时间动作是连贯的：点开输入框立刻打字，打完立刻点下一个框，中间只剩
手指从键盘挪到鼠标的零点几秒。所以本模块把时间拆成两层：

  gap    动作内部间隙，0.04~0.9s 量级，不套用「最少停顿」
  pause  真正的停顿，长尾混合分布，只在语义断点出现

一次发布通常有 4~8 条「动作流」（打开页面 → 上传 → 填表 → 选合集 → 提交），
流内几乎不停，流与流之间才停一下。既像人，也不会把一次发布拖成一小时。
"""
from __future__ import annotations

import random
import time

from . import api

# ---------------------------------------------------------------- 动作语义
# 每个步骤类型归到一个语义类别，停顿规则按类别组合判定，
# 而不是按步骤名硬编码——新增步骤类型只要归类就行。
NAV = "nav"        # 换页面：之后的停是「等它出来并看清」
WINDOW = "window"  # 只是把窗口调到前台，画面没变：看一眼就动手
TARGET = "target"  # 需要先找到并指向某个位置：点、双击、滚动、选文件
INPUT = "input"    # 作用于当前焦点，手已经在键盘上：打、粘、按键、组合键
SETTLE = "settle"  # 等画面自己变：等等、等稳定、等变化
META = "meta"      # 闸门 / 显式停顿 / 记录：自己的时间自己管
END = "end"        # 流程结束

TAGS = {
    "focus": WINDOW, "open": NAV, "goto": NAV,
    "click": TARGET, "rightclick": TARGET, "dblclick": TARGET, "scroll": TARGET, "file": TARGET,
    "type": INPUT, "paste": INPUT, "key": INPUT, "combo": INPUT,
    "wait": SETTLE, "wait_stable": SETTLE, "wait_change": SETTLE,
    "pause": META, "gate": META, "login_gate": META, "shot": META, "note": META,
}


def tag_of(op: str | None) -> str:
    """步骤类型 → 动作语义。未登记的类型按「需要定位目标」处理（最保守）。"""
    if not op:
        return END
    return TAGS.get(op, TARGET)


class TimeKeeper:
    def __init__(self, cfg: dict | None = None, rnd: random.Random | None = None, verbose: bool = True) -> None:
        cfg = cfg or {}
        self.r = rnd or random
        self.min_s = float(cfg.get("min_s", 0.5))
        self.max_s = float(cfg.get("max_s", 120.0))
        # 混合分布权重：短 / 中 / 长 / 离开（只在真正的停顿点用）
        w = cfg.get("weights") or [0.86, 0.105, 0.03, 0.005]
        self.w = [float(x) for x in w]
        self.enabled = bool(cfg.get("enabled", True))
        self.scale = float(cfg.get("scale", 1.0))
        self.verbose = verbose
        self.spent = 0.0       # 真正的停顿累计
        self.gap_spent = 0.0   # 动作间隙累计
        self.count = 0         # 停顿次数
        self.thoughts = 0      # 中途停下来想一下的次数
        self.recent: list[dict] = []

    # -------------------------------------------------- 停顿：长尾混合分布
    def _bucket(self) -> str:
        x = self.r.random()
        acc = 0.0
        names = ("short", "medium", "long", "away")
        for name, weight in zip(names, self.w):
            acc += weight
            if x <= acc:
                return name
        return "short"

    def delay(self, lo: float | None = None, hi: float | None = None) -> float:
        """按混合分布取一个停顿时长（秒），并夹在 [min_s, max_s] 内。

        用于「阶段级」的停顿：换大环节、提交前、被叫走。不要在每步之后调用。
        """
        if not self.enabled:
            return 0.0
        bucket = self._bucket()
        r = self.r
        if bucket == "short":
            # 对数正态，峰在 1.1 秒左右，避免均匀分布那种「平均 2 秒」的机械感
            v = r.lognormvariate(0.1, 0.52)
        elif bucket == "medium":
            v = r.uniform(4.0, 25.0)
        elif bucket == "long":
            v = r.uniform(25.0, 60.0)
        else:
            v = r.uniform(60.0, 120.0)
        v *= self.scale
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return max(self.min_s, min(self.max_s, v))

    def wait(self, seconds: float, fidget: bool = True) -> None:
        """真实等待。等待期间手会有轻微动作（人不会像机器人一样完全静止）。"""
        if seconds <= 0:
            return
        self.spent += seconds
        self.count += 1
        end = time.time() + seconds
        # 长等待拆段，中间夹 0~2 次鼠标微动
        moves = 0
        max_moves = 2 if seconds > 6 else 0
        while time.time() < end:
            remain = end - time.time()
            slice_s = min(remain, self.r.uniform(0.8, 2.6))
            time.sleep(max(0.0, slice_s))
            if fidget and moves < max_moves and self.r.random() < 0.45:
                x, y = api.cursor_pos()
                api.move_absolute(x + self.r.randint(-2, 2), y + self.r.randint(-1, 1))
                moves += 1

    # -------------------------------------------------- 间隙：动作连贯性
    def gap(self, lo: float = 0.06, hi: float = 0.4) -> float:
        """动作内部间隙：手从一个位置挪到下一个位置的耗时。

        这是连贯性，不是停顿，所以不受 min_s 约束。填一个表单里三个框，
        框与框之间就是这个量级——真实的人不会每填一格都发呆两秒。
        """
        if not self.enabled:
            return 0.0
        v = max(0.0, self.r.uniform(lo, hi) * self.scale)
        if v > 0:
            time.sleep(v)
        self.gap_spent += v
        return v

    # -------------------------------------------------- 场景化停留
    def glance(self) -> float:
        """点熟悉位置前的抬眼一看：手已经在鼠标上，只是确认一下。"""
        if not self.enabled:
            return 0.0
        v = self.r.uniform(0.12, 0.62) * self.scale
        time.sleep(v)
        self.gap_spent += v
        return v

    def search(self) -> float:
        """第一次找某个控件：视线要从上到下扫一遍才会落到目标上。"""
        if not self.enabled:
            return 0.0
        if self.r.random() < 0.18:
            v = self.r.uniform(2.4, 9.0) * self.scale
        else:
            v = self.r.uniform(0.5, 2.4) * self.scale
        self.wait(v)
        return v

    def think(self) -> float:
        """途中忽然停一下（想起点什么、回头确认上一个字段填对没有）。"""
        if not self.enabled:
            return 0.0
        self.thoughts += 1
        v = self.r.uniform(1.4, 6.5) * self.scale
        self.wait(v)
        return v

    def reading_pause(self) -> float:
        """到达新页面后「读一下」：真人会扫一眼再动手。"""
        if not self.enabled:
            return 0.0
        v = self.r.uniform(0.9, 6.5) if self.r.random() < 0.85 else self.r.uniform(6.5, 18.0)
        v = max(self.min_s, v * self.scale)
        self.wait(v)
        return v

    def hesitation(self) -> float:
        """提交前犹豫：这一步决定要不要发出去，真人会停得久一点。"""
        if not self.enabled:
            return 0.0
        v = max(self.min_s, self.r.uniform(2.5, 18.0) * self.scale)
        self.wait(v)
        return v

    # -------------------------------------------------- 桥接：由语义决定间隙
    def bridge(self, cur: str, nxt: str, cur_st: dict | None = None, nxt_st: dict | None = None) -> float:
        """决定并执行两步之间该等多久，返回秒数。

        规则来自真实动作的因果关系，不是随机数：

          window→ *       只是把窗口切到前台，画面没变，抬眼就动手
          nav   → *       页面刚打开，得等它出来并扫一眼（下一步就是等画面则免）
          input → input   手还在键盘上，只是换框，几乎立刻继续
          target→ input   刚点中框，光标就位，马上开始打
          input → target  打完字，手从键盘移到鼠标，比反向慢
          target→ target  点完得看界面反应，再找下一个目标在哪
          settle→ *       等完了确认一眼就走
          *    → meta     闸门/暂停自己管时间，不重复加
        """
        cur_st = cur_st or {}
        nxt_st = nxt_st or {}
        if not self.enabled:
            return 0.0

        # 流程作者显式声明「这几步是一条连续动作」→ 只留手指间隙
        if nxt_st.get("chain") is not None and nxt_st.get("chain") == cur_st.get("chain"):
            return self.gap(0.05, 0.28)
        if nxt_st.get("glued") or cur_st.get("glued_next"):
            return self.gap(0.04, 0.2)

        if nxt == META:
            return 0.0
        if nxt == END:
            return self.gap(0.3, 1.2)
        if cur == META:
            return self.gap(0.2, 0.9)

        if cur == WINDOW:
            # 切窗口而已：下一步要是导航或等画面，那时间它自己会花
            if nxt in (NAV, SETTLE):
                return self.gap(0.15, 0.5)
            return self.glance()

        if cur == NAV:
            # 下一步本来就是「等画面稳定/变化」，加载交给它，人不必再读
            if nxt == SETTLE:
                return self.gap(0.15, 0.6)
            return self.reading_pause()

        if cur == INPUT and nxt == INPUT:
            # 同一动作流里换框：极短；偶发一次真的停下来想
            if self.r.random() < 0.08:
                return self.think()
            return self.gap(0.06, 0.4)

        if cur == TARGET and nxt == INPUT:
            return self.gap(0.15, 0.55)

        if cur == INPUT and nxt == TARGET:
            return self.gap(0.22, 0.85)

        if cur == TARGET and nxt == TARGET:
            # 少数情况点完发现不对，停下来看清楚再点下一个
            if self.r.random() < 0.08:
                v = self.r.uniform(2.2, 9.0) * self.scale
                self.wait(v)
                return v
            return self.gap(0.35, 1.8)

        if cur == SETTLE:
            return self.gap(0.15, 0.9)

        return self.gap(0.2, 0.9)

    # -------------------------------------------------- 兼容旧流程
    def after_step(self, op: str = "") -> None:
        """（旧接口）老版本每步之后都停一次。现在等价于「这一步是动作流结尾」。"""
        self.bridge(tag_of(op), END)

    def before_click(self) -> None:
        """（旧接口）点击前的犹豫。新流程用 glance/search 更贴真实。"""
        if not self.enabled:
            return
        if self.r.random() < 0.09:
            self.wait(self.delay(3.0, 40.0))
        else:
            self.glance()

    def report(self) -> dict:
        return {"pauses": self.count, "paused_s": round(self.spent, 1),
                "gap_s": round(self.gap_spent, 1), "thoughts": self.thoughts}
