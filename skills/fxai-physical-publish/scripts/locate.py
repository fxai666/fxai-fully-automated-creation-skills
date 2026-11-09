# -*- coding: utf-8 -*-
"""按像素特征定位界面元素，输出相对客户区的精确矩形。

不读 DOM、不注入：纯粹对截屏做像素分析，等价于「人眼看屏幕找按钮」。
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from human import api, screen  # noqa: E402

PRESETS = {
    # 常见主色（界面按钮）的判定：命中的像素集合取最大连通块的外接矩形
    "blue": lambda r, g, b: b > 170 and 110 < g < 215 and r < 95,
    "lightblue": lambda r, g, b: 200 < r < 248 and 222 < g < 252 and b > 244,  # Chrome 权限气泡的浅蓝按钮
    "deepblue": lambda r, g, b: b > 140 and g < 120 and r < 80,
    "red": lambda r, g, b: r > 175 and g < 95 and b < 95,
    "ksred": lambda r, g, b: r > 225 and 25 < g < 80 and 80 < b < 135,  # 快手品牌红 #F83060
    "orange": lambda r, g, b: r > 200 and 90 < g < 175 and b < 90,
    "green": lambda r, g, b: g > 140 and r < 120 and b < 140,
    "white": lambda r, g, b: r > 244 and g > 244 and b > 244,
    "dark": lambda r, g, b: r < 60 and g < 60 and b < 60,
}


def rows_of_boxes(data: bytes, w: int, h: int, pred, y0: int, y1: int, x0: int, x1: int, min_run: int = 12):
    """按行扫描连续命中像素段，再把上下相邻、水平重叠的行合并成块。"""
    boxes: list[list[int]] = []  # [x0,y0,x1,y1]
    active: list[list[int]] = []  # 正在延伸的块

    for y in range(y0, min(y1, h)):
        runs = []
        start = None
        row = y * w * 4
        for x in range(x0, min(x1, w)):
            i = row + x * 4
            hit = pred(data[i + 2], data[i + 1], data[i])
            if hit and start is None:
                start = x
            elif not hit and start is not None:
                if x - start >= min_run:
                    runs.append((start, x - 1))
                start = None
        if start is not None and min(x1, w) - start >= min_run:
            runs.append((start, min(x1, w) - 1))

        nxt: list[list[int]] = []
        used = [False] * len(runs)
        for bx in active:
            merged = False
            for ri, (rx0, rx1) in enumerate(runs):
                if used[ri]:
                    continue
                # 水平有重叠且上下相邻
                if rx0 <= bx[2] + 2 and rx1 >= bx[0] - 2:
                    bx[0] = min(bx[0], rx0)
                    bx[2] = max(bx[2], rx1)
                    bx[3] = y
                    used[ri] = True
                    merged = True
                    break
            if merged:
                nxt.append(bx)
            else:
                boxes.append(bx)
        for ri, (rx0, rx1) in enumerate(runs):
            if not used[ri]:
                nxt.append([rx0, y, rx1, y])
        active = nxt

    boxes.extend(active)
    out = []
    for b in boxes:
        bw = b[2] - b[0] + 1
        bh = b[3] - b[1] + 1
        out.append({"rect": [b[0], b[1], bw, bh], "area": bw * bh})
    out.sort(key=lambda d: -d["area"])
    return out


def locate(color: str = "blue", y0: int = 0, y1: int = 100000, x0: int = 0, x1: int = 100000,
           min_area: int = 800, top: int = 8, save: str | None = None) -> list[dict]:
    pred = PRESETS.get(color)
    if not pred:
        raise SystemExit("未知颜色预设：%s（可选 %s）" % (color, ", ".join(PRESETS)))
    from human import browser

    wins = browser.main_windows(600)  # 只要主窗口，排除权限气泡/下载气泡等小窗
    if not wins:
        raise SystemExit("没有浏览器主窗口")
    hwnd = wins[0]["hwnd"]
    # 必须先激活：窗口被别的窗口盖住时，截到的是遮挡物
    api.activate_window(hwnd)
    import time as _t

    _t.sleep(0.4)
    cx, cy, cw, ch = api.client_rect(hwnd)
    w_, h_, data = api.grab(cx, cy, cw, ch)
    if save:
        screen.save_png(save, w_, h_, screen.bgra_to_rgba(data))
    boxes = rows_of_boxes(data, w_, h_, pred, y0, y1, x0, x1)
    boxes = [b for b in boxes if b["area"] >= min_area][:top]
    return boxes


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="按颜色定位控件矩形（相对客户区）")
    ap.add_argument("--color", default="blue")
    ap.add_argument("--y0", type=int, default=0)
    ap.add_argument("--y1", type=int, default=100000)
    ap.add_argument("--x0", type=int, default=0)
    ap.add_argument("--x1", type=int, default=100000)
    ap.add_argument("--min-area", type=int, default=800)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--save", default="")
    a = ap.parse_args()
    boxes = locate(a.color, a.y0, a.y1, a.x0, a.x1, a.min_area, a.top, a.save or None)
    for b in boxes:
        r = b["rect"]
        print("rect=%-26s %4dx%-3d area=%-7d 中心=(%d,%d)" % (r, r[2], r[3], b["area"], r[0] + r[2] // 2, r[1] + r[3] // 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
