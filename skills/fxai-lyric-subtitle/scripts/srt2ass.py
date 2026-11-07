# -*- coding: utf-8 -*-
"""SRT -> ASS（PlayRes 用真实分辨率，字号按竖/横固定档，不按比例缩放）。

为什么不用 SRT + force_style：
  SRT 没有 PlayRes，libass 按 288 基准把字号放大 ×(视频高/288)。竖屏 1280 即 ×4.44，
  force_style 里写的 28 实际烧成 124px em，是「字幕巨大」的根因。ASS 自带 PlayRes，
  字号单位 = 真实像素，写多少就是多少。

规范来源：
  fxai-lyric-subtitle/SKILL.md「单条字幕的上限（按画面方向分档）」
    竖屏 768×1280 -> 字号 32（单条 ≤16 字 / ≤4.0s）
    横屏 1280×768 -> 字号 38（单条 ≤24 字 / ≤6.0s）
  fxai-mv-pipeline/lyrics_align.py ass_header()：PlayResX/Y 必须=视频真实分辨率，
    白色款 primary=&H00FFFFFF, outline=&H00181A1C，微软雅黑加粗，描边 2.5，投影 1.2，字距 0.8。

用法：
  python srt2ass.py in.srt out.ass [--size 768x1280] [--fontsize 32]
  --size 省略时按 768x1280 处理；--fontsize 0（默认）= 按画面方向取固定档。
  分辨率未知时用 ffprobe 取真实宽高再传，别猜。
"""
import argparse
import re


def parse_srt(path):
    txt = open(path, encoding="utf-8-sig").read().replace("\r\n", "\n")
    out = []
    for blk in re.split(r"\n\s*\n", txt.strip()):
        lines = [x for x in blk.split("\n") if x.strip()]
        if len(lines) < 3:
            continue
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", lines[1])
        if not m:
            continue
        g = list(map(int, m.groups()))
        s = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
        e = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
        body = "\n".join(lines[2:]).strip()
        out.append((s, e, body))
    return out


def ass_time(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    if cs == 100:
        cs = 0
        s += 1
    return "%d:%02d:%02d.%02d" % (h, m, s, cs)


def build_header(w, h, fontsize, primary="&H00FFFFFF", outline_colour="&H00181A1C"):
    mv = max(24, int(round(h * 0.078)))
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: %d\nPlayResY: %d\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n" % (w, h)
        + "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Microsoft YaHei,%d,%s,&H000000FF,%s,&H50000000,"
        "1,0,0,0,100,100,0.8,0,1,2.5,1.2,2,30,30,%d,1\n\n" % (fontsize, primary, outline_colour, mv)
        + "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Text\n"
    )


def convert(srt, out, size="768x1280", fontsize=0, primary="&H00FFFFFF", outline_colour="&H00181A1C"):
    """纯函数入口：返回 (ass_path, fontsize)。size 形如 '768x1280'。"""
    w, h = [int(x) for x in size.lower().split("x")]
    land = w >= h
    fs = fontsize or (38 if land else 32)
    segs = parse_srt(srt)
    lines = [build_header(w, h, fs, primary, outline_colour)]
    for s, e, body in segs:
        lines.append("Dialogue: 0,%s,%s,Default,,0,0,0,%s"
                     % (ass_time(s), ass_time(e), body.replace("\n", "\\N")))
    open(out, "w", encoding="utf-8", newline="").write("\n".join(lines) + "\n")
    return out, fs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("srt")
    ap.add_argument("out")
    ap.add_argument("--size", default="768x1280", help="视频真实分辨率，如 768x1280")
    ap.add_argument("--fontsize", type=int, default=0, help="0 = 按画面方向取固定档（竖 32 / 横 38）")
    a = ap.parse_args()
    out, fs = convert(a.srt, a.out, a.size, a.fontsize)
    land = int(a.size.lower().split("x")[0]) >= int(a.size.lower().split("x")[1])
    print("[srt2ass] %s -> %s | PlayRes %s | 字号 %d（%s）"
          % (a.srt, out, a.size, fs, "横屏档" if land else "竖屏档"))


if __name__ == "__main__":
    main()
