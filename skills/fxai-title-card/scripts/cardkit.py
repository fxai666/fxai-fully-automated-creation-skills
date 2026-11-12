#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标题卡公共层：读时间码、定位 ffmpeg、字体与居中绘制。"""
import json, os, subprocess, sys

# ---------- 字体白名单（OFL） ----------
# 字体目录按环境解析：FXAI_FONT_DIR > %WINDIR%/Fonts（系统盘符不写死）
_FONTS = os.environ.get("FXAI_FONT_DIR") or os.path.join(
    os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows", "Fonts")
F_TITLE  = os.path.join(_FONTS, "SmileySans-Oblique.otf")       # 得意黑
F_NOTO   = os.path.join(_FONTS, "Noto Sans SC (TrueType).otf")  # Noto Sans SC
F_NOTO_B = os.path.join(_FONTS, "Noto Sans SC Bold (TrueType).otf")
F_NOTO_M = os.path.join(_FONTS, "Noto Sans SC Medium (TrueType).otf")

GOLD = (224, 180, 95)
DARK = (45, 33, 14)

BASE_W = 768   # 竖版基准（字号档的锚点，只用来对照，不参与计算）
BASE_H = 1280

# ---------------- 字号标准表（固定值，不按画布比例算） ----------------
# 按**短边**分四档：同一档内所有分辨率用完全相同的字号，不做 1.452 这种倍乘。
# 竖版 768×1280 与 720×1280 都是「小」，1080×1920 与 1920×1080 都是「中」。
# 改字号只改这张表，别在项目里另调。
TIERS = (
    dict(name="小", upto=800,
         title=64,  title_long=54,  sub=36,  sub_v=44,  foot=27, foot_v=28,
         stroke=4,  top_y=80,  bottom_pad=300, side_x=96,  side_y=80,
         gap_sub=36,  gap_foot=20),
    dict(name="中", upto=1200,
         title=96,  title_long=80,  sub=54,  sub_v=66,  foot=40, foot_v=42,
         stroke=6,  top_y=120, bottom_pad=450, side_x=144, side_y=120,
         gap_sub=54,  gap_foot=30),
    dict(name="大", upto=1800,
         title=128, title_long=106, sub=72,  sub_v=88,  foot=54, foot_v=56,
         stroke=8,  top_y=160, bottom_pad=600, side_x=192, side_y=160,
         gap_sub=72,  gap_foot=40),
    dict(name="超大", upto=99999,
         title=192, title_long=160, sub=108, sub_v=132, foot=80, foot_v=84,
         stroke=12, top_y=240, bottom_pad=900, side_x=288, side_y=240,
         gap_sub=108, gap_foot=60),
)


def tier_for(W, H):
    """按短边取字号档：短边 ≤800 小 / ≤1200 中 / ≤1800 大 / 其余超大。

    用短边而不是对角线，是为了让「同一档」真的同字号：
    768×1280、720×1280、1280×720 都是小档，字号一模一样。
    """
    short = min(int(W), int(H))
    for t in TIERS:
        if short <= t["upto"]:
            return t
    return TIERS[-1]


def canvas_scale(W, H):
    """字号缩放系数（旧算法，仅作兼容保留）。

    只按宽度缩会把横屏（1280×768）算出 1.67 倍，字大得压画面；
    按对角线算，1280×768 与 768×1280 同为 1.0，1080×1920 为 1.44。
    🔴 叠字已改成固定字号档（`tier_for`），新代码不得再用这个乘字号。
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


# ==================== 竖排（横版侧边标题卡） ====================
# 竖版标题卡压顶部会挡横版人物面部，所以横版改走「画面左侧 / 右侧竖着排」。
# 竖排规则：CJK 与全角标点逐字一行（从上到下），连续半角数字/字母成簇横排
# （≤3 字纵中横，>3 字整串转 90° 竖置）；一列排不下按从上到下切列，
# 主标贴边、副标与引语依次向内（主标永远是最好找的那一列）。

CJK_RANGES = ((0x3000, 0x303F), (0x3040, 0x30FF), (0x3400, 0x4DBF),
              (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0xFF01, 0xFF60))


def is_cjk(ch):
    o = ord(ch)
    return any(a <= o <= b for a, b in CJK_RANGES)


def vclusters(text):
    """竖排单元：CJK/全角标点逐字；连续半角数字字母并成一簇。"""
    out, buf = [], ""
    for ch in (text or ""):
        if is_cjk(ch):
            if buf:
                out.append(buf); buf = ""
            out.append(ch)
        elif ch.isspace():
            if buf:
                out.append(buf); buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def vcell(font):
    """竖排一格的高度（字距按字号算，不按 bbox，防止不同字大小不一）。"""
    return int(font.size * 1.16)


def _cluster_img(cluster, font, fill, stroke=0, stroke_fill=None):
    """长英文/数字串 → 横排小图（后面转 90° 竖置）。"""
    from PIL import Image, ImageDraw
    pad = max(4, int(font.size * 0.2) + stroke)
    d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bb = d0.textbbox((0, 0), cluster, font=font, stroke_width=stroke)
    w, h = bb[2] - bb[0] + pad * 2, bb[3] - bb[1] + pad * 2
    im = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((pad - bb[0], pad - bb[1]), cluster, font=font,
                            fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    return im


def draw_vcol(im, draw, x, y0, clusters, font, fill, stroke=0, stroke_fill=None):
    """在中心 x 处竖排一列，返回 (列宽, 列高)。"""
    cell = vcell(font)
    y, wmax = y0, 0
    for c in clusters:
        if not is_cjk(c[0]) and len(c) > 3:          # 长串：整串转 90°
            rot = _cluster_img(c, font, fill, stroke, stroke_fill).rotate(-90, expand=True)
            im.alpha_composite(rot, (int(x - rot.width / 2), int(y)))
            wmax = max(wmax, rot.width)
            y += rot.height
            continue
        bb = draw.textbbox((0, 0), c, font=font, stroke_width=stroke)
        w, h = bb[2] - bb[0], bb[3] - bb[1]
        draw.text((x - w / 2 - bb[0], y + (cell - h) / 2 - bb[1]), c, font=font,
                  fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
        wmax = max(wmax, w, font.size * 0.9)
        y += cell
    return wmax, y - y0
