# -*- coding: utf-8 -*-
"""公众号图文封面生成器 —— 尺寸写死，别再量历史文件。

固定规格（改这里等于改全站封面，动手前先想清楚）：
    画布     1152 x 512  （微信图文封面展示区 2.35:1，历史全部按这个尺寸出，别改）
    导出     同名 .png 母版 + .jpg 发布版（quality=95, subsample=0, optimize=True）
    字体     微软雅黑粗体/常规（白名单内），缺失回退 Noto Sans SC
    配色     深墨蓝底 + 橙色重点（品牌色 #ff6b35 / #16233b）

用法：
    python scripts/make_cover.py --title "配置低，|也能做 AI" --subtitle "智能体 + 云端 GPU + 技能包" --out <项目>/image
    python scripts/make_cover.py --title "标题一行就够" --motif cloud --out image
    python scripts/make_cover.py --title "..." --tag "" --accent "#40c4a5" --out image

标题里用 | 显式分行（最多 3 行）；最后一行自动上强调色。
"""
import argparse
import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("缺 Pillow：pip install pillow")

COVER_W = 1152          # 写死：微信封面展示区 2.35:1，历史统一 1152x512
COVER_H = 512
JPG_QUALITY = 95
JPG_SUBSAMPLE = 0       # 4:4:4，文字边缘不发虚
TITLE_MAX_W = 560       # 左侧文字区宽度
TITLE_SIZES = (78, 72, 66, 60, 54, 48)
TITLE_MAX_LINES = 3

BG_TOP = (13, 22, 30)
BG_BOTTOM = (21, 32, 40)
WHITE = (240, 245, 249)
GREY = (146, 163, 178)
DIM = (120, 140, 155)


def _font_dir():
    """字体目录按环境解析：FXAI_FONT_DIR > %WINDIR%/Fonts（系统盘符不写死）。"""
    return os.environ.get("FXAI_FONT_DIR") or os.path.join(
        os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows", "Fonts")


def find_font(bold):
    """字体只从白名单取：微软雅黑（系统自带、可商用）→ Noto Sans SC → 黑体。"""
    fd = _font_dir()
    cands = (
        [os.path.join(fd, "msyhbd.ttc"), os.path.join(fd, "msyh.ttc")]
        if bold
        else [os.path.join(fd, "msyh.ttc"), os.path.join(fd, "msyhbd.ttc")]
    )
    cands += [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold
        else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        os.path.join(fd, "simhei.ttf"),
    ]
    for p in cands:
        if p and os.path.exists(p):
            return p
    sys.exit("找不到可用中文字体（白名单：微软雅黑 / Noto Sans SC / 黑体）")


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
    cx, cy = 890, 300
    d.ellipse([860, -180, 1320, 280], fill=(26, 46, 58))
    d.ellipse([980, -100, 1240, 160], fill=(30, 55, 66))
    d.rounded_rectangle([cx - 90, cy - 62, cx + 90, cy + 34], 10, fill=(38, 56, 70))
    d.rectangle([cx - 74, cy - 48, cx + 74, cy + 18], fill=(22, 36, 46))
    d.rounded_rectangle([cx - 104, cy + 34, cx + 104, cy + 50], 5, fill=(52, 72, 88))
    d.text((cx - 62, cy - 32), "指挥", font=ImageFont.truetype(find_font(True), 24), fill=(64, 196, 165))
    d.line([(cx, cy - 62), (cx, cy - 108)], fill=(90, 112, 130), width=3)
    for (ox, oy, r) in [(-46, -18, 34), (0, -30, 42), (46, -16, 32)]:
        d.ellipse([cx + ox - r, cy - 150 + oy - r, cx + ox + r, cy - 150 + oy + r], fill=(46, 72, 90))
    d.rounded_rectangle([cx - 78, cy - 162, cx + 78, cy - 118], 18, fill=(46, 72, 90))
    d.text((cx - 40, cy - 148), "GPU", font=ImageFont.truetype(find_font(True), 26), fill=accent)


def draw_motif_blocks(d, accent):
    d.ellipse([880, -160, 1300, 260], fill=(26, 46, 58))
    x, y, w, h, gap = 800, 170, 240, 46, 18
    colors = [(64, 196, 165), accent, (150, 170, 230)]
    for i, c in enumerate(colors):
        d.rounded_rectangle([x, y + i * (h + gap), x + w, y + i * (h + gap) + h], 10, fill=c)
    d.text((x, y + 3 * (h + gap) + 8), "一层一层叠起来", font=ImageFont.truetype(find_font(False), 20), fill=DIM)


def draw_motif_none(d, accent):
    d.ellipse([880, -170, 1320, 270], fill=(24, 42, 54))
    d.ellipse([1010, 40, 1230, 260], outline=accent, width=3)
    d.line([(1060, 150), (1180, 150)], fill=accent, width=3)


def main():
    ap = argparse.ArgumentParser(description="公众号图文封面生成器（1152x512 写死）")
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

    im = Image.new("RGB", (COVER_W, COVER_H), BG_TOP)
    d = ImageDraw.Draw(im)
    for y in range(COVER_H):  # 竖向渐变
        t = y / COVER_H
        d.line(
            [(0, y), (COVER_W, y)],
            fill=tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)),
        )

    if args.motif == "cloud":
        draw_motif_cloud(d, accent)
    elif args.motif == "blocks":
        draw_motif_blocks(d, accent)
    else:
        draw_motif_none(d, accent)

    if args.tag:
        d.text((72, 62), args.tag, font=ImageFont.truetype(reg, 22), fill=DIM)

    size, lines = fit_title(d, args.title, bold)
    f = ImageFont.truetype(bold, size)
    y = 112
    for i, ln in enumerate(lines):
        color = accent if i == len(lines) - 1 and len(lines) > 1 else WHITE
        d.text((72, y), ln, font=f, fill=color)
        y += int(size * 1.12)

    y += 12
    d.line([(74, y), (150, y)], fill=accent, width=4)
    y += 24
    if args.subtitle:
        for ln in args.subtitle.split("\n"):
            for sub in wrap(d, ln, ImageFont.truetype(reg, 25), TITLE_MAX_W):
                d.text((72, y), sub, font=ImageFont.truetype(reg, 25), fill=(185, 200, 212))
                y += 40

    os.makedirs(args.out, exist_ok=True)
    png = os.path.join(args.out, args.name + ".png")
    jpg = os.path.join(args.out, args.name + ".jpg")
    im.save(png)
    im.save(jpg, "JPEG", quality=JPG_QUALITY, subsample=JPG_SUBSAMPLE, optimize=True)
    print(f"封面 {COVER_W}x{COVER_H}  标题字号 {size}  {len(lines)} 行")
    print(f"  {png}  {os.path.getsize(png) / 1024:.0f}KB")
    print(f"  {jpg}  {os.path.getsize(jpg) / 1024:.0f}KB")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
