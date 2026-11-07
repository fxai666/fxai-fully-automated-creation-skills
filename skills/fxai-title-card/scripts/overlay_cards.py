#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把标题卡 PNG 按时间码叠到任意视频上（不动音频、不拼封面）。

· 视频尺寸自动 ffprobe；PNG 尺寸不匹配时自动缩放，不报错。
· 每张卡在 [start, end] 区间显示（overlay enable=between）。
· 音轨 `-c:a copy` 原样保留；无音轨也能跑。
· 输出保持原分辨率/帧率，只重编码视频。

用法：
  python scripts/overlay_cards.py --video in.mp4 --titles-dir titles/ \
         --timeline timeline.json --out out.mp4
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cardkit as K


def overlay(video, titles_dir, beats, out, crf=20, prefix="title_"):
    FF = K.ffmpeg_bin()
    vw, vh = K.video_size(video)
    if not vw:
        sys.exit("读不出视频尺寸：%s" % video)

    cards = []
    for b in beats:
        if not (b.get("title") or b.get("sub")):
            continue
        p = os.path.join(titles_dir, "%s%02d.png" % (prefix, b["i"]))
        if not os.path.isfile(p):
            sys.exit("缺标题卡 %s（先用 gen_cards.py 出图）" % p)
        cards.append((p, b))

    if not cards:
        print("没有任何非空标题卡，直接复制视频")
        K.run([FF, "-y", "-i", video, "-c", "copy", out])
        return out

    inputs, chains, prev = [], [], "[0:v]"
    for j, (p, b) in enumerate(cards, start=1):
        inputs += ["-i", p]
        tag = "[v%d]" % j
        # PNG 尺寸与视频不一致时先缩放
        src = "[%d:v]" % j
        chains.append("%sscale=%d:%d%s" % (src, vw, vh, "[s%d]" % j))
        chains.append("%s[s%d]overlay=0:0:enable='between(t,%.3f,%.3f)'%s"
                      % (prev, j, float(b["start"]), float(b["end"]), tag))
        prev = tag
    fc = ";".join(chains)

    def build(enc):
        v = (["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
             if enc == "h264_nvenc" else
             ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf)])
        cmd = ([FF, "-y", "-i", video] + inputs
               + ["-filter_complex", fc, "-map", prev])
        cmd += (["-map", "0:a", "-c:a", "copy"] if K.has_audio(video) else ["-an"])
        cmd += v + ["-pix_fmt", "yuv420p", out]
        return cmd

    try:
        K.run(build("h264_nvenc"))
    except SystemExit:
        print("[warn] nvenc 失败，回落 libx264")
        K.run(build("libx264"))
    print("标题卡叠加完成 -> %s（%d 张）" % (out, len(cards)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, help="任意视频")
    ap.add_argument("--titles-dir", required=True)
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--cards", default=None, help="兼容：分离式文字清单")
    ap.add_argument("--out", required=True)
    ap.add_argument("--crf", type=int, default=20)
    a = ap.parse_args()
    beats = K.load_timeline(a.timeline, a.cards)
    overlay(a.video, a.titles_dir, beats, a.out, a.crf)


if __name__ == "__main__":
    main()
