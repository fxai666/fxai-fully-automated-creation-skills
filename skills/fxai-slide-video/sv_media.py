# -*- coding: utf-8 -*-
"""fxai-slide-video · 拼接 / 字幕 / 烧录

字幕一律按「各段音频的精确时长」排时间轴，不走 whisper 对齐。
"""
# >>> 交付码率统一出口（fxai-standard/references/12-交付视频编码规格.md）>>>
import os as _os
import sys as _sys
_VC_ROOT = _os.path.normpath(_os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), '../fxai-common'))
if _VC_ROOT not in _sys.path:
    _sys.path.insert(0, _VC_ROOT)
import video_encode as _venc
# <<< 交付码率统一出口 <<<
import subprocess, pathlib, re, json, os, sys, datetime, shutil

_HERE = pathlib.Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import sv_layout          # 项目存储格式的唯一执行点：落盘一律走它，产物进 `<工作区>/<项目名>/`

# 公共件：混排素材槽（图片轮播 / 视频 / 网页 → 一条槽内视频流）
_ML = pathlib.Path(__file__).resolve().parent.parent / "fxai-mixed-layout"
if _ML.is_dir() and str(_ML) not in sys.path:
    sys.path.insert(0, str(_ML))
import ml_slots as ml      # noqa: E402
import ml_frame            # noqa: E402  上中下分栏的中区框（方向自适应）取自这里
import ml_trio             # noqa: E402  左中右分栏的中槽框（满高、宽度可调）取自这里

W, H, FPS = 768, 1280, 24
MAXC = 16          # 单条字幕上限字数（竖屏档位）
MINSEC = 0.6       # 单条最短时长

