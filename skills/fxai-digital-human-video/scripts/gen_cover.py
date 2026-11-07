#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""写实封面生成：背景生图 + PIL 叠字（05-封面.md 规范）。

── 规范（已落地）──
· 封面 = 写实背景（image/cover_bg.png）+ 三段文字：顶部胶囊 / 中部金标题+副标 / 底部品牌条。
· 字体白名单：得意黑 SmileySans-Oblique（标题/品牌），Noto Sans SC（副标/正文）。
· 🔴 每一级文字都自带三重可读性保障：局部压暗盘 + 柔光黑晕 + 实心描边。
  平台缩略图只有约 300px 宽，细体、无描边、浅色字压浅色背景会直接糊成一片；
  可读性靠文字自己带，不指望背景明暗配合。
· 封面 = 成片片头的静态帧（音轨在 overlay_titles 里垫，规则见 fxai-poster 05 册）。
· 品牌块只写品牌名，禁网址/微信号/二维码（导流限流红线）。

参数（cover.json 或命令行，缺省走默认）：
  pill        顶部胶囊词        "AI 全自动创作"
  title       中部大标题        "全周期自动化创作运营"（可用 "|" 指定断行）
  sub         副标             "从定位到复盘，一整套自己跑"
  foot        引语             "一个人 · 一台电脑 · 真跑通了"
  brand       品牌名           "凤希AI!"
  brand_sub   品牌副标          "公众号自动化运营 · 实操分享"
