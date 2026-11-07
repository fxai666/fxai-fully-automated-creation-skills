# -*- coding: utf-8 -*-
"""把 B-roll 画面**替换**进成片的指定时间段，音轨与总时长都不变。

口播镜头里人物只能正面讲，缺「真的在做事」的画面。B-roll（敲键盘、翻手册、
插线缆、画白板这类操作特写）补的就是这一块——按段落切进去，观众看到的是
「她在场景里干活」，而不是「一个人站着念稿」。

**覆盖式插入**：B-roll 顶掉原画面，不插入、不加长，所以音画始终同步。

用法：
  python scripts/insert_broll.py --video 成片.mp4 --out 成片_b.mp4 \
      --spec broll.json

broll.json：
  [{"video": "video/broll_01.mp4", "start": 20.0, "dur": 5.0},
   {"video": "video/broll_02.mp4", "start": 62.0, "dur": 6.0}]

也可用命令行直给（可重复）：
  --insert "video/broll_01.mp4@20.0@5.0"
"""
import argparse
import json
import os
import subprocess
import sys

FF_CAND = ["ffmpeg"]


def ffmpeg_bin():
    try:
        cfg = json.load(open(os.environ.get("FXAI_GLOBAL_CONFIG")
                             or os.path.expanduser("~/.workbuddy/fxai_global.json"),
                             encoding="utf-8"))
        sd = cfg.get("software_install_dir")
        if sd:
            p = os.path.join(sd, "ffmpeg", "ffmpeg.exe")
            if os.path.isfile(p):
                return p
    except Exception:
        pass
    for p in FF_CAND:
        if p == "ffmpeg" or os.path.isfile(p):
            return p
    return "ffmpeg"


def ffprobe_bin():
    ff = ffmpeg_bin()
    d = os.path.dirname(ff)
    p = os.path.join(d, "ffprobe.exe") if d else ""
    return p if p and os.path.isfile(p) else "ffprobe"


def run(cmd):
    print("$", " ".join(str(c) for c in cmd[:8]), "...", flush=True)
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        sys.stderr.write((p.stderr or "")[-2500:] + "\n")
        raise RuntimeError("ffmpeg 失败（exit %d）" % p.returncode)
    return p


def probe_duration(path):
    p = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return float((p.stdout or "").strip())
    except ValueError:
        return 0.0


def probe_size(path):
    p = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height,r_frame_rate",
                        "-of", "csv=p=0", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        parts = (p.stdout or "").strip().split(",")
        w, h = int(parts[0]), int(parts[1])
        num, den = (parts[2].split("/") + ["1"])[:2]
        fps = float(num) / float(den or 1)
        return w, h, round(fps, 3)
    except Exception:
        return 768, 1280, 24.0


def load_spec(a):
    items = []
    if a.spec:
        items.extend(json.load(open(a.spec, encoding="utf-8")))
    for s in a.insert or []:
        v, st, du = s.rsplit("@", 2)
        items.append({"video": v, "start": float(st), "dur": float(du)})
    items.sort(key=lambda x: float(x["start"]))
    return items


def main():
    ap = argparse.ArgumentParser(description="把 B-roll 覆盖进成片指定时间段（音轨不变）")
    ap.add_argument("--video", required=True, help="主视频（人物口播成片）")
    ap.add_argument("--out", required=True, help="输出视频")
    ap.add_argument("--spec", help="JSON：数组，每项 {video, start, dur}")
    ap.add_argument("--insert", action="append", help="命令行直给：path@start@dur（可重复）")
    ap.add_argument("--crf", type=int, default=20)
    a = ap.parse_args()

    items = load_spec(a)
    if not items:
        sys.exit("没有插入项：给 --spec 或 --insert")

    total = probe_duration(a.video)
    W, H, FPS = probe_size(a.video)
    if total <= 0:
        sys.exit("读不到主视频时长：%s" % a.video)

    # 区间校验：不越界、不重叠
    prev_end = -1.0
    for it in items:
        st, du = float(it["start"]), float(it["dur"])
        if st < 0 or st + du > total + 0.05:
            sys.exit("插入区间越界：start=%.2f dur=%.2f 总长=%.2f" % (st, du, total))
        if st < prev_end - 0.01:
            sys.exit("插入区间重叠：%.2f 与上一段结束 %.2f" % (st, prev_end))
        prev_end = st + du
        if not os.path.isfile(it["video"]):
            sys.exit("B-roll 文件不存在：%s" % it["video"])
        d = probe_duration(it["video"])
        if d + 0.02 < du:
            sys.exit("B-roll 太短：%s 只有 %.2fs，需要 %.2fs" % (it["video"], d, du))

    # 构造 filter_complex：主视频按插入点切成若干段，B-roll 顶掉中间那一段
    inputs = ["-i", a.video]
    parts = []
    chain = []
    idx = 0
    prev = 0.0
    for n, it in enumerate(items):
        inputs += ["-i", it["video"]]
        st, du = float(it["start"]), float(it["dur"])
        chain.append("[0:v]trim=start=%.3f:end=%.3f,setpts=PTS-STARTPTS[v%d]" % (prev, st, idx))
        parts.append("[v%d]" % idx)
        idx += 1
        chain.append("[%d:v]trim=start=0:end=%.3f,setpts=PTS-STARTPTS,"
                     "scale=%d:%d,fps=%s,setsar=1[b%d]"
                     % (n + 1, du, W, H, FPS, idx))
        parts.append("[b%d]" % idx)
        idx += 1
        prev = st + du
    chain.append("[0:v]trim=start=%.3f,setpts=PTS-STARTPTS[v%d]" % (prev, idx))
    parts.append("[v%d]" % idx)

    fc = ";".join(chain) + ";" + "".join(parts) + "concat=n=%d:v=1:a=0[v]" % len(parts)
    cmd = [ffmpeg_bin(), "-y"] + inputs + [
        "-filter_complex", fc, "-map", "[v]", "-map", "0:a",
        "-c:v", "libx264", "-crf", str(a.crf), "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-c:a", "copy", a.out]
    run(cmd)

    d2 = probe_duration(a.out)
    print("[broll] %d 段 B-roll 已插入：%.2fs -> %.2fs（差 %.2fs）"
          % (len(items), total, d2, d2 - total), flush=True)
    print("[broll] 输出 -> %s" % a.out, flush=True)
    if abs(d2 - total) > 0.35:
        print("[broll] ⚠️ 时长差异偏大，检查 B-roll 帧率/尺寸是否与主视频一致", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
