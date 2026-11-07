# -*- coding: utf-8 -*-
# =====================================================================
# fxai-poster · intro_card.py  片头信息贴片生成器（封面铺首帧叠字）
# ---------------------------------------------------------------------
# 用封面/底图生成「片头信息贴片」：背景裁切避开封面自带文字 +
# 叠 出品小字 / 剧名 / 集名 / 卖点 / 署名，默认不做上下渐变。
#
# 🟠 蒙版是可选项（--mask）：
#   - 背景太亮 / 太锐利、叠字不清晰时，传 --mask 0.1~0.2 加一层很淡的
#     均匀蒙版轻微压暗背景，让文字更清晰；背景干净可传 0（默认）不加深色层。
#   - 这不是上下渐变暗角，是整块均匀淡蒙版，可按需开关。
#
# 字体只走 fxai-standard/references/06 白名单：标题得意黑（SIL OFL），
# 正文微软雅黑（屏幕渲染场景）→ Noto Sans SC → PIL 默认。
# =====================================================================
import os, sys, argparse
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# 字体目录按环境解析：FXAI_FONT_DIR > %WINDIR%/Fonts（系统盘符不写死）
_FONTS = os.environ.get("FXAI_FONT_DIR") or os.path.join(
    os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows", "Fonts")
FONT_TITLE = os.path.join(_FONTS, "SmileySans-Oblique.otf")   # 得意黑（SIL OFL）
FONT_BODY  = os.path.join(_FONTS, "msyh.ttc")                 # 微软雅黑（屏幕渲染场景）

def _font(path, size):
    return ImageFont.truetype(path, size)

def _text_w(d, s, f):
    b = d.textbbox((0, 0), s, font=f)
    return b[2] - b[0]

def _stroke(d, xy, s, f, fill, stroke, sw):
    x, y = xy
    d.text((x+3, y+4), s, font=f, fill=(30, 22, 8), stroke_width=sw+1, stroke_fill=(20, 14, 6))
    d.text((x, y), s, font=f, fill=fill, stroke_width=sw, stroke_fill=stroke)

def make_intro_card(cover, title, ep, hook, made, top_text,
                    size=(1280, 768), mask=0.0, crop_ratio=0.55,
                    title_font=FONT_TITLE, body_font=FONT_BODY, out=None):
    """生成片头信息贴片 PNG。
    cover     : 封面/底图路径
    title/ep  : 剧名 / 集名（ep 放 title 正下方）
    hook/made : 卖点 / 管线署名（底部）
    top_text  : 顶部出品小字
    size      : 画布 (w, h)
    mask      : 蒙版强度 0=不加深色蒙版；>0（如0.16）加淡均匀蒙版压暗背景
    crop_ratio: 背景从封面取的起始比例（默认0.55，避开封面顶部标题/集名文字）
    out       : 输出路径
    """
    W, H = size
    bg = Image.open(cover).convert("RGB")
    cw, ch = bg.size
    bg = bg.resize((W, int(ch * W / cw)), Image.LANCZOS)
    bw, bh = bg.size
    top = int(bh * crop_ratio)
    bg = bg.crop((0, top, W, top + H))
    bg = bg.filter(ImageFilter.GaussianBlur(0.6))

    # 🟠 蒙版可选项：整块均匀淡蒙版（非上下渐变）
    if mask > 0:
        dark = Image.new("RGB", (W, H), (12, 12, 16))
        bg = Image.blend(bg, dark, mask)

    d = ImageDraw.Draw(bg)

    if top_text:
        f = _font(body_font, int(H * 0.034))
        x = (W - _text_w(d, top_text, f)) // 2
        _stroke(d, (x, int(H * 0.06)), top_text, f, (232, 201, 138), (30, 20, 8), 2)

    f = _font(title_font, int(H * 0.096))
    x = (W - _text_w(d, title, f)) // 2
    _stroke(d, (x, int(H * 0.20)), title, f, (245, 200, 100), (92, 62, 16), 8)

    if ep:
        f = _font(title_font, int(H * 0.055))
        x = (W - _text_w(d, ep, f)) // 2
        _stroke(d, (x, int(H * 0.35)), ep, f, (255, 216, 115), (70, 46, 10), 4)

    if hook:
        f = _font(body_font, int(H * 0.044))
        x = (W - _text_w(d, hook, f)) // 2
        _stroke(d, (x, int(H * 0.68)), hook, f, (255, 255, 240), (40, 26, 8), 4)

    if made:
        f = _font(body_font, int(H * 0.029))
        x = (W - _text_w(d, made, f)) // 2
        _stroke(d, (x, int(H * 0.80)), made, f, (225, 222, 214), (25, 18, 6), 2)

    if out:
        bg.save(out, "PNG")
    return bg

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="片头信息贴片生成器（蒙版可选项）")
    ap.add_argument("--cover", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--ep", default="")
    ap.add_argument("--hook", default="")
    ap.add_argument("--made", default="")
    ap.add_argument("--top", default="")
    ap.add_argument("--size", default="1280x768")
    ap.add_argument("--mask", type=float, default=0.0,
                    help="蒙版强度：0=不加深色蒙版（默认）；0.1~0.2=加淡均匀蒙版压暗背景，文字更清晰")
    ap.add_argument("--crop-ratio", type=float, default=0.55,
                    help="背景从封面取的起始比例（默认0.55，避开封面顶部标题/集名文字）")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    w, h = args.size.lower().split("x")
    make_intro_card(args.cover, args.title, args.ep, args.hook, args.made, args.top,
                    size=(int(w), int(h)), mask=args.mask, crop_ratio=args.crop_ratio,
                    out=args.out)
    print("片头信息贴片 OK -> %s (mask=%s)" % (args.out, args.mask))
