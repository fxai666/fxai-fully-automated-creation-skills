# -*- coding: utf-8 -*-
"""多镜头源片 · 按镜头切点切段（逐镜切段替换法的步 1~2）。

用法：
    python shot_split.py SRC --out-dir DIR
    python shot_split.py SRC --out-dir DIR --cuts 3.6,5.93,7.23
    python shot_split.py SRC --out-dir DIR --threshold 0.25 --min-frames 24 --fps 30

--cuts 传秒数列表时按给定切点切；留空则用 ffmpeg 的 select=gt(scene,T) 自动测切点。
切点换算成帧号边界，短于 --min-frames 的段并入相邻，再按帧号裁出独立片段。
片段写到 DIR/clips/，清单写到 DIR/manifest.json，file 字段为相对路径。
视频按帧号精确裁并重置时间戳；音频按对应秒裁，仅用于单段预览。
成片音轨由装配脚本整条取源片。只写 DIR，源片保持只读。
"""
import argparse
import json
import os
import re
import subprocess


def probe_streams(src):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate,nb_frames",
         "-of", "json", src], capture_output=True, text=True)
    v = json.loads(r.stdout)["streams"][0]
    num, den = v.get("r_frame_rate", "30/1").split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    total = int(v.get("nb_frames") or 0)
    a = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=index", "-of", "csv=p=0", src],
        capture_output=True, text=True)
    return fps, total, bool(a.stdout.strip())


def detect_cuts(src, threshold):
    r = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", src,
         "-vf", "select='gt(scene,%g)',showinfo" % threshold,
         "-an", "-f", "null", "-"], capture_output=True, text=True)
    return [float(x) for x in re.findall(r"pts_time:\s*([0-9.]+)", r.stderr)]


def merge_short(segs, min_frames):
    merged, i = [], 0
    while i < len(segs):
        a, b = segs[i]
        if b - a < min_frames:
            j, end = i + 1, b
            while j < len(segs) and segs[j][1] - segs[j][0] < min_frames:
                end = segs[j][1]
                j += 1
            if end - a < min_frames and j < len(segs):
                end = segs[j][1]
                j += 1
            merged.append([a, end])
            i = j
        else:
            merged.append([a, b])
            i += 1
    if len(merged) >= 2 and merged[-1][1] - merged[-1][0] < min_frames:
        merged[-2][1] = merged[-1][1]
        merged.pop()
    return merged


def main():
    ap = argparse.ArgumentParser(description="多镜头源片按切点切段")
    ap.add_argument("src", help="源视频路径")
    ap.add_argument("--out-dir", required=True, help="片段与清单输出目录")
    ap.add_argument("--cuts", default="", help="逗号分隔的切点秒数；留空则自动测")
    ap.add_argument("--threshold", type=float, default=0.25, help="自动测切点的场景阈值")
    ap.add_argument("--min-frames", type=int, default=24, help="短于此帧数的段并入相邻")
    ap.add_argument("--fps", type=float, default=0, help="覆盖探测到的帧率")
    ap.add_argument("--end-frame", type=int, default=-1, help="结束帧号；留空则取源片总帧数")
    a = ap.parse_args()

    fps, total, has_audio = probe_streams(a.src)
    fps = a.fps or fps
    end = a.end_frame if a.end_frame > 0 else total
    if end <= 0:
        raise SystemExit("无法取得源片总帧数，请显式传 --end-frame")

    cuts = ([float(x) for x in a.cuts.split(",") if x.strip()]
            if a.cuts.strip() else detect_cuts(a.src, a.threshold))
    bounds = [0] + [round(s * fps) for s in cuts] + [end]
    bounds = sorted(set(b for b in bounds if 0 <= b <= end))
    bounds[0], bounds[-1] = 0, end
    segs = merge_short([[bounds[i], bounds[i + 1]] for i in range(len(bounds) - 1)],
                       a.min_frames)

    clip_dir = os.path.join(a.out_dir, "clips")
    os.makedirs(clip_dir, exist_ok=True)
    manifest = []
    for k, (s, e) in enumerate(segs):
        name = "clip_%02d.mp4" % k
        dst = os.path.join(clip_dir, name)
        vf = "select='between(n\\,%d\\,%d)',setpts=N/(%g*TB)" % (s, e - 1, fps)
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", a.src]
        if has_audio:
            af = "atrim=start=%g:end=%g,asetpts=N/SR/TB" % (s / fps, e / fps)
            cmd += ["-filter_complex", "[0:v]%s[v];[0:a]%s[a]" % (vf, af),
                    "-map", "[v]", "-map", "[a]", "-c:a", "aac"]
        else:
            cmd += ["-vf", vf, "-an"]
        cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "fast",
                "-r", "%g" % fps, dst]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("切段 %d 失败: %s" % (k, r.stderr[-400:]))
        manifest.append({"idx": k, "start_frame": s, "end_frame": e,
                         "frames": e - s, "file": "clips/" + name})
        print("镜%02d: 帧%d~%d (%d帧, %.2fs)" % (k, s, e - 1, e - s, (e - s) / fps))

    mpath = os.path.join(a.out_dir, "manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("清单: %s（共 %d 段）" % (mpath, len(manifest)))


if __name__ == "__main__":
    main()
