#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""广告片组装：静图运镜 / 片段拼接 / 混音 / 片尾卡 / 正片接片尾。

子命令：
    kenburns  静图 → 运镜片段（读 segs.json）
    concat    片段 → 主体无声片
    mix       视频 + 多段口播 + BGM → 带音成片
    slots     时间线 → 无声画面片，按槽填素材；段里给 layout 就套版式
              （`thirds` 二分栏 / `frame` 上中下分栏）
    endcard   品牌信息 → 片尾卡 PNG
    attach    正片 + 片尾卡 → 品牌信息版

规范见 fxai-ad-video/references/03、04、06。
"""
# >>> 交付码率统一出口（fxai-standard/references/12-交付视频编码规格.md）>>>
import os as _os
import sys as _sys
_VC_ROOT = _os.path.normpath(_os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), '../../fxai-common'))
if _VC_ROOT not in _sys.path:
    _sys.path.insert(0, _VC_ROOT)
import video_encode as _venc
# <<< 交付码率统一出口 <<<
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True


# ---------- 外部工具 ----------

def tool(name):
    """按 FXAI_<NAME> → PATH → conda 的顺序解析可执行文件。"""
    exe = name + (".exe" if os.name == "nt" else "")
    for key in ("FXAI_" + name.upper(), name.upper()):
        p = os.environ.get(key)
        if p and os.path.isfile(p):
            return p
    p = shutil.which(name)
    if p:
        return p
    conda = os.environ.get("CONDA_PREFIX")
    if conda:
        for rel in ("Library/bin", "bin", ""):
            c = os.path.join(conda, rel, exe)
            if os.path.isfile(c):
                return c
    raise SystemExit("%s 未找到：用 FXAI_%s 指向可执行文件，或加进 PATH" % (name, name.upper()))


def run(cmd, quiet=True):
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        print("失败：%s" % " ".join(cmd[:8]), file=sys.stderr)
        print((r.stderr or "")[-1200:], file=sys.stderr)
        raise SystemExit(1)
    return r


def probe(path):
    """取时长 / 宽高 / 帧率。"""
    ff = tool("ffprobe")
    r = run([ff, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate",
             "-show_entries", "format=duration",
             "-of", "json", path])
    data = json.loads(r.stdout or "{}")
    st = (data.get("streams") or [{}])[0]
    dur = float((data.get("format") or {}).get("duration") or 0)
    fps = 25.0
    rate = st.get("r_frame_rate") or "25/1"
    try:
        num, _, den = rate.partition("/")
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        pass
    return {"duration": dur, "width": int(st.get("width") or 0),
            "height": int(st.get("height") or 0), "fps": round(fps, 3)}


# ---------- kenburns ----------

def kb_filter(motion, frames, w, h, fps):
    """源图先放大 2.25 倍再裁，位移在放大画布上做，边缘不露黑。"""
    sw, sh = int(w * 9 / 4), int(h * 9 / 4)
    pre = "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d" % (sw, sh, sw, sh)
    tail = "s=%dx%d:fps=%s" % (w, h, fps)
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "zoom-in":
        zp = "zoompan=z='min(zoom+0.0006,1.15)':x='%s':y='%s':d=%d:%s" % (cx, cy, frames, tail)
    elif motion == "zoom-out":
        zp = ("zoompan=z='1.15-0.15*on/%d':x='%s':y='%s':d=%d:%s"
              % (max(frames - 1, 1), cx, cy, frames, tail))
    elif motion == "pan-left":
        zp = ("zoompan=z='1.10':x='(iw-iw/zoom)*(1-on/%d)':y='%s':d=%d:%s"
              % (max(frames - 1, 1), cy, frames, tail))
    elif motion == "pan-right":
        zp = ("zoompan=z='1.10':x='(iw-iw/zoom)*on/%d':y='%s':d=%d:%s"
              % (max(frames - 1, 1), cy, frames, tail))
    else:
        raise SystemExit("motion 取 zoom-in / zoom-out / pan-left / pan-right，收到：%s" % motion)
    return pre + "," + zp


def cmd_kenburns(args):
    with open(args.segs, "r", encoding="utf-8") as f:
        spec = json.load(f)
    fps = spec.get("fps", 25)
    w, h = spec.get("size", [1280, 720])
    os.makedirs(args.out_dir, exist_ok=True)
    ff = tool("ffmpeg")
    for i, seg in enumerate(spec.get("segments") or [], 1):
        img = seg["image"]
        dur = float(seg["duration"])
        out = os.path.join(args.out_dir, seg.get("out") or "seg%02d.mp4" % i)
        frames = max(int(round(dur * fps)), 2)
        vf = kb_filter(seg.get("motion", "zoom-in"), frames, w, h, fps)
        run([ff, "-y", "-v", "error", "-i", img, "-vf", vf, "-t", "%.3f" % dur,
             "-c:v", "libx264", "-preset", "medium", "-crf", "19",
             "-pix_fmt", "yuv420p", "-an", out])
        print("OK  %s  %.2fs  %s" % (out, dur, seg.get("motion", "zoom-in")))
    print("共 %d 段" % len(spec.get("segments") or []))


# ---------- concat ----------

def cmd_concat(args):
    clips = list(args.clips or [])
    if args.list:
        with open(args.list, "r", encoding="utf-8") as f:
            clips = [ln.strip() for ln in f if ln.strip()]
    if not clips:
        raise SystemExit("给 --clips 或 --list")
    ff = tool("ffmpeg")
    lst = os.path.join(os.path.dirname(os.path.abspath(args.out)), "_concat_list.txt")
    with open(lst, "w", encoding="utf-8", newline="\n") as f:
        for c in clips:
            f.write("file '%s'\n" % os.path.abspath(c).replace("'", "'\\''"))
    r = subprocess.run([ff, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                        "-i", lst, "-c", "copy", args.out],
                       capture_output=True, text=True, errors="replace")
    if r.returncode == 0:
        print("OK  %s（直接拼接）" % args.out)
        return
    info = probe(clips[0])
    w, h, fps = info["width"] or 1280, info["height"] or 720, info["fps"] or 25
    parts, labels = [], []
    for i, _ in enumerate(clips):
        parts.append("[%d:v]scale=%d:%d:force_original_aspect_ratio=increase,"
                     "crop=%d:%d,setsar=1,fps=%s[v%d]" % (i, w, h, w, h, fps, i))
        labels.append("[v%d]" % i)
    graph = ";".join(parts) + ";%sconcat=n=%d:v=1:a=0[v]" % ("".join(labels), len(clips))
    cmd = [ff, "-y", "-v", "error"]
    for c in clips:
        cmd += ["-i", c]
    cmd += ["-filter_complex", graph, "-map", "[v]", "-c:v", "libx264",
            "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-an", args.out]
    run(cmd)
    print("OK  %s（规格归一后重编码）" % args.out)


# ---------- mix ----------

def parse_voice(spec):
    """`文件@起点秒` → (路径, 起点秒)。"""
    path, sep, start = spec.rpartition("@")
    if not sep:
        return spec, 0.0
    try:
        return path, float(start)
    except ValueError:
        return spec, 0.0


def cmd_mix(args):
    ff = tool("ffmpeg")
    video = args.video
    if not os.path.isfile(video):
        raise SystemExit("视频不存在：%s" % video)
    dur = args.duration or probe(video)["duration"]
    voices = [parse_voice(v) for v in (args.voice or [])]
    inputs = [video] + [p for p, _ in voices] + ([args.bgm] if args.bgm else [])
    graph, labels = [], []
    for i, (_, start) in enumerate(voices, 1):
        ms = int(round(start * 1000))
        graph.append("[%d:a]aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     "adelay=%d|%d,volume=%s[a%d]" % (i, ms, ms, args.voice_volume, i))
        labels.append("[a%d]" % i)
    if args.bgm:
        bi = 1 + len(voices)
        graph.append("[%d:a]aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     "volume=%s,atrim=0:%.3f,asetpts=PTS-STARTPTS[bg]"
                     % (bi, args.bgm_volume, dur))
        labels.append("[bg]")
    if not labels:
        raise SystemExit("给至少一条 --voice 或 --bgm")
    graph.append("%samix=inputs=%d:normalize=0:duration=longest,"
                 "alimiter=limit=0.89[aout]" % ("".join(labels), len(labels)))
    cmd = [ff, "-y", "-v", "error"]
    for p in inputs:
        cmd += ["-i", p]
    cmd += ["-filter_complex", ";".join(graph), "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", "%.3f" % dur, args.out]
    run(cmd)
    print("OK  %s（%.2fs，口播 %d 条%s）"
          % (args.out, dur, len(voices), "，BGM %.2f" % args.bgm_volume if args.bgm else ""))


# ---------- slots：按时间线填槽（画面侧） ----------

def ml_module():
    """导入混排公共件，并把本脚本解析到的 ffmpeg 注入进去。

    素材槽（单图 / 轮播 / 视频 / 网页）与「音频+槽→一段」的合成都归它，
    本脚本只负责读时间线、报进度、拼接。
    """
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "..", "fxai-mixed-layout"))
    if not os.path.isdir(root):
        raise SystemExit("缺公共件 fxai-mixed-layout（应在 %s）" % root)
    if root not in sys.path:
        sys.path.insert(0, root)
    import ml_slots as ml
    ml.configure(ffmpeg=tool("ffmpeg"), encoder=os.environ.get("FXAI_ENCODER") or "libx264")
    return ml


def page_recorder(fps):
    """网页槽的录制端：借幻灯技能的录制器（Chrome + rec_anim.js），本技能不重复实现。"""
    sv = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "..", "fxai-slide-video"))
    if not os.path.isdir(sv):
        return None
    if sv not in sys.path:
        sys.path.insert(0, sv)
    try:
        import slides
    except Exception as exc:            # 只影响网页槽，图片 / 视频槽照常
        print("[warn] 网页槽不可用：%s" % exc, file=sys.stderr)
        return None

    def render_page(item, name, od, w, h, sec):
        page = dict(item.get("page") or {})
        html = slides.media_page(page, w=w, h=h)
        clip = slides.record_anim(name, html, od, entrance=0.5, fps=fps, w=w, h=h,
                                  scroll="auto", audio_dur=sec)
        return ml_module().pad_to(clip, os.path.join(od, name + "_fit.mp4"), sec, fps)
    return render_page


def thirds_layout():
    """三分栏的遮罩渲染端：借幻灯技能的 HTML 渲染 + 公共件的 ml_split，本技能不重复实现。

    返回 {render, mask, box}；缺幻灯技能时返回 None（只有 thirds 段会用到）。
    """
    sv = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "..", "fxai-slide-video"))
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "..", "fxai-mixed-layout"))
    if not os.path.isdir(sv) or not os.path.isdir(root):
        return None
    for d in (root, sv):
        if d not in sys.path:
            sys.path.insert(0, d)
    try:
        import slides
        import ml_split
    except Exception as exc:
        print("[warn] 三分栏不可用：%s" % exc, file=sys.stderr)
        return None
    ml_split.configure(canvas_css=slides._canvas_css, sanitize=slides._sanitize_pills)
    return {"render": slides.render, "mask": slides.split_mask, "box": slides.split_box,
            "side": ml_split._side, "full": ml_split._full}


FRAME_TPL = ("frame", "上中下分栏", "上中下", "上下分栏", "栏目图文")


def frame_layout():
    """上中下分栏的壳：借幻灯渲染端出遮罩（上下带 + 花字，中区留透明）。

    壳是全片一份资产（不像二分栏的文字逐段重画），所以遮罩按壳字段做内容寻址
    缓存：同一份壳只截一次图，段数再多也不重复渲染。
    返回 {render, mask, box, key}；缺幻灯技能时返回 None。
    """
    sv = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "..", "fxai-slide-video"))
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "..", "fxai-mixed-layout"))
    if not os.path.isdir(sv) or not os.path.isdir(root):
        return None
    for d in (root, sv):
        if d not in sys.path:
            sys.path.insert(0, d)
    try:
        import slides
        import ml_frame
    except Exception as exc:
        print("[warn] 上中下分栏不可用：%s" % exc, file=sys.stderr)
        return None
    ml_frame.configure(canvas_css=slides._canvas_css, sanitize=slides._sanitize_pills)
    return {"render": slides.render, "mask": slides.frame_mask,
            "box": ml_frame.frame_box}


def _shell_key(fields):
    """壳的内容指纹：上下带文本 + 花字决定这一张遮罩长什么样。"""
    keep = {k: fields.get(k) for k in
            ("title1", "title2", "logo", "logo_en", "logo_img", "tagline", "slogan",
             "overlay", "overlay_pos") if fields.get(k) is not None}
    return hashlib.md5(json.dumps(keep, ensure_ascii=False, sort_keys=True)
                       .encode("utf-8")).hexdigest()[:10]


def cmd_slots(args):
    """时间线 → 一条无声画面片（每段一块槽，填图 / 轮播 / 视频 / 网页）。"""
    ml = ml_module()
    with open(args.spec, "r", encoding="utf-8") as f:
        spec = json.load(f)
    cv = spec.get("canvas") or {}
    w = int(cv.get("w") or spec.get("w") or 1280)
    h = int(cv.get("h") or spec.get("h") or 720)
    fps = int(cv.get("fps") or spec.get("fps") or 25)
    segs = spec.get("segments") or spec.get("beats") or []
    if not segs:
        raise SystemExit("时间线里没有 segments（每段要有 start / end / media）")
    base = os.path.dirname(os.path.abspath(args.spec))
    out = args.out or os.path.join(base, "_tmp", "timeline_visual.mp4")
    tmp = os.path.abspath(args.tmp or os.path.join(os.path.dirname(out), "slots"))
    os.makedirs(tmp, exist_ok=True)
    fade = float(spec.get("fade") or 0)
    render_page = page_recorder(fps)

    parts = []
    shells = {}          # 壳指纹 → 遮罩 PNG：上中下分栏的壳全片一张，按需渲染一次
    fl = None
    print("%-3s %-15s %-8s %s" % ("段", "起-止", "时长", "槽内容"), flush=True)
    for k, seg in enumerate(segs, 1):
        st = float(seg.get("start") or 0)
        en = float(seg.get("end") or 0)
        d = en - st
        if d <= 0.05:
            raise SystemExit("第 %d 段时长 ≤ 0（start %.2f / end %.2f）" % (k, st, en))
        media = seg.get("media")
        if not media:
            raise SystemExit("第 %d 段没有 media（时间码 %s-%s）：本命令按槽填画面，"
                             "每段要给出素材清单" % (k, st, en))
        fields = dict(seg.get("fields") or {})
        lay = str(seg.get("layout") or "").strip().lower()
        is_frame = lay in FRAME_TPL
        is_thirds = (not is_frame) and (
            lay in ("thirds", "split", "三分栏", "分栏") or bool(fields.get("title")))
        box = mask = None
        tag = ""
        if is_frame:
            if fl is None:
                fl = frame_layout()
                if fl is None:
                    raise SystemExit("第 %d 段是上中下分栏，但没找到幻灯技能的渲染端"
                                     "（fxai-slide-video/slides.py）" % k)
            key = _shell_key(fields)
            if key not in shells:
                shells[key] = fl["render"]("shell" + key, fl["mask"](fields, w, h),
                                           os.path.join(tmp, "mask"), w=w, h=h,
                                           transparent=True)
                print("[壳] 渲一张遮罩 %s（同壳的段落共用）" % key, flush=True)
            mask = shells[key]
            box = fl["box"](w, h)
            tag = "上中下分栏 "
        elif is_thirds:
            th = thirds_layout()
            if th is None:
                raise SystemExit("第 %d 段是三分栏，但没找到幻灯技能的渲染端"
                                 "（fxai-slide-video/slides.py）" % k)
            mask = th["render"]("mask%02d" % k, th["mask"](fields, w, h),
                                os.path.join(tmp, "mask"), w=w, h=h, transparent=True)
            box = th["box"](w, h, side=th["side"](fields), full=th["full"](fields))
            tag = "二分栏 "
        cw, ch = (box[2], box[3]) if box else (w, h)
        bg = str(seg.get("bg") or spec.get("bg") or "static").strip().lower()

        items = ml.normalize_items(media, base, seg.get("fit") or spec.get("fit"))
        need_page = any(it.get("kind") == "page" for it in items)
        if need_page and render_page is None:
            raise SystemExit("第 %d 段是网页槽，但没找到幻灯技能的录制器"
                             "（fxai-slide-video/rec_anim.js）" % k)
        items = ml.materialize(items, os.path.join(tmp, "page"), "s%02d" % k,
                               cw, ch, d, fps, render_page=render_page)
        zoom = float(seg.get("zoom", spec.get("zoom", 0)) or 0)
        trans = seg.get("trans") or spec.get("trans") or "push_left"
        tdur = float(seg.get("trans_dur", spec.get("trans_dur", 0.55)) or 0.55)
        if len(items) == 1:
            slot = items[0]["path"]
            desc = "%s %s" % (items[0]["kind"], os.path.basename(slot))
        else:
            slot = ml.build_reel(items, os.path.join(tmp, "reel%02d.mp4" % k), d, cw, ch,
                                 fps=fps, trans=trans, trans_dur=tdur, zoom=zoom)
            desc = "轮播 %d 格（%s）" % (len(items), trans)
            zoom = 0.0
        if tag:
            desc = tag + desc
        seg_out = os.path.join(tmp, "seg%02d.mp4" % k)
        ml.part(slot, None, seg_out, w, h, box=box, mask=mask,
                fit=(items[0].get("fit") if items else "cover"),
                zoom=zoom, fade=float(seg.get("fade", fade) or 0),
                fade_in=(k > 1), fade_out=(k < len(segs)), fps=fps, total=d,
                # 虚化底只给二分栏：它的文字区下面要垫素材；上中下分栏的上下带在壳里
                bg_slot=(slot if (box and not is_frame and bg != "static") else None))
        parts.append(seg_out)
        print("%-3d %5.2f-%5.2f  %5.2fs  %s" % (k, st, en, d, desc), flush=True)

    ml.concat(parts, out, tmp)
    info = probe(out)
    gap = abs(info["duration"] - sum(float(s.get("end") or 0) - float(s.get("start") or 0)
                                     for s in segs))
    print("OK  %s（%dx%d / %dfps / %.2fs / 段落和偏差 %.2fs）"
          % (out, w, h, fps, info["duration"], gap))
    return out


# ---------- endcard ----------

# 字体族关键字：字体名 → 文件名词根（比对时统一去空格与标点）。白名单见 fxai-standard 06 册。
FONT_ALIASES = {
    "得意黑": ("smileysans", "smiley"),
    "微软雅黑": ("msyh",),
    "noto sans sc": ("notosanssc",),
    "思源黑体": ("sourcehansans",),
    "思源宋体": ("sourcehanserif",),
    "黑体": ("simhei",),
    "宋体": ("simsun",),
}
# 中文兜底链：指定字体缺字形时按序退到这几款
CJK_FALLBACK = ("微软雅黑", "黑体", "宋体", "Noto Sans SC")


def font_dir():
    return (os.environ.get("FXAI_FONT_DIR")
            or os.path.join(os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or "", "Fonts"))


def norm_key(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def font_file(name, bold=False):
    base = font_dir()
    if not base or not os.path.isdir(base):
        return None
    key = (name or "").strip().lower()
    keys = [norm_key(k) for k in FONT_ALIASES.get(key, (key,))]
    hits = []
    for fn in os.listdir(base):
        if not fn.lower().endswith((".ttf", ".ttc", ".otf")):
            continue
        nfn = norm_key(fn)
        if any(k and k in nfn for k in keys):
            hits.append(fn)
    if not hits:
        return None
    if bold:
        hits = [f for f in hits if "bd" in f.lower() or "bold" in f.lower()
                or "heavy" in f.lower()] or hits
    return os.path.join(base, hits[0])


def load_font(names, size, bold=False):
    """按给定顺序找字体，末位退到中文兜底链，全缺才用 PIL 默认。"""
    from PIL import ImageFont
    for n in list(names) + list(CJK_FALLBACK):
        p = font_file(n, bold)
        if p:
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def hex2rgb(s, fallback=(255, 255, 255)):
    s = (s or "").lstrip("#")
    if len(s) != 6:
        return fallback
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return fallback


def cmd_endcard(args):
    """左对齐品牌卡：英文小标 → 品牌名 → slogan → 分隔线 → 参数行 → 信息行。"""
    from PIL import Image, ImageDraw, ImageFilter
    with open(args.brand, "r", encoding="utf-8") as f:
        b = json.load(f)
    if isinstance(args.size, str) and "x" in args.size.lower():
        args.size = [int(v) for v in args.size.lower().split("x")[:2]]
    w, h = (args.size or b.get("size") or [1280, 768])[:2]
    k = h / 768.0
    accent = hex2rgb(b.get("accent"), (185, 138, 94))
    top = hex2rgb(b.get("bg_top"), (25, 27, 30))
    bot = hex2rgb(b.get("bg_bottom"), (38, 42, 47))
    fg, sub = (246, 243, 238), (176, 172, 165)

    img = Image.new("RGB", (w, h), top)
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(h - 1, 1)
        d.line([(0, y), (w, y)],
               fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))

    glow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(glow).ellipse([w * 0.50, -h * 0.70, w * 1.30, h * 0.80], fill=30)
    img = Image.composite(Image.new("RGB", (w, h), (252, 244, 232)), img,
                          glow.filter(ImageFilter.GaussianBlur(150 * k)))
    d = ImageDraw.Draw(img)

    motif = b.get("motif_png")
    if motif and os.path.isfile(motif):
        m = Image.open(motif).convert("RGBA")
        scale = min(w * 0.34 / m.width, h * 0.62 / m.height)
        m = m.resize((max(int(m.width * scale), 1), max(int(m.height * scale), 1)), Image.LANCZOS)
        img.paste(m, (int(w * 0.70), int((h - m.height) / 2)), m)
        d = ImageDraw.Draw(img)

    fonts = b.get("font") or {}
    f_en = load_font([fonts.get("body") or "Noto Sans SC"], int(21 * k))
    f_brand = load_font([fonts.get("title") or "得意黑"], int(132 * k), bold=True)
    f_slogan = load_font([fonts.get("bold") or "微软雅黑"], int(46 * k), bold=True)
    f_spec = load_font([fonts.get("body") or "Noto Sans SC"], int(25 * k))
    f_tail = load_font([fonts.get("body") or "Noto Sans SC"], int(24 * k))

    L = int(118 * k)
    name = b.get("brand_cn") or b.get("brand") or ""
    if b.get("brand_en"):
        d.text((L, int(196 * k)), b["brand_en"], font=f_en, fill=accent)
    d.text((L - int(4 * k), int(232 * k)), name, font=f_brand, fill=fg)
    d.text((L, int(418 * k)), b.get("slogan") or "", font=f_slogan, fill=(226, 221, 213))
    d.line([(L, int(500 * k)), (L + int(470 * k), int(500 * k))], fill=(64, 66, 70), width=2)
    d.line([(L, int(500 * k)), (L + int(96 * k), int(500 * k))], fill=accent, width=3)
    d.text((L, int(530 * k)), b.get("spec_line") or b.get("specs") or "", font=f_spec, fill=sub)
    d.text((L, int(586 * k)), b.get("tail_line") or b.get("info") or "", font=f_tail,
           fill=(150, 147, 141))
    img.save(args.out)
    print("OK  %s（%dx%d）" % (args.out, w, h))


# ---------- attach ----------

def cmd_attach(args):
    ff = tool("ffmpeg")
    info = probe(args.main)
    dur, w, h = info["duration"], info["width"], info["height"]
    hold = float(args.hold)
    fo, fi = float(args.fade_out), float(args.fade_in)
    if dur <= fo + 0.1:
        raise SystemExit("正片太短，淡出 %.2fs 放不下" % fo)
    graph = (
        "[0:v]scale=%d:%d,setsar=1,fade=t=out:st=%.3f:d=%.3f[v0];"
        "[1:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,setsar=1,"
        "fade=t=in:st=0:d=%.3f,trim=duration=%.3f,setpts=PTS-STARTPTS[v1];"
        "[0:a]afade=t=out:st=%.3f:d=0.780,atrim=0:%.3f,asetpts=PTS-STARTPTS[a0];"
        "[2:a]atrim=0:%.3f[a1];"
        "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
        % (w, h, dur - fo, fo,
           w, h, w, h, fi, hold,
           max(dur - 0.78, 0), dur,
           hold)
    )
    cmd = [ff, "-y", "-v", "error", "-i", args.main,
           "-loop", "1", "-t", "%.3f" % hold, "-i", args.endcard,
           "-f", "lavfi", "-t", "%.3f" % hold, "-i", "anullsrc=r=48000:cl=stereo",
           "-filter_complex", graph, "-map", "[v]", "-map", "[a]"] \
          + _venc.delivery_venc(audio_copy=False, audio_bps=192000) + [args.out]
    run(cmd)
    print("OK  %s（%.2fs = 正片 %.2fs + 片尾 %.2fs）" % (args.out, dur + hold, dur, hold))


# ---------- 入口 ----------

def main():
    ap = argparse.ArgumentParser(description="广告片组装")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("kenburns", help="静图 → 运镜片段")
    p.add_argument("--segs", required=True, help="segs.json")
    p.add_argument("--out-dir", default="_clips")
    p.set_defaults(func=cmd_kenburns)

    p = sub.add_parser("concat", help="片段 → 主体无声片")
    p.add_argument("--clips", nargs="+")
    p.add_argument("--list", help="每行一个片段路径")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_concat)

    p = sub.add_parser("mix", help="视频 + 口播 + BGM → 带音成片")
    p.add_argument("--video", required=True)
    p.add_argument("--voice", action="append", default=[], help="文件@起点秒，可重复")
    p.add_argument("--voice-volume", default="1.0")
    p.add_argument("--bgm")
    p.add_argument("--bgm-volume", type=float, default=0.20)
    p.add_argument("--duration", type=float, default=0.0)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_mix)

    p = sub.add_parser("slots", help="时间线 → 无声画面片（按槽填素材）")
    p.add_argument("--spec", required=True, help="时间线.json（canvas + segments[start,end,media]）")
    p.add_argument("--out", help="输出无声画面片（默认 <spec 目录>/_tmp/timeline_visual.mp4）")
    p.add_argument("--tmp", help="中间件目录（默认与输出同级 slots/）")
    p.set_defaults(func=cmd_slots)

    p = sub.add_parser("endcard", help="品牌信息 → 片尾卡 PNG")
    p.add_argument("--brand", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--size", help="覆盖 brand.json 的 size，形如 1280x768")
    p.set_defaults(func=cmd_endcard)

    p = sub.add_parser("attach", help="正片 + 片尾卡 → 品牌信息版")
    p.add_argument("--main", required=True)
    p.add_argument("--endcard", required=True)
    p.add_argument("--hold", default="2.6", help="片尾停留秒数")
    p.add_argument("--fade-out", default="0.32")
    p.add_argument("--fade-in", default="0.5")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_attach)

    args = ap.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
