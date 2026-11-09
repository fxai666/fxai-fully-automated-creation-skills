# -*- coding: utf-8 -*-
"""凤希AI 品牌尾卡生成器（跨技能通用）。

用法：
    python -B make_brand_card.py --size 960x512                  # 干净版（默认）
    python -B make_brand_card.py --size 960x512 --variant contact
    python -B make_brand_card.py --size 960x512 --variant all    # 三个变体一次出
    python -B make_brand_card.py --size 960x512 --out 任意路径.png   # 指定落点

变体：
    clean     ：品牌名 + 标语，无任何联系方式 → 公域平台投放（默认）
    contact   ：+ 官网 / 微信 / QQ群 → 自有渠道
    xianguyun ：+ 独立一行「仙宫云 · 镜像广场搜凤希AI」→ 仙宫云渠道

品牌资源全部取全局配置 ~/.workbuddy/fxai_global.json 的 brand 段，
Logo 用官方 avatar_512，不自行生成、不改写。

默认落点：本技能包 assets/brand/尾卡_<变体中文名>_<W>x<H>.png
"""
import argparse
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), 'assets', 'brand')

ORANGE = (255, 122, 85)
GREY = (130, 138, 152)
LIGHT = (232, 236, 244)

VARIANT_CN = {'clean': '干净版', 'contact': '带联系', 'xianguyun': '仙宫云'}

# ---------- 版式表 ----------
# 横屏基准 960x512：左侧 Logo + 右侧文字块，所有值按 H/512 等比缩放
HORIZ = {
    'clean': dict(logo=196, logo_cy=275, lx=92,
                  title_y=190, title=72,
                  sub_y=318, sub=30, sub_gap=190,
                  divider=None, rows=[], row_y0=0, row_gap=46,
                  row_label=19, row_value=23),
    'contact': dict(logo=196, logo_cy=279, lx=92,
                    title_y=104, title=72,
                    sub_y=232, sub=30, sub_gap=190,
                    divider=300, rows=('contact',), row_y0=332, row_gap=46,
                    row_label=19, row_value=23),
    'xianguyun': dict(logo=172, logo_cy=258, lx=92,
                      title_y=102, title=62,
                      sub_y=188, sub=27, sub_gap=168,
                      divider=238, rows=('contact', 'xianguyun'),
                      row_y0=258, row_gap=42,
                      row_label=18, row_value=21),
}

# 竖屏基准 512x960：Logo 在上、文字块在下，全部居中；所有值按 W/512 等比缩放
VERT = {
    'clean': dict(logo=196, logo_cy=430,
                  title_y=590, title=62,
                  sub_y=688, sub=28,
                  divider=None, rows=(), row_y0=0, row_gap=48,
                  row_label=20, row_value=24),
    'contact': dict(logo=176, logo_cy=320,
                    title_y=460, title=56,
                    sub_y=542, sub=25,
                    divider=592, rows=('contact',), row_y0=630, row_gap=50,
                    row_label=19, row_value=23),
    'xianguyun': dict(logo=158, logo_cy=266,
                      title_y=388, title=50,
                      sub_y=460, sub=23,
                      divider=504, rows=('contact', 'xianguyun'),
                      row_y0=538, row_gap=44,
                      row_label=18, row_value=21),
}


def load_brand():
    p = os.path.expanduser('~/.workbuddy/fxai_global.json')
    with open(p, encoding='utf-8') as f:
        return json.load(f)['brand']


BRAND = load_brand()
FONT_DIR = BRAND.get('font_dir', 'C:/Windows/Fonts')


def font(size, bold=False):
    cands = (['msyhbd.ttc', 'msyh.ttc', 'simhei.ttf'] if bold
             else ['msyh.ttc', 'msyhbd.ttc', 'simhei.ttf'])
    for name in cands:
        p = os.path.join(FONT_DIR, name)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, int(size))
            except Exception:
                pass
    return ImageFont.load_default()


