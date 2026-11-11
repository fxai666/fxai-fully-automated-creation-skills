# -*- coding: utf-8 -*-
"""公众号图文封面生成器（彩色线路）—— 尺寸写死，别再量历史文件。

2026-11-11 配色线路更新：弃用深墨蓝底（#16233b 渐变），改为明亮淡彩渐变 +
高饱和彩色几何 + 深调字 + 底部彩色渐变细带。配图公共件见 scripts/figure_lib.py，
六套主色系、光晕 / 有机形状 / 贴字 / 页脚细带都在那里，本脚本只做文字型封面兜底。

固定规格（改这里等于改全站封面，动手前先想清楚）：
    画布     1152 x 512  （微信图文封面展示区 2.35:1，历史全部按这个尺寸出，别改）
    导出     同名 .png 母版 + .jpg 发布版（quality=95, subsample=0, optimize=True）
    字体     Noto Sans SC / Bold（SIL OFL，白名单 A），缺失回退微软雅黑
    配色     明亮淡彩底 + 品牌橙 / 天青 / 柠檬黄 / 薰衣草紫，深色底一律弃用

用法：
    python scripts/make_cover.py --title "配置低，|也能做 AI" --subtitle "智能体 + 云端 GPU + 技能包" --out <项目>/image
    python scripts/make_cover.py --title "标题一行就够" --motif cloud --out image
    python scripts/make_cover.py --title "..." --tag "" --accent "#40c4a5" --out image

标题里用 | 显式分行（最多 3 行）；最后一行自动上强调色。
"""
import argparse
import os
import sys

from PIL import Image, ImageDraw, ImageFont

from figure_lib import (COVER_W, COVER_H, PAPER, PALETTES, background, footer,
                        glow, star, lerp, save)

JPG_QUALITY = 95
JPG_SUBSAMPLE = 0       # 4:4:4，文字边缘不发虚
TITLE_MAX_W = 560       # 左侧文字区宽度
TITLE_SIZES = (78, 72, 66, 60, 54, 48)
TITLE_MAX_LINES = 3

_C = PALETTES["cover"]
BG_TOP, BG_BOTTOM = _C["bg_top"], _C["bg_bot"]
INK = (38, 52, 66)         # 深墨蓝灰字：浅底上可读，不是深色底
GREY = (118, 110, 104)
DIM = (150, 140, 132)
TAG = (128, 122, 114)