# ---------- ffmpeg（必须带 libass） ----------
def global_cfg():
    """全局配置 ~/.workbuddy/fxai_global.json（可用环境变量 FXAI_GLOBAL_CONFIG 覆盖）。"""
    p = os.environ.get("FXAI_GLOBAL_CONFIG") or os.path.join(
        os.path.expanduser("~"), ".workbuddy", "fxai_global.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def ffmpeg_bin():
    """带 libass 的 ffmpeg：从全局配置 software_install_dir 拼 <安装目录>/ffmpeg/ffmpeg.exe。不硬编码盘符、不扫盘。"""
    cfg = global_cfg()
    inst = cfg.get("software_install_dir") or cfg.get("video_install_dir") or ""
    if inst:
        for nm in ("ffmpeg.exe", "ffmpeg"):
            c = os.path.join(inst, "ffmpeg", nm)
            if os.path.isfile(c):
                return os.path.normpath(c)
    v = cfg.get("video_ffmpeg") or cfg.get("ffmpeg")
    if v and os.path.isfile(v):
        return v
    import shutil as _sh
    return _sh.which("ffmpeg") or "ffmpeg"

def ffprobe_bin():
    f = ffmpeg_bin()
    if f != "ffmpeg":
        cand = str(pathlib.Path(f).with_name("ffprobe.exe"))
        if os.path.isfile(cand):
            return cand
    return "ffprobe"

def dur(p):
    o = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    try:
        return float((o.stdout or "0").strip())
    except ValueError:          # 静图/无时长流会返回 N/A：当作 0，别让合成端炸在这里
        return 0.0

def audio_dir(base):
    """段音频在 音频/（目录名统一由 sv_layout 定义）。"""
    for d in sv_layout.dirs_of(base)["music"]:
        if d.is_dir() and any(d.glob("*.wav")):
            return d
    return base


def audio_of(base, project, num):
    """取第 num 段音频。"""
    return sv_layout.find(base, f"{project}{num}.wav", kind="music")


def speech_subtitles(lines, audios, out_dir, name="字幕", words_dir=None, margin_v=None):
    """逐段识别，复用公共字幕对齐，合并局部时间轴。"""
    import importlib.util
    import hashlib
    shared = pathlib.Path(__file__).resolve().parent.parent / "fxai-lyric-subtitle" / "scripts" / "gen_lyric_subtitle.py"
    spec = importlib.util.spec_from_file_location("fxai_shared_subtitle", shared)
    sub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sub)
    out_dir = pathlib.Path(out_dir)
    cache = pathlib.Path(words_dir) if words_dir else out_dir / "_tmp" / "speech_words"
    cache.mkdir(parents=True, exist_ok=True)
    if len(lines) != len(audios):
        raise ValueError("文案段数与音频段数不一致")
    items, report, offset = [], [], 0.0
    for i, (line, audio) in enumerate(zip(lines, audios), 1):
        audio = pathlib.Path(audio)
        transcript = cache / ("words_%02d.txt" % i)
        signature = hashlib.sha256(audio.read_bytes()).hexdigest()
        marker = transcript.with_suffix(".sha256")
        if not transcript.exists() or not marker.exists() or marker.read_text().strip() != signature:
            # 走 whisper 服务接口（地址由全局配置决定），逐段词级时间戳
            sub.whisper_client().write_transcript(str(audio), str(transcript), words=True,
                                                  language="zh", condition=False)
            marker.write_text(signature)
        duration = dur(audio)
        _MIN = MINSEC + 0.05          # 目标下限（留 50ms 余量，抵消 SRT 毫秒取整）
        try:
            cues = sub.align_speech_captions([line], sub.parse_words(transcript), MAXC, 4.0, _MIN)
        except ValueError as e:
            # 匹配率不足（方言 / 重口音）→ 按时长均分兜底
            print("[speech] 第%d段对齐失败（%s）→ 按时长均分兜底" % (i, e), flush=True)
            cues = _even_cues(line, duration, _MIN)
        # 裁进本段音频范围；提速后语音紧凑，末条常被音频时长裁到过短（<MINSEC）——
        # 先往前借静音空隙补足，借不到就与上一条合并（不超字数上限），保证最短时长。
        local = [[max(0.0, float(c[0])), min(duration, float(c[1])), c[2]] for c in cues]
        for idx in range(len(local) - 1, -1, -1):
            cur = local[idx]
            if cur[1] - cur[0] >= _MIN:
                continue
            lower = local[idx - 1][1] if idx else 0.0
            cur[0] = round(max(lower, cur[1] - _MIN), 3)
            if cur[1] - cur[0] < MINSEC and idx and len(local[idx - 1][2]) + len(cur[2]) <= MAXC:
                prev = local[idx - 1]
                local[idx - 1:idx + 1] = [[prev[0], max(prev[1], cur[1]), prev[2] + cur[2]]]
        for st, en, text in local:
            if st < -0.01 or en > duration + 0.3:
                raise ValueError("第%d段字幕超出音频范围" % i)
            items.append((st + offset, min(duration, en) + offset, text, ""))
        report.append({"segment": i, "audio": audio.name, "offset": offset, "duration": duration,
                       "captions": [{"start": c[0], "end": c[1], "text": c[2]} for c in local]})
        offset += duration
        print("[speech] 第%d段完成，%d条字幕" % (i, len(local)), flush=True)
    sub.check_subtitle_timing(items, offset, min_sec=MINSEC - 0.005, max_sec=4.05)
    (cache / "alignment.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_caption_items(items, out_dir, name, margin_v=margin_v)
    return len(items)


# ---------- 字幕切分 ----------
def _is_en(c):
    return c.isascii() and (c.isalpha() or c.isdigit())

def split_text(t, maxc=MAXC):
    """按标点断句；过长按字数硬断（英文词整段保留）；再合并短句保证最短时长。"""
    parts = re.split(r'([。！？；，、：…—])', t)
    units, buf = [], ""
    for i, s in enumerate(parts):
        if i % 2 == 0:
            buf = s
        else:
            units.append(buf + s); buf = ""
    if buf:
        units.append(buf)
    out = []
    for u in units:
        u = u.strip()
        while len(u) > maxc:
            cut = maxc
            while cut > 1 and _is_en(u[cut - 1]) and _is_en(u[cut]):
                cut -= 1
            out.append(u[:cut]); u = u[cut:].strip()
        if u:
            out.append(u)
    out = [x for x in out if x]
    merged, buf = [], ""
    for u in out:
        if not buf:
            buf = u
        elif len(buf) + len(u) <= maxc:
            buf += u
        else:
            merged.append(buf); buf = u
    if buf:
        merged.append(buf)
    return merged

def _even_cues(line, duration, min_sec, max_sec=4.0):
    """语音对齐匹配率不足时的兜底：按各条字数把本段音频时长均分。

    文字取正文；时间轴按字数比例排，不精确到字。单条夹在 [min_sec, max_sec]。
    """
    parts = split_text(line, MAXC)
    n = len(parts)
    weights = [max(1, len(strip_punct(p))) for p in parts]
    total = float(sum(weights)) or 1.0
    ends, t = [], 0.0
    for k in range(n - 1):
        t += duration * weights[k] / total
        ends.append(t)
    ends.append(duration)
    # 逐条夹在 [min_sec, max_sec]：往前不早于上一条 +min_sec，往后给剩下每条留够 min_sec
    for k in range(n - 1):
        prev = ends[k - 1] if k else 0.0
        lo = prev + min_sec
        hi = min(prev + max_sec, duration - min_sec * (n - 1 - k))
        ends[k] = hi if hi < lo else min(max(ends[k], lo), hi)
    cues, st = [], 0.0
    for k, p in enumerate(parts):
        en = max(ends[k], st)
        cues.append((round(st, 3), round(en, 3), p))
        st = en
    return cues

def _ts(s, comma=False):
    s = max(0.0, s)
    h, m, sec = int(s // 3600), int(s % 3600 // 60), s % 60
    return "%d:%02d:%06.3f" % (h, m, sec) if not comma else "%d:%02d:%06.3f" % (h, m, sec)

def strip_punct(t):
    return re.sub(r'[。！？；，、：…—]', '', t).strip()

SUB_MARGIN_V = None   # 字幕距底像素；None = 用默认（round(80*H/1280)）。
                      # 上中下分栏要把字幕压进中区下沿，由 --sub-bottom 设定。
SUB_FONT_SIZE = None  # 字幕字号；None = 用默认（横版 40 / 竖版 36）。
                      # 上中下分栏媒体区只有 440 高，字要更大才看得清，由 --sub-size 设定。
SUB_FONT = None       # 字幕字体名（ASS Fontname）；None = 微软雅黑。
                      # 🔴 与画面标题用同一族时传同名（如 "SimHei"）——壳是黑体、字幕是雅黑，
                      # 两套字一眼能看出不统一（用户 2026-10-25 指出）。
SUB_COLOR = None      # 字幕主色（#RRGGBB）；None = 默认暖白 + 墨绿描边。
                      # 给了就用它 + 黑描边（上中下分栏用黄字黑边，压在写实图上更醒目）。


def _ass_color(hexcolor, alpha="00"):
    """#RRGGBB → ASS 的 &HAABBGGRR。ASS 用 BGR 倒序，直接拼 RRGGBB 会变色。"""
    c = str(hexcolor or "").strip().lstrip("#")
    if len(c) != 6:
        return None
    r, g, b = c[0:2], c[2:4], c[4:6]
    return "&H%s%s%s%s" % (alpha, b.upper(), g.upper(), r.upper())


def write_caption_items(items, out_dir, name="字幕", margin_v=None):
    """写入已对齐的字幕事件，不重新分配时间。"""
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    srt = []
    for n, (st, en, tx, *_) in enumerate(items, 1):
        srt.append("%d\n%s --> %s\n%s\n" % (n, _ts(st, True).replace(".", ","),
                                            _ts(en, True).replace(".", ","), strip_punct(tx)))
    (out_dir / f"{name}.srt").write_text("\n".join(srt), encoding="utf-8")

    fs = SUB_FONT_SIZE if SUB_FONT_SIZE else (40 if W > H else 36)
    mv = margin_v if margin_v is not None else SUB_MARGIN_V
    if mv is None:
        mv = max(20, round(80 * H / 1280.0))
    mv = int(mv)
    # 字幕配色：默认暖白 + 墨绿描边；给了 --sub-color 就换成「自定义色 + 黑描边」
    if SUB_COLOR:
        pc = _ass_color(SUB_COLOR) or "&H00F5FBE8"
        oc, outline = "&H00101010", 3.0
    else:
        pc, oc, outline = "&H00F5FBE8", "&H003A3F0E", 2.5
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
# 字幕样式复用系统「内字幕」那一套（fxai-lyric-subtitle 的 _mv_ass_header：MV 默认薄荷青款）
# 暖白字 &H00F5FBE8 + 墨绿描边 &H003A3F0E + 描边 2.5 + 阴影 1.2；宽>高即横版，字号 40，竖版 36。
Style: Default,{SUB_FONT or "Microsoft YaHei"},{fs},{pc},&H000000FF,{oc},&H50000000,1,0,0,0,100,100,0.8,0,1,{outline},1.2,2,30,30,{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    def ass_time(t):
        cs = int(round(t * 100))
        return "%d:%02d:%02d.%02d" % (cs // 360000, cs // 6000 % 60, cs // 100 % 60, cs % 100)
    ev = ["Dialogue: 0,%s,%s,Default,,0,0,0,,%s" % (ass_time(st), ass_time(en), strip_punct(tx))
          for st, en, tx, *_ in items]
    (out_dir / f"{name}.ass").write_text(head + "\n".join(ev) + "\n", encoding="utf-8")
    return len(items)

def check_subtitles(srt_path, max_sec=4.05):
    """落盘前自检：时长/字数/时序，不合格抛错。"""
    blocks = [b for b in pathlib.Path(srt_path).read_text(encoding="utf-8").strip().split("\n\n") if b.strip()]
    bad, prev = [], 0.0
    for b in blocks:
        ls = b.split("\n")
        st, en = ls[1].split(" --> ")
        def sec(t):
            h, m, rest = t.split(":")
            return int(h) * 3600 + int(m) * 60 + float(rest.replace(",", "."))
        s0, s1, txt = sec(st), sec(en), ls[2]
        if s1 - s0 < MINSEC: bad.append(("太短", round(s1 - s0, 2), txt))
        if s1 - s0 > max_sec: bad.append(("超长", round(s1 - s0, 2), txt))
        if len(txt) > MAXC: bad.append(("超字", len(txt), txt))
        if s0 < prev - 0.01: bad.append(("时序回退", s0, txt))
        prev = s1
    if bad:
        raise SystemExit("字幕不合格：\n  - " + "\n  - ".join(str(x) for x in bad[:12]))
    return len(blocks)

# ---------- 拼接 / 烧录 ----------
def _encoder():
    """优先显卡编码（NVENC）；检测失败回落 libx264。
    统一走 gpu.py 的全局检测（真编一帧验证，进程内缓存）；FXAI_VIDEO_ENCODER 可强制指定。"""
    force = os.environ.get("FXAI_VIDEO_ENCODER", "").strip()
    if force:
        return force
    try:
        import gpu
        return gpu.video_encoder()
    except Exception:
        pass
    try:
        o = subprocess.run([ffmpeg_bin(), "-hide_banner", "-encoders"],
                           capture_output=True, text=True, errors="replace")
        if "h264_nvenc" in (o.stdout or ""):
            return "h264_nvenc"
    except Exception:
        pass
    return "libx264"

def _common(enc=None, fps=None):
    enc = enc or _encoder()
    if enc == "h264_nvenc":
        # 显卡编码：vbr + cq 约等于 crf 的画质档；p4 = 均衡预设
        v = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "20", "-b:v", "0"]
    else:
        v = ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]
    # out_fps 不传则回落模块默认 FPS（可被调用方覆盖，保证「指定帧率」贯穿到最后合成）
    return v + ["-pix_fmt", "yuv420p", "-r", str(fps if fps is not None else FPS),
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"]

def _ffmpeg(build_cmd, desc):
    """跑 ffmpeg；若显卡编码失败则自动回落 libx264 重试一次。"""
    enc = _encoder()
    r = subprocess.run(build_cmd(enc), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 and enc != "libx264":
        print("[warn] %s 用 %s 失败，回落 libx264 重试" % (desc, enc), flush=True)
        r = subprocess.run(build_cmd("libx264"), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r

# ---------- 分段响度归一化（EBU R128 线性增益，拉齐各段听感音量） ----------
LOUDNORM_I = -16.0   # 目标整合响度（LUFS）；全局配置键 audio_loudness 可覆盖

def _loudness_measure(p):
    """EBU R128 测量：返回 (整合响度 I (LUFS), 峰值 (dBFS/dBTP))；测不到 I 返回 (None, 0)。"""
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(p),
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    txt = r.stderr or ""
    m_i = re.findall(r"I:\s*(-?[\d.]+)\s*LUFS", txt)
    m_pk = (re.findall(r"True peak:\s*(-?[\d.]+)", txt)
            or re.findall(r"Peak:\s*(-?[\d.]+)\s*dB", txt))
    return (float(m_i[-1]) if m_i else None, float(m_pk[-1]) if m_pk else 0.0)

def ensure_loudnorm(audio, target=None):
    """把分段音频的整合响度拉齐到统一目标（默认 -16 LUFS）：线性增益、不压动态、防削波。
    处理过写 <名>.wav.norm 标记（记目标值）；目标一致的重跑直接跳过。返回音频路径。"""
    audio = pathlib.Path(audio)
    if not audio.exists() or audio.suffix.lower() not in (".wav", ".mp3", ".flac", ".m4a"):
        return audio
    target = float(target if target is not None
                   else global_cfg().get("audio_loudness", LOUDNORM_I))
    marker = audio.with_suffix(audio.suffix + ".norm")
    if marker.exists():
        try:
            if json.loads(marker.read_text(encoding="utf-8")).get("target") == target:
                return audio
        except Exception:
            pass
    i, peak = _loudness_measure(audio)
    if i is None:
        return audio
    gain = target - i
    if gain > 0 and peak + gain > -1.0:          # 提升会削波 → 限到峰值 -1 dBFS
        gain = max(0.0, -1.0 - peak)
    if abs(gain) < 0.1:
        marker.write_text(json.dumps({"target": target, "gain": 0.0}), encoding="utf-8")
        return audio
    tmp = audio.with_suffix(".norm_tmp" + audio.suffix)
    r = subprocess.run([ffmpeg_bin(), "-y", "-i", str(audio),
                        "-af", "volume=%.2fdB" % gain, "-c:a", "pcm_s16le", str(tmp)],
                       capture_output=True, text=True)
    if r.returncode == 0 and tmp.exists() and dur(tmp) > 0.1:
        tmp.replace(audio)
        marker.write_text(json.dumps({"target": target, "gain": round(gain, 2)}), encoding="utf-8")
        print("[loudnorm] %s  I=%.1f LUFS → %.1f（增益 %+.1f dB）" % (audio.name, i, target, gain), flush=True)
    elif tmp.exists():
        tmp.unlink()
    return audio

# ---------- 素材槽：归一化 + 多图轮播（实现已归公共件 fxai-mixed-layout）----------
# 「槽里填什么」不归某个展示形式管：图片轮播、视频、网页最终都归一成一条 w×h 无声视频。
# 实现在 skills/fxai-mixed-layout/ml_slots.py，这里只做薄绑定 + 兼容旧名。
FRAME_SS = ml.FRAME_SS
FRAME_ZOUT = ml.FRAME_ZOUT
ZOOM_MIN_STEP = ml.ZOOM_MIN_STEP
REEL_TRANS = ml.REEL_TRANS
_trans_of = ml._trans_of
_cell_chain = ml.cell_chain
_cell_chain.__doc__ = ml.cell_chain.__doc__
_pad_clip = ml.pad_to
ml.configure(ffmpeg=ffmpeg_bin(), encoder=_encoder())


def build_reel(items, out, total, w, h, fps=None, trans="push_left",
               trans_dur=0.55, zoom=0.0, bg="0x0B0B0C"):
    """段内多张素材 → 一条覆盖 total 秒的中区轮播片（转发公共件）。"""
    return ml.build_reel(items, out, total, w, h, fps=fps, trans=trans,
                         trans_dur=trans_dur, zoom=zoom, bg=bg)

def page_clip(item, name, outdir, w, h, total, fps):
    """中区网页槽：造一页 HTML（或直接给网址）→ Chrome 真实录屏 → 铺满声的无声 mp4。

    这就是「网页渲出来 → 录下来 → 垫进壳里」那一步。图片轮播、视频直贴、网页动效
    在合成端被抹平成同一种东西：一条 w×h 的视频流，交给 make_part 当 video 用。
    """
    import slides as S                     # 延迟导入：只有真的用到网页槽才碰浏览器
    page = dict(item.get("page") or {})
    typ = str(page.get("type") or "text").lower()
    outdir = pathlib.Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    scroll = page.get("scroll", "auto")
    scroll = "auto" if scroll is True else ("none" if scroll is False else str(scroll))
    if typ == "url":
        clip = S.record_url(page["url"], name, outdir, duration=total + 0.5, fps=fps,
                            w=w, h=h, wait_ms=int(page.get("wait_ms") or 2500),
                            scroll=scroll, audio_dur=total, hold=None)
    else:
        html = S.media_page(page, w=w, h=h)
        clip = S.record_anim(name, html, outdir, entrance=0.6, fps=fps, w=w, h=h,
                             scroll=scroll, audio_dur=total)
    return _pad_clip(clip, outdir / (name + "_fit.mp4"), total, fps)


def materialize_pages(items, outdir, tag, w, h, total, fps):
    """把素材清单里的网页槽先录成视频，其余原样返回（转发公共件，注入录制端 page_clip）。

    轮播段里混网页也走这里：网页先变成 clip，再跟图片一起交给 build_reel 串起来。
    """
    return ml.materialize(items, outdir, tag, w, h, total, fps=fps,
                          render_page=lambda it, nm, od, ww, hh, sec:
                          page_clip(it, nm, od, ww, hh, sec, fps))


def make_part(img, audio, out, w=W, h=H, fade=0.0, fade_in=True, fade_out=True,
              frames=None, fps=20, clip=None, out_fps=None,
              mask=False, still=None, video=None, zoom=0.0, fit="cover", box=None,
              bg_slot=None):
    """一段 = 一张图（或一段素材）+ 一段音频 → 一段 mp4。

    mask/still/video：上中下分栏的「中区放映」模式。img 这时是带 alpha 的壳遮罩，
        素材（still 静图 / video 视频）缩放进媒体区垫在遮罩下面 —— 遮罩不动、素材动。
    box：(x, y, w, h) 槽位。**由调用方按该段的版式算好传进来**（frame_box / trio_box）；
        缺省按上中下分栏的中区框兜底。
    zoom：中区缓慢推近倍数（0=不推）。只在「中区放映」模式下有意义：整页放大壳会跟着缩放，
        推近必须只作用在媒体区，所以它依赖遮罩。

    实际合成走公共件 `ml_slots.part()`（整页/广告的整幅槽、栏目的中区槽同一条路径）；
    只有「PNG 帧序列」留在本地，因为那是幻灯专有的出图方式，公共件不接管。
    """
    audio = ensure_loudnorm(audio)   # 分段响度归一化（已处理的带标记直接跳过）
    d = dur(audio)
    ofps = out_fps if out_fps is not None else FPS

    if frames:
        # 镜头内动画帧序列：定格最后一帧补齐到音频时长（tpad clone），再套缩放/淡入淡出
        nfr = len(list(pathlib.Path(frames).glob("f_*.png")))
        hold = max(0.0, d - nfr / float(fps))
        vf = f"scale={w}:{h},setsar=1"
        if fade and d > fade * 2 + 0.15:
            g = []
            if fade_in:
                g.append(f"fade=t=in:st=0:d={fade:.2f}")
            if fade_out:
                g.append(f"fade=t=out:st={d - fade:.3f}:d={fade:.2f}")
            if g:
                vf += "," + ",".join(g)
        def build(enc):
            return ([ffmpeg_bin(), "-y", "-framerate", str(fps),
                     "-i", str(pathlib.Path(frames) / "f_%04d.png"), "-i", str(audio),
                     "-vf", f"tpad=stop_mode=clone:stop_duration={hold:.3f},{vf}"] + _common(enc, out_fps) +
                    ["-t", f"{d:.3f}", "-shortest", str(out)])
        r = _ffmpeg(build, "片段 %s" % img)
        if r.returncode != 0:
            raise SystemExit("片段失败 %s: %s" % (img, (r.stderr or "")[-400:]))
        return out

    if mask and (still or video):
        # 槽位坐标一律由调用方按版式算好传进来（frame_box / trio_box），
        # 别在这里按 .328/.344 再算一遍，方向或版式一换就对不上。
        return ml.part(video or still, audio, out, w, h, box=box or ml_frame.frame_box(w, h),
                       mask=str(img), fit=fit, zoom=zoom, fade=fade,
                       fade_in=fade_in, fade_out=fade_out, fps=ofps,
                       bg_slot=bg_slot)
    if clip:
        # 实时录制片段（已是 CFR mp4）：公共件按音频时长补齐/裁齐
        return ml.part(clip, audio, out, w, h, fit=fit, fade=fade,
                       fade_in=fade_in, fade_out=fade_out, fps=ofps)
    return ml.part(img, audio, out, w, h, fit=fit, fade=fade,
                   fade_in=fade_in, fade_out=fade_out, fps=ofps)

def _relp(p, base):
    return pathlib.Path(os.path.relpath(str(p), str(base))).as_posix()


def concat(parts, out, tmp=None):
    lst = pathlib.Path(tmp or pathlib.Path(out).parent) / "_concat.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write("file '%s'\n" % pathlib.Path(p).as_posix())
    r = subprocess.run([ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("拼接失败：" + (r.stderr or "")[-400:])
    return out

def mix_bgm(video, bgm, out, vol=0.20, tmp=None):
    """背景配乐混入成片音轨（fxai-slide-video 04 册「BGM 环节」执行点）。

    - BGM 无限循环后 atrim 到成片时长（整片铺满，不给静音机会）；
    - volume=vol 垫底（默认 0.20，约低于口播 14dB，口径同 fxai-ad-video 04 册）；
    - 与口播 amix（normalize=0：口播不因混合被归一压低）；
    - 混后整片 loudnorm 到 -16 LUFS / TP -1.5（既有响度口径，交付一致）。
    """
    base = pathlib.Path(video).parent
    # 配乐路径兜底：相对路径先按当前目录找，找不到按项目根找（混音端 cwd 是项目根）
    b = pathlib.Path(bgm)
    if not b.is_absolute():
        if not b.is_file() and (base / b).is_file():
            b = base / b
        b = pathlib.Path(os.path.abspath(str(b)))
    if not b.is_file():
        raise SystemExit("找不到配乐文件：%s（配乐怎么来见 04 册「BGM 环节」）" % bgm)
    o = _relp(out, base)
    d = dur(video)
    fc = (
        "[1:a]aloop=loop=-1:size=2097152,atrim=duration=%.3f,asetpts=PTS-STARTPTS,"
        "volume=%.3f[bg];[0:a][bg]amix=inputs=2:duration=first:normalize=0[a1];"
        "[a1]loudnorm=I=-16:TP=-1.5:LRA=11[aout]" % (d, vol)
    )
    r = subprocess.run(
        [ffmpeg_bin(), "-y", "-i", _relp(video, base), "-i", b.as_posix(),
         "-filter_complex", fc, "-map", "0:v", "-map", "[aout]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", o],
        cwd=base, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("BGM 混音失败：" + (r.stderr or "")[-400:])
    return out


def _attach_cover(cover_png, video, out, frames, base):
    """铺首帧封面：转调 fxai-poster 的 `attach_to_video`。

    帧数、垫音（25Hz 正弦）、接缝规则的全部口径都在 fxai-poster 05/04 册，
    这里只负责找技能、找图、把路走通，**不复写第二套数**。
    """
    sp = pathlib.Path(__file__).resolve().parent.parent / "fxai-poster"
    if str(sp) not in sys.path:
        sys.path.insert(0, str(sp))
    try:
        import poster as _poster
    except Exception as e:
        raise SystemExit("铺首帧封面需要 fxai-poster 技能，未能加载（%s）：%s" % (sp, e))
    c = pathlib.Path(cover_png)
    if not c.is_absolute() and not c.is_file() and (base / c).is_file():
        c = base / c
    if not c.is_file():
        raise SystemExit("找不到封面图：%s" % cover_png)
    return _poster.attach_to_video(str(c), str(video), out=str(out), frames=int(frames))


def burn(video, ass, out, fps=None, cwd=None):
    base = pathlib.Path(cwd) if cwd else pathlib.Path(video).parent
    ofps = fps if fps is not None else FPS
    def build(enc):
        v = _venc.delivery_venc(audio_copy=True, extra=["-r", str(ofps)])
        # 必须 cwd=项目根 + 相对路径：subtitles 滤镜遇到中文绝对路径会挂
        return ([ffmpeg_bin(), "-y", "-i", _relp(video, base),
                 "-vf", f"subtitles=filename={_relp(ass, base)}"] + v +
                [_relp(out, base)])
    enc = _encoder()
    r = subprocess.run(build(enc), cwd=base, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 and enc != "libx264":
        print("[warn] 烧录用 %s 失败，回落 libx264 重试" % enc, flush=True)
        r = subprocess.run(build("libx264"), cwd=base, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("烧录失败：" + (r.stderr or "")[-400:])
    return out

# ---------- CLI ----------
def main():
    import argparse
    ap = argparse.ArgumentParser(description="拼接配图段 + 排字幕 + 烧录成片")
    ap.add_argument("--project", required=True, help="项目名（产物落 <工作区>/<项目名>/）")
    ap.add_argument("--root", default=None, help="工作区根目录（默认当前工作区）")
    ap.add_argument("--segs", help="段落映射 JSON：[[段号, 配图名], ...]；不给则按序号自动配对")
    ap.add_argument("--no-burn", action="store_true", help="不烧字幕")
    ap.add_argument("--fade", type=float, default=0.0,
                    help="换页淡入淡出时长(秒)：>0 则每页视频首尾淡到黑，实现黑场换页过渡（音频不淡）")
    ap.add_argument("--fps", type=int, default=None,
                    help="输出帧率（默认 24；营销快节奏建议 30，与录制帧率一致可跳过二次重采样）")
    ap.add_argument("--size", default=None,
                    help="输出尺寸预设：竖屏(768×1280，默认) / 竖屏1366(768×1366) / 竖屏高清(1080×1920) / "
                         "横屏(1366×768) / 横屏小(1280×720) / 横屏高清(1920×1080)；也可直写 1366x768")
    ap.add_argument("--w", type=int, default=None, help="输出宽度（覆盖 --size；须与配图/录制一致）")
    ap.add_argument("--h", type=int, default=None, help="输出高度（覆盖 --size；须与配图/录制一致）")
    ap.add_argument("--sub-bottom", type=int, default=None,
                    help="字幕距画面底部像素（默认 round(80*H/1280)）。上中下分栏压进中区下沿用 ~455")
    ap.add_argument("--sub-size", type=int, default=None,
                    help="字幕字号（默认横版 40 / 竖版 36）。上中下分栏媒体区矮，用 ~46 才看得清")
    ap.add_argument("--sub-color", default=None,
                    help="字幕主色 #RRGGBB（不传=默认暖白+墨绿描边）。上中下分栏用 #FFD84D 黄字黑边")
    ap.add_argument("--zoom", type=float, default=0.0,
                    help="上中下分栏中区缓慢推近倍数（0=不推；0.05 约放大 5%%，全片呼吸感更足）")
    ap.add_argument("--trans", default=None,
                    help="段内多图转场（覆盖规格里的 trans）：push_left/左右推移、push_up/上下切换、"
                         "fade、cut、mix；也可给 list 轮换")
    ap.add_argument("--trans-dur", type=float, default=None,
                    help="转场时长秒（默认 0.55；越大越柔，0.3 更利落）")
    ap.add_argument("--bgm", default=None,
                    help="背景配乐：给文件路径（wav/mp3/…）或给 auto（按成片时长自动生成，落 配乐/）。"
                         "铺满整片（含封面段），口播不降，BGM 垫底。见 04 册「BGM 环节」")
    ap.add_argument("--bgm-volume", type=float, default=None,
                    help="BGM 混音音量（不给则按人声档自动：纯器乐 0.20、带唱 0.15；"
                         "段间留白多可整体抬到 0.25–0.28）")
    ap.add_argument("--bgm-style", dest="bgm_style", default=None,
                    help="--bgm auto 时给风格描述（默认内置 caption：明亮克制、中频留空）")
    ap.add_argument("--bgm-vocal", dest="bgm_vocal", default="none",
                    help="--bgm auto 的人声档：none（默认，纯器乐）或描述如「轻柔女声哼唱」（须配歌词）")
    ap.add_argument("--bgm-lyrics", dest="bgm_lyrics", default=None,
                    help="--bgm auto 带唱时的歌词文本")
    ap.add_argument("--bgm-lyrics-file", dest="bgm_lyrics_file", default=None,
                    help="--bgm auto 带唱时从 .txt 读歌词")
    ap.add_argument("--bgm-model", dest="bgm_model", default="minimax3", choices=["minimax3", "yue2"],
                    help="--bgm auto 用哪个音乐模型（默认 minimax3；yue2 更慢但可给更长）")
    ap.add_argument("--cover", default=None,
                    help="视频封面 PNG：烧完字幕就铺首帧（10 帧/0.4s，垫音照旧），落 成片_*_带封面.mp4。"
                         "🔴 铺封面一定排在 BGM 之前 —— BGM 是最后一道，从第 0 帧（封面第一帧）铺满整片")
    ap.add_argument("--cover-frames", dest="cover_frames", type=int, default=10,
                    help="封面段帧数（默认 10；按帧不按秒。24fps 下 0.4167s、25fps 下 0.4s）")
    a = ap.parse_args()
    bgm_auto = False          # --bgm auto 现场生成的配乐，交付清单要标明

    # 尺寸：--w/--h 显式 > --size 预设 > 默认竖屏 768×1280（解析后 a.w/a.h 一定是具体数字）
    a.w, a.h, _sz_src = sv_layout.resolve_size(a.size, a.w, a.h)
    globals()["W"] = int(a.w)
    globals()["H"] = int(a.h)
    if a.sub_size:
        globals()["SUB_FONT_SIZE"] = int(a.sub_size)
    if a.sub_color:
        globals()["SUB_COLOR"] = str(a.sub_color)
    if a.fps:
        globals()["FPS"] = int(a.fps)
    print("[fps] 输出 %dx%d @ %d帧  编码器 %s" % (W, H, FPS, _encoder()), flush=True)

    # 🔴 项目存储格式唯一入口（见 sv_layout.py）：
    #    成片 / 合并 / 字幕 / 文案直接落项目根（打开项目就看得见），
    #    成组的配图进 配图/，中间产物进 _tmp/。
    L = sv_layout.layout(a.project, a.root)
    base, img_dir = L["root"], L["image"]
    video_dir, doc = base, base
    tmp = L["tmp"]; tmp.mkdir(parents=True, exist_ok=True)
    print("[layout] 项目目录：%s" % base, flush=True)

    # 🔴 运行日志落项目 _tmp/（工作区根目录保持干净）
    _ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    _log = tmp / ("media_%s.log" % _ts)
    class _Tee:
        def __init__(self, *s):
            self.s = s
        def write(self, x):
            for o in self.s:
                o.write(x)
        def flush(self):
            for o in self.s:
                o.flush()
    sys.stdout = _Tee(sys.stdout, open(str(_log), "a", encoding="utf-8", buffering=1))
    sys.stderr = _Tee(sys.stderr, open(str(_log), "a", encoding="utf-8", buffering=1))
    print("[log] 运行日志 -> %s" % _log, flush=True)

    if a.segs:
        segs = json.loads(pathlib.Path(a.segs).read_text(encoding="utf-8"))
    else:
        imgs = sorted(img_dir.glob("*.png"))
        adir = audio_dir(base)
        auds = sorted(adir.glob(f"{a.project}*.wav"),
                      key=lambda p: int(re.search(r"(\d+)\.wav$", p.name).group(1)))
        if len(imgs) != len(auds):
            raise SystemExit(f"配图 {len(imgs)} 张 与 音频 {len(auds)} 段 数量不符，请给 --segs")
        # 直接记音频路径：项目名以数字结尾时（如 xx_v2），再按「项目名+序号」拼文件名会错位
        segs = [[str(au), im.stem] for im, au in zip(imgs, auds)]

    # 上中下分栏：哪几段的中区是外部素材（{配图名: {"kind": image|video, "path": 素材}}）。
    # 这些段的配图是「壳遮罩」（带 alpha），素材由这里垫进去。
    fmap = {}
    _fm = tmp / "frame_media.json"
    if _fm.exists():
        try:
            fmap = json.loads(_fm.read_text(encoding="utf-8"))
            print("[frame] 中区放映 %d 段（遮罩 + 素材）" % len(fmap), flush=True)
        except Exception as e:
            print("[warn] frame_media.json 读不了，按普通配图处理：%s" % e, flush=True)
            fmap = {}

    # 字幕位置：带壳版式（frame / trio）的上下带会压住字幕，位置必须跟着中区槽走，
    # 不得沿用「距底 80*H/1280」的通用值 —— 横屏下那个值正好落在下带、跟品牌 logo 撞。
    # 只在调用方没显式指定（SUB_MARGIN_V 为 None）时推算；显式给了就以调用方为准。
    if fmap and SUB_MARGIN_V is None:
        _m0 = next(iter(fmap.values()))
        _lay = _m0.get("layout") or "frame"
        if _lay == "trio":
            _tx, _ty, _tw, _th = ml_trio.trio_box(W, H, _m0.get("media_w"),
                                                  _m0.get("portrait_media"))
            if _th >= H - 2:      # 满高竖条（竖素材，画布横竖都一样）：没有下带，贴底留 7.5%
                globals()["SUB_MARGIN_V"] = max(20, int(round(H * 0.075)))
            else:                 # 16:9 横带（横素材）：压在中槽下沿内侧
                globals()["SUB_MARGIN_V"] = max(20, H - (_ty + _th) + 16)
        else:
            _bx, _by, _bw, _bh = ml_frame.frame_box(W, H)
            globals()["SUB_MARGIN_V"] = max(20, H - (_by + _bh) + 16)     # 压在中区下沿内侧
        print("[sub] 字幕位置跟中区走：bottom=%d（%s %dx%d）"
              % (SUB_MARGIN_V, _lay, W, H), flush=True)

    parts = []
    audios = []
    nseg = len(segs)
    for i, (num, img) in enumerate(segs, 1):
        # 给的是音频文件路径就直接用；给的是序号才按「项目名+序号」找
        audio = (pathlib.Path(num) if str(num).lower().endswith((".wav", ".mp3", ".flac", ".m4a"))
                 else audio_of(base, a.project, num))
        audios.append(audio)
        fr = img_dir / "anim" / img
        clip = fr / "clip.mp4"                       # 实时录制版优先
        use_fr = (fr / "f_0000.png").exists()        # 逐帧定格版
        use_clip = clip.exists()
        media = fmap.get(img) or {}
        # 段内素材：新格式 {"items":[...]}；老格式 {"path":...} 也认（当单张处理）
        items = media.get("items") or ([media] if media.get("path") else [])
        use_mask = bool(items)
        still = video = None
        zoom = a.zoom if use_mask else 0.0
        fit = (items[0].get("fit") if items else None) or "cover"
        slot_box = None
        if use_mask:
            # 槽位按该段登记的版式取：trio = 满高竖条 / 16:9 横带（由槽里素材方向定），
            # 其余（frame / 老数据没写 layout）按上中下分栏的全宽 16:9 一条带。
            if (media.get("layout") or "frame") == "trio":
                slot_box = ml_trio.trio_box(W, H, media.get("media_w"),
                                            media.get("portrait_media"))
            else:
                slot_box = ml_frame.frame_box(W, H)   # 中区框，横屏 896×504 / 竖屏 768×440
            _bw, _bh = slot_box[2], slot_box[3]
            if any(it.get("kind") == "page" for it in items):
                print("  [page] 段%02d 中区网页：%s" % (
                    i, "、".join(str((it.get("page") or {}).get("type") or "text")
                                for it in items if it.get("kind") == "page")), flush=True)
                items = materialize_pages(items, tmp / "page", "s%02d" % i, _bw, _bh,
                                          dur(audio), a.fps or FPS)
                fit = (items[0].get("fit") if items else None) or "cover"
        if use_mask and len(items) > 1:
            # 段内多图 → 先做一条轮播片（推移/上下切换/淡入），再垫到壳下面
            dseg = dur(audio)
            video = build_reel(items, tmp / f"reel{i}.mp4", dseg, _bw, _bh,
                               fps=a.fps, trans=(a.trans or media.get("trans")),
                               trans_dur=(a.trans_dur if a.trans_dur is not None
                                          else media.get("trans_dur")), zoom=zoom)
            zoom = 0.0                       # 推近已在轮播片里做掉了
            fit = "cover"                    # 轮播片已是成品尺寸，别再套 contain 补边
            tagx = " [reel %d 图/%s]" % (len(items), media.get("trans") or "push_left")
        elif use_mask:
            tagx = ""
            if items[0].get("kind") == "video":
                video = items[0].get("path")
            else:
                still = items[0].get("path")
        else:
            tagx = ""
        # 全画布虚化背景层（bg_slot）：中区素材（视频/图片）兼作整幅虚化底，
        # 壳（上带标题 / 下带品牌）与中区清晰素材浮在虚化底上。
        # trio = 中槽完整竖放、两翼有虚化底；frame = 上下带铺当前段素材的虚化放大版。
        # 网页段 materialize 后已变成页面录屏 clip（kind=video），同样作虚化底。
        bg_slot = (video or still) if use_mask else None
        parts.append(make_part(img_dir / f"{img}.png", audio, tmp / f"p{i}.mp4",
                               w=W, h=H,
                               fade=a.fade,
                               fade_in=((i != 1) and not (use_fr or use_clip)),
                               fade_out=(i != nseg),
                               frames=(fr if use_fr else None),
                               clip=(clip if use_clip else None),
                               mask=use_mask,
                               still=still,
                               video=video,
                               zoom=zoom,
                               out_fps=a.fps,
                               fit=fit,
                               box=slot_box,
                               bg_slot=bg_slot))
        tag = "[rec]" if use_clip else ("[anim]" if use_fr else ("[frame]" if use_mask else ""))
        print("OK", "%02d" % i, img, tag + tagx, flush=True)

    merged = concat(parts, video_dir / f"合并_{W}x{H}.mp4", tmp=tmp)
    print("MERGED", sv_layout.rel(base, merged), f"{dur(merged):.2f}s", flush=True)

    # 配乐：这一步只**准备**配乐文件，真正的混音排在铺封面之后（最后一道工序）
    _bgm_file = None
    bgm_vocal = "none"
    if a.bgm:
        if str(a.bgm).strip().lower() == "auto":
            import sv_bgm
            _need = int(dur(merged)) + 2          # 比成片多一点，末尾多留余量不断乐
            _lyr = a.bgm_lyrics
            if a.bgm_lyrics_file:
                _lf = pathlib.Path(a.bgm_lyrics_file)
                if not _lf.is_absolute() and (base / _lf).is_file():
                    _lf = base / _lf
                if not _lf.is_file():
                    raise SystemExit("找不到歌词文件：%s" % a.bgm_lyrics_file)
                _lyr = _lf.read_text(encoding="utf-8").strip()
            r = sv_bgm.ensure(base / "配乐", duration=_need, style=a.bgm_style,
                              model=a.bgm_model, vocal=a.bgm_vocal, lyrics=_lyr)
            a.bgm = r["wav"]
            bgm_vocal = r.get("vocal") or "none"
            bgm_auto = True
            print("[bgm] 自动生成配乐 → %s（%.1fs，%s）"
                  % (sv_layout.rel(base, a.bgm), r["duration"],
                     ("带唱：" + bgm_vocal) if sv_bgm.is_vocal(bgm_vocal) else "纯器乐"),
                  flush=True)
        # 音量：显式给出优先；否则纯器乐 0.20、带唱 0.15（有词要和口播抢注意力，垫更后）
        import sv_bgm as _sb
        if a.bgm_volume is None:
            a.bgm_volume = _sb.VOL_VOCAL if _sb.is_vocal(bgm_vocal) else _sb.VOL_INSTRUMENTAL
        _bgm_file = a.bgm
        print("[bgm] 配乐就绪 → %s（vol %.2f）；混音排在铺封面之后，从第 0 帧铺满整片"
              % (sv_layout.rel(base, _bgm_file), a.bgm_volume), flush=True)

    script = sv_layout.find(base, "口播文案.md")
    lines = [l.strip() for l in script.read_text(encoding="utf-8").split("\n") if l.strip()]
    n = speech_subtitles(lines, audios, doc, words_dir=(tmp / "speech_words"),
                         margin_v=a.sub_bottom)
    cnt = check_subtitles(doc / "字幕.srt")
    print("字幕 %d 条，校验通过 → %s" % (cnt, sv_layout.rel(base, doc / "字幕.srt")), flush=True)

    if not a.no_burn:
        final = video_dir / ("成片_%dx%d%s.mp4" % (W, H, "_带封面" if a.cover else ""))
        cur = burn(merged, doc / "字幕.ass", tmp / "burned.mp4", cwd=base)

        # 铺首帧封面：垫音照旧（25Hz 正弦），10 帧/0.4s，规则全在 fxai-poster 05/04 册
        if a.cover:
            cur = _attach_cover(a.cover, cur, tmp / "covered.mp4", a.cover_frames, base)
            print("COVER", sv_layout.rel(base, cur), "%d 帧" % a.cover_frames, flush=True)

        # 🔴 BGM 是最后一道：作用于**已铺封面的整片**，从第 0 帧（封面第一帧）铺满到片尾。
        #    先混 BGM 再铺封面会把封面段换成垫音，开头就缺一截配乐。
        if _bgm_file:
            cur = mix_bgm(cur, _bgm_file, tmp / "final.mp4", vol=a.bgm_volume)
            print("BGM 混音完成：vol=%.2f（含封面段，从第 0 帧铺满，口播不降）"
                  % a.bgm_volume, flush=True)

        shutil.copy(str(cur), str(final))
        out = final
        print("DONE", sv_layout.rel(base, final), f"{dur(final):.2f}s", flush=True)

    # 交付清单落项目根：按实际产物重建（路径相对项目根）
    try:
        if a.bgm:
            _nm = pathlib.Path(a.bgm).name
            import sv_bgm as _sb2
            _voc = "，带唱" if _sb2.is_vocal(bgm_vocal) else ""
            _bgm_txt = (("含背景配乐（自动生成 %s%s，vol %.2f）" % (_nm, _voc, a.bgm_volume))
                        if bgm_auto else ("%s%s（vol %.2f）" % (_nm, _voc, a.bgm_volume)))
        else:
            _bgm_txt = "无配乐"
        _extra = {"bgm": _bgm_txt}
        if a.cover:
            _extra["cover"] = {"png": pathlib.Path(a.cover).name,
                               "frames": a.cover_frames or 10,
                               "note": "铺首帧排在 BGM 之前，配乐从封面第一帧起铺满"}
        sv_layout.write_delivery(a.project, a.root, extra=_extra)
    except Exception as e:
        print("[warn] 交付清单写入失败（产物本身已生成）：%s" % e, flush=True)

if __name__ == "__main__":
    main()
