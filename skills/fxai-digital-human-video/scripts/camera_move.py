#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""后期运镜：给**已生成的视频**补机位运动（推 / 拉 / 横移 / 摇 / 手持呼吸感）。

为什么不用提示词做运镜：multiref 把背景逐段重算，一旦提示词要求平移 / 拉远 / 环绕，
背景就被反复重算，表现为突然闪烁跳跃。后期是在成片像素上做变换，背景一次都不重算，
所以**生成层锁机位、机位运动交给本脚本**（分工见 references/08-镜头语言.md 4.4）。

用法：
  python scripts/camera_move.py --video 成片.mp4 --move pull-out --out 成片_move.mp4
  python scripts/camera_move.py --video a.mp4 --move pan-right --amount 0.10 --out b.mp4
  python scripts/camera_move.py --video a.mp4 --move push-in --handheld 1.2 --out c.mp4

运动方式（--move）：
  still       极轻微呼吸推（几乎看不出动，但画面不是死帧）
  push-in     缓推近（推的落幅 = 1+amount）
  pull-out    缓拉远（起幅紧、落幅松，常用来做开场「拉开交代环境」）
  pan-left    缓左移（画面内容向右滑，镜头往左走）
  pan-right   缓右移
  tilt-up     缓上摇
  tilt-down   缓下摇
  drift-tl    斜向漂移（左上）
  drift-br    斜向漂移（右下）
  handheld    机位不动，只加轻微手持晃动

