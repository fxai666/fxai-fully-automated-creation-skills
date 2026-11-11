# -*- coding: utf-8 -*-
"""区域局部校验（ROI probe）与布局漂移自动重定位。

用户 2026-11-11 口径：
  记录坐标之后，第二次不要再整屏截图；按已有坐标范围**只截那一小块**，
  确认它还是不是目标控件（发布按钮 / 那两个字）。没有命中才全屏重新扫描、
  重新取实际坐标并回写台账。这样上方增删行带来的布局变化能被自动吸收。

零依赖实现（ctypes + 标准库）：
  - 模板指纹：录一次该区域的低分辨率灰度网格，之后同位置重截比对；
  - 主色占比：用 locate.PRESETS 的颜色谓词算按钮底色覆盖率；
  - 局部图另存 PNG，上层（模型 / 人）可只看这一小块确认文字。
"""
from __future__ import annotations

import os
import sys
import time
from typing import Callable

from . import api, regions, screen

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)


def _presets() -> dict:
    # 延迟导入：避免 human 包加载期与 locate.py 形成循环
    from locate import PRESETS
    return PRESETS


def _boxes():
    from locate import rows_of_boxes
    return rows_of_boxes


# ---------------------------------------------------------------- 抓局部
def roi_rect(rect: tuple[int, int, int, int], pad: int = 12) -> tuple[int, int, int, int]:
    """rect 是屏幕绝对矩形；外扩 pad 后返回抓图矩形（相对屏幕）。"""
    x, y, w, h = rect
    p = int(pad)
    return (max(0, x - p), max(0, y - p), w + 2 * p, h + 2 * p)


def grab_roi(rect: tuple[int, int, int, int], pad: int = 12):
    x, y, w, h = roi_rect(rect, pad)
    gw, gh, data = api.grab(x, y, w, h)
    return (x, y, gw, gh, data)


