#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M 段 · 素材贴图镜头：一张图 → 一段镜头（缓推 / 缓移 / 静止）。

用途：截图、界面、图表、对比图、产品特写铺满画面，作为「给证据 / 给数据 / 给对比」的镜头，
与人物镜头（S/L/W）、演示空镜（E）、环境 B-roll（B）按分镜顺序拼接。

编码参数与成片对齐，可直接 concat：
  768×1280 / 24fps / yuv420p / h264；默认带 aac 24000Hz stereo 静音轨。

用法：
  python scripts/make_material_seg.py --image shot.png --dur 6 --out seg_M01.mp4
  python scripts/make_material_seg.py --image chart.png --dur 5 --motion pan-right \
         --size 1280x768 --out seg_M02.mp4
  python scripts/make_material_seg.py --image logo.png --dur 4 --fit contain --out seg_M03.mp4

运动方式：
  still      静止（带极轻微缓推，画面有呼吸感）
  zoom-in    缓推近（默认）
  zoom-out   缓拉远
  pan-left   缓左移
  pan-right  缓右移
"""
import argparse, os, subprocess, sys

FF_CAND = ["ffmpeg"]


def ffmpeg_bin():
    import json
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


def run(cmd):
    print("$", " ".join(str(c) for c in cmd[:6]), "...", flush=True)
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        print(p.stdout[-1200:]); print(p.stderr[-2000:])
        raise SystemExit("命令失败：%s" % cmd[0])


def build_filter(motion, W, H, dur, fps, fit):
    """返回 -vf 滤镜串。先按目标尺寸铺底，再做 zoompan 缓动。"""
    frames = max(2, int(round(dur * fps)))
    # 铺底：cover 裁满（可能裁边）/ contain 留边填黑
    if fit == "contain":
        base = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
                "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=0x101820" % (W, H, W, H))
    else:
        base = ("scale=%d:%d:force_original_aspect_ratio=increase,"
                "crop=%d:%d" % (W, H, W, H))
    # 放大 1.25 倍再 zoompan，留出移动余量，边缘不露黑
    pre = "scale=%d:%d" % (int(W * 1.25), int(H * 1.25))
    s = "s=%dx%d:fps=%d" % (W, H, fps)

    if motion == "zoom-in":
        z = "min(zoom+0.0010,1.18)"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif motion == "zoom-out":
        z = "if(eq(on,1),1.18,max(zoom-0.0010,1.0))"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif motion == "pan-left":
        z = "1.10"
        x = "(iw-iw/zoom)*(1-on/%d)" % max(1, frames - 1)
        y = "ih/2-(ih/zoom/2)"
    elif motion == "pan-right":
        z = "1.10"
        x = "(iw-iw/zoom)*(on/%d)" % max(1, frames - 1)
        y = "ih/2-(ih/zoom/2)"
    else:  # still
        z = "min(zoom+0.0004,1.06)"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"

    zp = "zoompan=z='%s':x='%s':y='%s':d=%d:%s" % (z, x, y, frames, s)
    return ",".join([base, pre, zp])


def make_segment(image, dur, out, motion="zoom-in", size="768x1280",
                 fps=24, fit="cover", silent_audio=True, crf=20):
    if not os.path.isfile(image):
        sys.exit("图片不存在：%s" % image)
    W, H = [int(x) for x in size.lower().split("x")]
    FF = ffmpeg_bin()
    vf = build_filter(motion, W, H, dur, fps, fit)

    cmd = [FF, "-y", "-loop", "1", "-i", image]
    if silent_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=24000:cl=stereo"]
    cmd += ["-vf", vf, "-t", "%.2f" % dur,
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-r", str(fps)]
    if silent_audio:
        cmd += ["-c:a", "aac", "-ar", "24000", "-ac", "2", "-shortest"]
    cmd += [out]
    run(cmd)
    print("M 段 -> %s（%s，%.1fs，%dx%d）" % (out, motion, dur, W, H))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--dur", type=float, required=True, help="镜头时长（秒）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--motion", default="zoom-in",
                    choices=["still", "zoom-in", "zoom-out", "pan-left", "pan-right"])
    ap.add_argument("--size", default="768x1280")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--fit", default="cover", choices=["cover", "contain"])
    ap.add_argument("--no-audio", action="store_true", help="不带静音轨")
    ap.add_argument("--crf", type=int, default=20)
    a = ap.parse_args()
    make_segment(a.image, a.dur, a.out, a.motion, a.size, a.fps,
                 a.fit, not a.no_audio, a.crf)


if __name__ == "__main__":
    main()
