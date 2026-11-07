# -*- coding: utf-8 -*-
"""fxai 公共件：分段视频的「影调对齐 + 接缝对齐」后处理。

算法与 ComfyUI 节点 `FxAiToneMatch` / `FxAiSeamColorMatch` 完全同口径，
区别只是这边直接在 mp4 上做，不依赖 8188、不占显存、可以单独重跑。

为什么分段生成一定会跳色：
    H3 每一段都是从噪声独立跑出来的，模型对「整屏曝光/色温」没有跨段记忆。
    实测接缝处整屏亮度会掉 1.6~2.0 个灰阶，而正常帧间变化 95 分位只有 0.6~0.7
    —— 帧差指标看不出来（被大量本来就在微动的像素稀释了），眼睛一眼就看见。

两道工序，顺序不得颠倒：

    ① 影调对齐（乘法增益）—— 把本段背景拉回参考色阶。
       增益能同时修亮度与对比度，是「发闷」的正解；但它是乘法，
       暗背景上算出的比值可能是灾难（实测 ×0.2986，对比度 44→13.6 变灰雾），
       所以卡 [0.7, 1.4]：超范围判定采样不可靠，本段跳过、像素一点不动。

    ② 接缝对齐（加法平移）—— 把本段首帧贴到上一段末帧。
       只加偏移、不乘系数，对比度绝对不变，所以它自己不可能造成发灰；
       偏移硬卡 12 灰阶，即使参考帧是闪白/黑场也带不跑整段。

先影调后接缝：影调先把每段拉回参考，接缝再消残余跳变。反过来的话，
影调的增益会把接缝刚做好的平移又乘一遍，越修越偏。

用法（代码）：
    import video_tone
    fixed = video_tone.align(["s1.mp4", "s2.mp4"], outdir="tmp/tone")
    # fixed 里就是校正后的路径，按原顺序拼即可

用法（命令行，排查/单独修片用）：
    python video_tone.py --in s1.mp4 s2.mp4 --outdir tmp/tone --dry-run
    python video_tone.py --in s1.mp4 s2.mp4 --outdir tmp/tone --ref 源片.mp4
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

# 增益的合理区间。实测正常漂移只有 12%（比值 0.87），±40% 已远超正常范围；
# 超出去说明采样不可靠（暗背景、闪白、构图突变），宁可不调。
GAIN_MIN, GAIN_MAX = 0.7, 1.4

# 判定「不用动」的阈值：校正量小于半个灰阶就跳过，省一次重编码。
NEGLIGIBLE = 0.5

REGIONS = ("八方位", "四角", "上部", "边缘带", "全图")
MODES = ("增益", "偏移", "不启用")


# ---------------------------------------------------------------- 基础工具
def ffmpeg_bin(exe=None):
    """ffmpeg 可执行文件：调用方显式给 > 环境变量 FXAI_FFMPEG > PATH > ffmpeg。"""
    if exe:
        return exe
    env = os.environ.get("FXAI_FFMPEG")
    if env and (os.path.isfile(env) or shutil.which(env)):
        return env
    return shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_bin(exe=None):
    """ffprobe：跟 ffmpeg 同目录找，找不到就退回 PATH。"""
    f = ffmpeg_bin(exe)
    d = os.path.dirname(f)
    cand = os.path.join(d, "ffprobe.exe" if os.name == "nt" else "ffprobe")
    if d and os.path.isfile(cand):
        return cand
    return shutil.which("ffprobe") or "ffprobe"


def _run(cmd, timeout=1800):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("命令失败：%s\n%s"
                           % (" ".join(cmd[:4]), (r.stderr or b"").decode("utf-8", "replace")[-600:]))
    return r


def probe(path, exe=None):
    """取宽高 / 帧率 / 时长 / 帧数。帧率优先用「帧数÷时长」当裁判（同 fxai_video_utils）。"""
    out = _run([ffprobe_bin(exe), "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames,duration",
                "-show_entries", "format=duration", "-of", "json", path], timeout=60)
    j = json.loads((out.stdout or b"{}").decode("utf-8", "replace"))
    st = (j.get("streams") or [{}])[0]
    w = int(st.get("width") or 0)
    h = int(st.get("height") or 0)
    dur = float(st.get("duration") or (j.get("format") or {}).get("duration") or 0)
    frames = int(st.get("nb_frames") or 0)
    fps = _pick_fps(st, dur, frames)
    if not frames and dur and fps:
        frames = int(round(dur * fps))
    return {"path": path, "w": w, "h": h, "fps": fps, "dur": dur, "frames": frames}


def _parse_rate(s):
    try:
        a, _, b = str(s or "").partition("/")
        v = float(a) / float(b or 1)
        return v if v > 0 else 0.0
    except Exception:
        return 0.0


def _pick_fps(st, dur, frames):
    """帧率裁判法：标称值和「帧数÷时长」差超过 5% 就丢标称值。

    容器 time_base 会让帧间隔在 33333µs / 33334µs 之间抖，r_frame_rate 于是
    报成 240（能精确表示所有时间戳的最小公倍数），不是真实帧率。
    """
    nominal = [r for r in (_parse_rate(st.get("avg_frame_rate")),
                           _parse_rate(st.get("r_frame_rate"))) if r > 0]
    if frames > 0 and dur > 0:
        measured = frames / dur
        if not nominal:
            return measured
        best = min(nominal, key=lambda r: abs(r - measured))
        return measured if abs(best - measured) / measured > 0.05 else best
    return nominal[0] if nominal else 30.0


# ---------------------------------------------------------------- 采样
def _grab(path, dst, t=None, last=False, exe=None):
    """抽一帧存 PNG。t=None 取首帧；last=True 取末帧（从尾部 seek，省掉从头解码）。"""
    if last:
        # 从尾部 seek 0.4 秒，把这段的帧全吐出来，取最后一张 —— 比 -sseof 单帧准
        d = os.path.dirname(dst) or "."
        pat = os.path.join(d, "_last_%03d.png")
        _run([ffmpeg_bin(exe), "-y", "-sseof", "-0.4", "-i", path,
              "-vsync", "0", "-f", "image2", pat], timeout=300)
        got = sorted(f for f in os.listdir(d) if f.startswith("_last_"))
        if not got:
            raise RuntimeError("取不到末帧：%s" % path)
        shutil.move(os.path.join(d, got[-1]), dst)
        for f in got[:-1]:
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass
        return dst
    cmd = [ffmpeg_bin(exe), "-y"]
    if t:
        cmd += ["-ss", "%.3f" % t]
    cmd += ["-i", path, "-frames:v", "1", "-f", "image2", dst]
    _run(cmd, timeout=300)
    return dst


def _load_rgb(png):
    """PNG → numpy float32 [H, W, 3]（0-1）。numpy/PIL 延迟导入，没装只影响本模块。"""
    from PIL import Image
    import numpy as np
    with Image.open(png) as im:
        a = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
    return a


def luma(rgb):
    """亮度，系数与节点一致（BT.601 全范围）。"""
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def patch_means(gray, region, k):
    """gray [H, W] → 该区域各采样块的平均亮度（0-1）列表。与节点逐行同口径。"""
    h, w = gray.shape
    if region == "全图":
        return [float(gray.mean())]

    kk = min(k, h // 2, w // 2)
    if region == "八方位":
        # 3×3 网格去掉正中那块：中间一定是人物，采进来会被服装主导
        b = min(k, h // 3, w // 3)
        cy, cx = h // 2, w // 2
        half = b // 2
        blocks = [
            gray[:b, :b],
            gray[:b, cx - half:cx + half],
            gray[:b, w - b:],
            gray[cy - half:cy + half, :b],
            gray[cy - half:cy + half, w - b:],
            gray[h - b:, :b],
            gray[h - b:, cx - half:cx + half],
            gray[h - b:, w - b:],
        ]
        return [float(b_.mean()) for b_ in blocks]

    tl = float(gray[:kk, :kk].mean())
    tr = float(gray[:kk, w - kk:].mean())
    bl = float(gray[h - kk:, :kk].mean())
    br = float(gray[h - kk:, w - kk:].mean())
    if region == "四角":
        return [tl, tr, bl, br]
    if region == "上部":
        return [tl, tr]

    top = float(gray[:kk, :].mean())
    bot = float(gray[h - kk:, :].mean())
    if h > 2 * kk:
        left = float(gray[kk:h - kk, :kk].mean())
        right = float(gray[kk:h - kk, w - kk:].mean())
    else:
        left = float(gray[:, :kk].mean())
        right = float(gray[:, w - kk:].mean())
    return [top, bot, left, right]


def analyze(path, region="八方位", exe=None, _tmp=None):
    """抽首/中/末三帧，算出背景区亮度块均值与逐通道均值。

    只抽三帧是因为节点默认「逐帧=False」——整段用一个估计值，
    用的是中间帧。首末帧另有用途：接缝对齐要拿它们当锚点。
    """
    if region not in REGIONS:
        raise ValueError("采样区只能是 %s，收到 %s" % ("/".join(REGIONS), region))
    info = probe(path, exe)
    tmp = _tmp or tempfile.mkdtemp(prefix="fxtone_")
    try:
        p_first = _grab(path, os.path.join(tmp, "f.png"), None, exe=exe)
        p_last = _grab(path, os.path.join(tmp, "l.png"), None, last=True, exe=exe)
        mid_t = (info["dur"] or 0) * 0.5
        p_mid = _grab(path, os.path.join(tmp, "m.png"), mid_t, exe=exe) if mid_t > 0.1 else p_first

        rgb_first = _load_rgb(p_first)
        rgb_mid = _load_rgb(p_mid)
        rgb_last = _load_rgb(p_last)

        h, w = rgb_mid.shape[:2]
        k = max(16, min(h, w) // 8)
        info.update({
            "patches": patch_means(luma(rgb_mid), region, k),
            "rgb_first": [float(x) for x in rgb_first.reshape(-1, 3).mean(axis=0)],
            "rgb_last": [float(x) for x in rgb_last.reshape(-1, 3).mean(axis=0)],
            "rgb_mid": [float(x) for x in rgb_mid.reshape(-1, 3).mean(axis=0)],
        })
        return info
    finally:
        if _tmp is None:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 规划
def _median(vals):
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return 0.0
    m = n // 2
    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2.0


def _robust(vals):
    """剔掉最离群的一块再平均。与节点同算法（理由见节点注释）。"""
    if len(vals) <= 2:
        return sum(vals) / len(vals) if vals else 0.0
    med = _median(vals)
    if len(vals) == 3:
        return med
    worst = max(range(len(vals)), key=lambda i: abs(vals[i] - med))
    rest = [v for i, v in enumerate(vals) if i != worst]
    return sum(rest) / len(rest)


def plan_tone(segs, ref=None, mode="增益", strength=1.0, max_adjust=20.0):
    """每段算一个校正量。返回与 segs 等长的 plan 列表。

    ref 为 None 时以**第一段**为锚（后续段全部拉到第一段色阶）；
    给了 ref（路径或与 segs 等长的路径列表）就对齐到参考片，效果等同节点接「参考序列」。
    """
    if mode not in MODES:
        raise ValueError("方式只能是 %s，收到 %s" % ("/".join(MODES), mode))
    limit = max(0.0, float(max_adjust)) / 255.0
    s = min(1.0, max(0.0, float(strength)))

    refs = None
    if ref:
        if isinstance(ref, str):
            r = analyze(ref)
            refs = [r["patches"]] * len(segs)
        else:
            refs = [analyze(p)["patches"] for p in ref]
            if len(refs) != len(segs):
                raise ValueError("参考片数量 %d 与段数 %d 不一致" % (len(refs), len(segs)))
    elif segs:
        refs = [segs[0]["patches"]] * len(segs)

    plans = []
    for i, seg in enumerate(segs):
        p = {"gain": 1.0, "offset": 0.0, "note": "", "ratios": None, "base": 1.0}
        if mode == "不启用" or s <= 0.0 or limit <= 0.0 or not refs:
            p["note"] = "未启用"
            plans.append(p)
            continue
        cur_p, ref_p = seg["patches"], refs[i]

        if mode == "偏移":
            diffs = [r - c for r, c in zip(ref_p, cur_p)]
            est = max(-limit, min(limit, _robust(diffs)))
            p.update(offset=est * s, note="平移", diffs=[d * 255 for d in diffs])
        else:
            ratios = [(r / c) if c > 1e-4 else 1.0 for r, c in zip(ref_p, cur_p)]
            est = _robust(ratios)
            p["ratios"], p["base"] = ratios, est
            if not (GAIN_MIN <= est <= GAIN_MAX):
                p["note"] = "比值 ×%.4f 超出 %.1f~%.1f，采样不可靠，跳过" % (est, GAIN_MIN, GAIN_MAX)
                plans.append(p)
                continue
            cur_m = _robust(cur_p)
            max_g = 1.0 + limit / max(cur_m, 1e-3)
            est = max(1.0 / max_g, min(max_g, est))
            p.update(gain=1.0 + (est - 1.0) * s, note="增益")
        plans.append(p)
    return plans


def plan_seam(segs, tone=None, max_offset=12.0, base_rgb=None, max_jump=30.0):
    """逐通道求「上一段末帧 − 本段首帧」，返回每段的偏移（0-1 空间三元组）。

    必须在影调之后算，而且要**按影调校正后的值**算：影调已经乘过增益了，
    首帧亮度早就变了。拿原始首帧去算接缝，等于把同一份差异校正两遍，
    段头会明显偏亮/偏暗 —— 这个坑第一次写的时候踩过。
    串起来算：本段校正后末帧 = 原末帧 × 增益 + 偏移 + 接缝偏移，不用回头重新解码。
    第一段没有上一段 → 偏移为零；给了 base_rgb 就把它当上一段末帧（源片末帧）。

    max_jump：差值超过这个灰阶数就判定「换场了」（硬切到另一个场景），本段不接。
    口播/幻灯片这类同一场景的分段，段间差只有十几灰阶；剪辑类技能拼的是不同镜头，
    硬接会把两个场景拉成同一色阶，画面反而变味。判定换场就原样透传。
    """
    limit = max(0.0, float(max_offset)) / 255.0
    jump = max(0.0, float(max_jump)) / 255.0
    tone = tone or [{"gain": 1.0, "offset": 0.0} for _ in segs]

    def after_tone(rgb, tp):
        g = float(tp.get("gain", 1.0))
        d = float(tp.get("offset", 0.0))
        return [v * g + d for v in rgb]

    prev = list(base_rgb) if base_rgb else None
    plans = []
    for seg, tp in zip(segs, tone):
        first = after_tone(seg["rgb_first"], tp)
        last = after_tone(seg["rgb_last"], tp)
        if prev is None or limit <= 0.0:
            plans.append({"delta": (0.0, 0.0, 0.0), "raw": (0.0, 0.0, 0.0), "note": "无参考"})
            prev = last
            continue
        raw = [a - b for a, b in zip(prev, first)]
        if jump > 0 and max(abs(d) for d in raw) > jump:
            plans.append({"delta": (0.0, 0.0, 0.0), "raw": tuple(raw),
                          "note": "跳变 %+.0f 灰阶，判定换场，不接" % (max(raw, key=abs) * 255)})
            prev = last
            continue
        delta = [max(-limit, min(limit, d)) for d in raw]
        plans.append({"delta": tuple(delta), "raw": tuple(raw), "note": "接缝"})
        prev = [a + d for a, d in zip(last, delta)]
    return plans


# ---------------------------------------------------------------- 施加
def _lut_expr(gain, off):
    """一通道的 lutrgb 表达式：clip(val*gain + off*255, 0, 255)。"""
    return "clip(val*%.6f%+.4f,0,255)" % (gain, off * 255.0)


def apply_correction(src, dst, gain=1.0, offset=(0.0, 0.0, 0.0), exe=None):
    """把 gain（乘法）+ offset（逐通道加法，0-1）烤进视频。

    校正量可以忽略时直接原样返回 src，不重编码 —— 分段多的时候省一大截时间，
    也省掉无谓的二次压缩损失。
    """
    off = tuple(float(x) for x in offset)
    if abs(gain - 1.0) * 255.0 < NEGLIGIBLE and max(abs(x) for x in off) * 255.0 < NEGLIGIBLE:
        return src
    os.makedirs(os.path.dirname(os.path.abspath(dst)) or ".", exist_ok=True)
    vf = "format=rgb24,lutrgb=r='%s':g='%s':b='%s'" % (
        _lut_expr(gain, off[0]), _lut_expr(gain, off[1]), _lut_expr(gain, off[2]))
    _run([ffmpeg_bin(exe), "-y", "-i", src,
          "-vf", vf,
          "-map", "0:v:0", "-map", "0:a?",
          "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p",
          "-c:a", "copy", "-movflags", "+faststart", dst])
    return dst


def align(paths, outdir=None, mode="增益", region="八方位", seam=True,
          ref=None, strength=1.0, max_adjust=20.0, max_offset=12.0, max_jump=30.0,
          exe=None, verbose=True):
    """一站式：分析 → 影调规划 → 接缝规划 → 逐段校正。返回校正后的路径（原顺序）。

    校正量可忽略的段会原样返回原路径，所以调用方不必关心「哪段动了、哪段没动」，
    直接拿返回值去拼即可。
    """
    paths = [p for p in (paths or []) if p and os.path.isfile(p)]
    if not paths:
        raise ValueError("没有可处理的视频")
    outdir = outdir or os.path.join(os.path.dirname(os.path.abspath(paths[0])), "_tone")
    os.makedirs(outdir, exist_ok=True)

    segs = [analyze(p, region, exe) for p in paths]
    tone = plan_tone(segs, ref=ref, mode=mode, strength=strength, max_adjust=max_adjust)

    base_rgb = None
    if ref and isinstance(ref, str):
        base_rgb = analyze(ref, region, exe)["rgb_last"]
    seam_plans = plan_seam(segs, tone, max_offset=max_offset, base_rgb=base_rgb,
                           max_jump=max_jump) if seam else \
        [{"delta": (0.0, 0.0, 0.0), "raw": (0.0, 0.0, 0.0), "note": "未启用"} for _ in segs]

    out = []
    for i, (p, tp, sp) in enumerate(zip(paths, tone, seam_plans)):
        dst = os.path.join(outdir, "%02d_%s" % (i + 1, os.path.basename(p)))
        fixed = apply_correction(p, dst, tp["gain"], sp["delta"], exe=exe)
        if verbose:
            if fixed == p:
                print("[影调] 第%d段 不用动（影调：%s ｜ 接缝：%s）"
                      % (i + 1, tp["note"], sp["note"]), flush=True)
            else:
                extra = ""
                if tp["note"].startswith("比值"):
                    extra += "（影调跳过：%s）" % tp["note"]
                if sp["note"].startswith("跳变"):
                    extra += "（%s）" % sp["note"]
                print("[影调] 第%d段 增益 ×%.4f ｜ 接缝 %+.1f %+.1f %+.1f 灰阶%s"
                      % (i + 1, tp["gain"],
                         sp["delta"][0] * 255, sp["delta"][1] * 255, sp["delta"][2] * 255,
                         extra), flush=True)
        out.append(fixed)
    return out


# ---------------------------------------------------------------- 命令行
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="分段视频的影调/接缝对齐（纯后处理）")
    ap.add_argument("--in", dest="inputs", nargs="+", required=True, help="按播放顺序给分段")
    ap.add_argument("--outdir", default="", help="校正后的段落到哪（默认 首个分段目录/_tone）")
    ap.add_argument("--mode", default="增益", choices=MODES, help="影调方式，默认增益")
    ap.add_argument("--region", default="八方位", choices=REGIONS, help="采样区，默认八方位")
    ap.add_argument("--strength", type=float, default=1.0, help="影调强度 0-1，默认 1")
    ap.add_argument("--max-adjust", type=float, default=20.0, help="影调最大调整灰阶，默认 20")
    ap.add_argument("--seam", type=float, default=12.0, help="接缝最大偏移灰阶，0=关闭，默认 12")
    ap.add_argument("--seam-cut", type=float, default=30.0,
                    help="段间差超过这个灰阶数就判定换场、不接，0=不判定，默认 30")
    ap.add_argument("--ref", default="", help="参考片（源片）。不给就以第一段为锚")
    ap.add_argument("--dry-run", action="store_true", help="只算不写，打印每段的校正量")
    ap.add_argument("--ffmpeg", default="", help="指定 ffmpeg 路径")
    a = ap.parse_args(argv)

    paths = [p for p in a.inputs if os.path.isfile(p)]
    if not paths:
        print("没有可处理的视频", file=sys.stderr)
        return 2
    segs = [analyze(p, a.region, a.ffmpeg or None) for p in paths]
    tone = plan_tone(segs, ref=a.ref or None, mode=a.mode,
                     strength=a.strength, max_adjust=a.max_adjust)
    base_rgb = analyze(a.ref, a.region, a.ffmpeg or None)["rgb_last"] if a.ref else None
    seam = plan_seam(segs, tone, max_offset=a.seam, base_rgb=base_rgb, max_jump=a.seam_cut)

    for i, (seg, tp, sp) in enumerate(zip(segs, tone, seam)):
        print("第%d段 %s  %dx%d %.1fs" % (i + 1, os.path.basename(seg["path"]),
                                          seg["w"], seg["h"], seg["dur"]))
        print("      背景块 %s" % "/".join("%.0f" % (v * 255) for v in seg["patches"]))
        if tp.get("ratios"):
            print("      各块比 %s → 采用 ×%.4f  %s"
                  % ("/".join("%.3f" % r for r in tp["ratios"]), tp["gain"], tp["note"]))
        elif tp["note"]:
            print("      影调：%s" % tp["note"])
        if a.seam > 0:
            print("      接缝 需要 %+.1f %+.1f %+.1f 灰阶，实加 %+.1f %+.1f %+.1f"
                  % (sp["raw"][0] * 255, sp["raw"][1] * 255, sp["raw"][2] * 255,
                     sp["delta"][0] * 255, sp["delta"][1] * 255, sp["delta"][2] * 255))

    if a.dry_run:
        print("\n（--dry-run：没有写任何文件）")
        return 0

    out = align(paths, outdir=a.outdir or None, mode=a.mode, region=a.region,
                seam=a.seam > 0, ref=a.ref or None, strength=a.strength,
                max_adjust=a.max_adjust, max_offset=a.seam, max_jump=a.seam_cut,
                exe=a.ffmpeg or None)
    print("\n校正后：")
    for p in out:
        print("  " + p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
