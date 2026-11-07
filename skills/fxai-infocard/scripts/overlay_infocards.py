#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""信息贴片 · 叠加阶段：把 make_infocards.py 出的紧致半透明 PNG，
按 render.json 的时间轴叠到视频任意位置，带淡入/滑入/弹入 + 停留 + 淡出。

──── 机制（三条硬约束）────
· 每张 PNG 以 `-loop 1 -i png` 喂入，流时间轴与视频共用（t 从 0 起）。
  输出加 `-shortest`，否则无限循环的输入会让编码不结束。
· `fade=...:alpha=1` 必须放在 `scale` 之前：scale 用 eval=frame 逐帧改尺寸，
  变尺寸流会让后面的 fade 报 Invalid argument。顺序固定为：
      format=rgba → fade(in/out) → scale(pop 专用) → overlay
· pop 的缩放已在 scale 里做，overlay 用自带的 overlay_w/overlay_h 居中即可；
  overlay 的 x/y 写嵌套 if() 时本机 ffmpeg 报 Invalid argument。
· 保留原音轨（-c:a copy）；视频帧本身**从不逐帧编辑**，只是合成。

用法：
  python -B scripts/overlay_infocards.py --video in.mp4 \
         --render _tmp/infocards/render.json --out out.mp4
"""
import argparse, os, sys, json, subprocess

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cardkit as K

FADE = 0.3          # 淡入淡出时长（秒）
SLIDE_RATIO = 0.08  # 滑入位移占画布短边的比例


def video_duration(path):
    p = subprocess.run([K.ffprobe_bin(), "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return float(p.stdout.strip().splitlines()[0])
    except Exception:
        return 0.0


def build_chain(items, VW, VH):
    """返回 (inputs列表, filter_complex 字符串, 末节点tag)。"""
    inputs, chains = [], []
    prev = "[0:v]"
    OFF = SLIDE_RATIO * min(VW, VH)  # 滑入位移像素

    for k, it in enumerate(items, start=1):
        W, H = it["w"], it["h"]
        cx, cy = it["cx"], it["cy"]
        cxpx, cypx = cx * VW, cy * VH
        T, HOLD, anim = it["t"], it["hold"], it["anim"]
        src = "[%d:v]" % k
        inputs += ["-loop", "1", "-i", it["png"]]

        # pop 的缩放表达式（只用在 scale 里）
        S = "if(lt(t-%g\\,%.2f)\\,0.88+0.12*(t-%g)/%.2f\\,1)" % (T, FADE, T, FADE)

        chain = ("%sformat=rgba,fade=in:st=%g:d=%.2f:alpha=1,"
                 "fade=out:st=%g:d=%.2f:alpha=1"
                 % (src, T, FADE, T + HOLD - FADE, FADE))
        if anim == "pop":
            chain += (",scale=w='trunc(iw*%s)':h='trunc(ih*%s)':eval=frame" % (S, S))
        chain += "[fb%d]" % k
        chains.append(chain)

        baseX = cxpx - W / 2.0
        baseY = cypx - H / 2.0
        if anim == "slide-up":
            xexpr, yexpr = ("%g" % baseX,
                            "%g+(1-min((t-%g)/%.2f\\,1))*%g" % (baseY, T, FADE, OFF))
        elif anim == "slide-left":
            xexpr, yexpr = ("%g+(1-min((t-%g)/%.2f\\,1))*%g" % (baseX, T, FADE, OFF),
                            "%g" % baseY)
        elif anim == "slide-right":
            xexpr, yexpr = ("%g-(1-min((t-%g)/%.2f\\,1))*%g" % (baseX, T, FADE, OFF),
                            "%g" % baseY)
        elif anim == "pop":
            xexpr, yexpr = ("%g-overlay_w/2" % cxpx, "%g-overlay_h/2" % cypx)
        else:  # fade
            xexpr, yexpr = "%g" % baseX, "%g" % baseY

        chains.append("%s[fb%d]overlay=x='%s':y='%s':"
                      "enable='between(t\\,%g\\,%g)'[v%d]"
                      % (prev, k, xexpr, yexpr, T, T + HOLD, k))
        prev = "[v%d]" % k

    return inputs, ";".join(chains), prev


def overlay(video, render_json, out, crf=20):
    FF = K.ffmpeg_bin()
    items = json.load(open(render_json, encoding="utf-8")).get("items", [])
    if not items:
        print("没有任何信息贴片，直接复制视频")
        K.run([FF, "-y", "-i", video, "-c", "copy", out])
        return out
    VW, VH = K.video_size(video)
    if not VW:
        sys.exit("读不出视频尺寸：%s" % video)
    inputs, fc, last = build_chain(items, VW, VH)

    def build(enc):
        v = (["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
             if enc == "h264_nvenc" else
             ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf)])
        cmd = ([FF, "-y", "-i", video] + inputs
               + ["-filter_complex", fc, "-map", last])
        cmd += (["-map", "0:a", "-c:a", "copy"] if K.has_audio(video) else ["-an"])
        cmd += v + ["-pix_fmt", "yuv420p", "-shortest", out]
        return cmd

    try:
        K.run(build("h264_nvenc"))
    except SystemExit:
        print("[warn] nvenc 失败，回落 libx264")
        K.run(build("libx264"))
    print("信息贴片叠加完成 -> %s（%d 条）" % (out, len(items)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--render", required=True, help="make_infocards.py 出的 render.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--crf", type=int, default=20)
    a = ap.parse_args()
    overlay(a.video, a.render, a.out, a.crf)


if __name__ == "__main__":
    main()
