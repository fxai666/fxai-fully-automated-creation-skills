# -*- coding: utf-8 -*-
"""
视频自动剪辑 + 字幕烧录 + 转场拼接（通用脚本）
================================================
用法:
  python build_highlight.py --video IN.mp4 --config segments.json --out OUT.mp4
                          [--ffmpeg <软件安装目录>/ffmpeg/ffmpeg.exe]
                          [--ffprobe <软件安装目录>/ffmpeg/ffprobe.exe]

segments.json:
{
  "resolution": [960, 1280],   // 可选，缺省自动 ffprobe 探测
  "xfade": 0.6,                // 转场时长(秒)，默认 0.6
  "afade": 0.5,                // 首段淡入/末段淡出时长(秒)，默认 0.5
  "segments": [
    {
      "start": 96.1, "end": 129.1,
      "title": "中秋不中秋",                                 // 章节标题(可选)
      "lines": [[96.3, 101.3, "中秋不中秋，与我也不过是"], ...]  // 歌词/字幕，时间为原片绝对时间
    }
  ]
}
注意:
  - lines 里的时间戳是「原片绝对时间」，脚本内部减去 start 转成段内时间。
  - ffmpeg 必须带 libass（subtitles 滤镜）。conda 自带 ffmpeg 7.0.1 没有，请用 gyan 全量版。
  - ASS 文件 UTF-8 无 BOM，字幕中文用 Microsoft YaHei。
"""
import os
import sys
import json
import shutil
import tempfile
import subprocess
import argparse

