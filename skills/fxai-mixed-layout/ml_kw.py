# -*- coding: utf-8 -*-
"""fxai-mixed-layout · 关键词动效层（kw 层）

上中下分栏（参考片风格）里，中区画面上会压一颗**超大白字关键词**：白字 + 厚黑描边，
从外部进入放大落定，停到该镜结尾再轻微放大消散。参考片里就是「第一个坑」「能谈」这种。

它与媒体、壳三层正交：**媒体在底层、kw 层烧在媒体之上、字幕在最上**。
shell（上下带）不动，媒体推近/切换，关键词跟着镜头内容走 —— 动的东西各归各的层。

实现选型：PIL 逐帧出带 alpha 的 PNG 序列，ffmpeg overlay 烧进片子。
不用 drawtext 表达式是因为它无法缩放字号 —— 「从外部进入放大」的放大是这套动效的灵魂。
零 fxai 依赖：ffmpeg / 编码器由调用方 configure() 注入（与 ml_slots 同规矩）。
"""
import json
import os
import pathlib
import subprocess

CFG = {
    "ffmpeg": "ffmpeg",
    "ffprobe": "",
    "encoder": "libx264",
    "fps": 25,
    "font_chain": None,      # 覆盖字体候选链（PIL 用文件路径）；None = 用 FONT_CHAIN
}

# 字体候选链（粗黑系）：得意黑 → 微软雅黑粗 → 黑体。授权白名单见 fxai-standard 06 册。
# 得意黑放在兄弟技能 fxai-slide-video 里，按「本文件 → 技能根 → skills 根」相对定位，换机器/换 skills 目录都跟着走。
FONT_CHAIN = [
    pathlib.Path(__file__).resolve().parents[1] / "fxai-slide-video" / "assets" / "fonts"
    / "SmileySans-Oblique.otf",
    pathlib.Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "Fonts" / "msyhbd.ttc",
    pathlib.Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "Fonts" / "simhei.ttf",
]

KW_FILL = (255, 255, 255, 255)          # 字本体：白（可被 cue 里的 color 覆盖）
KW_STROKE = (8, 8, 8, 255)              # 描边：近黑（亮画面上读得清）

# 🔴 **禁用黑描边**（2026-10-25 用户两次强调：「我觉得没必要要描边」「描边把我们画面挡完了」）。
#    字够不够厚靠「同色外扩」解决 —— 同色描边在视觉上就是字变粗，不会多出一圈黑。
#    字为什么要透着：中区放的是人/画面，字压到不透明就等于糊了一块膏药，所以峰值 alpha 0.8。
KW_STROKE_PCT = 0.0       # 可见黑边宽 / 字号：0 = 完全不做黑边
KW_BOLD_PCT = 0.018       # 同色加粗（字芯外扩）/ 字号
KW_PEAK_ALPHA = 0.80      # 停留时的峰值不透明度（用户 2026-10-25 定：0.8，后面的人要看得见）

IN_DUR = 0.45        # 进入：从 1.9 倍收回 + 淡入（用户描述「从画外、从大、从透明到 1」）
OUT_DUR = 0.40       # 退场：放大 + 淡出 + 起雾（参考片退场明显发虚）
IN_SCALE = 1.9       # 进入起始倍率（外部进入的「外部」）
OUT_SCALE = 1.12     # 退场结束倍率
OUT_BLUR = 5.0       # 退场模糊半径（像素，按 1080 宽标定，运行时按画布宽缩放）


def configure(ffmpeg=None, ffprobe=None, encoder=None, fps=None, font_chain=None):
    """font_chain: 字体文件路径列表（按优先级）。壳用 SimHei 时这里也传 simhei.ttf，
    中区关键词才和上下带是同一套字 —— 否则壳是黑体、花字是得意黑，一眼两套东西。"""
    if ffmpeg:
        CFG["ffmpeg"] = str(ffmpeg)
    if ffprobe is not None:
        CFG["ffprobe"] = str(ffprobe)
    if encoder:
        CFG["encoder"] = str(encoder)
    if fps:
        CFG["fps"] = int(fps)
    if font_chain:
        CFG["font_chain"] = [pathlib.Path(x) for x in font_chain]
    return dict(CFG)


def _ffmpeg_bin():
    return CFG["ffmpeg"]


def _font(size):
    """按候选链取第一个存在的字体，按目标字号加载；全缺退 PIL 默认（无 .path，勿再包一层）。"""
    from PIL import ImageFont
    for p in (CFG.get("font_chain") or FONT_CHAIN):
        if p.is_file():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def _hex_rgb(v):
    """#RRGGBB → (r,g,b)；已是元组则原样返回。"""
    v = str(v).strip().lstrip("#")
    if len(v) == 6:
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    return KW_FILL[:3]