"""
import argparse, json, os, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 768, 1280
# 字体目录按环境解析：FXAI_FONT_DIR > %WINDIR%/Fonts（系统盘符不写死）
_FONTS = os.environ.get("FXAI_FONT_DIR") or os.path.join(
    os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows", "Fonts")
F_TITLE  = os.path.join(_FONTS, "SmileySans-Oblique.otf")
F_NOTO   = os.path.join(_FONTS, "Noto Sans SC (TrueType).otf")
F_NOTO_B = os.path.join(_FONTS, "Noto Sans SC Bold (TrueType).otf")
F_NOTO_M = os.path.join(_FONTS, "Noto Sans SC Medium (TrueType).otf")
GOLD  = (238, 196, 112)
DARK  = (26, 18, 6)
AMBER = (240, 184, 96)

DEFAULTS = dict(pill="AI 全自动创作", title="全周期自动化创作运营",
                sub="从定位到复盘，一整套自己跑", foot="一个人 · 一台电脑 · 真跑通了",
                brand="凤希AI", brand_sub="公众号自动化运营 · 实操分享")

# ── 版式参数：要调封面只改这一段 ──────────────────────────────
PAD_X        = 44     # 文字离左右边的最小距离
PILL_SIZE    = 32
PILL_Y       = 88
BLOCK_CY     = 486    # 中部文字块的垂直中心
TITLE_MAX    = 92     # 大标题最大字号（得意黑）
TITLE_MIN    = 48
SINGLE_MIN   = 76     # 单行能排到这么大就不折行；排不到就对折两行
SUB_SIZE     = 40
FOOT_SIZE    = 31
GAP_TS       = 30     # 标题 → 副标
GAP_SF       = 16     # 副标 → 引语
BRAND_SIZE   = 56
BRAND_Y      = 1112
BRAND_SUB_SIZE = 31
BRAND_SUB_Y  = 1196
GLOBAL_DIM   = 0.88   # 整图轻微压暗，抬对比但不成黑块
PLATE_A_MID  = 146    # 中部压暗盘峰值 alpha（0~255）
PLATE_A_BOT  = 172    # 底部压暗盘峰值 alpha
PLATE_BLUR   = 46     # 羽化半径：小了就成「灰横带」，要糊到看不出边界
PLATE_PADX   = 26     # 压暗盘比文字左右各宽出多少
CONTRAST_MIN = 2.6    # 字/底亮度比下限，低于它缩略图上必糊（顶部胶囊自带实心底，不计入）

_MEAS = ImageDraw.Draw(Image.new("L", (1, 1)))


def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.truetype(F_NOTO_B, size)


def _wrap(text, font, maxw):
    """按实际字宽折行（多行用）；"|" 是显式断点。标题走 fit_title，这里给长文本兜底。"""
    lines, cur = [], ""
    for ch in text:
        if ch == "|":
            lines.append(cur)
            cur = ""
            continue
        cand = cur + ch
        if cur and _MEAS.textbbox((0, 0), cand, font=font)[2] > maxw:
            lines.append(cur)
            cur = ch
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return [ln for ln in lines if ln] or [""]


def _wid(text, font):
    return _MEAS.textbbox((0, 0), text, font=font)[2]


def _best_split(text, font):
    """对折切点：让较长那行最短，挡掉「…运 / 营」这种孤字掉行。"""
    best, bk = None, len(text) // 2
    for k in range(1, len(text)):
        m = max(_wid(text[:k], font), _wid(text[k:], font))
        if best is None or m < best - 0.5:
            best, bk = m, k
    return bk


def fit_title(text, maxw=None, hi=None, lo=None):
    """定标题字号与断行。

    规则：单行能排到 ≥SINGLE_MIN 就用单行（字号大）；排不到就按平衡切点对折两行，
    两行再放不下才继续缩字号。折行优先于缩字——封面上大字比多一行更值钱。
    """
    maxw = maxw or (W - PAD_X * 2)
    hi = hi or TITLE_MAX
    lo = lo or TITLE_MIN
    if "|" in text:
        lines = [s for s in text.split("|") if s]
        size = hi
        while size > lo and max(_wid(l, _font(F_TITLE, size)) for l in lines) > maxw:
            size -= 2
        return lines, _font(F_TITLE, size)

    size = hi
    while size > lo and _wid(text, _font(F_TITLE, size)) > maxw:
        size -= 2
    if size >= SINGLE_MIN:
        return [text], _font(F_TITLE, size)

    k = _best_split(text, _font(F_TITLE, hi))
    lines = [text[:k], text[k:]]
    size = hi
    while size > lo:
        f = _font(F_TITLE, size)
        if max(_wid(l, f) for l in lines) <= maxw:
            return lines, f
        size -= 2
    return lines, _font(F_TITLE, lo)


def draw_line(img, cx, y, text, font, fill, stroke=0, stroke_fill=DARK,
              halo=0.9, halo_blur=None):
    """居中画一行：柔光黑晕 → 实心描边 → 字面。返回 (left, top, w, h)。

    黑晕是给缩略图用的：描边保证字形边缘硬边，黑晕保证字周围一圈压下去，
    两者叠加后无论背景是亮窗还是白墙，字都立得住。
    """
    if not text:
        return (cx, y, 0, 0)
    bb = _MEAS.textbbox((0, 0), text, font=font, stroke_width=stroke)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    x = cx - tw / 2 - bb[0]
    blur = halo_blur if halo_blur is not None else max(6.0, font.size * 0.28)

    if halo > 0:
        pad = int(blur * 3) + stroke + 8
        lw = int(tw) + stroke * 2 + pad * 2
        lh = int(th) + stroke * 2 + pad * 2
        lay = Image.new("L", (lw, lh), 0)
        ImageDraw.Draw(lay).text((pad - bb[0], pad - bb[1]), text, font=font,
                                 fill=255, stroke_width=stroke, stroke_fill=255)
        lay = lay.filter(ImageFilter.GaussianBlur(blur))
        blk = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
        blk.putalpha(lay.point(lambda v: int(v * halo)))
        img.alpha_composite(blk, (int(round(x + bb[0] - pad)), int(round(y + bb[1] - pad))))

    ImageDraw.Draw(img).text((x, y), text, font=font, fill=fill,
                             stroke_width=stroke, stroke_fill=stroke_fill)
    return (x + bb[0], y + bb[1], tw, th)


def plate(img, box, alpha, blur=PLATE_BLUR, radius=30):
    """局部压暗盘：圆角矩形 → 高斯羽化 → 只压这一块。"""
    lay = Image.new("L", img.size, 0)
    ImageDraw.Draw(lay).rounded_rectangle(box, radius=radius, fill=int(alpha))
    lay = lay.filter(ImageFilter.GaussianBlur(blur))
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ov.putalpha(lay)
    img.alpha_composite(ov)


# 封面背景的硬约束：封面是「主题氛围图」，不是场景图（见 05 册第二节）。
BG_GUARD = "，竖构图，写实摄影质感，浅景深，画面里没有人物，屏幕上与纸面上没有可读文字，没有 logo，没有二维码"


def gen_bg(project, prompt, tag=None, force=False, w=1080, h=1920):
    """按主题提示词生成封面背景图 -> image/cover_bg.png。

    封面背景与场景图是两种东西：场景图要给人物留出空地面，封面背景要给文字留出
    可压的底、同时把主题实物摆足（封面上没有人物，实物就是内容本身）。
    """
    sk = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    p = os.path.join(sk, "fxai-scene-gen")
    if p not in sys.path:
        sys.path.insert(0, p)
    from scene_gen import gen_scene_image
    tag = tag or ("%s_coverbg" % (os.path.basename(os.path.abspath(project)) or "cover"))
    src, _rel = gen_scene_image(prompt.rstrip("。，") + BG_GUARD, role=None,
                                w=w, h=h, tag=tag, subdir="dh_cover",
                                num=0, force=force)
    dst = os.path.join(project, "image", "cover_bg.png")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.abspath(src) != os.path.abspath(dst):
        import shutil
        shutil.copyfile(src, dst)
    print("[cover] 背景图 ->", dst)
    return dst


def _lum(rgb):
    """感知亮度（0~255），用来算字与底的对比比。"""
    r, g, b = rgb[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b


def check_contrast(base, boxes, name="封面"):
    """机械自检：每行字底下的背景亮度 vs 字色亮度，比值过低就报。

    `base` 是**叠字之前**的图（已含压暗盘），boxes 是每行的 (left, top, w, h, fill)。
    这一步替代不了人眼看缩略图，但能挡住「浅色字压浅色背景」这类必然翻车的组合。
    """
    worst, worst_name, rows = 99.0, "", []
    px = base.load()
    for (x, y, w, h, fill, label) in boxes:
        if w <= 0 or h <= 0:
            continue
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(base.width, int(x + w)), min(base.height, int(y + h))
        if x1 <= x0 or y1 <= y0:
            continue
        tot = n = 0
        for yy in range(y0, y1, 3):
            for xx in range(x0, x1, 3):
                tot += _lum(px[xx, yy])
                n += 1
        if not n:
            continue
        bg_l = tot / n
        fg_l = _lum(fill)
        ratio = (max(bg_l, fg_l) + 8.0) / (min(bg_l, fg_l) + 8.0)
        rows.append((ratio, label))
        if ratio < worst:
            worst, worst_name = ratio, label
    if not rows:
        return
    flag = "✅" if worst >= CONTRAST_MIN else "⚠️"
    print("[cover] 可读性自检 %s %d 行文字，最低对比 %.2f（%s，阈值 %.1f）"
          % (flag, len(rows), worst, worst_name, CONTRAST_MIN))
    if worst < CONTRAST_MIN:
        bad = [l for r, l in rows if r < CONTRAST_MIN]
        print("[cover] ⚠️ 这几级字对比不够：%s —— 调那一级的字号/黑晕，或换背景图"
              % "、".join(bad))


def fallback_bg(path, W=768, H=1280):
    """无背景图时的兜底底图：品牌深色渐变 + 暖光晕。

    🔴 仅兜底，非常规。正常流程应当用写实场景图/生图当背景（05-封面.md）。
    但没有背景图不该让整条链路卡死——默认链路要能一路跑完。
    """
    import numpy as np
    y = np.linspace(0, 1, H).reshape(-1, 1)
    r = 16 + 14 * y
    g = 24 + 16 * y
    b = 42 + 30 * y
    arr = np.dstack([np.repeat(r, W, 1), np.repeat(g, W, 1), np.repeat(b, W, 1)])
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt(((xx - W * 0.28) / (W * 0.95)) ** 2 + ((yy - H * 0.22) / (H * 0.55)) ** 2)
    glow = np.clip(1 - d, 0, 1) ** 2 * 46
    arr = np.clip(arr + np.dstack([glow * 1.0, glow * 0.62, glow * 0.30]), 0, 255)
    Image.fromarray(arr.astype("uint8"), "RGB").save(path)
    print("[cover] ⚠️ 无背景图，已生成兜底渐变 %s（建议换成写实场景图）" % path)


def _plain(s):
    """去掉标点与空白，只留字，用来数字数与重合度。"""
    return "".join(ch for ch in (s or "") if ch.isalnum())


def check_copy(cfg):
    """封面文案判据自检（05 册第三节六条里可机械判的四条）。"""
    t, s, f, p = cfg.get("title", ""), cfg.get("sub", ""), cfg.get("foot", ""), cfg.get("pill", "")
    rows = []

    n = len(_plain(t))
    rows.append((8 <= n <= 14, "主文案 %d 字（8~14）" % n))
    rows.append((any(ch.isdigit() for ch in t) or
                 any(w in t for w in "一二三四五六七八九十百千万"),
                 "主文案含具体数字"))

    ps, pt = set(_plain(s)), set(_plain(t))
    ov = (len(ps & pt) / max(1, len(ps))) if ps else 0.0
    rows.append((ov < 0.5, "副标与主文案重合 %d%%（<50%%）" % int(ov * 100)))
    rows.append((10 <= len(_plain(s)) <= 16, "副标 %d 字（10~16）" % len(_plain(s))))

    nf = len(_plain(f))
    rows.append((10 <= nf <= 18, "证据行 %d 字（10~18）" % nf))
    rows.append((2 <= len(_plain(p)) <= 8, "胶囊 %d 字（2~8）" % len(_plain(p))))

    bad = [m for ok, m in rows if not ok]
    flag = "✅" if not bad else "⚠️"
    print("[cover] 文案自检 %s %d 项，%d 项待改" % (flag, len(rows), len(bad)))
    for m in bad:
        print("[cover]   ⚠️ %s" % m)
    return not bad


def gen_cover(project, cfg, bg_path=None, out_path=None):
    explicit = bg_path is not None
    bg_path = bg_path or os.path.join(project, "image", "cover_bg.png")
    if not os.path.isfile(bg_path):
        if explicit:
            sys.exit("指定的封面背景不存在：%s" % bg_path)
        os.makedirs(os.path.dirname(bg_path) or ".", exist_ok=True)
        fallback_bg(bg_path)
    bg = Image.open(bg_path).convert("RGB")
    if bg.size != (W, H):
        bg = bg.resize((W, H), Image.LANCZOS)
    if GLOBAL_DIM < 1.0:
        bg = Image.eval(bg, lambda v: int(v * GLOBAL_DIM))
    bg = bg.convert("RGBA")

    check_copy(cfg)

    # ── 先量，再摆 ──
    lines, ft = fit_title(cfg["title"])
    lh = int(ft.size * 1.16)
    fsub = _font(F_NOTO_M, SUB_SIZE)
    ffoot = _font(F_NOTO, FOOT_SIZE)
    hs = _MEAS.textbbox((0, 0), "国", font=fsub)[3]
    hf = _MEAS.textbbox((0, 0), "国", font=ffoot)[3]
    block_h = lh * len(lines) + GAP_TS + hs + GAP_SF + hf
    top = int(BLOCK_CY - block_h / 2)

    # ── 两块局部压暗盘 ──
    plate(bg, (PAD_X - PLATE_PADX, top - 34, W - PAD_X + PLATE_PADX, top + block_h + 28),
          PLATE_A_MID)
    plate(bg, (PAD_X - PLATE_PADX, BRAND_Y - 44, W - PAD_X + PLATE_PADX,
               BRAND_SUB_Y + 66), PLATE_A_BOT, radius=34)

    d = ImageDraw.Draw(bg)
    base = bg.copy()          # 叠字前的底图，供可读性自检用
    boxes = []

    def put(y, text, font, fill, stroke, halo, label, halo_blur=None):
        bb = draw_line(bg, W / 2, y, text, font, fill, stroke=stroke, halo=halo,
                       halo_blur=halo_blur)
        boxes.append((bb[0], bb[1], bb[2], bb[3], fill, label))
        return bb

    # ── 顶部胶囊（实心色块，本身就是最高对比） ──
    cap = cfg["pill"]
    fc = _font(F_NOTO_B, PILL_SIZE)
    bb = d.textbbox((0, 0), cap, font=fc)
    cw, ch = bb[2] - bb[0], bb[3] - bb[1]
    cx0 = (W - cw) / 2 - 36
    box = [cx0, PILL_Y, cx0 + cw + 72, PILL_Y + ch + 36]
    d.rounded_rectangle([box[0] + 3, box[1] + 5, box[2] + 3, box[3] + 5],
                        radius=(ch + 36) / 2, fill=(0, 0, 0, 90))
    d.rounded_rectangle(box, radius=(ch + 36) / 2, fill=AMBER + (255,))
    d.text(((W - cw) / 2, PILL_Y + 18 - bb[1]), cap, font=fc, fill=(28, 20, 6, 255))

    # ── 中部：大标题 / 副标 / 引语 ──
    y = top
    for ln in lines:
        put(y, ln, ft, GOLD + (255,), 6, 0.95, "大标题",
            halo_blur=max(14, ft.size * 0.22))
        y += lh
    y += GAP_TS - int(lh * 0.10)
    put(y, cfg["sub"], fsub, (255, 255, 255, 255), 4, 0.92, "副标")
    y += hs + GAP_SF
    put(y, cfg["foot"], ffoot, (250, 246, 238, 255), 3, 0.88, "引语")

    # ── 底部品牌条 ──
    fb = _font(F_TITLE, BRAND_SIZE)
    put(BRAND_Y, cfg["brand"], fb, GOLD + (255,), 5, 0.95, "品牌",
        halo_blur=max(12, BRAND_SIZE * 0.22))
    fbs = _font(F_NOTO_M, BRAND_SUB_SIZE)
    put(BRAND_SUB_Y, cfg["brand_sub"], fbs, (252, 249, 243, 255), 3, 0.88, "品牌副标")

    check_contrast(base, boxes)

    out = out_path or os.path.join(project, "image", "cover.png")
    bg.convert("RGB").save(out)
    print("封面 ->", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    ap.add_argument("--bg", default=None)
    ap.add_argument("--cfg", default=None, help="cover.json 路径；缺省自动找项目根/image 下")
    ap.add_argument("--out", default=None, help="输出路径，默认 <项目>/image/cover.png")
    ap.add_argument("--bg-prompt", default=None,
                    help="按这段主题描述生成封面背景图（写实、无人物、无文字）")
    ap.add_argument("--force-bg", action="store_true", help="重出背景图（已有 cover_bg.png 也覆盖）")
    a = ap.parse_args()
    if a.bg_prompt:
        bgp = os.path.join(a.project, "image", "cover_bg.png")
        if a.force_bg or not os.path.isfile(bgp):
            gen_bg(a.project, a.bg_prompt, force=a.force_bg)
        else:
            print("[cover] 背景图已存在，跳过生成（要重出加 --force-bg）：%s" % bgp)
    cfg = dict(DEFAULTS)
    cands = [a.cfg] if a.cfg else []
    cands += [os.path.join(a.project, "cover.json"),
              os.path.join(a.project, "image", "cover.json")]
    for p in cands:
        if p and os.path.isfile(p):
            cfg.update(json.load(open(p, encoding="utf-8")))
            print("[cover] 读到 %s" % p)
            break
    gen_cover(a.project, cfg, a.bg, a.out)


if __name__ == "__main__":
    main()
