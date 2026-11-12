# -*- coding: utf-8 -*-
"""公众号配图「彩色线路」公共绘制库 —— 深色底一律弃用。

口径：明亮淡彩为主，每张一套主色系，配色有讲究；深色版本一律弃用。

固定做法：
    底色    明亮淡彩渐变（每张一套主色系），柔和彩色光晕，无暗角
    主体    高饱和彩色有机形状 / 几何拼贴插画，占画面大半
    重元素  由高饱和彩色承担（同时把 qc.py post 的 RMS 拉过 28），不用黑墨压场
    文字    留白处只贴一行 8~14 字短句 + 同色短竖条，字色用该张主色深调
    系列    底部一条彩色渐变细带 + 三枚小色点，取代旧版深墨横条
    字体    Noto Sans SC / Bold（SIL OFL 1.1，白名单 A）

规格：封面 1152x512，正文图 1152x760；PNG 母版由本库出，
      发布版 JPG 仍走 export_figures.py（q95 / 4:4:4）。

用法（项目脚本里）：
    import sys; sys.path.insert(0, r"<技能>/scripts")
    from figure_lib import *
    p = PALETTES["fig1"]
    im = background(FIG_W, FIG_H, p, seed=113)
    d = ImageDraw.Draw(im)
    ...
    stamp(im, p, "短句", 92, 92)
    footer(d, p, 706)
    save(im, "<项目>/image", "fig1")
"""
import math
import os
import random

from PIL import Image, ImageDraw, ImageFont, ImageFilter

COVER_W, COVER_H = 1152, 512
FIG_W, FIG_H = 1152, 760
PAPER = (255, 252, 244)
TAG = (128, 122, 114)

FONT_DIR = os.environ.get("FXAI_FONT_DIR") or os.path.join(
    os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows", "Fonts")
F_REG = os.path.join(FONT_DIR, "Noto Sans SC (TrueType).otf")
F_BOLD = os.path.join(FONT_DIR, "Noto Sans SC Bold (TrueType).otf")


def _pal(bg_top, bg_bot, ink, c1, c2, c3, c4, deep):
    return dict(bg_top=bg_top, bg_bot=bg_bot, ink=ink,
                c1=c1, c2=c2, c3=c3, c4=c4, deep=deep, paper=PAPER)


# 六套明亮主色系：封面 / fig1~fig5，每套都带「深调字色」与「彩色重元素」
PALETTES = {
    "cover": _pal((255, 248, 228), (255, 226, 218), (176, 64, 48),
                  (242, 106, 75), (58, 178, 196), (245, 196, 74), (124, 108, 204), (36, 124, 144)),
    "fig1": _pal((232, 250, 246), (212, 238, 250), (20, 96, 112),
                 (62, 200, 176), (64, 146, 214), (245, 196, 74), (242, 112, 92), (24, 102, 126)),
    "fig2": _pal((255, 250, 228), (255, 230, 220), (150, 74, 48),
                 (246, 178, 64), (240, 110, 86), (150, 124, 208), (62, 196, 170), (120, 76, 150)),
    "fig3": _pal((240, 236, 255), (222, 246, 246), (68, 52, 138),
                 (116, 96, 214), (58, 190, 176), (247, 206, 96), (240, 112, 116), (70, 52, 146)),
    "fig4": _pal((216, 236, 255), (255, 216, 232), (38, 86, 142),
                 (74, 156, 224), (244, 124, 150), (247, 192, 70), (80, 198, 180), (32, 88, 154)),
    "fig5": _pal((236, 250, 228), (255, 248, 214), (32, 104, 72),
                 (106, 196, 110), (240, 206, 84), (58, 180, 158), (240, 138, 96), (28, 110, 78)),
}


def font(size, bold=False):
    p = F_BOLD if bold else F_REG
    if not os.path.exists(p):
        p = F_REG
    if not os.path.exists(p):
        p = os.path.join(FONT_DIR, "msyhbd.ttc" if bold else "msyh.ttc")
    return ImageFont.truetype(p, size)


def lerp(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def background(w, h, p, seed):
    """明亮淡彩渐变 + 柔和彩色光晕 + 细颗粒（无暗角）。"""
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=lerp(p["bg_top"], p["bg_bot"], y / max(1, h - 1)))

    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lay)
    for cx, cy, r, col in ((w * 0.78, h * 0.24, 260, p["c3"]),
                           (w * 0.18, h * 0.78, 220, p["c2"]),
                           (w * 0.52, h * 0.52, 180, p["c4"])):
        ld.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col + (30,))
    lay = lay.filter(ImageFilter.GaussianBlur(90))
    im = Image.alpha_composite(im.convert("RGBA"), lay).convert("RGB")

    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    rnd = random.Random(seed + 991)
    for _ in range(4600):
        x, y = rnd.randrange(w), rnd.randrange(h)
        od.point((x, y), fill=(92, 82, 72, rnd.choice((5, 7, 9, 11, 13))))
    for _ in range(180):
        x, y = rnd.randrange(w), rnd.randrange(h)
        od.line([(x, y), (x + rnd.randrange(6, 30), y)],
                fill=(120, 108, 92, 9), width=1)
    return Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")


