#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条龙：文字清单 + 任意视频 → 叠好标题卡的视频。

内部 = gen_cards（出 PNG）+ overlay_cards（按时间码叠）。视频尺寸自动探测。

用法：
  python scripts/title_card.py --video in.mp4 --timeline timeline.json --out out.mp4
  python scripts/title_card.py --video in.mp4 --timeline t.json --cards c.json \
         --position top --out out.mp4
"""
import argparse, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cardkit as K
import gen_cards
import overlay_cards


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--cards", default=None, help="兼容：分离式文字清单")
    ap.add_argument("--position", default="auto",
                    choices=["auto", "alternate", "top", "center", "bottom", "left", "right"],
                    help="auto=竖版压顶/横版左右交替竖排；left/right 钉死一侧；alternate 同 auto")
    ap.add_argument("--titles-dir", default=None, help="PNG 留存目录（默认临时目录）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--crf", type=int, default=20)
    a = ap.parse_args()

    if not os.path.isfile(a.video):
        sys.exit("视频不存在：%s" % a.video)
    beats = K.load_timeline(a.timeline, a.cards)
    vw, vh = K.video_size(a.video)
    if not vw:
        sys.exit("读不出视频尺寸：%s" % a.video)
    print("视频 %dx%d，%d 拍" % (vw, vh, len(beats)))

    tdir = a.titles_dir or tempfile.mkdtemp(prefix="titlecard_")
    gen_cards.make_cards(beats, vw, vh, tdir, a.position)
    overlay_cards.overlay(a.video, tdir, beats, a.out, a.crf)
    if not a.titles_dir:
        print("（PNG 在临时目录 %s，要留存请给 --titles-dir）" % tdir)


if __name__ == "__main__":
    main()