_POS = {
    "mid": (0.50, 0.44),    # 中区中部偏上（字幕压 0.79 处，互不打架）
    "low": (0.50, 0.60),
    "up": (0.50, 0.30),
}


def _kw_layer(text, size, w, h, font_cache, style=None):
    """按最大动画尺寸（size × IN_SCALE）预渲染一张 RGBA 字层，帧循环里只缩放不重画。

    style 可选键：color / stroke（RGB 或 RGBA 元组）、stroke_pct、bold_pct。
    默认 **不画黑边**（KW_STROKE_PCT = 0）：只把字芯按同色往外扩一点（字才够厚）。
    要黑边（亮底上偶尔需要）就在 cue 的 style 里传 stroke_pct，例如 0.03。
    """
    from PIL import Image, ImageDraw
    st = style or {}
    fill = tuple(st.get("color") or KW_FILL)
    stroke = tuple(st.get("stroke") or KW_STROKE)
    if len(fill) == 3:
        fill = fill + (255,)
    if len(stroke) == 3:
        stroke = stroke + (255,)
    sw = max(0, round(size * float(st.get("stroke_pct", KW_STROKE_PCT))))   # 0 = 不画黑边
    bd = max(0, round(size * float(st.get("bold_pct", KW_BOLD_PCT))))
    f = font_cache.get(size)
    if f is None:
        f = _font(size)
        font_cache[size] = f
    outer = sw + bd                       # 黑边层外扩量 = 可见黑边 + 同色加粗
    tmp = Image.new("RGBA", (8, 8))
    d = ImageDraw.Draw(tmp)
    box = d.textbbox((0, 0), text, font=f, stroke_width=outer)
    tw, th = box[2] - box[0] + outer * 4, box[3] - box[1] + outer * 4
    pad = int(size * (IN_SCALE - 1.0)) + outer * 2 + int(h * 0.08)
    lay = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    xy = (pad - box[0] + outer, pad - box[1] + outer)
    if sw > 0:                            # 只有明确要黑边时才画这一层
        d.text(xy, text, font=f, fill=stroke, stroke_width=outer, stroke_fill=stroke)
    if bd > 0:                            # 字芯 + 同色外扩（视觉上加粗，不留黑边）
        d.text(xy, text, font=f, fill=fill, stroke_width=bd, stroke_fill=fill)
    else:
        d.text(xy, text, font=f, fill=fill)
    return lay


def _fit_size(text, w, h):
    """关键词字号：不超过中区高的 30%（参考片「能谈」实测约 32%），且总宽不超 74% 画布宽。"""
    from PIL import Image, ImageDraw
    f = _font(64)
    tmp = Image.new("RGBA", (8, 8))
    d = ImageDraw.Draw(tmp)
    n = max(1, len(text.replace(" ", "")))
    by_w = (w * 0.74) / (n * 1.06)
    return max(28, int(min(h * 0.30, by_w)))


