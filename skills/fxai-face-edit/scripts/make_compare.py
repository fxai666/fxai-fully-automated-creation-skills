# -*- coding: utf-8 -*-
"""按源片画幅生成原片对比：横屏 vstack、竖屏 hstack。"""
import argparse
import json
import os
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

sys.dont_write_bytecode = True


def frame_checker():
    """帧数验收共用实现在 fxai-standard/scripts/video_frames.py，判据只留一处。"""
    import os
    scripts = os.environ.get(
        "FXAI_STANDARD_SCRIPTS",
        os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..",
                                      "fxai-standard", "scripts")))
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import video_frames
    return video_frames


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
    streams = d.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if v is None:
        raise RuntimeError("ffprobe 未找到视频流：%s" % path)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return v, has_audio


def stamp(path):
    """文件指纹：路径 + 体积 + 修改时间 + 帧数。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    v, _ = probe(Path(path))
    return {"path": str(Path(path).resolve()), "size": st.st_size,
            "mtime": int(st.st_mtime),
            "frames": int(v.get("nb_frames") or 0)}


def verify(out):
    """校验对比片是否由当前成片生成：成片重出后旧对比片即判过期。"""
    meta_path = str(out) + ".meta.json"
    if not os.path.isfile(meta_path):
        print("对比片无生成记录（缺 %s），无法证明与当前成片同源，判过期。"
              % os.path.basename(meta_path))
        return 1
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    rec = meta.get("result") or {}
    cur = stamp(rec.get("path", ""))
    if cur is None:
        print("记录中的成片已不存在：%s，对比片判过期。" % rec.get("path"))
        return 1
    if (rec.get("size"), rec.get("mtime"), rec.get("frames")) != (
            cur["size"], cur["mtime"], cur["frames"]):
        print("对比片过期：成片已重出（记录 %s 字节 / %s，当前 %s 字节 / %s），须重做。"
              % (rec.get("size"), rec.get("mtime"), cur["size"], cur["mtime"]))
        return 1
    print("对比片与当前成片同源：%s" % cur["path"])
    return 0


def main():
    ap = argparse.ArgumentParser(description="按源片画幅生成 原片对比.mp4")
    ap.add_argument("--source", help="源片路径")
    ap.add_argument("--result", help="最终成片路径")
    ap.add_argument("--out", help="输出对比片路径")
    ap.add_argument("--verify", help="只校验：给定对比片路径，核对是否由当前成片生成")
    ap.add_argument("--width", type=int, help="单格宽；缺省取成片宽")
    ap.add_argument("--height", type=int, help="单格高；缺省取成片高")
    ap.add_argument("--fps", help="帧率；缺省取成片 r_frame_rate")
    ap.add_argument("--crf", type=int, default=18)
    o = ap.parse_args()

    if o.verify:
        sys.exit(verify(o.verify))
    if not (o.source and o.result and o.out):
        ap.error("生成需要 --source / --result / --out；校验用 --verify")

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
    dec = frame_checker().assert_frames(out, n) if n else int(ov.get("nb_frames") or 0)
    print("源片 %sx%s（%s）→ %s；输出 %sx%s / %s 帧（解码实测 %d）/ 音轨%s"
          % (sv["width"], sv["height"], "横屏" if landscape else "竖屏", layout,
             ov["width"], ov["height"], ov.get("nb_frames", "?"), dec,
             "有" if o_audio else "无"))

    # 生成记录：成片重出后本对比片即判过期，交付前用 --verify 核对
    meta = {"source": stamp(o.source), "result": stamp(o.result),
            "compare": stamp(out)}
    with open(str(out) + ".meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(out)


if __name__ == "__main__":
    main()
