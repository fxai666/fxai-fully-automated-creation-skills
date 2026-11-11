# -*- coding: utf-8 -*-
"""站点操作区域台账。

每站一个文件 `sites/<站点标识>.json`，只记「窗口定位方式 + 各区域相对客户区坐标」，
不记 cookie、不记登录态、不记任何页面数据。区域坐标一律换算成相对窗口客户区，
窗口挪位置不影响；客户区尺寸与录制时偏差过大则拒绝执行，要求重新录制。
"""
from __future__ import annotations

import json
import os
import time

def resolve_hub() -> str:
    """实例目录（站点台账所在）：环境变量 > 全局配置 publish_hub.hub_dir > 本包目录。"""
    env = os.environ.get("FXAI_PUBLISH_HUB", "").strip()
    if env and os.path.isdir(env):
        return env
    try:
        gp = os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json")
        with open(gp, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        hub = ((cfg.get("publish_hub") or {}).get("hub_dir") or "").strip()
        if hub and os.path.isdir(hub):
            return hub
    except Exception:
        pass
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ROOT = resolve_hub()
SITE_DIR = os.path.join(ROOT, "sites")


def _path(site: str) -> str:
    return os.path.join(SITE_DIR, "%s.json" % site)


def load(site: str) -> dict:
    p = _path(site)
    if not os.path.exists(p):
        raise FileNotFoundError("站点台账不存在：%s（先跑 mark 录制区域）" % p)
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save(site: str, data: dict) -> str:
    os.makedirs(SITE_DIR, exist_ok=True)
    p = _path(site)
    data["site"] = site
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return p


def ensure(site: str, exe: str | None = None, title_contains: str | None = None) -> dict:
    try:
        d = load(site)
    except FileNotFoundError:
        d = {"site": site, "window": {}, "client": None, "regions": {}}
    if exe:
        d.setdefault("window", {})["exe"] = exe
    if title_contains is not None:
        d.setdefault("window", {})["title_contains"] = title_contains
    return d


def put_region(
    site: str,
    name: str,
    rect_client: list[int],
    note: str = "",
    client_size: list[int] | None = None,
    anchor: str = "top",
    probe: dict | None = None,
) -> str:
    """录入区域。

    anchor="bottom" 时额外记 `bottom_offset` = 录制时「元素底边 → 视口底边」的像素距离，
    解析时按当前客户区高度反推 y。页面上方增删内容（提示条、协议行、流量推荐）只会让
    滚动条多滚一点，滚到底后元素到底边的距离不变，所以底部锚定比绝对 y 稳定得多。
    """
    d = ensure(site)
    client = client_size or d.get("client")
    reg = {
        "rect": [int(v) for v in rect_client],
        "note": note,
        "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "client": client,
        "hits": 0,
        "fails": 0,
    }
    if anchor == "bottom":
        if not client or not client[1]:
            raise ValueError("底部锚定需要录制时的客户区高度（client_size）")
        reg["anchor"] = "bottom"
        reg["bottom_offset"] = int(client[1]) - (int(rect_client[1]) + int(rect_client[3]))
        reg["scroll_state"] = "bottom"
    if probe:
        reg["probe"] = probe
    d.setdefault("regions", {})[name] = reg
    if client_size:
        d["client"] = [int(client_size[0]), int(client_size[1])]
    return save(site, d)


def reanchor(site: str, names: list[str], anchor: str = "bottom", client_size: list[int] | None = None) -> list[tuple]:
    """把已录入的区域批量转成底部（或顶部）锚定，用于存量台账迁移。

    返回 [(name, bottom_offset), ...]；不带 client 信息的区域按站点 client 计算。
    """
    d = load(site)
    client = client_size or d.get("client")
    out: list[tuple] = []
    for name in names:
        reg = (d.get("regions") or {}).get(name)
        if not reg:
            raise KeyError("站点 %s 没有区域 %s" % (site, name))
        rc = reg.get("client") or client
        if anchor == "bottom":
            if not rc or not rc[1]:
                raise ValueError("区域 %s 缺少录制时客户区尺寸，无法换算底部偏移" % name)
            reg["anchor"] = "bottom"
            reg["bottom_offset"] = int(rc[1]) - (int(reg["rect"][1]) + int(reg["rect"][3]))
            reg["scroll_state"] = "bottom"
            out.append((name, reg["bottom_offset"]))
        else:
            reg.pop("anchor", None)
            reg.pop("bottom_offset", None)
            reg.pop("scroll_state", None)
            out.append((name, None))
    save(site, d)
    return out


def set_probe(site: str, name: str, probe: dict) -> str:
    """给区域挂局部校验配置 / 学到的模板，不动 rect 与命中统计。"""
    d = load(site)
    reg = (d.get("regions") or {}).get(name)
    if not reg:
        raise KeyError("站点 %s 没有区域 %s" % (site, name))
    cur = dict(reg.get("probe") or {})
    cur.update(probe)
    reg["probe"] = cur
    return save(site, d)


def get_region(site: str, name: str) -> dict:
    d = load(site)
    reg = (d.get("regions") or {}).get(name)
    if not reg:
        raise KeyError("站点 %s 未记录区域 %s" % (site, name))
    return reg


def bump(site: str, name: str, ok: bool) -> None:
    try:
        d = load(site)
    except FileNotFoundError:
        return
    reg = d.get("regions", {}).get(name)
    if not reg:
        return
    reg["hits" if ok else "fails"] = int(reg.get("hits" if ok else "fails", 0)) + 1
    reg["last_used"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save(site, d)


def resolve(site: str, name: str, client_rect: tuple[int, int, int, int], strict: bool = True) -> tuple[int, int, int, int]:
    """把区域台账里的相对坐标换算成当前屏幕绝对矩形。"""
    d = load(site)
    reg = d.get("regions", {}).get(name)
    if not reg:
        raise KeyError("站点 %s 未记录区域 %s（已有：%s）" % (site, name, ", ".join(sorted(d.get("regions", {}))) or "无"))
    cx, cy, cw, ch = client_rect
    rec = reg.get("client") or d.get("client")
    anchor = reg.get("anchor", "top")
    if rec and strict:
        rw, rh = float(rec[0]), float(rec[1])
        if rw > 0 and rh > 0:
            # 底部锚定只依赖视口底边，客户区高度变化不该成为拒绝理由；
            # 宽度仍需校验（横向布局变了 x 就不准）。
            dev_w = abs(cw - rw) / rw
            dev = dev_w if anchor == "bottom" else max(dev_w, abs(ch - rh) / rh)
            if dev > 0.04:
                raise RuntimeError(
                    "窗口客户区尺寸已变化（录制 %dx%d，当前 %dx%d，最大偏差 %.0f%%%s）。"
                    "区域坐标不可靠，请重新录制该站区域，或把窗口调回原尺寸。"
                    % (int(rw), int(rh), cw, ch, dev * 100,
                       "；底部锚定只校验宽度" if anchor == "bottom" else "")
                )
    rx, ry, rw_, rh_ = reg["rect"]
    sx, sy = 1.0, 1.0
    if rec and rec[0] and rec[1]:
        sx = cw / float(rec[0])
        sy = ch / float(rec[1])
    out_w = int(round(rw_ * sx))
    is_bottom = anchor == "bottom" and "bottom_offset" in reg
    # 底部锚定的控件是固定像素的按钮/操作条，尺寸不随视口高度缩放；
    # 只有横向宽度随客户区宽度微调。若把 h 按 sy 缩放，反推 y 会差几像素。
    out_h = int(rh_) if is_bottom else int(round(rh_ * sy))
    if is_bottom:
        # 反推：元素底边 = 当前视口底边 - 录制时记下的固定偏移
        y = cy + ch - int(reg["bottom_offset"]) - out_h
    else:
        y = cy + int(round(ry * sy))
    return (cx + int(round(rx * sx)), y, out_w, out_h)


def list_sites() -> list[str]:
    if not os.path.isdir(SITE_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(SITE_DIR) if f.endswith(".json"))
