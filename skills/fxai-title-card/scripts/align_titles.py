# -*- coding: utf-8 -*-
"""titles 目录顶对齐器：把同一批标题卡按内容裁齐后钉到同一个起始高度。

为什么需要：gen_cards.py 的竖排是「块整体在安全区垂直居中」，三拍文字长短不一时
（比如 5 字主标 + 7 字副标 vs 4 字主标 + 4 字副标），换字那一刻主标会上下跳几十像素。
本脚本统一裁掉透明边，再把内容左上角钉死到同一个 (left, top)。

用法：
    python scripts/align_titles.py --src <原始titles> --out <对齐后titles> \
           --size 960x704 --left 96 --top 80

不传 --left / --top 时按 short_edge*0.1（侧边距）/ short_edge*0.11（顶部基线）估算，
与 cardkit.TIERS 小档（顶 80 / 边 96）一致。
"""
import argparse
import os
import glob


def parse_size(s):
    w, h = s.lower().split("x")
    return int(w), int(h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="gen_cards.py 输出的 titles 目录")
    ap.add_argument("--out", required=True, help="对齐后输出目录")
    ap.add_argument("--size", required=True, help="画布尺寸 WxH，与 gen_cards 的 --size 一致")
    ap.add_argument("--left", type=int, default=None)
    ap.add_argument("--top", type=int, default=None)
    args = ap.parse_args()

    from PIL import Image

    W, H = parse_size(args.size)
    short = min(W, H)
    left = args.left if args.left is not None else int(round(short * 0.12))
    top = args.top if args.top is not None else int(round(short * 0.10))

    os.makedirs(args.out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(args.src, "*.png")))
    if not files:
        raise SystemExit("源目录没有 png：%s" % args.src)

    done = []
    for p in files:
        im = Image.open(p).convert("RGBA")
        bb = im.getbbox()
        if bb is None:
            # 空卡（这一拍没字）→ 输出全透明同尺寸卡，保持拍数对齐
            canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        else:
            body = im.crop(bb)
            canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            canvas.paste(body, (left, top), body)
        name = os.path.basename(p)
        canvas.save(os.path.join(args.out, name))
        done.append((name, None if im.getbbox() is None else im.getbbox()[3] - im.getbbox()[1]))

    print("对齐 %d 张 → %s  锚点 (%d, %d)" % (len(done), args.out, left, top))
    for n, h in done:
        print("  %s  内容高 %s" % (n, h if h else "空"))


if __name__ == "__main__":
    main()
