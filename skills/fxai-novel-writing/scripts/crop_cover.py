#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""封面裁切：把母版按目标平台尺寸等比缩放后居中裁切。

用法：
    python crop_cover.py <源图> <平台> [-o 输出目录]
平台：番茄 起点 创世 云起 七猫 塔读 咪咕 晋江 微信读书 知乎盐选 黑岩
输出：<输出目录>/<源图名>_<平台>_<宽>x<高>.jpg
退出码：成功 0，失败 1。
"""

import argparse
import os
import sys

PLATFORMS = {
    "番茄": (600, 800),
    "起点": (600, 800),
    "创世": (600, 800),
    "云起": (600, 800),
    "七猫": (600, 800),
    "塔读": (600, 800),
    "咪咕": (600, 800),
    "晋江": (200, 280),
    "微信读书": (428, 616),
    "知乎盐选": (1200, 628),
    "黑岩": (400, 560),
}


def fit(img, w, h):
    from PIL import Image
    sw, sh = img.size
    scale = max(w / sw, h / sh)
    nw, nh = round(sw * scale), round(sh * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("platform")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    if args.platform not in PLATFORMS:
        print("未知平台：%s；可选：%s" % (args.platform, " ".join(PLATFORMS)))
        return 1
    try:
        from PIL import Image
    except ImportError:
        print("缺 Pillow：pip install pillow")
        return 1

    w, h = PLATFORMS[args.platform]
    out_dir = args.out or os.path.dirname(os.path.abspath(args.src))
    img = Image.open(args.src).convert("RGB")
    stem = os.path.splitext(os.path.basename(args.src))[0]
    dst = os.path.join(out_dir, "%s_%s_%dx%d.jpg" % (stem, args.platform, w, h))
    fit(img, w, h).save(dst, quality=95, subsampling=0)
    print(dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
