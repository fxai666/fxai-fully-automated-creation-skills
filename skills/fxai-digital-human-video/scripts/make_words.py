#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从「口播文案 + 逐段音频时间码」重建字幕时间轴（_words.txt）。

── 为什么需要它 ──
TTS 是逐段合成的，`_beat_times.json` 里每段的起止是**真实音频时长累加**出来的，
精确到 0.01s；但段内逐句的时间没有。此时若让 whisper 重新转写：
  · 识别错字会直接烧到画面上（「链路」→「练路」、「一屏」→「一瓶」）；
  · 同一句被切碎或合并，字幕断句跟文案对不上。
文案是准确的事实源，段边界是准确的时间源——用这两样合成，两处都不丢。

── 段内怎么分 ──
按标点拆句，再按**字数比例**切该段时长。段边界误差 0，句内误差通常在 ±0.3s，
字幕观感够用；真要更准就接 forced alignment，本脚本不承担。

── 用法 ──
  python scripts/make_words.py --project <项目>
  python scripts/make_words.py --script 口播文案.md --beats _beat_times.json \
      --total 113.12 --out _words.txt
"""
import argparse
import io
import json
import os
import re

# 字幕里中文数字换成阿拉伯更好读，只处理这段文案里真实出现的量词。
# 长词在前，让「三十」先匹配；「三」放后面也不会再匹配。
NUM_WORDS = [
    ("一百三十一", "131"), ("一百一十三", "113"), ("二十二", "22"),
    ("二十分钟", "20分钟"), ("两年前", "2年前"),
    ("三十", "30"), ("八个", "8个"), ("五个", "5个"), ("四个", "4个"),
    ("七个", "7个"), ("六个", "6个"), ("五摊", "5摊"),
]

# 顿号「、」不算断句：口播里「上云、装环境、等任务、更新、打包」是一个语块，
# 按它切会得到 5 个不到 1 秒的碎句，在画面上闪得看不清。
SPLIT_PUNCT = "，。！？；：,.!?;:"
STRIP_TAIL = "，。！？；：、,.!?;: "


def read_script(path):
    """读文案，按空行切段；丢掉 markdown 标题与列表符号。"""
    raw = io.open(path, encoding="utf-8").read()
    segs = []
    for blk in re.split(r"\n\s*\n", raw):
        lines = []
        for ln in blk.splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            ln = re.sub(r"^[-*+]\s+", "", ln)
            ln = re.sub(r"^\d+[.)]\s+", "", ln)
            lines.append(ln)
        txt = "".join(lines).strip()
        if txt:
            segs.append(txt)
    return segs


def to_arabic(text):
    for cn, ar in NUM_WORDS:
        text = text.replace(cn, ar)
    return text


def split_sentences(text):
    """按标点拆句，保留语序，去掉句尾标点（字幕不显示）。"""
    out, cur = [], ""
    for ch in text:
        if ch in SPLIT_PUNCT:
            s = cur.strip(STRIP_TAIL)
            if s:
                out.append(s)
            cur = ""
        else:
            cur += ch
    s = cur.strip(STRIP_TAIL)
    if s:
        out.append(s)
    return out or [text.strip(STRIP_TAIL)]


def build(script_path, beats_path, total=None, arabic=True):
    segs = read_script(script_path)
    beats = json.load(io.open(beats_path, encoding="utf-8"))["beats"]
    if len(segs) != len(beats):
        raise SystemExit("文案 %d 段，音频时间码 %d 段，对不上——先确认文案与音频是同一版"
                         % (len(segs), len(beats)))
    rows = []
    for seg, bt in zip(segs, beats):
        st, ed = float(bt["start"]), float(bt["end"])
        parts = split_sentences(seg)
        if arabic:
            parts = [to_arabic(p) for p in parts]
        # 按字数比例切时长；用「字数+1」平滑，让 1 字句仍分到一段可读时长
        w = [len(p) + 1 for p in parts]
        tot = float(sum(w))
        cur = st
        for i, (p, wi) in enumerate(zip(parts, w)):
            span = (ed - st) * wi / tot
            nxt = ed if i == len(parts) - 1 else cur + span
            rows.append((cur, nxt, p))
            cur = nxt
    # 最后一段的 end 以真实音频总长为准（重录过某段时 beats 里的 end 会偏）
    if total:
        s, _, p = rows[-1]
        rows[-1] = (s, min(float(total), s + (float(total) - s)), p)
    return rows


def write_words(rows, path):
    with io.open(path, "w", encoding="utf-8") as f:
        for st, ed, txt in rows:
            f.write("[%.1f->%.1f] %s\n" % (st, ed, txt))
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    ap.add_argument("--script", default=None, help="口播文案 md，默认 <项目>/口播文案.md")
    ap.add_argument("--beats", default=None, help="逐段时长时间码，默认 <项目>/_beat_times.json")
    ap.add_argument("--out", default=None, help="输出，默认 <项目>/_words.txt")
    ap.add_argument("--total", type=float, default=None,
                    help="音频真实总长（秒）；给了就用它校正最后一句的结束时间")
    ap.add_argument("--keep-cn", action="store_true", help="保留中文数字，不转阿拉伯")
    a = ap.parse_args()

    script = a.script or os.path.join(a.project, "口播文案.md")
    beats = a.beats or os.path.join(a.project, "_beat_times.json")
    out = a.out or os.path.join(a.project, "_words.txt")
    for p in (script, beats):
        if not os.path.isfile(p):
            raise SystemExit("找不到 %s" % p)

    rows = build(script, beats, total=a.total, arabic=not a.keep_cn)
    write_words(rows, out)
    span = rows[-1][1] - rows[0][0]
    print("字幕轴 -> %s（%d 句，覆盖 %.2fs）" % (out, len(rows), span))


if __name__ == "__main__":
    main()