缓动默认开（--no-ease 关）：线性匀速推拉看着像机器臂，smoothstep 才像人操机。
"""
import argparse, json, os, subprocess, sys

sys.dont_write_bytecode = True

FF_CAND = ["ffmpeg"]

MOVES = ("still", "push-in", "pull-out", "pan-left", "pan-right",
         "tilt-up", "tilt-down", "drift-tl", "drift-br", "handheld")


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


def probe(video):
    """返回 (宽, 高, 帧率, 时长秒)。"""
    p = subprocess.run([ffmpeg_bin().replace("ffmpeg.exe", "ffprobe.exe"),
                        "-v", "quiet", "-print_format", "json",
                        "-show_streams", "-show_format", video],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    d = json.loads(p.stdout or "{}")
    w = h = fps = 0
    dur = float(d.get("format", {}).get("duration") or 0)
    for s in d.get("streams", []):
        if s.get("codec_type") == "video":
            w, h = int(s["width"]), int(s["height"])
            num, den = (s.get("r_frame_rate") or "24/1").split("/")
            fps = float(num) / float(den or 1)
            if not dur:
                dur = float(s.get("duration") or 0)
    return w, h, (fps or 24.0), dur


def _ease(expr_p):
    """smoothstep：把线性进度 p 换成「起步慢、中间快、收尾慢」。"""
    return "(1-cos(PI*(%s)))/2" % expr_p


def build_zoompan(move, W, H, frames, fps, amount=0.12, ease=True,
                  handheld=0.0, pad=1.3):
    """返回 zoompan 滤镜串。输入先按 pad 倍超采样，把整像素取整的抖动压到亚像素。"""
    n = max(1, frames - 1)
    p = "on/%d" % n
    e = _ease(p) if ease else p
    # 拉远要反过来：起幅紧、落幅松
    e_rev = "(1-%s)" % e

    z_fix = "%.4f" % (1.0 + amount)
    cx = "iw/2-(iw/zoom/2)"
    cy = "ih/2-(ih/zoom/2)"
    xmax = "(iw-iw/zoom)"
    ymax = "(ih-ih/zoom)"

    if move == "push-in":
        z, x, y = "1+%.4f*(%s)" % (amount, e), cx, cy
    elif move == "pull-out":
        z, x, y = "%.4f-%.4f*(%s)" % (1.0 + amount, amount, e), cx, cy
    elif move == "pan-left":
        z, x, y = z_fix, "%s*(1-%s)" % (xmax, e), cy
    elif move == "pan-right":
        z, x, y = z_fix, "%s*(%s)" % (xmax, e), cy
    elif move == "tilt-up":
        z, x, y = z_fix, cx, "%s*(1-%s)" % (ymax, e)
    elif move == "tilt-down":
        z, x, y = z_fix, cx, "%s*(%s)" % (ymax, e)
    elif move == "drift-tl":
        z, x, y = z_fix, "%s*(1-%s)" % (xmax, e), "%s*(1-%s)" % (ymax, e)
    elif move == "drift-br":
        z, x, y = z_fix, "%s*(%s)" % (xmax, e), "%s*(%s)" % (ymax, e)
    elif move == "handheld":
        z = "1.0"          # 只晃不缩放，视野与原片一致
        x, y = cx, cy
    else:  # still
        z, x, y = "1+%.4f*(%s)" % (max(amount, 0.02) * 0.5, e), cx, cy

    # z=1.0 时 zoompan 把超采样后的输入按 cover 方式填满输出，视野与原片一致（实测
    # 同帧像素差 0.6/255），所以 pad 不需要在 z 里补偿；z>1 才是真的推近。

    # 手持呼吸感：两个不同频率的正弦叠加，去掉机械感；幅度按画面宽高比给
    if handheld:
        ax = 0.004 * handheld * W
        ay = 0.004 * handheld * H
        x = "(%s)+%.2f*sin(on*0.63)+%.2f*sin(on*1.97)" % (x, ax, ax * 0.45)
        y = "(%s)+%.2f*sin(on*0.51+1.2)+%.2f*sin(on*1.71)" % (y, ay, ay * 0.45)

    return ("zoompan=z='%s':x='%s':y='%s':d=1:s=%dx%d:fps=%.3f"
            % (z, x, y, W, H, fps))


def run(cmd):
    print("$", " ".join(str(c) for c in cmd[:8]), "...", flush=True)
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        print(p.stdout[-800:])
        print(p.stderr[-2000:])
        raise SystemExit("命令失败：%s" % cmd[0])


def apply_move(video, out, move="pull-out", amount=0.12, ease=True,
               handheld=0.0, crf=18, size=None, pad=1.3):
    if move not in MOVES:
        sys.exit("--move 只能是 %s" % " / ".join(MOVES))
    if not os.path.isfile(video):
        sys.exit("视频不存在：%s" % video)
    w, h, fps, dur = probe(video)
    if not w:
        sys.exit("读不到视频尺寸：%s" % video)
    W, H = (int(x) for x in size.lower().split("x")) if size else (w, h)
    frames = max(2, int(round(dur * fps)))

    zp = build_zoompan(move, W, H, frames, fps, amount, ease, handheld, pad)
    # 超采样再变换：zoompan 的 x/y 取整会带来 1px 级抖动，先放大就变成亚像素
    vf = "scale=%d:%d:flags=lanczos,%s" % (int(W * pad), int(H * pad), zp)

    cmd = [ffmpeg_bin(), "-y", "-i", video, "-vf", vf,
           "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
           "-pix_fmt", "yuv420p", "-r", "%.3f" % fps,
           "-c:a", "copy", "-movflags", "+faststart", out]
    run(cmd)
    print("后期运镜 -> %s（%s，幅度 %.0f%%，%dx%d，%.2fs%s）"
          % (out, move, amount * 100, W, H, dur,
             "，手持 %.1f" % handheld if handheld else ""))
    return out


def _cut(video, start, dur, out, crf, fps):
    """按时间段切一段（重编码，参数与 apply_move 一致，便于 concat）。"""
    cmd = [ffmpeg_bin(), "-y"]
    if start:
        cmd += ["-ss", "%.3f" % start]
    cmd += ["-i", video]
    if dur:
        cmd += ["-t", "%.3f" % dur]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-r", "%.3f" % fps,
            "-c:a", "aac", "-ar", "24000", "-ac", "2", "-movflags", "+faststart", out]
    run(cmd)
    return out


def apply_move_range(video, out, move, amount, ease, handheld, crf, size,
                     start, dur, pad=1.3):
    """只给 [start, start+dur) 这一段加运镜，其余原样保留。

    开场拉远、结尾推近这类「运镜是标点」的用法，整片都推就过了，
    用这个只动需要动的那几秒。
    """
    import tempfile, shutil
    w, h, fps, total = probe(video)
    W, H = (int(x) for x in size.lower().split("x")) if size else (w, h)
    end = min(total, start + dur)
    if start <= 0 and end >= total - 0.05:
        return apply_move(video, out, move, amount, ease, handheld, crf, size, pad)

    tmp = tempfile.mkdtemp(prefix="cammove_")
    try:
        parts = []
        if start > 0.02:
            parts.append(_cut(video, 0, start, os.path.join(tmp, "a.mp4"), crf, fps))
        mid = _cut(video, start, end - start, os.path.join(tmp, "m.mp4"), crf, fps)
        moved = os.path.join(tmp, "mv.mp4")
        frames = max(2, int(round((end - start) * fps)))
        zp = build_zoompan(move, W, H, frames, fps, amount, ease, handheld, pad)
        vf = "scale=%d:%d:flags=lanczos,%s" % (int(W * pad), int(H * pad), zp)
        run([ffmpeg_bin(), "-y", "-i", mid, "-vf", vf,
             "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
             "-pix_fmt", "yuv420p", "-r", "%.3f" % fps,
             "-c:a", "copy", "-movflags", "+faststart", moved])
        parts.append(moved)
        if end < total - 0.02:
            parts.append(_cut(video, end, total - end, os.path.join(tmp, "b.mp4"), crf, fps))
        lst = os.path.join(tmp, "list.txt")
        open(lst, "w", encoding="utf-8").write(
            "".join("file '%s'\n" % p.replace("\\", "/") for p in parts))
        run([ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0", "-i", lst,
             "-c", "copy", "-movflags", "+faststart", out])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("后期运镜 -> %s（%s，%.2f~%.2fs，幅度 %.0f%%%s）"
          % (out, move, start, end, amount * 100,
             "，手持 %.1f" % handheld if handheld else ""))
    return out


def main():
    ap = argparse.ArgumentParser(
        description="后期运镜：给已生成的视频补机位运动（生成层锁机位，机位运动在这里做）")
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--move", default="pull-out", choices=list(MOVES))
    ap.add_argument("--amount", type=float, default=0.12,
                    help="运动幅度（推拉为缩放比例，默认 0.12 = 12%%；横移/摇为可移动范围的占比）")
    ap.add_argument("--handheld", type=float, default=0.0,
                    help="叠加手持呼吸感强度（0 关，1 轻微，2 明显）")
    ap.add_argument("--no-ease", dest="ease", action="store_false", default=True,
                    help="关掉缓动（默认开：起步慢、中间快、收尾慢）")
    ap.add_argument("--size", help="输出尺寸，如 768x1280（默认沿用源尺寸）")
    ap.add_argument("--start", type=float, default=0.0,
                    help="只给这一段时间加运镜（配合 --dur；不给 dur 则到片尾）")
    ap.add_argument("--dur", type=float, default=0.0, help="运镜区间时长（秒）")
    ap.add_argument("--crf", type=int, default=18)
    a = ap.parse_args()
    if a.dur > 0 or a.start > 0:
        apply_move_range(a.video, a.out, a.move, a.amount, a.ease, a.handheld,
                         a.crf, a.size, a.start, a.dur or 1e9)
    else:
        apply_move(a.video, a.out, a.move, a.amount, a.ease, a.handheld,
                   a.crf, a.size)


if __name__ == "__main__":
    main()
