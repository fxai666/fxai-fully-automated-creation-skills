# -*- coding: utf-8 -*-
"""多镜头源片 · 逐镜替换结果按帧号嵌回（逐镜切段替换法的步 4~5）。

用法：
    python shot_assemble.py SRC --manifest DIR/manifest.json --seg-dir DIR/seg \
        --swap 0,1,2,5,6,7 --out OUT.mp4 [--fps 30]

--swap 列出的镜号用 seg-dir 里的替换段（文件名 镜NN.mp4 或 shot_NN.mp4），
其余镜号从源片按帧号裁同帧数回填。每段规整到清单帧数（多退少补、clone 尾帧），
concat 只拼画面，音轨整条从源片取，锁到清单总帧数。
seg-dir 之外的过程文件落在清单所在目录，源片保持只读。
"""
import argparse
import json
import os
import subprocess


def run(cmd, label):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("%s失败: %s" % (label, r.stderr[-400:]))


def find_seg(seg_dir, k):
    for name in ("镜%02d.mp4" % k, "shot_%02d.mp4" % k, "shot%02d.mp4" % k):
        p = os.path.join(seg_dir, name)
        if os.path.isfile(p):
            return p
    return None


def main():
    ap = argparse.ArgumentParser(description="逐镜替换结果按帧号嵌回")
    ap.add_argument("src", help="源视频路径（音轨取自它）")
    ap.add_argument("--manifest", required=True, help="shot_split.py 产出的清单")
    ap.add_argument("--seg-dir", required=True, help="替换段所在目录")
    ap.add_argument("--swap", required=True, help="逗号分隔的替换镜号，其余镜号回填原片")
    ap.add_argument("--out", required=True, help="成片输出路径")
    ap.add_argument("--fps", type=float, default=0, help="覆盖清单推算的帧率")
    a = ap.parse_args()

    with open(a.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    swap = set(int(x) for x in a.swap.split(",") if x.strip())
    fps = a.fps or 30.0
    total = sum(m["frames"] for m in manifest)
    base = os.path.dirname(os.path.abspath(a.manifest))
    work = os.path.join(base, "_assemble")
    os.makedirs(work, exist_ok=True)

    parts = []
    for m in manifest:
        k, n, s, e = m["idx"], m["frames"], m["start_frame"], m["end_frame"]
        dst = os.path.join(work, "norm_%02d.mp4" % k)
        if k in swap:
            src = find_seg(a.seg_dir, k)
            if src is None:
                raise SystemExit("缺替换段: 镜%02d（在 %s 下未找到）" % (k, a.seg_dir))
            vf = "trim=end_frame=%d,setpts=N/(%g*TB),tpad=stop_mode=clone:stop=%d" % (n, fps, n)
            run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vf", vf,
                 "-frames:v", str(n), "-an", "-c:v", "libx264", "-crf", "17",
                 "-preset", "fast", "-r", "%g" % fps, dst], "镜%02d 锁帧" % k)
        else:
            vf = "select='between(n\\,%d\\,%d)',setpts=N/(%g*TB)" % (s, e - 1, fps)
            run(["ffmpeg", "-y", "-v", "error", "-i", a.src, "-vf", vf, "-an",
                 "-c:v", "libx264", "-crf", "17", "-preset", "fast",
                 "-r", "%g" % fps, dst], "镜%02d 回填" % k)
        parts.append(dst)
        print("镜%02d: %s（%d帧）" % (k, "替换" if k in swap else "回填", n))

    listf = os.path.join(work, "concat_list.txt")
    with open(listf, "w", encoding="utf-8") as f:
        for p in parts:
            f.write("file '%s'\n" % p.replace("\\", "/"))
    video = os.path.join(work, "video_only.mp4")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", listf,
         "-c:v", "libx264", "-crf", "17", "-preset", "fast", "-r", "%g" % fps,
         "-an", video], "concat")

    vf = "trim=end_frame=%d,setpts=N/(%g*TB)" % (total, fps)
    run(["ffmpeg", "-y", "-v", "error", "-i", video, "-i", a.src,
         "-filter_complex", "[0:v]%s[v]" % vf,
         "-map", "[v]", "-map", "1:a:0", "-frames:v", str(total),
         "-c:v", "libx264", "-crf", "17", "-preset", "fast",
         "-c:a", "aac", "-r", "%g" % fps, "-movflags", "+faststart", a.out], "合成")
    print("成片: %s（%d帧）" % (a.out, total))


if __name__ == "__main__":
    main()
