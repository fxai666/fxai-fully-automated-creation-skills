#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公共层：字体解析、画布缩放、ffmpeg/ffprobe 定位、时间码读取、居中绘制。

`fxai-title-card` 与 `fxai-infocard` 共用本文件；改这里的字体栈或缩放口径对两者同时生效。
"""
import json, os, subprocess, sys

sys.dont_write_bytecode = True

from PIL import ImageFont

# ---------- 字体白名单（OFL / 系统自带） ----------
# 🔴 字体优先级（fxai-standard 06 查证定稿）：
#   得意黑 为首选品牌展示字体（标题/大字/标签）；缺失时回退 微软雅黑（屏幕渲染/输出可用，
#   见官方 FAQ「微软雅黑边界」），再回退 Noto Sans SC；最后 PIL 内置默认字。
# 字体目录按环境解析：FXAI_FONT_DIR > %WINDIR%/Fonts（系统盘符不写死）
_FONTS = os.environ.get("FXAI_FONT_DIR") or os.path.join(
    os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows", "Fonts")
F_TITLE  = os.path.join(_FONTS, "SmileySans-Oblique.otf")       # 得意黑（首选）
F_NOTO   = os.path.join(_FONTS, "Noto Sans SC (TrueType).otf")  # Noto Sans SC
F_NOTO_B = os.path.join(_FONTS, "Noto Sans SC Bold (TrueType).otf")
F_NOTO_M = os.path.join(_FONTS, "Noto Sans SC Medium (TrueType).otf")
F_YAHEI  = os.path.join(_FONTS, "msyh.ttc")                     # 微软雅黑（回退）
F_YAHEI_B= os.path.join(_FONTS, "msyhbd.ttc")                   # 微软雅黑 Bold
F_YAHEI_L= os.path.join(_FONTS, "msyhl.ttc")                    # 微软雅黑 Light

GOLD = (224, 180, 95)
DARK = (45, 33, 14)


def _try_first(paths, size):
    """依次尝试字体路径，返回第一个能加载的；都失败给 PIL 默认字。"""
    for p in paths:
        try:
            if os.path.isfile(p):
                return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def title_font(size):
    """标题/大字字体：得意黑 → 微软雅黑Bold → Noto Bold → 默认。"""
    return _try_first([F_TITLE, F_YAHEI_B, F_NOTO_B], size)


def body_font(size):
    """正文/补充文字：得意黑 → 微软雅黑 → Noto → 默认。

    🔴 用户指定默认用得意黑，得意黑缺失才用微软雅黑。"""
    return _try_first([F_TITLE, F_YAHEI, F_NOTO], size)

BASE_W = 768   # 排版基准（768×1280 竖屏）
BASE_H = 1280


def canvas_scale(W, H):
    """字号缩放系数：按画布对角线等比，横竖屏观感一致。

    只按宽度缩会把横屏（1280×768）算出 1.67 倍，字大得压画面；
    按对角线算，1280×768 与 768×1280 同为 1.0，1080×1920 为 1.44。
    """
    import math
    return math.sqrt(float(W) * H) / math.sqrt(float(BASE_W) * BASE_H)


def ffmpeg_bin():
    cand = []
    try:
        cfg = json.load(open(os.environ.get("FXAI_GLOBAL_CONFIG")
                             or os.path.expanduser("~/.workbuddy/fxai_global.json"),
                             encoding="utf-8"))
        sd = cfg.get("software_install_dir")
        if sd:
            cand.append(os.path.join(sd, "ffmpeg", "ffmpeg.exe"))
    except Exception:
        pass
    for c in cand:
        if os.path.isfile(c):
            return c
    return "ffmpeg"


def ffprobe_bin():
    p = os.path.join(os.path.dirname(ffmpeg_bin()), "ffprobe.exe")
    return p if os.path.isfile(p) else "ffprobe"


def run(cmd, cwd=None):
    print("$", " ".join(str(c) for c in cmd[:6]), "...", flush=True)
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        print(p.stdout[-1200:]); print(p.stderr[-2000:])
        raise SystemExit("命令失败：%s" % cmd[0])
    return p


def video_size(path):
    """(w, h) —— 竖屏 768x1280 这类真实像素。"""
    p = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height",
                        "-of", "csv=p=0:s=x", path],
                       capture_output=True, text=True)
    s = p.stdout.strip().splitlines()[0] if p.stdout.strip() else ""
    try:
        w, h = s.lower().split("x")[:2]
        return int(w), int(h)
    except Exception:
        return 0, 0


def has_audio(path):
    p = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=index", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    return bool(p.stdout.strip())


def load_timeline(timeline_path, cards_path=None):
    """读 timeline.json；给了 cards_path 就把文字按序合进去（兼容分离式）。"""
    if not os.path.isfile(timeline_path):
        sys.exit("缺时间码文件：%s" % timeline_path)
    data = json.load(open(timeline_path, encoding="utf-8"))
    beats = data["beats"] if isinstance(data, dict) else data
    if cards_path:
        if not os.path.isfile(cards_path):
            sys.exit("缺标题卡文字：%s" % cards_path)
        cards = json.load(open(cards_path, encoding="utf-8"))
        if len(cards) != len(beats):
            sys.exit("文字 %d 条 vs 时间码 %d 段 —— 必须一一对应"
                     % (len(cards), len(beats)))
        for b, c in zip(beats, cards):
            b.setdefault("title", c.get("title", ""))
            b.setdefault("sub", c.get("sub", ""))
            b.setdefault("foot", c.get("foot", ""))
    for i, b in enumerate(beats, 1):
        b.setdefault("i", i)
        if "start" not in b or "end" not in b:
            sys.exit("第 %d 拍缺 start/end" % i)
        if float(b["end"]) <= float(b["start"]):
            sys.exit("第 %d 拍 end(%.2f) 必须大于 start(%.2f)"
                     % (i, float(b["end"]), float(b["start"])))
    return beats


def text_center(draw, W, y, text, font, fill, stroke=0, stroke_fill=None):
    bb = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    tw = bb[2] - bb[0]
    draw.text(((W - tw) / 2 - bb[0], y), text, font=font, fill=fill,
              stroke_width=stroke, stroke_fill=stroke_fill)