def glow(im, cx, cy, r, color, alpha=46, blur=28):
    lay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(lay).ellipse((cx - r, cy - r, cx + r, cy + r), fill=color + (alpha,))
    lay = lay.filter(ImageFilter.GaussianBlur(blur))
    im.paste(Image.alpha_composite(im.convert("RGBA"), lay).convert("RGB"), (0, 0))


def shadow(im, box, color, r=20, radius=22, offset=(6, 16), alpha=44):
    """彩色柔影，不用黑。"""
    lay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(lay)
    b = (box[0] + offset[0], box[1] + offset[1], box[2] + offset[0], box[3] + offset[1])
    ld.rounded_rectangle(b, radius=r, fill=color + (alpha,))
    lay = lay.filter(ImageFilter.GaussianBlur(radius))
    im.paste(Image.alpha_composite(im.convert("RGBA"), lay).convert("RGB"), (0, 0))


def blob(d, cx, cy, r, fill, seed=1, wobble=0.13, outline=None, width=0, n=52, sy=1.0):
    rnd = random.Random(seed)
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        rr = r * (1 + wobble * math.sin(3 * a + seed) +
                  0.45 * wobble * math.sin(5 * a + seed * 0.7) + rnd.uniform(-.018, .018))
        pts.append((cx + rr * math.cos(a), cy + rr * sy * math.sin(a)))
    d.polygon(pts, fill=fill)
    if outline:
        d.line(pts + [pts[0]], fill=outline, width=width, joint="curve")


def star(d, x, y, r, c):
    pts = []
    for i in range(8):
        a = math.pi * i / 4 - math.pi / 2
        rr = r if i % 2 == 0 else r * 0.34
        pts.append((x + rr * math.cos(a), y + rr * math.sin(a)))
    d.polygon(pts, fill=c)


def confetti(d, cx, cy, spread, p, seed, n=16):
    rnd = random.Random(seed)
    cols = (p["c1"], p["c2"], p["c3"], p["c4"], PAPER)
    for _ in range(n):
        x = cx + rnd.uniform(-spread, spread)
        y = cy + rnd.uniform(-spread * .72, spread * .72)
        r = rnd.choice((3, 4, 5, 7, 9, 11))
        c = rnd.choice(cols)
        if rnd.random() < .55:
            d.ellipse((x - r, y - r, x + r, y + r), fill=c)
        elif rnd.random() < .5:
            star(d, x, y, r * 1.5, c)
        else:
            d.rounded_rectangle((x - r, y - r, x + r, y + r), radius=2, fill=c)


def curve(d, pts, color, width=14, dot=0):
    d.line(pts, fill=color, width=width, joint="curve")
    for x, y in pts[::max(1, len(pts) // 6)]:
        d.ellipse((x - 3, y - 3, x + 3, y + 3), fill=PAPER)
    if dot:
        d.ellipse((pts[-1][0] - dot, pts[-1][1] - dot,
                   pts[-1][0] + dot, pts[-1][1] + dot), fill=color)


def stamp(im, p, text, x, y, size=40, tag=None, tag_xy=None):
    """留白处只贴一行短句：同色短竖条 + 深调字。"""
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((x - 18, y + 7, x - 11, y + size + 3), radius=3, fill=p["c1"])
    d.text((x, y), text, font=font(size, True), fill=p["ink"])
    if tag:
        tx, ty = tag_xy or (x - 18, y - 44)
        d.text((tx, ty), tag, font=font(19), fill=TAG)


def footer(d, p, y, x0=92, x1=830):
    """彩色渐变细带 + 三枚小色点，取代旧版深墨横条。"""
    h = 12
    cols = (p["c1"], p["c2"], p["c3"], p["c4"])
    span = x1 - x0
    for i in range(span):
        t = i / span * (len(cols) - 1)
        k = min(int(t), len(cols) - 2)
        d.line([(x0 + i, y), (x0 + i, y + h)], fill=lerp(cols[k], cols[k + 1], t - k))
    d.ellipse((x0 - h // 2, y, x0 + h // 2, y + h), fill=cols[0])
    d.ellipse((x1 - h // 2, y, x1 + h // 2, y + h), fill=cols[-1])
    for i, c in enumerate(cols[:3]):
        cx = x1 + 46 + i * 34
        d.ellipse((cx - 8, y - 2, cx + 8, y + 14), fill=c)


def save(im, out_dir, name):
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, name + ".png")
    im.save(p, "PNG", optimize=True)
    print("写出", os.path.basename(p), im.size)
    return p