def fingerprint(data: bytes, w: int, h: int, gx: int = 20, gy: int = 8) -> list[float]:
    """低分辨率灰度网格指纹，抗抗锯齿/轻微 hover 变化，但能识别整块内容换掉。"""
    out: list[float] = []
    for j in range(gy):
        for i in range(gx):
            x0, x1 = int(i * w / gx), max(int(i * w / gx) + 1, int((i + 1) * w / gx))
            y0, y1 = int(j * h / gy), max(int(j * h / gy) + 1, int((j + 1) * h / gy))
            tot, n = 0, 0
            for yy in range(y0, y1, max(1, (y1 - y0) // 4)):
                row = yy * w * 4
                for xx in range(x0, x1, max(1, (x1 - x0) // 4)):
                    k = row + xx * 4
                    b, g, r = data[k], data[k + 1], data[k + 2]
                    tot += (r * 299 + g * 587 + b * 114) // 1000
                    n += 1
            out.append(tot / max(1, n))
    return out


def fp_diff(a: list[float], b: list[float]) -> float:
    """归一化平均绝对差（0~1）。"""
    if not a or len(a) != len(b):
        return 1.0
    return sum(abs(x - y) for x, y in zip(a, b)) / (len(a) * 255.0)


def color_ratio(data: bytes, w: int, h: int, color: str) -> float:
    pred: Callable = _presets().get(color or "")
    if not pred:
        return -1.0
    hit = 0
    for k in range(0, len(data), 4):
        b, g, r = data[k], data[k + 1], data[k + 2]
        if pred(r, g, b):
            hit += 1
    return hit / max(1, w * h)


def save_roi(path: str, w: int, h: int, data: bytes) -> str:
    return screen.save_png(path, w, h, screen.bgra_to_rgba(data))


# ---------------------------------------------------------------- 学模板 / 校验
def _probe_dir() -> str:
    return os.path.join(regions.ROOT, "shots", "probes")


def learn(
    site: str,
    name: str,
    rect: tuple[int, int, int, int],
    color: str = "",
    text: str = "",
    pad: int = 12,
    out_png: str = "",
) -> dict:
    """把当前这块 ROI 学成模板，写回区域台账的 probe.learned。"""
    x, y, w, h, data = grab_roi(rect, pad)
    fp = fingerprint(data, w, h)
    ratio = color_ratio(data, w, h, color) if color else -1.0
    p = out_png or os.path.join(_probe_dir(), "%s_%s.png" % (site, name))
    save_roi(p, w, h, data)
    learned = {
        "fp": [round(v, 1) for v in fp],
        "roi": [w, h],
        "color_ratio": round(ratio, 4),
        "png": p,
        "learned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    regions.set_probe(site, name, {"learned": learned, "color": color, "text": text, "pad": pad})
    return learned


def check(
    site: str,
    name: str,
    rect: tuple[int, int, int, int],
    out_png: str = "",
    tolerance: float | None = None,
) -> dict:
    """只截这一小块，与模板比对；不做整屏截图，不做任何输入动作。"""
    reg = regions.get_region(site, name)
    cfg = reg.get("probe") or {}
    learned = cfg.get("learned")
    pad = int(cfg.get("pad", 12))
    color = cfg.get("color", "")
    tol = float(tolerance if tolerance is not None else cfg.get("tolerance", 0.12))
    min_ratio = float(cfg.get("min_ratio", 0.20))

    x, y, w, h, data = grab_roi(rect, pad)
    p = out_png or os.path.join(_probe_dir(), "%s_%s_check.png" % (site, name))
    save_roi(p, w, h, data)

    ratio = color_ratio(data, w, h, color) if color else -1.0
    cur_fp = fingerprint(data, w, h)
    res = {
        "site": site, "name": name, "rect": list(rect), "shot": p,
        "color": color, "ratio": round(ratio, 4), "min_ratio": min_ratio,
        "expect_text": cfg.get("text", ""),
    }
    if not learned:
        res.update(ok=False, reason="no_template", fp_diff=None)
        return res
    if list(learned.get("roi") or []) != [w, h]:
        res.update(ok=False, reason="roi_size_changed", fp_diff=None)
        return res
    d = fp_diff([float(v) for v in learned["fp"]], cur_fp)
    res["fp_diff"] = round(d, 4)
    res["tolerance"] = tol
    color_ok = True
    if color:
        color_ok = ratio >= min_ratio
        res["color_ok"] = color_ok
    if d <= tol and color_ok:
        res.update(ok=True, reason="hit")
    elif not color_ok:
        res.update(ok=False, reason="color_missing")
    else:
        res.update(ok=False, reason="layout_changed")
    return res


# ---------------------------------------------------------------- 全屏重定位
def relocate(
    site: str,
    name: str,
    client_rect: tuple[int, int, int, int],
    color: str,
    old_rect: tuple[int, int, int, int],
    min_area: int = 1000,
) -> dict:
    """校验未命中时才调用：全客户区扫主色，挑最像的按钮，回写台账并重新学模板。"""
    cx, cy, cw, ch = client_rect
    pred = _presets().get(color)
    if not pred:
        raise ValueError("未知颜色预设 %r（可选 %s）" % (color, ", ".join(_presets())))
    gw, gh, data = api.grab(cx, cy, cw, ch)
    # 只在视口下半部找：发布按钮都在底部操作条
    boxes = _boxes()(data, gw, gh, pred, int(ch * 0.35), ch, 0, cw, min_run=12)
    reg = regions.get_region(site, name)
    old_w, old_h = int(reg["rect"][2]), int(reg["rect"][3])
    ox = old_rect[0] - cx + old_w / 2
    oy = old_rect[1] - cy + old_h / 2
    cand = []
    for b in boxes:
        x, y, w, h = b["rect"]
        if b["area"] < min_area or w < 50 or h < 20 or w > 340 or h > 90:
            continue
        if not (1.2 <= w / max(1, h) <= 9.0):
            continue
        dist = ((x + w / 2 - ox) ** 2 + (y + h / 2 - oy) ** 2) ** 0.5
        shape = abs(w - old_w) / max(1, old_w) + abs(h - old_h) / max(1, old_h)
        cand.append((dist + shape * 180, [x, y, w, h]))
    if not cand:
        return {"ok": False, "reason": "no_candidate", "boxes": len(boxes)}
    cand.sort(key=lambda t: t[0])
    x, y, w, h = cand[0][1]

    d = regions.load(site)
    r = d["regions"][name]
    hist = list(r.get("history") or [])
    hist.append({"rect": list(r["rect"]), "at": time.strftime("%Y-%m-%d %H:%M:%S"), "reason": "probe_miss"})
    r["history"] = hist[-8:]
    r["rect"] = [int(x), int(y), int(w), int(h)]
    if r.get("anchor") == "bottom":
        r["bottom_offset"] = int(ch) - (int(y) + int(h))
    r["scroll_state"] = "bottom"
    r["client"] = [int(cw), int(ch)]
    r["relocated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    regions.save(site, d)
    # 位置已确认，用新 ROI 重学模板，下次起恢复「只截局部」
    learned = learn(site, name, (cx + x, cy + y, w, h), color, (r.get("probe") or {}).get("text", ""))
    return {"ok": True, "reason": "relocated", "rect": [x, y, w, h],
            "bottom_offset": r.get("bottom_offset"), "learned": learned["png"]}


# ---------------------------------------------------------------- 组合入口
def guard(
    site: str,
    name: str,
    client_rect: tuple[int, int, int, int],
    on_miss: str = "relocate",
    out_dir: str = "",
) -> dict:
    """点击前的校验闸门。返回 {ok, action, detail, rect}。

    on_miss:
      relocate  未命中 → 全屏重扫 → 回写台账 → 复验通过才允许点（默认）
      abort     未命中 → 抛错停下，绝不盲点
      warn      只记日志仍按旧坐标点（兼容旧流程，不推荐）
      learn     没有模板时学一次就放行
    """
    rect = regions.resolve(site, name, client_rect)
    reg = regions.get_region(site, name)
    cfg = reg.get("probe") or {}
    out = out_dir or _probe_dir()
    os.makedirs(out, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    png = os.path.join(out, "%s_%s_%s.png" % (site, name, stamp))

    if not cfg.get("learned"):
        if on_miss == "abort":
            return {"ok": False, "action": "no_template", "detail": "区域没有模板，先跑 probe --learn"}
        color = cfg.get("color", "")
        info = learn(site, name, rect, color, cfg.get("text", ""), int(cfg.get("pad", 12)), png)
        return {"ok": True, "action": "learned", "detail": info["png"], "rect": list(rect)}

    # 截图前把鼠标挪开，避免 hover 态把按钮颜色改掉
    api.move_absolute(max(0, rect[0] - 60), max(0, rect[1] - 60))
    time.sleep(0.12)
    res = check(site, name, rect, png)
    if res.get("ok"):
        return {"ok": True, "action": "hit", "detail": res["shot"], "rect": list(rect), "check": res}

    if on_miss == "warn":
        return {"ok": True, "action": "warn_miss", "detail": res["shot"], "rect": list(rect), "check": res}
    if on_miss == "abort":
        return {"ok": False, "action": "miss", "detail": res["shot"], "rect": list(rect), "check": res}

    color = cfg.get("color", "")
    if not color:
        return {"ok": False, "action": "miss_no_color", "detail": "区域未配 probe.color，无法自动重定位",
                "rect": list(rect), "check": res}
    rr = relocate(site, name, client_rect, color, rect)
    if not rr.get("ok"):
        return {"ok": False, "action": "relocate_failed", "detail": rr, "rect": list(rect), "check": res}
    new_rect = regions.resolve(site, name, client_rect)
    again = check(site, name, new_rect, png)
    return {
        "ok": bool(again.get("ok")), "action": "relocated" if again.get("ok") else "relocate_unverified",
        "detail": rr, "rect": list(new_rect), "check": again,
    }
