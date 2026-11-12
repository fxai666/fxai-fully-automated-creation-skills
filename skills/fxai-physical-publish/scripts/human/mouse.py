# -*- coding: utf-8 -*-
"""真人化鼠标：贝塞尔轨迹 + 调速 + 抖动 + 过冲纠正 + 落点随机。

每次调用都重新抽样路径参数，同一点连续点两次轨迹不同。
"""
from __future__ import annotations

import math
import random
import time

from . import api


def _sleep(ms: float) -> None:
    if ms > 0:
        time.sleep(ms / 1000.0)


def _ease(t: float) -> float:
    """先加速后减速（类 minimum-jerk 的简化曲线）。"""
    return t * t * (3.0 - 2.0 * t) * 0.35 + t * 0.65


def plan_path(
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    rnd: random.Random | None = None,
) -> tuple[list[tuple[int, int]], float]:
    """规划一条到目标点的轨迹，返回 [(x,y)...] 与总时长(秒)。"""
    r = rnd or random
    dx = x1 - x0
    dy = y1 - y0
    dist = math.hypot(dx, dy)

    # 步数：距离越远步越多，但带随机扰动，防止固定节奏
    steps = int(max(8, min(64, dist / r.uniform(9.0, 15.0))))
    # 时长按 Fitts 律的直觉：距离大、时间长，但有个下限与上限
    duration = max(0.16, min(0.95, 0.11 + 0.115 * math.log2(1 + dist / 46.0)))
    duration *= r.uniform(0.85, 1.22)

    # 控制点：垂直于位移方向偏移，制造弧线；短距离弧形更小
    if dist < 6:
        ctrl = (x0 + dx * 0.5, y0 + dy * 0.5)
        path = [(x0, y0), ctrl, (x1, y1)]
    else:
        px, py = -dy / dist, dx / dist  # 单位法向量
        bow = r.uniform(0.06, 0.26) * dist * r.choice((1, 1, -1))
        t = r.uniform(0.35, 0.65)
        c1 = (x0 + dx * t + px * bow, y0 + dy * t + py * bow)
        t2 = r.uniform(0.35, 0.65)
        c2 = (
            x0 + dx * t2 + px * bow * r.uniform(0.2, 0.7),
            y0 + dy * t2 + py * bow * r.uniform(0.2, 0.7),
        )
        path = [(x0, y0), c1, c2, (x1, y1)]

    pts: list[tuple[int, int]] = []
    for i in range(steps + 1):
        t = _ease(i / steps)
        if len(path) == 3:
            (ax, ay), (bx, by), (cx, cy) = path
            u = 1 - t
            px_ = u * u * ax + 2 * u * t * bx + t * t * cx
            py_ = u * u * ay + 2 * u * t * by + t * t * cy
        else:
            (ax, ay), (bx, by), (cx, cy), (dx_, dy_) = path
            u = 1 - t
            px_ = u ** 3 * ax + 3 * u * u * t * bx + 3 * u * t * t * cx + t ** 3 * dx_
            py_ = u ** 3 * ay + 3 * u * u * t * by + 3 * u * t * t * cy + t ** 3 * dy_
        # 手抖：越接近终点抖动越小（人对准目标时会收敛）
        damp = max(0.0, 1.0 - t) ** 0.8
        jx = r.gauss(0, 1.1) * damp
        jy = r.gauss(0, 1.1) * damp
        pts.append((int(round(px_ + jx)), int(round(py_ + jy))))

    pts.append((x1, y1))
    return pts, duration


def move_to(x: int, y: int, rnd: random.Random | None = None, speed: float = 1.0) -> None:
    """沿真人轨迹移动到 (x, y)。"""
    r = rnd or random
    x0, y0 = api.cursor_pos()
    pts, duration = plan_path(x0, y0, x, y, r)
    total = len(pts) - 1
    if total <= 0:
        api.move_absolute(x, y)
        return
    per = (duration * 1000.0 / total) * speed
    for i, (px, py) in enumerate(pts):
        api.move_absolute(px, py)
        # 每步间隔带随机波动，模拟手速不匀
        jitter = r.uniform(0.62, 1.5)
        _sleep(per * jitter if i < total else 0)
    api.move_absolute(x, y)


def hover(x: int, y: int, rnd: random.Random | None = None) -> None:
    """移到目标后停一下（人对准按钮会短暂停顿），并带极小位移。"""
    r = rnd or random
    move_to(x, y, r)
    _sleep(r.uniform(70, 210))
    api.move_absolute(x + r.choice((-1, 0, 0, 1)), y + r.choice((-1, 0, 0, 1)))


def click(
    x: int,
    y: int,
    button: str = "left",
    rnd: random.Random | None = None,
    hover_first: bool = True,
) -> None:
    r = rnd or random
    # 偶尔先过冲一点点再回拉（真实手部动作）
    if r.random() < 0.22:
        ox = x + r.choice((-1, 1)) * r.randint(2, 6)
        oy = y + r.choice((-1, 1)) * r.randint(1, 4)
        move_to(ox, oy, r)
        _sleep(r.uniform(30, 80))
    if hover_first:
        hover(x, y, r)
    else:
        move_to(x, y, r)
    api.mouse_button(True, button)
    _sleep(r.uniform(42, 96))  # 按住时长
    api.mouse_button(False, button)
    _sleep(r.uniform(60, 180))


def double_click(x: int, y: int, rnd: random.Random | None = None) -> None:
    r = rnd or random
    hover(x, y, r)
    api.mouse_button(True)
    _sleep(r.uniform(40, 65))
    api.mouse_button(False)
    _sleep(r.uniform(70, 130))  # 两次点击间隔
    api.mouse_button(True)
    _sleep(r.uniform(38, 62))
    api.mouse_button(False)
    _sleep(r.uniform(80, 160))


def right_click(x: int, y: int, rnd: random.Random | None = None) -> None:
    click(x, y, button="right", rnd=rnd)


def scroll(clicks: int, x: int | None = None, y: int | None = None, rnd: random.Random | None = None) -> None:
    """滚动：拆成若干小格，间隔随机，像人滚轮。"""
    r = rnd or random
    if x is not None and y is not None:
        hover(x, y, r)
    left = abs(clicks)
    sign = 1 if clicks > 0 else -1
    while left > 0:
        step = min(left, r.choice((1, 1, 2, 2, 3)))
        api.mouse_wheel(sign * step)
        left -= step
        _sleep(r.uniform(35, 110))


def drag(x0: int, y0: int, x1: int, y1: int, rnd: random.Random | None = None) -> None:
    r = rnd or random
    hover(x0, y0, r)
    api.mouse_button(True)
    _sleep(r.uniform(90, 160))
    pts, duration = plan_path(x0, y0, x1, y1, r)
    per = (duration * 1000.0 / max(1, len(pts) - 1)) * 1.6
    for px, py in pts:
        api.move_absolute(px, py)
        _sleep(per * r.uniform(0.7, 1.4))
    _sleep(r.uniform(110, 200))
    api.mouse_button(False)
    _sleep(r.uniform(80, 160))


def random_point_in(rect: tuple[int, int, int, int], rnd: random.Random | None = None) -> tuple[int, int]:
    """在区域内部取点：避开边缘，落在中间约 56% 的范围。"""
    r = rnd or random
    x, y, w, h = rect
    inset_x = w * 0.22
    inset_y = h * 0.22
    px = x + inset_x + r.random() * max(1.0, (w - 2 * inset_x))
    py = y + inset_y + r.random() * max(1.0, (h - 2 * inset_y))
    return int(round(px)), int(round(py))