# ffmpeg 由全局部署配置（~/.workbuddy/fxai_global.json）的 software_install_dir 推导，不写死本机路径。
_GLOBAL_CFG_PATH = os.environ.get(
    "FXAI_GLOBAL_CONFIG",
    os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json"))

def _load_cfg():
    try:
        with open(_GLOBAL_CFG_PATH, encoding="utf-8") as _f:
            return json.load(_f)
    except Exception:
        return {}

_CFG = _load_cfg()
_SOFT_INSTALL = _CFG.get("software_install_dir", "")

def _detect_ffmpeg():
    """ffmpeg 默认随软件安装目录自带（带 libass）。"""
    if _SOFT_INSTALL:
        c = os.path.join(_SOFT_INSTALL, "ffmpeg", "ffmpeg.exe")
        if os.path.isfile(c):
            return os.path.normpath(c)
    return "ffmpeg"  # 兜底：依赖 PATH

# 本机带 libass 的 ffmpeg（conda 自带那版没有 subtitles 滤镜），由软件安装目录自动推导
DEFAULT_FFMPEG = _CFG.get("ffmpeg", "") or _detect_ffmpeg()

ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Lyric,Microsoft YaHei,54,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,2.5,1,2,40,40,64,1
Style: Title,Microsoft YaHei,72,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,8,40,40,150,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def run(cmd, cwd=None):
    print(">> " + " ".join(cmd)[:200], flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        sys.stderr.write("FAILED:\n" + r.stderr[-2000:] + "\n")
        sys.exit(1)


def get_duration(p, ffprobe_exe):
    r = subprocess.run([ffprobe_exe, "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", p],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def get_resolution(video, ffprobe_exe):
    r = subprocess.run([ffprobe_exe, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0", video],
                       capture_output=True, text=True)
    w, h = r.stdout.strip().split(",")
    return int(w), int(h)


def ts(t):
    t = max(0.0, float(t))
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return "%d:%02d:%05.2f" % (h, m, s)


def esc(text):
    # ASS 文本里的大括号需转义；换行用 \N
    return text.replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ffmpeg", default=DEFAULT_FFMPEG)
    ap.add_argument("--ffprobe", default="")
    args = ap.parse_args()

    ffmpeg = args.ffmpeg
    ffprobe_exe = args.ffprobe or os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe")
    if not os.path.exists(ffprobe_exe):
        ffprobe_exe = "ffprobe"

    # 渲染分段时 cwd 会被切到临时目录，输入/输出必须用绝对路径，否则 ffmpeg 找不到文件
    args.video = os.path.abspath(args.video)
    args.out = os.path.abspath(args.out)

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)

    W, H = cfg.get("resolution") or list(get_resolution(args.video, ffprobe_exe))
    xfade = float(cfg.get("xfade", 0.6))
    afade = float(cfg.get("afade", 0.5))
    segs = cfg["segments"]
    n = len(segs)
    if n == 0:
        sys.stderr.write("segments 为空\n")
        sys.exit(1)

    build = tempfile.mkdtemp(prefix="hl_")
    seg_files = []
    durations = []

    try:
        for i, seg in enumerate(segs):
            start = float(seg["start"])
            end = float(seg["end"])
            d = end - start
            if d <= 0:
                sys.stderr.write("第 %d 段时长无效\n" % i)
                sys.exit(1)
            title = seg.get("title", "")
            events = []
            if title:
                events.append("Dialogue: 0,%s,%s,Title,,0,0,0,,{\\fad(300,300)}%s"
                              % (ts(0.3), ts(min(3.8, d - 0.3)), esc(title)))
            for a, b, text in seg.get("lines", []):
                events.append("Dialogue: 0,%s,%s,Lyric,,0,0,0,,{\\fad(150,150)}%s"
                              % (ts(float(a) - start), ts(float(b) - start), esc(text)))
            ass = os.path.join(build, "seg%d.ass" % i)
            with open(ass, "w", encoding="utf-8") as f:
                f.write(ASS_HEAD.format(W=W, H=H) + "\n".join(events) + "\n")

            vf = "subtitles=filename=seg%d.ass" % i
            if i == 0:
                vf += ",fade=t=in:st=0:d=%.2f" % afade
            if i == n - 1:
                vf += ",fade=t=out:st=%.2f:d=%.2f" % (d - afade, afade)
            af = []
            if i == 0:
                af.append("afade=t=in:st=0:d=%.2f" % afade)
            if i == n - 1:
                af.append("afade=t=out:st=%.2f:d=%.2f" % (d - afade, afade))

            out_seg = os.path.join(build, "seg%d.mp4" % i)
            cmd = [ffmpeg, "-y", "-ss", "%.2f" % start, "-to", "%.2f" % end,
                   "-i", args.video, "-vf", vf,
                   "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
            if af:
                cmd += ["-af", ",".join(af)]
            cmd.append(out_seg)
            run(cmd, cwd=build)
            durations.append(get_duration(out_seg, ffprobe_exe))
            seg_files.append(out_seg)

        if n == 1:
            shutil.copy(seg_files[0], args.out)
            print("DONE (single) -> %s" % args.out)
            return

        # 多段 xfade 拼接
        parts_v = []
        parts_a = []
        for i in range(1, n):
            acc = sum(durations[:i])
            off = acc - xfade
            inp_prev_v = "[v%d]" % (i - 1) if i > 1 else "[0:v]"
            out_label_v = "[v%d]" % i
            trans = "fade" if i < n - 1 else "fadeblack"
            parts_v.append("%s[%d:v]xfade=transition=%s:duration=%.3f:offset=%.3f%s"
                           % (inp_prev_v, i, trans, xfade, off, out_label_v))
            inp_prev_a = "[a%d]" % (i - 1) if i > 1 else "[0:a]"
            out_label_a = "[a%d]" % i
            parts_a.append("%s[%d:a]acrossfade=d=%.3f%s"
                           % (inp_prev_a, i, xfade, out_label_a))

        fc = ";".join(parts_v + parts_a)
        final_v = "[v%d]" % (n - 1)
        final_a = "[a%d]" % (n - 1)
        cmd = [ffmpeg, "-y"]
        for sf in seg_files:
            cmd += ["-i", sf]
        cmd += ["-filter_complex", fc, "-map", final_v, "-map", final_a,
                "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", args.out]
        run(cmd)
        print("DONE (%d segs, %.2fs) -> %s" % (n, get_duration(args.out, ffprobe_exe), args.out))
    finally:
        shutil.rmtree(build, ignore_errors=True)


if __name__ == "__main__":
    main()