def kw_burn(src, cues, out, w, h, fps=None, watermark=None, tmpdir=None):
    """把关键词层烧进 src（w×h 无声或带音视频），写 out。

    cues     [(start, end, text, pos[, style])]，pos ∈ "mid"/"low"/"up" 或 (x_frac, y_frac)。
             时间相对 src 起点；end 可超过时长（自动截断）。
             style 可选：{"color": (r,g,b) | "#RRGGBB", "stroke": ..., "alpha": 0.9,
                          "stroke_pct": .045, "bold_pct": .02, "pos": ...}
             颜色按参考片的做法是**可变的**，不必一律白字黑边。
    watermark {"text": ..., "pos": (x, y), "alpha": 0.85} 常驻角标（参考片 @账号 位）。
    """
    from PIL import Image
    ofps = int(fps or CFG["fps"])
    norm = []
    for c in cues:
        a, b, t = float(c[0]), float(c[1]), str(c[2])
        p = c[3] if len(c) > 3 else "mid"
        st = dict(c[4]) if len(c) > 4 and isinstance(c[4], dict) else {}
        if isinstance(p, (list, tuple)):
            xy = (int(w * p[0]), int(h * p[1]))
        else:
            fx, fy = _POS.get(p, _POS["mid"])
            xy = (int(w * fx), int(h * fy))
        for k in ("color", "stroke"):
            if isinstance(st.get(k), str):
                st[k] = _hex_rgb(st[k])
        norm.append((a, b, t, xy, st))
    cues = norm
    wm = None
    if watermark and watermark.get("text"):
        wm = {"lay": _kw_layer(str(watermark["text"]), max(20, int(h * 0.034)), w, h, {}),
              "xy": (int(w * watermark.get("pos", (0.045, 0.06))[0]),
                     int(h * watermark.get("pos", (0.045, 0.06))[1])),
              "alpha": float(watermark.get("alpha", 0.85))}
    if not cues and wm is None:
        return src

    # 总帧数：按 src 时长（ffprobe 读不到就按 0 处理 → 上层保证 src 有时长）
    pr = subprocess.run([CFG["ffprobe"] or str(pathlib.Path(_ffmpeg_bin()).with_name("ffprobe.exe")),
                         "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                         str(src)], capture_output=True, text=True)
    total = float((pr.stdout or "0").strip() or 0)
    nfr = max(1, int(round(total * ofps)))
    td = pathlib.Path(tmpdir or (pathlib.Path(str(out)).parent / "_kw"))
    td.mkdir(parents=True, exist_ok=True)
    for old in td.glob("kw_*.png"):
        old.unlink()

    font_cache = {}
    layers = [(_kw_layer(t, _fit_size(t, w, h), w, h, font_cache, st),
               a, b, xy, float(st.get("alpha") or KW_PEAK_ALPHA))
              for a, b, t, xy, st in cues]
    layers = [L for L in layers if L[0] is not None]
    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    for i in range(nfr):
        t = i / ofps
        frame = base.copy()
        for lay, a, b, (cx, cy), peak in layers:
            if t < a - 0.001 or t >= b:
                continue
            lt = t - a
            dur = max(0.001, b - a)
            blur = 0.0
            if lt < IN_DUR:                       # 进入：从画外放大收回 + 由透明到实
                p = max(0.0, min(1.0, lt / IN_DUR))
                ease = 1.0 - (1.0 - p) ** 3
                sc = IN_SCALE - (IN_SCALE - 1.0) * ease
                dy = int(-h * 0.03 * (1.0 - ease))
                ga = ease * peak
            elif lt > dur - OUT_DUR:              # 退场：放大 + 淡出 + 起雾
                p = max(0.0, min(1.0, (lt - (dur - OUT_DUR)) / OUT_DUR))
                ease = p * p
                sc = 1.0 + (OUT_SCALE - 1.0) * ease
                dy = 0
                ga = peak * (1.0 - ease)
                blur = OUT_BLUR * (w / 1080.0) * ease
            else:                                 # 停留（峰值不是 1，让画面透出一点）
                sc, dy, ga = 1.0, 0, peak
            if ga <= 0.01:
                continue
            tw = max(1, int(lay.width * sc))
            th = max(1, int(lay.height * sc))
            piece = lay.resize((tw, th), Image.LANCZOS)
            if blur > 0.4:
                from PIL import ImageFilter
                piece = piece.filter(ImageFilter.GaussianBlur(blur))
            if ga < 0.999:                        # 整体透明度（PIL 无直接乘 alpha，走 putalpha）
                al = piece.split()[3].point(lambda v, g=ga: int(v * g))
                piece.putalpha(al)
            frame.alpha_composite(piece, (cx - tw // 2, cy - th // 2 + dy))
        if wm is not None:                        # 角标常驻：跟随帧透明底贴一次
            piece = wm["lay"]
            if wm["alpha"] < 0.999:
                al = piece.split()[3].point(lambda v, g=wm["alpha"]: int(v * g))
                piece = piece.copy()
                piece.putalpha(al)
            frame.alpha_composite(piece, wm["xy"])
        frame.save(td / ("kw_%05d.png" % i))

    # overlay：序列帧是全片时长，主输入到哪帧就贴哪帧
    enc = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "20", "-b:v", "0"] \
        if CFG["encoder"] == "h264_nvenc" else \
        ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]
    cmd = [_ffmpeg_bin(), "-y", "-v", "error", "-i", str(src),
           "-framerate", str(ofps), "-i", str(td / "kw_%05d.png"),
           "-filter_complex",
           "[0:v]fps=%d,format=yuv420p[bg];[bg][1:v]overlay=0:0:shortest=1,format=yuv420p[v]"
           % ofps,
           "-map", "[v]"] + enc + ["-pix_fmt", "yuv420p", "-r", str(ofps), "-an",
           "-t", "%.3f" % total, str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise SystemExit("关键词层烧录失败 %s: %s" % (out, (r.stderr or "")[-500:]))
    return out


if __name__ == "__main__":
    import sys
    configure(ffmpeg=sys.argv[3] if len(sys.argv) > 3 else "ffmpeg")
    print(kw_burn(sys.argv[1], json.loads(sys.argv[2]), sys.argv[1] + ".kw.mp4", 1080, 608))
