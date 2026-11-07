# -*- coding: utf-8 -*-
"""按源片画幅生成原片对比：横屏 vstack、竖屏 hstack。"""
import argparse
import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

sys.dont_write_bytecode = True


def run(cmd, capture=False):
    p = subprocess.run(cmd, capture_output=capture, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("命令失败（退出码 %d）：%s"
                           % (p.returncode, " ".join(map(str, cmd[:6]))))
    return p.stdout if capture else None


def probe(path):
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "stream=codec_type,width,height,nb_frames,r_frame_rate",
               "-of", "json", str(path)], capture=True)
    d = json.loads(out)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    has_audio = any(s["codec_type"] == "audio" for s in d["streams"])
    return v, has_audio


def main():
    ap = argparse.ArgumentParser(description="按源片画幅生成 原片对比.mp4")
    ap.add_argument("--source", required=True, help="源片路径")
    ap.add_argument("--result", required=True, help="最终成片路径")
    ap.add_argument("--out", required=True, help="输出对比片路径")
    ap.add_argument("--width", type=int, help="单格宽；缺省取成片宽")
    ap.add_argument("--height", type=int, help="单格高；缺省取成片高")
    ap.add_argument("--fps", help="帧率；缺省取成片 r_frame_rate")
    ap.add_argument("--crf", type=int, default=18)
    o = ap.parse_args()

    sv, _ = probe(Path(o.source))
    rv, r_audio = probe(Path(o.result))
    landscape = int(sv["width"]) >= int(sv["height"])
    layout = "上下堆叠" if landscape else "左右并排"
    stack = "vstack" if landscape else "hstack"

    W = (o.width or int(rv["width"])) // 2 * 2
    H = (o.height or int(rv["height"])) // 2 * 2
    rate = o.fps or rv["r_frame_rate"]
    fps = float(Fraction(rate))
    n = int(rv.get("nb_frames") or 0)

    filt = ("[0:v]scale=%d:%d,setsar=1,fps=%s,setpts=N/(%.6f*TB)[a];"
            "[1:v]scale=%d:%d,setsar=1,fps=%s,setpts=N/(%.6f*TB)[b];"
            "[a][b]%s=inputs=2[outv]"
            % (W, H, rate, fps, W, H, rate, fps, stack))

    out = Path(o.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(o.source), "-i", str(o.result),
           "-filter_complex", filt, "-map", "[outv]"]
    if r_audio:
        cmd += ["-map", "1:a"]
    cmd += ["-c:v", "libx264", "-crf", str(o.crf), "-preset", "medium",
            "-pix_fmt", "yuv420p"]
    if r_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-r", rate]
    if n:
        cmd += ["-frames:v", str(n)]
    cmd += ["-movflags", "+faststart", str(out)]
    run(cmd)

    ov, o_audio = probe(out)
    print("源片 %sx%s（%s）→ %s；输出 %sx%s / %s 帧 / 音轨%s"
          % (sv["width"], sv["height"], "横屏" if landscape else "竖屏", layout,
             ov["width"], ov["height"], ov.get("nb_frames", "?"),
             "有" if o_audio else "无"))
    print(out)


if __name__ == "__main__":
    main()