def _font_dir():
    """字体目录按环境解析：FXAI_FONT_DIR > %WINDIR%/Fonts（系统盘符不写死）。"""
    return os.environ.get("FXAI_FONT_DIR") or os.path.join(
        os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows", "Fonts")


def find_font(bold):
    """字体只从白名单取：Noto Sans SC（OFL）→ 微软雅黑 → 黑体。"""
    fd = _font_dir()
    cands = (
        [os.path.join(fd, "Noto Sans SC (TrueType).otf"),
         os.path.join(fd, "Noto Sans SC Bold (TrueType).otf")] if not bold else
        [os.path.join(fd, "Noto Sans SC Bold (TrueType).otf"),
         os.path.join(fd, "Noto Sans SC (TrueType).otf")]
    )
    cands += [os.path.join(fd, "msyhbd.ttc" if bold else "msyh.ttc"),
              os.path.join(fd, "msyh.ttc" if bold else "msyhbd.ttc"),
              os.path.join(fd, "simhei.ttf")]
    for p in cands:
        if p and os.path.exists(p):
            return p
    sys.exit("找不到可用中文字体（白名单：Noto Sans SC / 微软雅黑 / 黑体）")


def hex_rgb(s, fallback):
    s = (s or "").strip().lstrip("#")
    if len(s) != 6:
        return fallback
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return fallback


def wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def fit_title(draw, raw, font_path):
    """显式 | 分行，每段再按宽度折；返回 (字号, 行列表)。放不下就缩字号。"""
    parts = [p for p in raw.split("|") if p.strip()]
    for size in TITLE_SIZES:
        f = ImageFont.truetype(font_path, size)
        lines = []
        for p in parts:
            lines.extend(wrap(draw, p, f, TITLE_MAX_W))
        if len(lines) <= TITLE_MAX_LINES and all(
            draw.textlength(ln, font=f) <= TITLE_MAX_W for ln in lines
        ):
            return size, lines
    f = ImageFont.truetype(font_path, TITLE_SIZES[-1])
    lines = []
    for p in parts:
        lines.extend(wrap(draw, p, f, TITLE_MAX_W))
    return TITLE_SIZES[-1], lines[:TITLE_MAX_LINES]


def draw_motif_cloud(d, accent):
    """彩色云 + 彩色电脑：重元素全由高饱和彩色承担。"""
    cx, cy = 890, 300
    d.ellipse([860, -180, 1320, 280], fill=lerp(PAPER, _C["c2"], .42))
    d.ellipse([980, -100, 1240, 160], fill=lerp(PAPER, _C["c4"], .34))
    d.rounded_rectangle([cx - 90, cy - 62, cx + 90, cy + 34], 10, fill=_C["c2"])
    d.rectangle([cx - 74, cy - 48, cx + 74, cy + 18], fill=lerp(PAPER, _C["c2"], .72))
    d.rounded_rectangle([cx - 104, cy + 34, cx + 104, cy + 50], 5, fill=_C["c4"])
    d.text((cx - 62, cy - 32), "指挥", font=ImageFont.truetype(find_font(True), 24), fill=_C["c1"])
    d.line([(cx, cy - 62), (cx, cy - 108)], fill=_C["c3"], width=5)
    for (ox, oy, r) in [(-46, -18, 34), (0, -30, 42), (46, -16, 32)]:
        d.ellipse([cx + ox - r, cy - 150 + oy - r, cx + ox + r, cy - 150 + oy + r],
                  fill=_C["c2"])
    d.rounded_rectangle([cx - 78, cy - 162, cx + 78, cy - 118], 18, fill=_C["c1"])
    d.text((cx - 40, cy - 148), "GPU", font=ImageFont.truetype(find_font(True), 26), fill=PAPER)
    star(d, 1080, 96, 16, _C["c3"])
    star(d, 742, 196, 12, _C["c4"])


def draw_motif_blocks(d, accent):
    d.ellipse([880, -160, 1300, 260], fill=lerp(PAPER, _C["c2"], .40))
    x, y, w, h, gap = 800, 170, 240, 46, 18
    colors = (_C["c1"], _C["c2"], _C["c3"], _C["c4"])
    for i, c in enumerate(colors[:3]):
        d.rounded_rectangle([x, y + i * (h + gap), x + w, y + i * (h + gap) + h], 10, fill=c)
    d.text((x, y + 3 * (h + gap) + 8), "一层一层叠起来",
           font=ImageFont.truetype(find_font(False), 20), fill=GREY)


def draw_motif_none(d, accent):
    d.ellipse([880, -170, 1320, 270], fill=lerp(PAPER, _C["c3"], .38))
    d.ellipse([1010, 40, 1230, 260], outline=_C["c1"], width=8)
    d.line([(1060, 150), (1180, 150)], fill=_C["c2"], width=8)
    star(d, 1104, 92, 18, _C["c4"])


def main():
    ap = argparse.ArgumentParser(description="公众号图文封面生成器（彩色线路，1152x512 写死）")
    ap.add_argument("--title", required=True, help="主标题，用 | 分行，最后一行自动上强调色")
    ap.add_argument("--subtitle", default="", help="副标题，可含换行 \\n")
    ap.add_argument("--tag", default="技术分享 · 不卖课", help="左上角小字，给空串则不画")
    ap.add_argument("--out", required=True, help="输出目录（一般是 <项目>/image）")
    ap.add_argument("--name", default="封面", help="文件名（默认 封面，出 .png + .jpg）")
    ap.add_argument("--motif", default="none", choices=["cloud", "blocks", "none"], help="右侧图形")
    ap.add_argument("--accent", default="#ff6b35", help="强调色，默认品牌橙")
    args = ap.parse_args()

    accent = hex_rgb(args.accent, (255, 107, 53))
    bold = find_font(True)
    reg = find_font(False)

    im = background(COVER_W, COVER_H, _C, 71)
    glow(im, 690, 236, 150, _C["c3"], 52, 52)
    glow(im, 250, 400, 160, _C["c2"], 30, 60)
    d = ImageDraw.Draw(im)

    if args.motif == "cloud":
        draw_motif_cloud(d, accent)
    elif args.motif == "blocks":
        draw_motif_blocks(d, accent)
    else:
        draw_motif_none(d, accent)

    if args.tag:
        d.text((72, 62), args.tag, font=ImageFont.truetype(reg, 22), fill=TAG)

    size, lines = fit_title(d, args.title, bold)
    f = ImageFont.truetype(bold, size)
    y = 112
    for i, ln in enumerate(lines):
        color = accent if i == len(lines) - 1 and len(lines) > 1 else INK
        d.text((72, y), ln, font=f, fill=color)
        y += int(size * 1.12)

    y += 12
    d.line([(74, y), (150, y)], fill=accent, width=4)
    y += 24
    if args.subtitle:
        for ln in args.subtitle.split("\n"):
            for sub in wrap(d, ln, ImageFont.truetype(reg, 25), TITLE_MAX_W):
                d.text((72, y), sub, font=ImageFont.truetype(reg, 25), fill=GREY)
                y += 40

    footer(d, _C, 462, x0=56, x1=700)

    os.makedirs(args.out, exist_ok=True)
    png = os.path.join(args.out, args.name + ".png")
    jpg = os.path.join(args.out, args.name + ".jpg")
    im.save(png, "PNG", optimize=True)
    im.save(jpg, "JPEG", quality=JPG_QUALITY, subsample=JPG_SUBSAMPLE, optimize=True)
    print(f"封面（彩色线路）{COVER_W}x{COVER_H}  标题字号 {size}  {len(lines)} 行")
    print(f"  {png}  {os.path.getsize(png) / 1024:.0f}KB")
    print(f"  {jpg}  {os.path.getsize(jpg) / 1024:.0f}KB")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