def rows_for(keys):
    """按 keys 取信息行；空值行自动跳过。"""
    table = {
        'contact': [('官网', BRAND.get('official_site', 'www.fxai.site')),
                    ('微信', BRAND.get('wechat', 'fxai168')),
                    ('QQ群', BRAND.get('qq_group', ''))],
        'xianguyun': [('仙宫云', '镜像广场搜凤希AI')],
    }
    out = []
    for k in keys:
        out.extend(table[k])
    return [r for r in out if r[1]]


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def draw_bg(W, H):
    """深色纵向渐变 + 右下品牌色光晕。"""
    top, bot = (14, 16, 22), (30, 34, 44)
    canvas = Image.new('RGB', (W, H), top)
    d = ImageDraw.Draw(canvas)
    for y in range(H):
        d.line([(0, y), (W, y)], fill=lerp(top, bot, y / float(max(H - 1, 1))))

    glow = Image.new('RGB', (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy, r = int(W * 0.88), int(H * 1.05), int(W * 0.52)
    for i in range(48, 0, -1):
        t = i / 48.0
        rr = int(r * t)
        c = (int(255 * (1 - t) ** 1.6 * 0.55),
             int(107 * (1 - t) ** 1.6 * 0.55),
             int(74 * (1 - t) ** 1.6 * 0.55))
        gd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=c)
    base, gl = canvas.load(), glow.load()
    for y in range(H):
        for x in range(W):
            b, g = base[x, y], gl[x, y]
            base[x, y] = (min(255, b[0] + g[0]), min(255, b[1] + g[1]),
                          min(255, b[2] + g[2]))
    return canvas, ImageDraw.Draw(canvas)


def paste_logo(canvas, LS, cx_or_lx, cy, halo=True):
    logo = Image.open(BRAND['avatar_512']).convert('RGBA').resize(
        (LS, LS), Image.LANCZOS)
    if halo:
        hsz = int(LS + 56 * LS / 196.0)
        halo_img = Image.new('RGBA', (hsz, hsz), (0, 0, 0, 0))
        hd = ImageDraw.Draw(halo_img)
        steps = 26
        for i in range(steps, 0, -1):
            a = int(26 * (1 - i / float(steps)) ** 1.4)
            ii = int(i * hsz / 56.0 * 56.0 / 56.0 * 1.0)
            pad = int(i * (hsz - LS) / 2.0 / steps * 1.0) if steps else 0
            rr = hsz - i * (hsz - LS) // (2 * steps) * 2
            hd.ellipse([hsz - rr - (hsz - LS) // 2, hsz - rr - (hsz - LS) // 2,
                        rr + (hsz - LS) // 2, rr + (hsz - LS) // 2],
                       fill=(255, 122, 85, a))
        canvas.paste(halo_img, (cx_or_lx - (hsz - LS) // 2,
                                cy - LS // 2 - (hsz - LS) // 2), halo_img)
    canvas.paste(logo, (cx_or_lx, cy - LS // 2), logo)


def build(size, variant):
    W, H = size
    vertical = H > W
    base = (VERT if vertical else HORIZ)[variant]
    s = (W / 512.0) if vertical else (H / 512.0)

    def px(v):
        return int(round(v * s))

    canvas, d = draw_bg(W, H)

    LS = px(base['logo'])
    rows = rows_for(base['rows'])

    if vertical:
        # ---------- 竖屏：Logo 居中在上，文字块居中在下 ----------
        logo_cx = W // 2 - LS // 2
        logo_cy = px(base['logo_cy'])
        # 柔光
        hsz = int(LS * 1.28)
        halo_img = Image.new('RGBA', (hsz, hsz), (0, 0, 0, 0))
        hd = ImageDraw.Draw(halo_img)
        for i in range(26, 0, -1):
            a = int(26 * (1 - i / 26.0) ** 1.4)
            ins = int((hsz - LS) / 2.0 * i / 26.0)
            hd.ellipse([ins, ins, hsz - ins, hsz - ins], fill=(255, 122, 85, a))
        canvas.paste(halo_img, (logo_cx - (hsz - LS) // 2,
                                logo_cy - LS // 2 - (hsz - LS) // 2), halo_img)
        logo = Image.open(BRAND['avatar_512']).convert('RGBA').resize(
            (LS, LS), Image.LANCZOS)
        canvas.paste(logo, (logo_cx, logo_cy - LS // 2), logo)

        f_title = font(px(base['title']), True)
        f_sub = font(px(base['sub']), True)
        f_sub2 = font(px(base['sub']) - max(2, int(px(base['sub']) * 0.25)))

        def ctext(y, txt, f, fill):
            w = d.textlength(txt, font=f)
            d.text(((W - w) / 2.0, y), txt, font=f, fill=fill)

        ctext(px(base['title_y']), '凤希AI', f_title, (255, 255, 255))
        sub = '自动化创作  ·  全链路视频生成'
        ctext(px(base['sub_y']), sub, f_sub if False else f_sub2, ORANGE)

        if base['divider'] is not None:
            m = px(92)
            d.line([(m, px(base['divider'])), (W - m, px(base['divider']))],
                   fill=ORANGE, width=max(1, px(2)))

        y = px(base['row_y0'])
        f_l = font(px(base['row_label']))
        f_v = font(px(base['row_value']), True)
        for idx, (label, val) in enumerate(rows):
            last = (variant == 'xianguyun' and idx == len(rows) - 1)
            line = '%s  %s' % (label, val)
            w = d.textlength(line, font=f_v)
            d.text(((W - w) / 2.0, y), label, font=f_l, fill=GREY)
            lw = d.textlength(label, font=f_l)
            d.text(((W - w) / 2.0 + lw + px(14), y - px(3)), val, font=f_v,
                   fill=ORANGE if last else LIGHT)
            y += px(base['row_gap'])
    else:
        # ---------- 横屏：左 Logo + 右文字块 ----------
        lx = px(base['lx'])
        cy = px(base['logo_cy'])
        hsz = int(LS * 1.28)
        halo_img = Image.new('RGBA', (hsz, hsz), (0, 0, 0, 0))
        hd = ImageDraw.Draw(halo_img)
        for i in range(26, 0, -1):
            a = int(26 * (1 - i / 26.0) ** 1.4)
            ins = int((hsz - LS) / 2.0 * i / 26.0)
            hd.ellipse([ins, ins, hsz - ins, hsz - ins], fill=(255, 122, 85, a))
        canvas.paste(halo_img, (lx - (hsz - LS) // 2,
                                cy - LS // 2 - (hsz - LS) // 2), halo_img)
        logo = Image.open(BRAND['avatar_512']).convert('RGBA').resize(
            (LS, LS), Image.LANCZOS)
        canvas.paste(logo, (lx, cy - LS // 2), logo)

        tx = lx + LS + px(76)
        d.text((tx, px(base['title_y'])), '凤希AI',
               font=font(px(base['title']), True), fill=(255, 255, 255))
        d.text((tx + px(4), px(base['sub_y'])), '自动化创作',
               font=font(px(base['sub']), True), fill=ORANGE)
        d.text((tx + px(base['sub_gap']), px(base['sub_y']) + px(8)),
               '·  全链路视频生成',
               font=font(px(base['sub']) - px(8)), fill=(168, 176, 190))

        if base['divider'] is not None:
            d.line([(tx, px(base['divider'])), (W - px(92), px(base['divider']))],
                   fill=ORANGE, width=max(1, px(2)))

        y = px(base['row_y0'])
        for idx, (label, val) in enumerate(rows):
            last = (variant == 'xianguyun' and idx == len(rows) - 1)
            d.text((tx, y), label, font=font(px(base['row_label'])), fill=GREY)
            d.text((tx + px(78), y - px(3)), val,
                   font=font(px(base['row_value']), True),
                   fill=ORANGE if last else LIGHT)
            y += px(base['row_gap'])

    return canvas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--size', default='960x512', help='WxH，例如 960x512 / 512x960')
    ap.add_argument('--variant', default='clean',
                    choices=['clean', 'contact', 'xianguyun', 'all'])
    ap.add_argument('--out', default='', help='指定输出 PNG 路径（单变体时有效）')
    args = ap.parse_args()

    w, h = [int(x) for x in args.size.lower().replace('*', 'x').split('x')]
    variants = (['clean', 'contact', 'xianguyun']
                if args.variant == 'all' else [args.variant])

    os.makedirs(ASSETS, exist_ok=True)
    made = []
    for v in variants:
        img = build((w, h), v)
        if args.out and len(variants) == 1:
            out = args.out
        else:
            out = os.path.join(ASSETS, '尾卡_%s_%dx%d.png'
                               % (VARIANT_CN[v], w, h))
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        img.save(out, quality=96)
        made.append(out)
        print('尾卡[%s] → %s  %dx%d' % (v, out, w, h))
    return made


if __name__ == '__main__':
    main()
