# -*- coding: utf-8 -*-
"""成片尺寸探测 + 生图尺寸换算（片头卡 / 视频内嵌图唯一口径）。

2026-11-28 用户定硬规则：凡要铺进视频的图片，**最终尺寸必须等于视频实际分辨率**。
高分辨率封面缩进去再 pad，文字边缘会被抹糊（实测 1920×1080 → 1280×768 首帧发虚）。

千问2.1 只能原生出 32 的整数倍，所以：
    1. 先用 ffprobe 读视频 w×h（或显式传 --video-size WxH）；
    2. 生图尺寸 = 向上取到最近 32 倍数（ceil(w/32)*32, ceil(h/32)*32）；
    3. 生成后**只裁不缩**，居中裁回视频 w×h；全程不允许 scale/resize。

本模块只做尺寸解析与换算，不生图、不拼视频。规范正文见
`references/04-接入视频首帧.md`「片头卡必须按成片尺寸原生直出」。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess


def ffprobe_bin():
    """ffprobe：PATH 优先；找不到再从 fxai-audio-video 的 libass ffmpeg 同目录取。"""
    exe = shutil.which("ffprobe")
    if exe:
        return exe
    here = os.path.dirname(os.path.abspath(__file__))
    share = os.path.dirname(here)
    av_dir = os.path.join(share, "fxai-audio-video")
    if av_dir not in __import__("sys").path:
        __import__("sys").path.insert(0, av_dir)
    try:
        import audio_video as av
        ffmpeg = av.ffmpeg_libass()
        sibling = os.path.join(os.path.dirname(ffmpeg),
                               "ffprobe.exe" if ffmpeg.lower().endswith(".exe") else "ffprobe")
        if os.path.isfile(sibling):
            return sibling
    except Exception:
        pass
    return "ffprobe"


def probe_size(video):
    """读成片宽高，返回 (w, h)。读不到抛 RuntimeError（绝不静默退回默认尺寸）。"""
    if not video or not os.path.isfile(video):
        raise RuntimeError("视频不存在，无法探测尺寸：%s" % video)
    out = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", video],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RuntimeError("ffprobe 读成片失败：%s" % (out.stderr or "")[-400:])
    streams = json.loads(out.stdout or "{}").get("streams") or []
    if not streams:
        raise RuntimeError("成片没有视频流：%s" % video)
    st = streams[0]
    if not st.get("width") or not st.get("height"):
        raise RuntimeError("ffprobe 未返回宽高：%s" % video)
    return int(st["width"]), int(st["height"])


def parse_size(value):
    """'1280x768' / '1280×768' / (1280, 768) → (int, int)。非法输入抛 ValueError。"""
    if isinstance(value, (tuple, list)) and len(value) == 2:
        w, h = value
    else:
        txt = str(value or "").strip().lower().replace("×", "x").replace("*", "x")
        parts = txt.split("x")
        if len(parts) != 2:
            raise ValueError("尺寸格式应为 WxH，例如 1280x768；收到：%s" % value)
        w, h = parts
    try:
        w, h = int(w), int(h)
    except (TypeError, ValueError):
        raise ValueError("尺寸格式应为 WxH，例如 1280x768；收到：%s" % value)
    if w < 64 or h < 64:
        raise ValueError("尺寸过小：%dx%d" % (w, h))
    return w, h


def native_size(w, h, unit=32):
    """千问2.1 原生尺寸：宽高各自向上取到 unit 倍数。"""
    return ((int(w) + unit - 1) // unit) * unit, ((int(h) + unit - 1) // unit) * unit


def crop_box(native, target, anchor="center"):
    """原生尺寸 → 目标尺寸的裁切框 (left, top, right, bottom)，只裁不缩。"""
    nw, nh = native
    tw, th = target
    if tw > nw or th > nh:
        raise ValueError("目标 %dx%d 大于生图 %dx%d，不能用裁切得到（禁止缩小）"
                         % (tw, th, nw, nh))
    a = (anchor or "center").lower()
    if a in ("left", "right"):
        left = 0 if a == "left" else nw - tw
        top = (nh - th) // 2
    elif a in ("top", "bottom"):
        left = (nw - tw) // 2
        top = 0 if a == "top" else nh - th
    else:
        left, top = (nw - tw) // 2, (nh - th) // 2
    return left, top, left + tw, top + th


def resolve(video=None, size_text=None, cfg=None, default=None):
    """确定视频内嵌图的最终尺寸，返回 ((w,h), 来源说明)。

    优先级：显式 --video-size > ffprobe(--video) > 项目配置 > 注册表兜底。
    注册表兜底只给老项目用；新交付必须给视频或显式宽高。
    """
    if size_text:
        return parse_size(size_text), "显式 --video-size"
    if video:
        return probe_size(video), "ffprobe(%s)" % video
    cfg = cfg or {}
    for key in ("video_size", "video_head_size"):
        if cfg.get(key):
            return parse_size(cfg[key]), "封面.json.%s" % key
    w = cfg.get("video_width")
    h = cfg.get("video_height")
    if w and h:
        return parse_size((w, h)), "封面.json.video_width/height"
    if default:
        return parse_size(default), "cover_sizes.json.video_head（兜底，建议传 --video）"
    raise RuntimeError("无法确定视频尺寸：请传 --video <成片.mp4> 或 --video-size WxH")
