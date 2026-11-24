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

· 默认所有卡钉 (left, top)。传 --timeline 时按每张卡的 `side` 字段分边：
  side=left → 钉 (left, top)；side=right → 右缘钉 (W-left-内容宽, top)，
  即左右镜像等距——多拍引导卡左右交替换边时用它消掉同位硬切。
"""
import argparse
import json
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
    ap.add_argument("--timeline", default=None,
                    help="timeline.json：按每张卡的 side 字段分边（right 镜像等距），左右交替换边用")
    args = ap.parse_args()

    from PIL import Image

    W, H = parse_size(args.size)
    short = min(W, H)
    left = args.left if args.left is not None else int(round(short * 0.12))
    top = args.top if args.top is not None else int(round(short * 0.10))

    sides = {}
    if args.timeline:
        tl = json.load(open(args.timeline, encoding="utf-8"))
        for b in tl.get("beats", []):
            sides["%02d" % b["i"]] = (b.get("side") or "left").strip().lower()

    os.makedirs(args.out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(args.src, "*.png")))
    if not files:
        raise SystemExit("源目录没有 png：%s" % args.src)

    done = []
    for p in files:
        im = Image.open(p).convert("RGBA")
        bb = im.getbbox()
        key = os.path.splitext(os.path.basename(p))[0].split("_")[-1]
        right = sides.get(key) == "right"
        if bb is None:
            # 空卡（这一拍没字）→ 输出全透明同尺寸卡，保持拍数对齐
            canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        else:
            body = im.crop(bb)
            canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            bw = bb[2] - bb[0]
            # 右缘镜像等距：右锚内容贴 (W-left) 处，与左锚 left 严格对称
            x = (W - left - bw) if right else left
            canvas.paste(body, (x, top), body)
        name = os.path.basename(p)
        canvas.save(os.path.join(args.out, name))
        done.append((name, None if bb is None else bb[3] - bb[1], "R" if right else "L"))

    print("对齐 %d 张 → %s  左锚 (%d,%d) / 右缘锚 (%d,%d)"
          % (len(done), args.out, left, top, W - left, top))
    for n, h, s in done:
        print("  %s [%s] 内容高 %s" % (n, s, h if h else "空"))


if __name__ == "__main__":
    main()
