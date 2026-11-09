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


def put_region(site: str, name: str, rect_client: list[int], note: str = "", client_size: list[int] | None = None) -> str:
    d = ensure(site)
    d.setdefault("regions", {})[name] = {
        "rect": [int(v) for v in rect_client],
        "note": note,
        "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "client": client_size or d.get("client"),
        "hits": 0,
        "fails": 0,
    }
    if client_size:
        d["client"] = [int(client_size[0]), int(client_size[1])]
    return save(site, d)


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
    if rec and strict:
        rw, rh = float(rec[0]), float(rec[1])
        if rw > 0 and rh > 0:
            dev = max(abs(cw - rw) / rw, abs(ch - rh) / rh)
            if dev > 0.04:
                raise RuntimeError(
                    "窗口客户区尺寸已变化（录制 %dx%d，当前 %dx%d，偏差 %.0f%%）。"
                    "区域坐标不可靠，请重新录制该站区域，或把窗口调回原尺寸。"
                    % (int(rw), int(rh), cw, ch, dev * 100)
                )
    rx, ry, rw_, rh_ = reg["rect"]
    sx, sy = 1.0, 1.0
    if rec and rec[0] and rec[1]:
        sx = cw / float(rec[0])
        sy = ch / float(rec[1])
    return (cx + int(round(rx * sx)), cy + int(round(ry * sy)), int(round(rw_ * sx)), int(round(rh_ * sy)))


def list_sites() -> list[str]:
    if not os.path.isdir(SITE_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(SITE_DIR) if f.endswith(".json"))
