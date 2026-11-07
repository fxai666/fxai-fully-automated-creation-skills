#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从「逐段音频」算出时间码，并与文字清单合成 timeline.json。

一段音频 = 一拍。start/end 由各段真实时长累加得出，不估。
没有音频的场合（比如给外部视频叠字）就手写 timeline.json，不用本脚本。

用法：
  python scripts/make_timeline.py --audio-dir music --cards cards.json --out timeline.json
  python scripts/make_timeline.py --audio-dir . --out timeline.json      # 只要时间码
"""
import argparse, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cardkit as K

SKIP = {"口播音频.wav"}


def natkey(name):
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", os.path.basename(name))]


def find_audio(d):
    got = [os.path.join(d, f) for f in os.listdir(d)
           if f.lower().endswith((".wav", ".mp3", ".m4a", ".flac"))
           and f not in SKIP and not f.startswith("_")]
    if not got:
        sys.exit("目录里没有音频：%s" % d)
    return sorted(got, key=natkey)


def dur(path):
    if path.lower().endswith(".wav"):
        try:
            import wave
            with wave.open(path, "rb") as w:
                fr = w.getframerate()
                if fr:
                    return w.getnframes() / float(fr)
        except Exception:
            pass
    import subprocess
    p = subprocess.run([K.ffprobe_bin(), "-v", "error", "-show_entries",
                        "format=duration", "-of", "default=nw=1:nk=1", path],
                       capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except Exception:
        sys.exit("读不出音频时长：%s" % path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio-dir", required=True)
    ap.add_argument("--cards", default=None, help="cards.json（可省，只出时间码）")
    ap.add_argument("--out", default="timeline.json")
    a = ap.parse_args()

    files = find_audio(a.audio_dir)
    cards = None
    if a.cards:
        if not os.path.isfile(a.cards):
            sys.exit("缺文字清单：%s" % a.cards)
        cards = json.load(open(a.cards, encoding="utf-8"))
        if len(cards) != len(files):
            sys.exit("文字 %d 条 vs 音频 %d 段 —— 必须一一对应"
                     % (len(cards), len(files)))

    beats, t = [], 0.0
    for i, p in enumerate(files, 1):
        d = dur(p)
        b = {"i": i, "start": round(t, 3), "end": round(t + d, 3),
             "dur": round(d, 3), "audio": os.path.basename(p)}
        if cards:
            c = cards[i - 1]
            b.update(title=c.get("title", ""), sub=c.get("sub", ""),
                     foot=c.get("foot", ""))
        beats.append(b)
        t += d

    json.dump({"total": round(t, 3), "beats": beats},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("%d 拍，总时长 %.2fs -> %s" % (len(beats), t, a.out))


if __name__ == "__main__":
    main()
