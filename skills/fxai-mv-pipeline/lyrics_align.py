# -*- coding: utf-8 -*-
"""歌词精确对齐（Lyric-First）模块 —— MV 管线的「音乐之后、创作之前」关键一步。

音乐一出来，先用 whisper 把整首歌的**真实时间轴 + 识别文本**取出来，
与我们的歌词做强制对齐，拿到「每句歌词的真实起止秒」，再按音频分段逐段判断
「这段有没有人在唱、从第几秒唱到第几秒」→ 分镜与提示词才写得准。

产出（落在项目目录）：
  - 字幕.srt / 字幕.ass   ← 最终烧录用，**全流程只识别这一次**
  - 歌词时间轴.json        ← 机器/人都能读：分段表 + 每段的段落类型 + 段内人声窗口 + 命中歌词

★ 字幕只做一次：成片阶段直接用这里的 字幕.ass 烧录，whisper 只跑这一次；
  仅当人工检查发现偏移/错字时，才用 --regen-subtitle 重跑并重合成。

用法（命令行自检）：
  python lyrics_align.py --audio 音乐.wav --lyrics 歌词.txt --out-dir <项目目录>
"""


import os
import re
import sys
import json
import difflib
import collections
import subprocess
import shutil

# ---------------------------------------------------------------------------
# 路径：一律从用户级全局部署配置 ~/.workbuddy/fxai_global.json 推导，不写死机器路径
# ---------------------------------------------------------------------------
def _load_global():
    p = os.environ.get(
        "FXAI_GLOBAL_CONFIG",
        os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json"))
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


_G = _load_global()


def ffmpeg_path():
    """带 libass 的 ffmpeg（conda 自带的没有 libass，中文会烧成豆腐块）。"""
    c = _G.get("ffmpeg") or ""
    if c and os.path.isfile(c):
        return c
    si = _G.get("software_install_dir") or ""
    if si:
        p = os.path.join(si, "ffmpeg", "ffmpeg.exe")
        if os.path.isfile(p):
            return p
    return "ffmpeg"


SUB_SCRIPT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "fxai-lyric-subtitle",
    "scripts", "gen_lyric_subtitle.py"))


def _audio_duration(audio):
    """音频真实时长（秒）。wav 直接读头，其余用 ffprobe；失败返回 None。"""
    try:
        import wave
        with wave.open(audio, "rb") as w:
            fr = w.getframerate() or 1
            return w.getnframes() / float(fr)
    except Exception:
        pass
    ff = ffmpeg_path()
    fp = os.path.join(os.path.dirname(os.path.abspath(ff)), "ffprobe.exe")
    if os.path.isfile(fp):
        try:
            r = subprocess.run(
                [fp, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", audio],
                capture_output=True, text=True, timeout=30)
            return float(r.stdout.strip())
        except Exception:
            return None
    return None


def _clamp_items(items, duration):
    """硬约束：任何歌词窗不得超出音频时长。"""
    if not duration:
        return items
    out = []
    for it in items:
        e = float(it.get("end", 0))
        if e > duration - 0.05:
            it = dict(it)
            it["end"] = round(max(float(it.get("start", 0)) + 0.1, duration - 0.05), 2)
            it["clamped"] = True
        out.append(it)
    return out


def parse_lyrics_struct(text):
    """歌词文本 → [(句子, 结构段名)]。

`[主歌]` / `[副歌]` / `[间奏]` 这类方括号行本身不作为唱词，只作为结构分隔，
其后的歌词行归属该结构（默认「主歌」）。与 gen_lyric_subtitle 的过滤规则一致。
"""

    out, cur = [], "主歌"
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("[") and s.endswith("]"):
            cur = s.strip("[]").strip() or cur
            continue
        out.append((s, cur))
    return out


# ★ 人声判定必须基于「whisper 原始时间刻度」，歌词强制对齐结果只作参考不作依据。
# 歌词强制对齐会把真唱段当幻觉丢掉，把「段内其实在唱」判成无人声。
# 所以这里自己做一份**保守**过滤：只丢真正的幻觉，不用相似度一刀切。
_HALLU_RE = re.compile(
    r"詞曲|詞\s*李宗盛|作曲|作词|编曲|李宗盛|独播剧场|Television|Exclusive|YoYo|河上床|杨茜|感谢观看|"
    r"字幕|字幕由|提供|转载|请订阅|订阅|点赞|关注|版权|未经授权"
)


def parse_transcript(path):
    """解析 whisper 转写文件（每行 `[start->end] 文本`）→ [(start, end, text), ...]。"""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\[([\d.]+)\s*->\s*([\d.]+)\]\s*(.*)", line.strip())
                if m:
                    out.append((float(m.group(1)), float(m.group(2)), m.group(3).strip()))
    except Exception:
        pass
    return out


def _sim_chars(a, b):
    a2 = (a or "").replace(" ", "").replace("　", "")
    b2 = (b or "").replace(" ", "").replace("　", "")
    if not a2 or not b2:
        return 0.0
    return difflib.SequenceMatcher(None, a2, b2, autojunk=False).ratio()


def filter_vocal_segments(segs, lyrics=None, duration=None, repeat_min=3, sim_ok=0.35):
    """从 whisper 原始段里剔除**真正**的幻觉，保留所有人声（含副歌重复）。

过滤条件（保守，宁可多留）：
  ① 台标/署名类关键词（詞曲 李宗盛、独播剧场、Television…）→ 必丢；
  ② 文本 ≤2 字 → 丢；
  ③ 起点超出音频时长 → 丢；
   ④ 同一文本重复 ≥N 次 **且** 与任何歌词都不像（sim < sim_ok）→ 丢。
     ⚠️ 必须带相似度条件：副歌重复是正常演唱，只按重复次数删会把真演唱删掉。
"""

    lyrics = [l for l in (lyrics or []) if l]
    cnt = collections.Counter(s[2] for s in segs)
    keep, dropped = [], []
    for s, e, t in segs:
        tt = (t or "").strip()
        if not tt:
            continue
        if _HALLU_RE.search(tt):
            dropped.append((s, e, tt, "幻觉关键词")); continue
        if len(tt) <= 2:
            dropped.append((s, e, tt, "过短")); continue
        if duration and s >= duration:
            dropped.append((s, e, tt, "超出音频时长")); continue
        mx = max((_sim_chars(l, tt) for l in lyrics), default=0.0)
        if cnt[tt] >= repeat_min and mx < sim_ok:
            dropped.append((s, e, tt, "重复%d次且不像歌词(sim=%.2f)" % (cnt[tt], mx))); continue
        keep.append((s, e, tt))
    return keep, dropped


def _group_words(words, gap=0.6):
    """词级行 [(a,b,word)] → 短语级段 [(a,b,text)]（间隙 <gap 秒连续合并）。

whisper 词级时间戳在唱词内部间隙通常 <0.5s，换气/间奏处 >0.6s；
合并后既保留词级边界精度，又给 DP 提供带文本的匹配单元。
"""

    out = []
    for a, b, t in sorted(words):
        t = (t or "").strip()
        if not t:
            continue
        if out and a - out[-1][1] <= gap:
            pa, pb, pt = out[-1]
            out[-1] = (pa, max(pb, b), pt + t)
        else:
            out.append((a, b, t))
    return out


def _align_lines_words(words, lyrics, sections=None, gap=0.6):
    """快路径两阶段对齐（whisper 只跑一次词级时用）。

确定性两阶段：
  1) 行→短语：单调分配 DP（软先验=按字数比例的期望短语位置）；
  2) 短语内切分：短语是连续演唱单元（内部全是唱词时间），
     按各行字数权重把短语内的「词」分给各行，边界=词级时间戳。
返回 [{index,start,end,lyric,whisper,section}, ...]
"""

    phrases = _group_words(words, gap=gap)
    n, m = len(phrases), len(lyrics)
    if not n or not m:
        return []
    w = [max(1, len(l.replace(" ", "").replace("　", ""))) for l in lyrics]
    W = float(sum(w))
    sim = [[_sim_chars(lyrics[i], phrases[j][2]) for j in range(n)] for i in range(m)]
    # 软先验：第 i 行的期望短语位置 ≈ 前缀字数占比 × (n-1)
    cum, pos = 0, []
    for i in range(m):
        pos.append((n - 1) * cum / W)
        cum += w[i]
    NEG = float("-inf")
    dp = [[NEG] * n for _ in range(m)]
    par = [[-1] * n for _ in range(m)]
    for j in range(n):
        dp[0][j] = sim[0][j] - 0.08 * abs(j - pos[0])
    for i in range(1, m):
        best_prev, best_j = NEG, 0
        for j in range(n):
            if dp[i - 1][j] > best_prev:
                best_prev, best_j = dp[i - 1][j], j
            dp[i][j] = best_prev + sim[i][j] - 0.08 * abs(j - pos[i])
            par[i][j] = best_j
    # 回溯：取末行最优短语
    j = max(range(n), key=lambda x: dp[m - 1][x])
    assign = [0] * m
    for i in range(m - 1, -1, -1):
        assign[i] = j
        if i > 0:
            j = par[i][j] if par[i][j] >= 0 else j
    # 行→短语 连续段
    runs = []
    s = 0
    for i in range(1, m + 1):
        if i == m or assign[i] != assign[s]:
            runs.append((s, i - 1, assign[s]))
            s = i
    items = []
    for (i1, i2, j) in runs:
        pa, pb, ptext = phrases[j]
        pw = [(a, b, t) for (a, b, t) in words if pa - 1e-6 <= a < pb + 1e-6]
        nlines = i2 - i1 + 1
        tot = sum(w[i] for i in range(i1, i2 + 1))
        if not pw:
            # 理论少见：短语无词，按时间比例切
            span = (pb - pa) / nlines
            for k, i in enumerate(range(i1, i2 + 1)):
                items.append({
                    "index": i + 1, "start": round(pa + k * span, 2),
                    "end": round(pa + (k + 1) * span, 2), "lyric": lyrics[i],
                    "whisper": ptext[:24], "section": (sections or [None] * m)[i],
                })
            continue
        nw = len(pw)
        if nw >= nlines:
            # 按字数权重切词块；保证每行至少 1 词
            bounds, c = [0], 0
            for i in range(i1, i2 + 1):
                c += w[i]
                bounds.append(max(bounds[-1] + 1, min(nw - (nlines - len(bounds) - 1),
                                                       int(round(nw * c / tot)))))
            bounds[-1] = nw
            for k, i in enumerate(range(i1, i2 + 1)):
                wa, wb = bounds[k], bounds[k + 1]
                if wb <= wa:
                    wb = min(nw, wa + 1)
                blk = pw[wa:wb]
                items.append({
                    "index": i + 1,
                    "start": round(blk[0][0], 2), "end": round(blk[-1][1], 2),
                    "lyric": lyrics[i],
                    "whisper": "".join(x[2] for x in blk),
                    "section": (sections or [None] * m)[i],
                })
        else:
            # 词少行多：按时间比例切
            acc = 0.0
            for k, i in enumerate(range(i1, i2 + 1)):
                a0 = pa + (pb - pa) * acc / tot
                acc += w[i]
                a1 = pa + (pb - pa) * acc / tot
                items.append({
                    "index": i + 1, "start": round(a0, 2), "end": round(a1, 2),
                    "lyric": lyrics[i], "whisper": ptext[:24],
                    "section": (sections or [None] * m)[i],
                })
    return items


def _merge_repeated_segs(segs, sim_thr=0.72):
    """合并「相邻且文本高度相似」的 whisper 段（副歌重复演唱同一句时常见）。

whisper 常把重复演唱的同一句切成多段。这类重复会让对齐把多余段错误塞进相邻歌词行，时间轴即错位。
只合并**序列上相邻**的段，文本相似即合并，不跨句；合并后取时间并集，文本保留其一。
"""

    out = []
    for s, e, t in segs:
        if out and _sim_chars(out[-1][2], t) >= sim_thr:
            out[-1] = (out[-1][0], e, out[-1][2])
        else:
            out.append((s, e, t))
    return out


def align_lyrics_monotonic(vocal_segs, lyrics, sections=None, word_level=False):
    """把歌词**按演唱顺序**单调映射到 whisper 人声段：时间刻度全来自 whisper，文字换成我们的歌词。

策略：
  0. 先合并相邻相似段（副歌重复演唱合并成一段，重复段不会被塞进相邻歌词行）；
  1. 主体用「相似度 + 时长约束」的单调 DP：识别准的句子（副歌）被精确锁定，
     错字多的句子靠「该行时长 ≈ 按字数应得时长」兜底，严格单调不回退；
  2. DP 失败才 fallback 到「按字数比例吸附段边界」（同样单调、真实边界）。

word_level=True 时 vocal_segs 是词级单元（whisper 只跑一次的快路径）：
跳过 _merge_repeated_segs（相邻同字会被误并），放宽每行可消费的词块数。

返回 [{index, start, end, lyric, whisper, section}, ...]
"""

    if vocal_segs and not word_level:
        vocal_segs = _merge_repeated_segs(vocal_segs)
    n, m = len(vocal_segs), len(lyrics)
    if not n or not m:
        return []
    w = [max(1, len(l.replace(" ", "").replace("　", ""))) for l in lyrics]
    W = float(sum(w))
    t0, t1 = vocal_segs[0][0], vocal_segs[-1][1]
    T = max(0.001, t1 - t0)
    sim = [[_sim_chars(lyrics[i], vocal_segs[j][2]) for j in range(n)] for i in range(m)]
    if word_level:
        # 词级：每句歌词消费一段连续词块；词数≈字数，放宽到 2 倍冗余 + 余量
        max_take = max(6, (n // m) * 2 + 8)
    else:
        max_take = max(3, (n + m - 1) // m + 2)
    NEG = float("-inf")
    dp = [[NEG] * (n + 1) for _ in range(m + 1)]
    par = [[-1] * (n + 1) for _ in range(m + 1)]
    dp[0][0] = 0.0
    for i in range(1, m + 1):
        expect = T * w[i - 1] / W
        for j in range(1, n + 1):
            lo = max(i - 1, j - max_take)
            for k in range(lo, j):
                if dp[i - 1][k] == NEG:
                    continue
                dur = vocal_segs[j - 1][1] - vocal_segs[k][0]
                if word_level:
                    # 词级：全局跨度含间奏会令 expect 系统性偏大，绝对偏差惩罚失效
                    # 改用相对偏差，压缩/拉伸同权重
                    dev = abs(dur - expect) / max(expect, 1.0)
                    score = (dp[i - 1][k]
                             + sum(sim[i - 1][x] for x in range(k, j)) / float(j - k)
                             - 1.2 * dev)
                else:
                    score = (dp[i - 1][k] + sum(sim[i - 1][x] for x in range(k, j)) / float(j - k) - 0.10 * abs(dur - expect))
                if score > dp[i][j]:
                    dp[i][j] = score
                    par[i][j] = k
    if dp[m][n] > NEG:
        seg_of, j = [], n
        for i in range(m, 0, -1):
            k = par[i][j]
            if k < 0:
                seg_of = []
                break
            seg_of.append((k, j))
            j = k
        seg_of.reverse()
        if len(seg_of) == m and seg_of[0][0] == 0:
            items = []
            for i, (k, j) in enumerate(seg_of):
                items.append({
                    "index": i + 1,
                    "start": round(vocal_segs[k][0], 2),
                    "end": round(vocal_segs[j - 1][1], 2),
                    "lyric": lyrics[i],
                    "whisper": " ".join(vocal_segs[x][2] for x in range(k, j)),
                    "section": (sections or [None] * m)[i],
                })
            return items
    # fallback：按字数比例吸附段边界
    cum, targets = 0, []
    for wi in w:
        cum += wi
        targets.append(t0 + T * cum / W)
    items, j = [], 0
    for i, l in enumerate(lyrics):
        if i == m - 1:
            k = n - 1
        elif j < n:
            k = min(range(j, n), key=lambda x: abs(vocal_segs[x][1] - targets[i]))
        else:
            k = n - 1
        k = max(k, j)
        if j >= n:
            s = e = vocal_segs[n - 1][1]; raw = ""
        else:
            s = vocal_segs[j][0]; e = vocal_segs[k][1]
            raw = " ".join(vocal_segs[x][2] for x in range(j, k + 1))
        items.append({
            "index": i + 1, "start": round(s, 2), "end": round(e, 2),
            "lyric": l, "whisper": raw, "section": (sections or [None] * m)[i],
        })
        j = min(k + 1, n - 1) if (k + 1) < n else n
    return items


def _refine_with_word_spans(items, word_spans, max_line_sec=10.5,
                            min_density=2.0, gap_thr=0.5):
    """用「词级 whisper」人声窗修正句级对齐，解决大段合并/器乐间隙被算进唱词的问题。

用本函数把句级起点修正到词级开唱点，并把尾部非人声的间隙切掉，让时间轴更贴近真实演唱。

返回 (refined_items, report_lines)
"""

    if not items or not word_spans:
        return items, []
    spans = sorted([(float(a), float(b)) for a, b in word_spans if b > a])
    refined, report = [], []
    prev_end = 0.0
    for it in items:
        s, e = float(it.get("start", 0)), float(it.get("end", 0))
        txt = it.get("lyric", "")
        dur = max(0.0, e - s)
        nch = max(1, len(txt.replace(" ", "").replace("　", "")))
        density = nch / dur if dur > 0 else 999.0
        best_span, best_dur = None, 0.0
        for a, b in spans:
            x, y = max(a, s), min(b, e)
            if y > x:
                d = y - x
                if d > best_dur:
                    best_dur, best_span = d, (a, b)
        # 用原始词级窗（不限于当前句级窗）判断起点/终点偏差，才能发现「句级窗把器乐段包进来」
        lead = (best_span[0] - s) if best_span else 0.0
        tail = (e - min(best_span[1], e)) if best_span else 0.0
        # 需要的情况：窗口过长、密度过低、向词级窗外延伸过多
        need = (dur > max_line_sec or density < min_density or
                abs(lead) > gap_thr or tail > gap_thr)
        ns, ne = s, e
        if best_span and need:
            # 起点词级窗起点明显不同，且不会覆盖上一句 → 拉过去
            if abs(best_span[0] - s) > gap_thr and best_span[0] > prev_end:
                ns = best_span[0]
            else:
                ns = max(s, prev_end)
            # 终点按词级窗交集的尾部裁剪掉非人声尾巴（不扩到邻句）
            ne = min(best_span[1], e)
        ns = max(ns, prev_end)
        if best_span and need and ne > ns + 0.05:
            changed = abs(ns - s) > 0.05 or abs(ne - e) > 0.05
            it2 = dict(it)
            it2["start"] = round(ns, 2)
            it2["end"] = round(ne, 2)
            if changed:
                it2["refined"] = True
            refined.append(it2)
            if abs(ns - s) > 0.3 or abs(ne - e) > 0.3:
                report.append('  修正 #%02d [%s]  %.2f-%.2f → %.2f-%.2f' % (
                    it2.get("index", 0), txt, s, e, ns, ne))
            prev_end = ne
            continue
        # 没有可用人声窗，或无需保留原值（仅防止回退到上一句之前）
        it2 = dict(it)
        if s < prev_end - 0.05:
            it2["start"] = round(prev_end, 2)
        refined.append(it2)
        prev_end = max(prev_end, it2["end"])
    return refined, report


def _srt_time(t):
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return ("%02d:%02d:%06.3f" % (h, m, s)).replace(".", ",")


def _ass_time(t):
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return "%d:%02d:%05.2f" % (h, m, s)


# 字幕配色预设
# primary=主字色，outline=描边色，back=阴影色（半透明黑通用）
STYLE_PRESETS = {
    "white": {"label": "A 经典白",
              "primary": "&H00FFFFFF", "outline": "&H00181A1C"},
    "cream": {"label": "B 奶油暖黄（温馨/怀旧向）",
              "primary": "&H00C8E9FF", "outline": "&H00102846"},
    "mint": {"label": 'C 薄荷青（默认款）',
             "primary": "&H00F5FBE8", "outline": "&H003A3F0E"},
    "gold": {"label": "D 香槟金（大气/副歌感）",
             "primary": "&H00A8E4FF", "outline": "&H000E3246"},
    "sakura": {"label": "E 樱粉（甜美/情感）",
               "primary": "&H00EAE0FF", "outline": "&H002E1E3C"},
    "ice": {"label": "F 冰蓝（清冷/空灵）",
            "primary": "&H00FFF1DF", "outline": "&H00462818"},
}
DEFAULT_STYLE = "mint"  # 用户从 A-F 中选定薄荷青为全局默认


def ass_header(w=1280, h=768, style=DEFAULT_STYLE):
    """生成 ASS 头。★ PlayResX/Y 必须等于**视频实际分辨率**：

竖屏时若用错误的横屏画布，libass 按错误画布算坐标，字幕位置与字号全错（飘到画面外/压在人物脸上）。

★ 样式规范（MV 通用款）：
  - 微软雅黑加粗 + 配色见 STYLE_PRESETS（描边比纯黑柔和）
    + 半透明柔和投影（BackColour alpha 0x50）+ 字距 0.8；
  - 横屏字号加大一档：字号 ∝ 短边，横屏 0.056、竖屏 0.048；
  - 事件行 Text 为纯歌词文本，不挂内联特效标签（如 \\fad）——本机 libass 会把内联标签当字面文本烧出来。
"""

    w, h = int(w) if w else 1280, int(h) if h else 768
    st = STYLE_PRESETS.get(style or DEFAULT_STYLE, STYLE_PRESETS[DEFAULT_STYLE])
    land = w >= h
    fs = max(20, int(round(min(w, h) * (0.056 if land else 0.048))))
    mv = max(24, int(round(h * 0.078)))  # 下边距 ∝ 高（768→60，1280→100）
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: %d\nPlayResY: %d\nWrapStyle: 0\n\n" % (w, h)
        + "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Microsoft YaHei,%d,%s,&H000000FF,%s,&H50000000,"
        "1,0,0,0,100,100,0.8,0,1,2.5,1.2,2,30,30,%d,1\n\n" % (fs, st["primary"], st["outline"], mv)
        + "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Text\n"
    )


ASS_HEADER = ass_header(1280, 768)  # 兼容旧调用


def _split_long_line(txt, dur, max_sec=9.0):
    """一句歌词停留过久（长音/拖腔）→ 切成两拍显示，解决字幕一行挂十几秒。

返回 [(文本, 占比), ...]；占比按「去空格字数」加权。切不动（如纯单字）就整句返回。
"""

    if dur <= max_sec:
        return [(txt, 1.0)]
    parts = [p for p in txt.split(" ") if p]
    if len(parts) >= 2:  # 优先在空格处切，取最靠近中点的那个
        w = [len(p) for p in parts]
        tot = float(sum(w))
        best, bd = None, 1e9
        for k in range(1, len(parts)):
            d = abs(sum(w[:k]) / tot - 0.5)
            if d < bd:
                bd, best = d, k
        a = " ".join(parts[:best])
        b = " ".join(parts[best:])
    else:  # 没有空格 → 按字数中点硬切
        body = txt.strip()
        n = len(body)
        if n < 4:
            return [(txt, 1.0)]
        a, b = body[:n // 2], body[n // 2:]
    wa, wb = float(len(a.replace(" ", ""))), float(len(b.replace(" ", "")))
    t = wa + wb
    return [(a, wa / t), (b, wb / t)] if t > 0 else [(txt, 1.0)]


def _is_inst_placeholder(txt):
    """器乐/间奏占位文本（如「（器乐，无词）」「（器乐渐弱，风声与鸟鸣）」）。

这类文本来自 歌曲.md 歌词里的 [前奏]/[尾奏] 标记，**不是歌词，一律跳过不上屏**。
齐括号包裹 + 含标记词才算占位，正常带括号的歌词（如「哈哈（开心）」照常上屏。
"""

    t = (txt or "").strip()
    if not t:
        return True
    if t[0] in "（(【[" and t[-1] in "）)】]":
        inner = t[1:-1]
        if any(k in inner for k in ("器乐", "无词", "间奏", "渐弱", "风声", "纯音乐", "music")):
            return True
    return False


def write_subtitles(items, out_dir, out_name="字幕", karaoke=False,
                    max_line_sec=10.5, video_size=None, style=DEFAULT_STYLE):
    """按歌词时间轴写 字幕.srt / 字幕.ass（UTF-8 无 BOM，FontName=Microsoft YaHei）。

★ 超长句（> max_line_sec）自动切成两拍显示：歌词文本不变，只是分两行、各占对应时长，
  解决「一行字幕挂十几秒」的观感问题。
★ video_size=(w,h)：ASS 的 PlayResX/Y 与字号按**视频真实分辨率**生成（竖屏必备）。
★ 器乐/间奏占位（「（器乐，无词）」等）自动跳过，不上屏（见 _is_inst_placeholder）。
"""

    srt, ev, n = [], [], 0
    for it in items:
        s, e, txt = it["start"], it["end"], it.get("lyric", "")
        if _is_inst_placeholder(txt):
            continue
        dur = max(0.0, e - s)
        cur = s
        pieces = _split_long_line(txt, dur, max_line_sec)
        for j, (seg_txt, ratio) in enumerate(pieces):
            seg_end = e if j == len(pieces) - 1 else round(cur + dur * ratio, 2)
            n += 1
            srt.append(str(n))
            srt.append("%s --> %s" % (_srt_time(cur), _srt_time(seg_end)))
            srt.append(seg_txt)
            srt.append("")
            if karaoke:
                nch = max(1, len(seg_txt.replace(" ", "")))
                per = max(1, int(round((seg_end - cur) * 100.0 / nch)))  # \k 单位=厘秒
                kar = "".join("{\\k%d}%s" % (per, ch) for ch in seg_txt)
                ev.append("Dialogue: 0,%s,%s,Default,,0,0,0,%s"
                          % (_ass_time(cur), _ass_time(seg_end), kar))
            else:
                ev.append("Dialogue: 0,%s,%s,Default,,0,0,0,%s"
                          % (_ass_time(cur), _ass_time(seg_end), seg_txt))
            cur = seg_end
    p1 = os.path.join(out_dir, out_name + ".srt")
    p2 = os.path.join(out_dir, out_name + ".ass")
    with open(p1, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(srt))
    _w, _h = video_size if video_size else (1280, 768)
    with open(p2, "w", encoding="utf-8", newline="\n") as f:
        f.write(ass_header(_w, _h, style=style) + "\n".join(ev) + "\n")
    return p1, p2


# ===========================================================================
# whisper 多分段多版对齐（同一模型、不同分段长度硬切 9/11/13s 互为校验）
#
# 各版各自用我们的正确歌词替换识别文本，再逐行校对其对齐位置。分段是**硬切**
# （faster-whisper clip_timestamps），不是 VAD——VAD 对连续伴奏的歌曲无效
# （详见 whisper_word_passes 注释）。切点不同 → 边界效应/漏词的暴露点不同；
# 多版多数同意的行即高可信，只有分歧行才需人工复核。
# ===========================================================================

DEFAULT_SEG_LENS = (9.0, 11.0, 13.0)  # 三版硬切 9/11/13s（2s 间距）
_PRIMARY_SEG = 11.0  # 主版分段长度：三版 9/11/13 的中枢 11s，作合并基准/同分优先
_LINE_TOL = 1.5  # 行级校对容忍带（秒）：两版起点差在此内视为「一致」


def _is_cjk_or_alnum(c):
    return c.isalnum() or "\u4e00" <= c <= "\u9fff"


# 硬幻觉词（只可能是 whisper 幻觉、歌词里几乎不可能出现的）——
# 用于**词级连字剔除**：whisper 幻觉常被拆成单字词（中/文/字/幕/志/愿/者/李/宗/盛），
# 逐词匹配抓不住，必须把词拼起来命中后再映射回词删。
_HALLU_HARD_RE = re.compile(
    r"字幕|志愿者|李宗盛|独播剧场|Television|Exclusive|YoYo|河上床|杨茜|感谢观看|"
    r"未经授权|转载|请订阅|点赞|订阅|词曲|作词|作曲|编曲")


def _strip_halluc_words(words):
    """词级幻觉剔除（连字判断）→ (words, 剔除词数)。

硬切分段后，幻觉若被拆成单字词，短语级「含关键词就丢整条」会把同一短语里的真唱词一起删掉。
先把幻觉**词**摘掉再拼短语，真唱词就不会被连坐。
"""

    if not words:
        return words, 0
    chars, owner = [], []
    for i, (a, b, t) in enumerate(words):
        for ch in t:
            chars.append(ch)
            owner.append(i)
    s = "".join(chars)
    bad = set()
    for m in _HALLU_HARD_RE.finditer(s):
        for k in range(m.start(), m.end()):
            bad.add(owner[k])
    if not bad:
        return words, 0
    return [w for i, w in enumerate(words) if i not in bad], len(bad)


def whisper_word_passes(audio, out_dir, seg_lens=DEFAULT_SEG_LENS, duration=None):
    """同一音频按**不同分段长度硬切**各跑一版 whisper（词级），返回各版词表。

★ 硬切（faster-whisper clip_timestamps）而不是 VAD：VAD 对连续伴奏的歌曲无效
  （silero 切成 <9s 短块 → VAD 上限触发不到，多版转写字节相同，还会丢真唱）；
  硬切则切点不同 → 边界效应不同 → 两版真正互为校验。时间戳仍是整首绝对时间。
★ 硬切必须关 condition_on_previous_text（关键）：
  cond 开时上一片段的幻觉文本会成为下一片段的提示词、自我强化成幻觉链。
返回：[{"seg":9.0, "path":.., "words":[(a,b,text)], "error":..}, ...]
"""

    passes = []
    for sl in seg_lens:
        sl = float(sl)
        out = os.path.join(out_dir, "_transcript_%ds.txt" % int(round(sl)))
        rec = {"seg": sl, "path": out, "words": []}
        try:
            run_whisper(audio, out, word_ts=True, clip_seg=sl, duration=duration,
                        condition=False)
            rec["words"] = [(a, b, t) for a, b, t in parse_transcript(out)
                            if t and b > a and not _HALLU_RE.search(t)
                            and any(_is_cjk_or_alnum(c) for c in t)
                            and (duration is None or a < duration)]
        except Exception as e:
            rec["error"] = str(e)
            print("[warn] whisper 分段 %gs 版失败：%s" % (sl, e))
        passes.append(rec)
    return passes


def _pass_items(words, struct, duration=None):
    """一版 whisper 词表 → 正确歌词行时间轴（幻觉过滤 + 演唱顺序对齐）。

★ 顺序铁律仍适用：幻觉过滤在**短语级**做（能整条抓掉台标/署名幻觉），
  但我们只在短语内部用词级时间戳，歌词文字一律换成 struct 里的正确歌词。
"""

    lyrics = [s for s, _ in struct]
    sections = [sec for _, sec in struct]
    # 先摘掉幻觉**词**（连字判断），再拼短语——否则幻觉会连坐同短语里的真唱词
    words, _nbad = _strip_halluc_words(words)
    if _nbad:
        print("[align] 词级幻觉剔除 %d 个词（连字判断）" % _nbad)
    phrases = _group_words(words, gap=0.6)
    keep_p, dropped = filter_vocal_segments(phrases, lyrics, duration=duration)
    keep = [(a, b, t) for a, b, t in words
            if any(pa - 1e-6 <= a and b <= pb + 1e-6 for pa, pb, _ in keep_p)]
    items = _align_lines_words(keep, lyrics, sections=sections)
    spans = _merge_spans([(a, b) for a, b, _ in keep])
    return items, spans, dropped, keep


def _largest_cluster(cands, tol):
    """在若干候选版本里找「彼此起点&终点都落在 tol 内」的最大簇（通用，支持任意 N）。

cands: [(k, item), ...]；返回该簇在 cands 里的下标列表。
用途：多版硬切校正时，若 2 版一致、1 版离群，应取那 2 版的中位数而非被离群版带偏，
     只有「全部互不接近」才走分歧择版。通过「两两接近的最大团」通用求解，支持任意 N。
"""

    import itertools
    n = len(cands)
    if n <= 1:
        return list(range(n))

    def _close(a, b):
        return (abs(float(a[1]["start"]) - float(b[1]["start"])) <= tol
                and abs(float(a[1]["end"]) - float(b[1]["end"])) <= tol)

    # 邻接（两两是否接近）
    adj = [[_close(cands[i], cands[j]) for j in range(n)] for i in range(n)]
    # 从大到小枚举子集，首个「两两接近」(团) 即最大簇
    for r in range(n, 1, -1):
        for combo in itertools.combinations(range(n), r):
            if all(adj[i][j] for i in combo for j in combo if i < j):
                return list(combo)
    return [0]  # 全不接近 → 无簇（走分歧）


def merge_line_passes(passes, struct, tol=_LINE_TOL):
    """逐行校对各版歌词行时间轴（通用「行级表决」，不依赖任何模型的具体实现）。

passes: [{"seg":9.0,"items":[{index,start,end,lyric,whisper,...}]}, ...]（2 或 3 版均可）
规则（通用，与歌无关，已支持多版**多数簇表决**）：
  ① 仅 1 版有该句            → 采用（其余版多半漏识），src=single
  ② ≥2 版彼此起点&终点都落在 tol 内（多数簇）→ 取该簇中位数（比单版稳健）；
     全部版都落在簇内 = src=all；仅部分版（如 3 版里 2 版一致）= src=majority
  ③ 各版都有但互不接近（无 ≥2 簇）→ 分歧：取「窗内识别文本与本行歌词最像」的版本，src=conflict
★ 多数簇原则：2 版一致 + 1 版离群时，应取那 2 版的中位数，而非被离群版带偏、也非误走分歧；
  只有全部互不接近才判分歧。
★ 起点**和**终点都要落在容忍带内：只比起点会把明显过长的行（吞掉下一句时间）误判成一致。
返回 (items, report)；report 键：all / majority / single / conflict / conflicts / missing / passes。
"""

    rep = {"all": 0, "majority": 0, "single": 0, "conflict": 0, "conflicts": [],
           "missing": [], "passes": ["%gs" % p["seg"] for p in (passes or [])]}
    if not passes:
        return [], rep
    tags = ["%gs" % p["seg"] for p in passes]
    pri = min(range(len(passes)), key=lambda k: abs(passes[k]["seg"] - _PRIMARY_SEG))
    maps = [{it["index"]: it for it in p.get("items", [])} for p in passes]

    items = []
    for idx in range(1, len(struct) + 1):
        cands = [(k, maps[k][idx]) for k in range(len(passes)) if idx in maps[k]]
        if not cands:
            rep["missing"].append(idx)
            continue
        if len(cands) == 1:
            new = dict(cands[0][1])
            new["_src"] = "single"
            rep["single"] += 1
        else:
            cluster = _largest_cluster(cands, tol)
            if len(cluster) >= 2:
                cl = [cands[k] for k in cluster]
                st = sorted(float(c[1]["start"]) for c in cl)
                en = sorted(float(c[1]["end"]) for c in cl)
                mid = len(st) // 2
                new = dict(cl[0][1])
                new["start"], new["end"] = round(st[mid], 2), round(en[mid], 2)
                new["whisper"] = "｜".join((c[1].get("whisper") or "")[:12] for c in cl)
                if len(cluster) == len(passes):
                    new["_src"] = "all"
                    rep["all"] += 1
                else:
                    new["_src"] = "majority"
                    rep["majority"] += 1
            else:

                def _score(c, _ly=struct[idx - 1][0]):
                    return _sim_chars(c[1].get("whisper", ""), _ly) + \
                        (0.02 if c[0] == pri else 0.0)  # 同分时优先主版

                k, it = max(cands, key=_score)
                new = dict(it)
                new["_src"] = "conflict"
                rep["conflict"] += 1
                rep["conflicts"].append({
                    "index": idx, "lyric": struct[idx - 1][0],
                    "picked": tags[k],
                    "cands": [{"seg": tags[kk], "start": float(ii["start"]),
                               "end": float(ii["end"])} for kk, ii in cands]})
        new["index"] = idx
        new["lyric"] = struct[idx - 1][0]
        new["section"] = struct[idx - 1][1]
        items.append(new)
    items.sort(key=lambda x: (float(x["start"]), x["index"]))
    return items, rep


def _dedupe_overlaps(items, min_ov=0.25, min_len=0.5):
    """两版合并后相邻行时间窗重叠 → 按**中点切开**（默认保留两句），过短者才丢。

保留两句是硬要求：另一版把边界估歪时，这句照样留在字幕里。中点切分让两句各让一半，比整句丢弃稳。
返回 (items, dropped_pairs)；dropped_pairs 仅记录真正被丢弃的相邻行号对。
"""

    items = sorted(items, key=lambda x: (float(x["start"]), x["index"]))
    out, dropped = [], []
    for it in items:
        if out and float(it["start"]) < float(out[-1]["end"]) - min_ov:
            prev = out[-1]
            mid = round((float(it["start"]) + float(prev["end"])) / 2.0, 2)
            lp, li = mid - float(prev["start"]), float(it["end"]) - mid
            if lp >= min_len and li >= min_len:
                out[-1] = dict(prev, end=mid)
                out.append(dict(it, start=mid))
            elif lp < li:  # 前一行所剩太短 → 丢前一行，把窗口让给后一行
                out[-1] = dict(it, start=round(float(prev["start"]), 2))
                dropped.append((prev["index"], it["index"]))
            else:  # 后一行太短 → 丢后一行
                dropped.append((prev["index"], it["index"]))
            continue
        out.append(it)
    return out, dropped


def _absorb_orphan_phrases(items, passes, gap=2.5, min_len=0.4):
    """未认领短语回收（通用规则）。

`_align_lines_words` 的 DP 让每行只绑**一个**短语；但一行歌词常被唱成两截
（中间换气 >0.6s 就切成两个短语）→ 后半截短语无人认领，字幕留白而歌声还在继续。

规则：凡**没有任何行覆盖**的高置信短语（非幻觉、时长 ≥min_len），若与某行
的间隙 ≤gap，就并入间隙最小的那一行（尾接延 end / 头接提前 start），并夹住
邻行边界，产生的窗口与邻行不重叠。返回 (items, 回收条数)。
"""

    if not items or not passes:
        return items, 0
    prim = min(passes, key=lambda p: abs(p["seg"] - _PRIMARY_SEG))
    words = list(prim.get("keep") or [])
    if not words:
        return items, 0
    seq = sorted(items, key=lambda x: (float(x["start"]), x["index"]))
    n_abs = 0
    for pa, pb, ptext in _group_words(sorted(words), gap=0.6):
        if pb - pa < min_len:
            continue
        if any(not (pb <= float(it["start"]) or pa >= float(it["end"])) for it in seq):
            continue  # 已被某行覆盖
        best = None
        for k, it in enumerate(seq):
            if float(it["end"]) <= pa:  # 尾接
                lim = (float(seq[k + 1]["start"]) - 0.05) if k + 1 < len(seq) else 1e9
                new = min(pb, lim)
                d = pa - float(it["end"])
                if new - float(it["end"]) >= min_len and d <= gap and \
                        (best is None or d < best[0]):
                    best = (d, it, "end", new)
            elif float(it["start"]) >= pb:  # 头接
                lim = (float(seq[k - 1]["end"]) + 0.05) if k > 0 else 0.0
                new = max(pa, lim)
                d = float(it["start"]) - pb
                if float(it["start"]) - new >= min_len and d <= gap and \
                        (best is None or d < best[0]):
                    best = (d, it, "start", new)
        if not best:
            continue
        _d, it, key, val = best
        it[key] = round(float(val), 2)
        it["whisper"] = ((it.get("whisper") or "") + "｜" + ptext)[:48]
        it["_src"] = (it.get("_src") or "") + "+absorb"
        n_abs += 1
    return items, n_abs


def run_align(audio, lyrics_text, out_dir, out_name="字幕", karaoke=False,
              transcript=None, video_size=None, multi_pass=0, verify=True,
              duration=None, style=DEFAULT_STYLE, seg_lens=DEFAULT_SEG_LENS):
    """whisper 多分段各一版（默认 9/11/13 三版）→ 正确歌词替换 → 行级校对位置（通用主入口）。

video_size=(w,h)：用于生成正确 PlayRes 的 ASS。
返回 [{index,start,end,lyric,whisper,section,_src}, ...]（真实时间戳）。
落盘：{out_name}.srt / {out_name}.ass / 歌词时间轴.json / _transcript_<N>s.txt /
      _align_report.json（行级校对报告）/ _align_verify.json（自检）
"""

    os.makedirs(out_dir, exist_ok=True)
    struct = parse_lyrics_struct(lyrics_text)
    if not struct:
        raise RuntimeError("歌词为空，无法对齐：%s" % audio)
    duration = duration or _audio_duration(audio)
    lyrics_lines = [s for s, _ in struct]
    sections = [sec for _, sec in struct]

    trs_path = os.path.join(out_dir, "_transcript.txt")
    passes = []
    if audio and os.path.isfile(audio):
        passes = [p for p in whisper_word_passes(
            audio, out_dir, seg_lens=seg_lens, duration=duration) if p.get("words")]
    if passes:
        prim = min(passes, key=lambda p: abs(p["seg"] - _PRIMARY_SEG))
        try:
            shutil.copyfile(prim["path"], trs_path)  # 供下游（人声判定/复跑）复用
        except Exception:
            pass
        print("[align] whisper 多分段转写（%d 版：%s）：%s" % (
            len(passes), "×".join("%gs" % p["seg"] for p in passes),
            "｜".join("分段 %gs → %d 词" % (p["seg"], len(p["words"])) for p in passes)))
    else:
        print("[warn] whisper 多分段未产出词表，尝试复用已有 %s" % trs_path)

    items, spans, raw = [], [], []
    for p in passes:
        it, sp, dp, kp = _pass_items(p["words"], struct, duration=duration)
        p["items"], p["spans"], p["dropped"], p["keep"] = it, sp, dp, kp
        if dp:
            print("[align] 分段 %gs 版幻觉过滤剔除 %d 条短语：%s" % (
                p["seg"], len(dp), "；".join("[%.1f-%.1f]%s" % (d[0], d[1], d[2][:10])
                                            for d in dp[:3])))
    if passes:
        items, mrep = merge_line_passes(passes, struct)
        print("[align] 行级校对（%s · %d 版）：全体一致 %d｜多数一致 %d｜单版 %d｜分歧 %d｜未匹配 %d" % (
            "×".join("%gs" % p["seg"] for p in passes),
            len(passes),
            mrep["all"], mrep["majority"], mrep["single"], mrep["conflict"], len(mrep["missing"])))
        if mrep["conflicts"]:
            print("[align] ⚠ 分歧（已取窗内文本更贴近歌词的一版，建议人工复核）：")
            for c in mrep["conflicts"]:
                print('         #%d「%s」 %s' % (
                    c["index"], c["lyric"][:12],
                    "｜".join("%s 起 %.2fs" % (x["seg"], x["start"]) for x in c["cands"])))
        if mrep["missing"]:
            print("[align] ⚠ 两版都没匹配上的行（%d 句，未上屏）：%s" % (
                len(mrep["missing"]), "、".join("#%d" % i for i in mrep["missing"])))
        items, drp = _dedupe_overlaps(items)
        if drp:
            print("[align] ⚠ 相邻行时间窗重叠 → 已按中点切开；太短而丢弃的行：%s" %
                  "、".join("#%d/#%d" % d for d in drp))
        items, nabs = _absorb_orphan_phrases(items, passes)
        if nabs:
            print("[align] 未认领短语回收 %d 处（跨短语唱出的行，补回被漏掉的半句）" % nabs)
        mrep["final"] = [{"index": it["index"], "start": float(it["start"]),
                          "end": float(it["end"]), "src": it.get("_src", "")}
                         for it in items]
        try:
            with open(os.path.join(out_dir, "_align_report.json"), "w",
                      encoding="utf-8") as f:
                json.dump(mrep, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("[warn] _align_report.json 写出失败：%s" % e)
        _allsp = []
        for p in passes:
            _allsp += list(p["spans"])
        spans = _merge_spans(_allsp)
        raw = list(spans)
    else:
        # 兜底：无词表 → 复用已有转写做句级对齐；仍失败则交共享字幕脚本
        try:
            raw = [(a, b, t) for a, b, t in parse_transcript(trs_path) if t and b > a]
        except Exception:
            raw = []

    if raw and not items:
        keep, dropped = filter_vocal_segments(raw, lyrics_lines, duration=duration)
        items = align_lyrics_monotonic(keep, lyrics_lines, sections=sections)
        spans = _merge_spans([(a, b) for a, b, _ in keep])
        print("[align] 句级兜底对齐：人声 %d 段 → 歌词 %d 句" % (len(keep), len(items)))
    if not items:
        # 兜底：共享字幕脚本（句级 whisper + 相似度对齐）。其相似度对齐在唱歌
        # 场景会因错字整体错位，只作兜底，主路径一律用 whisper 原始时间刻度重算。
        tmp = os.path.join(out_dir, "_lyrics_for_align.txt")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lyrics_lines))
        cmd = [sys.executable, os.path.abspath(SUB_SCRIPT),
               "--audio", audio, "--lyrics", tmp,
               "--out-dir", out_dir, "--out-name", out_name, "--no-burn"]
        if karaoke:
            cmd.append("--karaoke")
        if transcript:
            cmd += ["--transcript", transcript]
        subprocess.run(cmd, check=True)
        try:
            os.remove(tmp)
        except Exception:
            pass
        items = _items_from_record(out_dir, struct)

    if not items:
        raise RuntimeError("对齐失败：whisper 多分段与兜底路径都没产出（%s）" % audio)

    write_subtitles(items, out_dir, out_name, karaoke=karaoke, video_size=video_size,
                    style=style)
    json.dump([list(x) for x in spans],
              open(os.path.join(out_dir, "_vocal_spans.json"), "w", encoding="utf-8"),
              ensure_ascii=False)

    # ★ whisper 多版仲裁（可选，--multi-pass）：两版之外再跑几版，只在人工要求时用
    if multi_pass and audio and os.path.isfile(audio):
        try:
            xrep = cross_check_whisper(audio, n=multi_pass, duration=duration,
                                       tmp_dir=out_dir)
            json.dump(xrep, open(os.path.join(out_dir, "_xcheck.json"), "w",
                                 encoding="utf-8"), ensure_ascii=False, indent=2)
            word_spans = xrep.get("word_consensus_spans") or []
            if word_spans:
                items, refine_report = _refine_with_word_spans(items, word_spans)
                for ln in refine_report:
                    print(ln)
                spans = [(float(a), float(b)) for a, b in word_spans if b > a]
            print(xrep["report"])
        except Exception as e:
            print("[warn] 多版仲裁失败（已跳过）：%s" % e)

    # ★ 硬约束：时间轴不得超出音频时长（尾段崩坏防护）
    items = _clamp_items(items, duration)
    # ★ 人工钉定行（_line_pins.json：{"行号": [start, end]}）＝ 听感金标准，最后覆盖
    items = _apply_pins(items, out_dir, struct)
    items.sort(key=lambda x: (float(x["start"]), x["index"]))

    # 后重新写字幕
    write_subtitles(items, out_dir, out_name, karaoke=karaoke, video_size=video_size,
                    style=style)
    json.dump([list(x) for x in spans],
              open(os.path.join(out_dir, "_vocal_spans.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    if verify:
        # ★ VAD 静音检测（纯人声）与 whisper 人声窗交叉，检出落在静音区的错位句
        _compute_vad_spans(out_dir)  # 落盘 _vocal_vad.json（报告用）
        _vad_wav = os.path.join(out_dir, "人声.wav")
        vrep = verify_alignment(items, raw_vocal_spans=spans, duration=duration,
                                vad_wav=_vad_wav if os.path.isfile(_vad_wav) else None)
        print(vrep["report"])
        json.dump(vrep, open(os.path.join(out_dir, "_align_verify.json"), "w",
                             encoding="utf-8"), ensure_ascii=False, indent=2)
    return items


def _items_from_record(out_dir, struct):
    """共享字幕脚本的对齐记录 → items（兜底路径用）。"""
    rec_path = os.path.join(out_dir, "_alignment_record.json")
    if not os.path.isfile(rec_path):
        return []
    try:
        rec = json.load(open(rec_path, encoding="utf-8"))
    except Exception:
        return []
    items = []
    for i, r in enumerate(rec):
        items.append({
            "index": i + 1,
            "start": float(r.get("start", 0.0)),
            "end": float(r.get("end", 0.0)),
            "lyric": r.get("lyric", ""),
            "whisper": r.get("whisper", ""),
            "section": struct[i][1] if i < len(struct) else "主歌",
        })
    return items


def _apply_pins(items, out_dir, struct):
    """人工钉定（_line_pins.json：{"行号":[start,end]}）＝ 听感金标准。

两个动作（通用机制，不写单曲补丁）：
  ① 同号行由钉定值**覆盖**（钉定行永不被动）；
  ② 与钉定窗冲突的邻行**裁掉重叠部分**（裁完 <0.4s 才丢）——否则字幕会两行压一起。
返回排序后的 items。
"""

    pins_path = os.path.join(out_dir, "_line_pins.json")
    if not os.path.isfile(pins_path):
        return items
    try:
        pins = json.load(open(pins_path, encoding="utf-8"))
    except Exception as e:
        print("[warn] _line_pins.json 读取失败（跳过）：%s" % e)
        return items
    pinrows, wins = {}, []
    for k, v in pins.items():
        try:
            idx, s, e = int(k), round(float(v[0]), 2), round(float(v[1]), 2)
        except Exception:
            continue
        if idx < 1 or idx > len(struct) or e <= s:
            continue
        pinrows[idx] = {
            "index": idx, "start": s, "end": e, "lyric": struct[idx - 1][0],
            "whisper": "(人工钉定)", "section": struct[idx - 1][1], "_src": "pin",
        }
        wins.append((s, e))
    if not wins:
        return items
    kept, dropped = [], []
    for it in items:
        if it["index"] in pinrows:
            continue  # 同号行由钉定行取代
        s, e = float(it["start"]), float(it["end"])
        for ps, pe in wins:
            if min(e, pe) - max(s, ps) <= 0.2:
                continue
            if s < ps:
                e = min(e, ps)
            else:
                s = max(s, pe)
        if e - s >= 0.4:
            kept.append(dict(it, start=round(s, 2), end=round(e, 2)))
        else:
            dropped.append(it["index"])
    kept, _ov = _dedupe_overlaps(kept)  # 裁剪后可能再出现相邻重叠
    print("[align] 人工钉定 %d 句（金标准，覆盖同号行 / 裁剪冲突邻行）：%s%s" % (
        len(pinrows),
        "；".join("#%d %.2f-%.2f" % (i, pinrows[i]["start"], pinrows[i]["end"])
                  for i in sorted(pinrows)),
        ("；冲突被裁掉的行：%s" % "、".join("#%d" % i for i in sorted(dropped))) if dropped else ""))
    out = sorted(kept + list(pinrows.values()),
                 key=lambda x: (float(x["start"]), x["index"]))
    return out


# 歌词里的结构标记（[verse]/[chorus]...）→ 中文段落名；无人声段另用 前奏/间奏/尾奏
_SECTION_CN = {
    "intro": "前奏", "verse": "主歌", "pre-chorus": "预副歌", "prechorus": "预副歌",
    "chorus": "副歌", "bridge": "桥段", "outro": "尾声", "hook": "副歌",
    "间奏": "间奏", "尾奏": "尾奏",
}


def _merge_spans(spans, gap=0.35):
    spans = sorted([(a, b) for a, b in spans if b > a])
    out = []
    for a, b in spans:
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 2), round(b, 2)) for a, b in out]


# ===== VAD 静音检测（纯人声能量门限 · 与 whisper 人声窗交叉验证）=====
# 为什么用「人声.wav」而非「音乐.wav」：人声分离 + RNNoise 去噪后，前奏/间奏的纯器乐段
# 在人声轨上接近静音，演唱段才有显著能量，阈值判定比在混合音乐上干净得多，能可靠区分
# 真在唱与静音/器乐残留。

def _read_wav_mono(wav):
    """读 wav → (float32 单声道样本, 采样率)。支持 16/24/32-bit PCM、多声道取平均。
    解析失败返回 None。优先 numpy（faster-whisper 依赖，必在）。"""
    try:
        import wave
        with wave.open(wav, "rb") as w:
            fr = w.getframerate() or 1
            nch = w.getnchannels() or 1
            sw = w.getsampwidth()
            raw = w.readframes(w.getnframes())
    except Exception:
        return None
    try:
        import numpy as np
        if sw == 2:
            pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32)
        elif sw == 3:
            a = np.frombuffer(raw, dtype=np.uint8)
            n3 = len(a) // 3
            a = a[:n3 * 3].reshape(n3, 3)
            pcm = (a[:, 0].astype(np.int32) | (a[:, 1].astype(np.int32) << 8) |
                   (a[:, 2].astype(np.int32) << 16)).astype(np.float32)
            pcm[pcm >= 2 ** 23] -= 2 ** 24
        elif sw == 4:
            pcm = np.frombuffer(raw, dtype="<i4").astype(np.float32)
        else:
            pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32)
        if nch > 1:
            pcm = pcm.reshape(-1, nch).mean(axis=1)
        scale = float(2 ** (8 * sw - 1))
        return pcm.astype(np.float32) / scale, fr
    except Exception:
        # 纯 Python 兜底（零依赖）：环境里没装 numpy 时仍能算
        import array
        if sw == 2:
            a = array.array("h"); a.frombytes(raw)
            samples = [x / 32768.0 for x in a]
        elif sw == 4:
            a = array.array("i"); a.frombytes(raw)
            samples = [x / 2147483648.0 for x in a]
        elif sw == 3:
            samples = [int.from_bytes(raw[i:i + 3], "little", signed=True) / 8388608.0
                       for i in range(0, len(raw) - 2, 3)]
        else:
            a = array.array("h"); a.frombytes(raw)
            samples = [x / 32768.0 for x in a]
        if nch > 1:
            samples = [sum(samples[i:i + nch]) / nch
                       for i in range(0, len(samples) - nch + 1, nch)]
        return samples, fr


def estimate_vocal_activity(vocal_wav, frame_sec=0.1, vad_gain=30.0,
                           merge_gap=0.35, min_span=0.8):
    """基于纯人声（去噪后）的能量门限静音检测（轻量 VAD，零外部依赖）。

    阈值 = 全曲能量 15% 分位（底噪水平）× vad_gain（默认 30≈30dB）。前奏/间奏的器乐残留
    （底噪级）判静音、演唱段（远高于底噪）判有声；自适应于响度归一化差异。
    有声段需连续 ≥ min_span（默认 0.8s）才保留，过滤灰区零星尖峰。优先 numpy（快），
    无则纯 Python 兜底。返回 {duration, frame_sec, vad_gain, vad_spans, voiced_ratio} 或 None。
    vad_spans 为合并后的有声区间 [(s,e),...]，用于与 whisper 人声窗交叉验证。
    """
    r = _read_wav_mono(vocal_wav)
    if r is None:
        return None
    samples, fr = r
    n = len(samples)
    if n == 0:
        return None
    dur = n / float(fr)
    fsize = max(1, int(round(frame_sec * fr)))
    nframes = max(1, (n + fsize - 1) // fsize)
    try:
        import numpy as np
        s = np.asarray(samples, dtype=np.float64)
        energy = np.zeros(nframes, dtype=np.float64)
        for i in range(nframes):
            seg = s[i * fsize: min((i + 1) * fsize, n)]
            energy[i] = np.sqrt(np.mean(seg * seg)) if seg.size else 0.0
        p_low = float(np.percentile(energy, 15))
        thr = max(p_low * vad_gain, 1e-7)
        voiced = energy >= thr
        t = (np.arange(nframes) * fsize + fsize / 2.0) / float(fr)
        spans, i = [], 0
        while i < nframes:
            if voiced[i]:
                j = i
                while j + 1 < nframes and (voiced[j + 1] or (t[j + 1] - t[j]) <= merge_gap):
                    j += 1
                a = max(0.0, t[i] - frame_sec / 2.0)
                b = min(dur, t[j] + frame_sec / 2.0)
                if b - a >= min_span:
                    spans.append((a, b))
                i = j + 1
            else:
                i += 1
        spans = _merge_spans(spans, gap=merge_gap)
        voiced_ratio = float(np.mean(voiced))
    except Exception:
        energy = [0.0] * nframes
        for i in range(nframes):
            s0 = i * fsize; s1 = min(s0 + fsize, n)
            seg = samples[s0:s1]
            if seg:
                ss = 0.0
                for v in seg:
                    ss += v * v
                energy[i] = (ss / len(seg)) ** 0.5
            else:
                energy[i] = 0.0
        se = sorted(energy)
        p_low = se[max(0, int(0.15 * len(se)) - 1)]
        thr = max(p_low * vad_gain, 1e-7)
        voiced = [e >= thr for e in energy]
        t = [(i * fsize + fsize / 2.0) / float(fr) for i in range(nframes)]
        spans, i = [], 0
        while i < nframes:
            if voiced[i]:
                j = i
                while j + 1 < nframes and (voiced[j + 1] or (t[j + 1] - t[j]) <= merge_gap):
                    j += 1
                a = max(0.0, t[i] - frame_sec / 2.0)
                b = min(dur, t[j] + frame_sec / 2.0)
                if b - a >= min_span:
                    spans.append((a, b))
                i = j + 1
            else:
                i += 1
        spans = _merge_spans(spans, gap=merge_gap)
        voiced_ratio = sum(1 for v in voiced if v) / len(voiced) if voiced else 0.0
    return {"duration": round(dur, 2), "frame_sec": frame_sec,
            "vad_gain": vad_gain, "vad_spans": spans,
            "voiced_ratio": round(voiced_ratio, 3)}


def _segment_rms(vocal_wav, bounds):
    """算每个时间区间内的 RMS（线性，已归一化到 [-1,1]）。返回 [(s,e,rms),...] 或 None。
    优先 numpy（快），无则纯 Python 兜底。"""
    r = _read_wav_mono(vocal_wav)
    if r is None:
        return None
    samples, fr = r
    n = len(samples)
    try:
        import numpy as np
        s = np.asarray(samples, dtype=np.float64)
        out = []
        for a_, b_ in bounds:
            a = max(0, int(round(a_ * fr))); b = min(n, int(round(b_ * fr)))
            if b > a:
                seg = s[a:b]
                out.append((a_, b_, float(np.sqrt(np.mean(seg * seg)))))
            else:
                out.append((a_, b_, 0.0))
        return out
    except Exception:
        out = []
        for a_, b_ in bounds:
            a = max(0, int(round(a_ * fr))); b = min(n, int(round(b_ * fr)))
            if b > a:
                seg = samples[a:b]
                ss = 0.0
                for v in seg:
                    ss += v * v
                out.append((a_, b_, (ss / len(seg)) ** 0.5))
            else:
                out.append((a_, b_, 0.0))
        return out


def cross_validate_segments_with_vad(seg_info, vocal_wav, auto_fix=True,
                                     fix_ratio=0.10, warn_ratio=0.30, leak_ratio=0.50):
    """段级交叉验证：把 classify_segments 的「whisper 人声窗判定」与「纯人声段内能量」交叉比对。

    方法：算每段（人声.wav 去噪纯人声）段内 RMS，与全曲「被标有人声段」的 RMS 均值（演唱
    基准 ref）比较。比全局能量阈值更稳 —— 能区分「前奏/尾奏的器乐残留（远低于 ref）」与
    「真唱（≈ref）」，不误杀尾奏、也不漏判前奏。
    规则：
      - 段标 vocal=True 但 段内RMS < ref*fix_ratio 且段长>4s → 识别一定出错（如 whisper 把片尾
        署名幻觉到前奏），auto_fix 时自动降级为无人声（type 重分类，从根上防住伪人声窗→前奏
        误判演唱→字幕整体提前）。
      - 段标 vocal=True 但 段内RMS < ref*warn_ratio → 警告（疑似伪人声窗），不自动改。
      - 段标 vocal=False 但 段内RMS > ref*leak_ratio → 警告（可能漏判真在唱），不自动改。
    返回 (new_seg_info, reports[...])。
    """
    if not vocal_wav or not os.path.isfile(vocal_wav):
        return seg_info, []
    rms_list = _segment_rms(vocal_wav, [(s["start"], s["end"]) for s in seg_info])
    if not rms_list:
        return seg_info, []
    rms_map = {s["index"]: r for s, (a_, b_, r) in zip(seg_info, rms_list)}
    ref = sum(rms_map[s["index"]] for s in seg_info if s["vocal"])
    ref_n = sum(1 for s in seg_info if s["vocal"])
    ref = (ref / ref_n) if ref_n else (max(rms_map.values()) or 1e-9)
    if ref <= 0:
        ref = 1e-9
    _name = os.path.basename(vocal_wav)
    reports, out = [], []
    for s in seg_info:
        seg = dict(s)
        er = rms_map.get(s["index"], 0.0)
        ratio = er / ref
        if s["vocal"]:
            if ratio < fix_ratio and (s["end"] - s["start"]) > 4.0:
                if auto_fix:
                    seg["vocal"] = False
                    seg["sing"] = False
                    seg["vocal_ratio"] = 0.0
                    seg["vocal_windows"] = []
                    reports.append("  [VAD·自动降级] 段%d (%ss-%ss) 标有人声但段内能量仅为演唱基准的 %.0f%%"
                                   "（%s）→ 判为识别错误，降级为无人声"
                                   % (s["index"], s["start"], s["end"], ratio * 100, _name))
                else:
                    reports.append("  [VAD·错误] 段%d (%ss-%ss) 标有人声但段内能量仅为演唱基准的 %.0f%%"
                                   "，疑似识别错误" % (s["index"], s["start"], s["end"], ratio * 100))
            elif ratio < warn_ratio:
                reports.append("  [VAD·警告] 段%d (%ss-%ss) 标有人声但段内能量仅为演唱基准的 %.0f%%"
                               "，疑似伪人声窗，请人工复核" % (s["index"], s["start"], s["end"], ratio * 100))
        else:
            if ratio > leak_ratio:
                reports.append("  [VAD·警告] 段%d (%ss-%ss) 标无人声但段内能量达演唱基准的 %.0f%%"
                               "，可能漏判真在唱，请人工复核" % (s["index"], s["start"], s["end"], ratio * 100))
        out.append(seg)
    if auto_fix:
        idxs = [r["index"] for r in out if r["vocal"]]
        for r in out:
            if r["vocal"]:
                secs = []
                for h in r["lyrics"]:
                    _cn = _SECTION_CN.get((h.get("section") or "").strip().lower(),
                                          (h.get("section") or "").strip())
                    if _cn and _cn not in secs:
                        secs.append(_cn)
                r["type"] = "/".join(secs) if secs else "演唱"
            elif not idxs or r["index"] < idxs[0]:
                r["type"] = "前奏"
            elif r["index"] > idxs[-1]:
                r["type"] = "尾奏"
            else:
                r["type"] = "间奏"
    return out, reports


def _compute_vad_spans(out_dir):
    """算纯人声 VAD 静音检测；优先用 out_dir/人声.wav（去噪纯人声）。落盘 _vocal_vad.json，
    返回 vad_spans 或 None。幂等：重复调用只重算并覆盖。"""
    vw = os.path.join(out_dir, "人声.wav")
    if not os.path.isfile(vw):
        return None
    rep = estimate_vocal_activity(vw)
    if not rep:
        return None
    try:
        json.dump(rep, open(os.path.join(out_dir, "_vocal_vad.json"), "w",
                            encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception:
        pass
    return rep.get("vad_spans")


def classify_segments(bounds, lyric_items, vocal_spans=None, vocal_min_ratio=0.15):
    """按「音频分段内有没有人声命中」判定每段的演唱状态与段落类型。

bounds: H3 精确分段边界 [(s, e), ...]
vocal_spans: **whisper 原始人声段** [(s, e), ...]（权威，推荐必传）。
    人声判定用 whisper 原始时间刻度，而不是歌词强制对齐结果——
    强制对齐会把真歌词段判成幻觉丢掉，把「其实在唱」判成前奏。
    传 None 时退回用 lyric_items（仅在没有转写文件时兜底，可能不准）。
返回：[{index, start, end, len, vocal, vocal_ratio, vocal_windows, lyrics, type}, ...]
  vocal_windows 是**相对该段起点**的秒区间（写提示词时转成切点：首档不写时刻，其后每档 `At 00:03.500, …`）
  type ∈ 前奏 / 间奏 / 尾奏 / 主歌 / 副歌 / 桥段 …（取自歌词里的 [结构] 标记）
"""

    if vocal_spans:
        spans = _merge_spans([(a, b) for a, b in vocal_spans if b > a])
    else:
        spans = _merge_spans([(it["start"], it["end"]) for it in lyric_items])
    res = []
    for i, (s, e) in enumerate(bounds, 1):
        win = []
        for a, b in spans:
            x, y = max(a, s), min(b, e)
            if y > x:
                win.append((x - s, y - s))  # 段内相对时间
        win = _merge_spans(win)
        cov = sum(b - a for a, b in win)
        ratio = (cov / (e - s)) if e > s else 0.0
        hits = [it for it in lyric_items if it["end"] > s and it["start"] < e]
        res.append({
            "index": i,
            "start": round(s, 2),
            "end": round(e, 2),
            "len": round(e - s, 2),
            "vocal": bool(win) and ratio >= vocal_min_ratio,
            "vocal_ratio": round(ratio, 3),
            "vocal_windows": win,
            "lyrics": hits,
            "type": "",
        })
    idxs = [r["index"] for r in res if r["vocal"]]
    for r in res:
        if r["vocal"]:
            secs = []
            for h in r["lyrics"]:
                _cn = _SECTION_CN.get((h.get("section") or "").strip().lower(),
                                      (h.get("section") or "").strip())
                if _cn and _cn not in secs:
                    secs.append(_cn)
            r["type"] = "/".join(secs) if secs else "演唱"
        elif not idxs or r["index"] < idxs[0]:
            r["type"] = "前奏"
        elif r["index"] > idxs[-1]:
            r["type"] = "尾奏"
        else:
            r["type"] = "间奏"
        r["sing"] = bool(r["vocal"])
    return res


def write_timeline(path, song, duration, bounds, lyric_items, seg_info):
    """落盘 歌词时间轴.json（人可读、脚本可回读的中间产物）。"""
    data = {
        "song": song,
        "duration_sec": round(duration or 0, 2),
        "segments": [{
            "index": s["index"], "start": s["start"], "end": s["end"], "len": s["len"],
            "type": s["type"], "vocal": s["vocal"], "vocal_ratio": s["vocal_ratio"],
            "vocal_windows": s["vocal_windows"],
            "lyrics": [{"text": it["lyric"], "whisper": it["whisper"],
                        "start": it["start"], "end": it["end"],
                        "section": it.get("section", "")} for it in s["lyrics"]],
        } for s in seg_info],
        "lyric_lines": [{
            "index": it["index"], "text": it["lyric"], "whisper": it["whisper"],
            "start": round(it["start"], 2), "end": round(it["end"], 2),
            "section": it.get("section", ""),
        } for it in lyric_items],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def load_timeline(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def burn_subtitle(ass_path, video, out_mp4, ffmpeg=None):
    """用带 libass 的 ffmpeg 把 ASS 烧进视频。

★ 必须 cwd 到字幕所在目录 + 用相对文件名（Windows 盘符在 subtitles 滤镜里会转义报错）。
"""

    ff = ffmpeg or ffmpeg_path()
    d = os.path.dirname(os.path.abspath(ass_path))
    out_dir = os.path.dirname(os.path.abspath(out_mp4))
    subprocess.run([ff, "-y", "-i", video,
                    "-vf", "subtitles=filename=%s" % os.path.basename(ass_path),
                    "-c:a", "copy", os.path.basename(out_mp4)],
                   cwd=d, check=True)
    # 若输出目录与字幕目录不同，搬运过去
    src = os.path.join(d, os.path.basename(out_mp4))
    if os.path.abspath(src) != os.path.abspath(out_mp4) and os.path.isfile(src):
        os.replace(src, out_mp4)
    return out_mp4


# ===========================================================================
# 通用对齐自检 + whisper 多版交叉
# ---------------------------------------------------------------------------
# 自检项：
# ① 把 whisper 原始识别内容**逐条检查**——空识别(漏识)/内容相对时间段过长
# (一行塞太多字)/零长度窗口/副歌重复漂移 都必须报出来；
# ② whisper 可以**多跑几版**（调 VAD 开/关、词级/句级时间戳），把各版人声窗拿来
# **对比 / 综合参考**——一致度高说明时间轴稳，低一致区才需要人工复核；
# ③ 把我们的正确歌词替换上去后，再比对「窗内识别内容是否真的是这一句」判断对不对。
# 这两步都做成**通用函数**，对任意新歌复用。
# ===========================================================================

def _whisper_skill_dir():
    """定位 fxai-whisper-transcribe 技能目录（配置 whisper_skill 优先，不写死路径）。"""
    p = _G.get("whisper_skill") or ""
    if p and os.path.isfile(os.path.join(p, "whisper_client.py")):
        return os.path.normpath(p)
    base = os.path.dirname(os.path.abspath(__file__))
    for rel in (os.path.join(base, "..", "fxai-whisper-transcribe"),
                os.path.join(base, "..", "..", "fxai-whisper-transcribe")):
        d = os.path.normpath(rel)
        if os.path.isfile(os.path.join(d, "whisper_client.py")):
            return d
    return os.path.normpath(os.path.join(base, "..", "fxai-whisper-transcribe"))


_WC = None


def whisper_client():
    """导入 whisper 客户端（全接口，服务地址由全局配置决定）；导入一次后缓存。"""
    global _WC
    if _WC is not None:
        return _WC
    d = _whisper_skill_dir()
    if not os.path.isfile(os.path.join(d, "whisper_client.py")):
        raise RuntimeError("找不到 fxai-whisper-transcribe 技能目录：%s" % d)
    if d not in sys.path:
        sys.path.insert(0, d)
    import whisper_client as wc
    _WC = wc
    return wc


def run_whisper(audio, out_path, vad=False, word_ts=False, beam=5, condition=True,
                clip_seg=None, duration=None):
    """调 whisper 服务接口产出一个转写文件（每行 `[start->end] 文本`），多版交叉各版共用。

clip_seg：按该秒数**硬切**音频分段转写（9/11/13）；不同分段长度各跑一版即互为校验。
"""
    wc = whisper_client()
    wc.write_transcript(audio, out_path, words=bool(word_ts), vad=bool(vad),
                        beam=int(beam or 5), condition=bool(condition),
                        clip_seg=(float(clip_seg) if clip_seg else None))


def _grid_coverage(spans, duration, step=0.5, dilate=0.0):
    """把人声窗铺到 0.5s 网格上。dilate>0 时把每段前后各膨胀 dilate 秒——

whisper 词级时间戳各版之间有 ±0.3~0.7s 的**边界抖动**，字幕/分镜用途 1s 内无感，
故交叉一致度按「容忍带」算（否则纯抖动被误判为不一致，一致度永远上不去）。
"""

    nb = int(duration / step) + 1
    v = [False] * nb
    nd = int(round(dilate / step))
    for a, b in spans:
        ia = max(0, int((a - dilate) / step))
        ib = min(nb - 1, int((b + dilate) / step) + 1)
        for i in range(ia, ib + 1):
            v[i] = True
    return v


# 多版交叉预设：默认多版＝**硬切分段 9s / 11s / 13s**（与主链路同思路，三版硬切互为校验），
# 另补 beam/句级 差异版扩搜；VAD 无效（见 whisper_word_passes 注释），一律硬切。
# VAD 是为「语音+静音间隙」设计的，歌曲是连续伴奏：开 VAD 会并成 1 段，且
# max_speech_duration_s 不触发，所以分段一律用**硬切** clip_timestamps，不用 VAD。
_XCHECK_PRESETS = [
    {"vad": False, "word_ts": True, "tag": "词级·硬切9s", "clip_seg": 9, "condition": False},
    {"vad": False, "word_ts": True, "tag": "词级·硬切11s", "clip_seg": 11, "condition": False},
    {"vad": False, "word_ts": True, "tag": "词级·硬切13s", "clip_seg": 13, "condition": False},
    {"vad": False, "word_ts": True, "tag": "词级·beam3·无上文", "beam": 3, "condition": False},
    {"vad": False, "word_ts": False, "tag": "句级·整首"},
]

# 词级边界抖动容忍带（秒）：whisper 各版词级起止点天然有 ±0.3~0.7s 抖动，
# 字幕/分镜用途 1s 内无感，交叉一致度按此容忍带计算。
_WORD_TOL = 0.75


def cross_check_whisper(audio, n=3, duration=None, tmp_dir=None):
    """whisper 跑 n 版（不同参数），比对各版人声窗一致度，标出低一致区。

返回 {passes, consensus_spans, agreement, low_conf_regions, report}
  consensus_spans：多数版本都判定有人声的区间（综合参考后的可靠人声窗）；
  low_conf_regions：各版意见不一致的时间区间（需人工复核）。
说明：whisper 文字错字极多，这里**只比对时间轴（人声窗）**，不比文字。
"""

    import tempfile
    tmp_dir = tmp_dir or tempfile.mkdtemp(prefix="xcheck_")
    presets = _XCHECK_PRESETS[:max(1, min(n, len(_XCHECK_PRESETS)))]
    # 一次上传、服务端连跑多版：省掉「每版重传一遍整首音频」（云端公网尤其明显）
    try:
        results = whisper_client().transcribe_passes(
            audio,
            [{"beam": p.get("beam", 5), "vad": p["vad"], "word_ts": p["word_ts"],
              "condition": p.get("condition", True), "clip_seg": p.get("clip_seg")}
             for p in presets])
    except Exception as e:      # 服务整体不可用：每版都记失败，不阻断主流程
        results = [{"error": str(e)} for _ in presets]

    per_pass = []
    for p, r in zip(presets, results):
        try:
            if r.get("error"):
                raise RuntimeError(r["error"])
            segs_all = r.get("segments") or []
            raw = [(s["start"], s["end"], (s.get("text") or "").strip()) for s in segs_all]
            if p["word_ts"]:
                # 词级：每个词已是真实语音，不做「≤2字」短词过滤（单字极多），仅去幻觉/超长
                words = []
                for s in segs_all:
                    for w in (s.get("words") or []):
                        t = (w.get("word") or "").strip()
                        if t:
                            words.append((w["start"], w["end"], t))
                segs = [(a, b, t) for a, b, t in words
                        if not _HALLU_RE.search(t)
                        and (duration is None or a < duration)]
                spans = _merge_spans([(a, b) for a, b, _ in segs])
            else:
                keep, _ = filter_vocal_segments(raw)
                spans = _merge_spans([(a, b) for a, b, _ in keep])
            per_pass.append({"tag": p["tag"], "n_seg": len(spans), "spans": spans})
        except Exception as e:  # 某一版失败不影响其它版
            per_pass.append({"tag": p["tag"], "error": str(e)})

    dur = duration or (max((b for pp in per_pass for a, b in pp.get("spans", [])),
                           default=0.0) + 5.0)
    cov = [_grid_coverage(pp["spans"], dur) for pp in per_pass if "spans" in pp]
    P = len(cov)
    nb = int(dur / 0.5) + 1
    consensus = [False] * nb
    agree = conflict = 0
    for i in range(nb):
        vals = [c[i] for c in cov]
        if len(set(vals)) > 1:
            conflict += 1
        else:
            agree += 1
        consensus[i] = sum(vals) >= (P + 1) // 2
    agreement = (agree / nb) if nb else 0.0

    conspans, i = [], 0
    while i < nb:
        if consensus[i]:
            j = i
            while j < nb and consensus[j]:
                j += 1
            conspans.append((round(i * 0.5, 2), round(j * 0.5, 2)))
            i = j
        else:
            i += 1

    low, i = [], 0
    while i < nb:
        if cov and len(set(c[i] for c in cov)) > 1:
            j = i
            while j < nb and len(set(c[j] for c in cov)) > 1:
                j += 1
            low.append((round(i * 0.5, 2), round(j * 0.5, 2)))
            i = j
        else:
            i += 1

    # ★ 词级两两交叉：最终采用的时间轴就是词级窗，所以「词级各版互相一致度」
    # 才是判断时间轴稳不稳的真指标（句级对歌太粗）。词级时间戳各版有 ±0.3~0.7s 边界抖动，按容忍带算。
    word_passes = [pp for pp in per_pass
                   if "spans" in pp and "词级" in pp.get("tag", "")]
    word_agreement = None
    word_consensus_spans = []
    if len(word_passes) >= 2:
        wcov = [_grid_coverage(pp["spans"], dur, dilate=_WORD_TOL)
                for pp in word_passes]
        wnb = int(dur / 0.5) + 1
        wall = 0
        for i in range(wnb):
            if all(c[i] for c in wcov):
                wall += 1
        word_agreement = wall / wnb if wnb else 0.0
        wmaj = (len(wcov) + 1) // 2  # 多数票，单版漏词不会把窗打碎
        wcons, i = [], 0
        while i < wnb:
            if sum(c[i] for c in wcov) >= wmaj:
                j = i
                while j < wnb and sum(c[j] for c in wcov) >= wmaj:
                    j += 1
                wcons.append((round(i * 0.5, 2), round(j * 0.5, 2)))
                i = j
            else:
                i += 1
        word_consensus_spans = wcons

    lines = ["=== whisper 多版交叉自检（%d 版）===" % P]
    for pp in per_pass:
        lines.append("  [×] %s 失败：%s" % (pp["tag"], pp["error"])
                     if "error" in pp else '  [✓] %s 人声段 %d 段' % (pp["tag"], pp["n_seg"]))
    lines.append("各版时间轴一致度（网格投票）：%.1f%%" % (agreement * 100))
    if word_agreement is not None:
        lines.append("词级各版一致度（实际采用的时间轴）：%.1f%%" % (word_agreement * 100))
    if low:
        lines.append("⚠ 低一致区（需人工复核）：")
        for a, b in low:
            lines.append('   %.1f–%.1f s' % (a, b))
    else:
        lines.append("✓ 各版人声窗基本一致，无需额外复核")
    return {"passes": per_pass, "consensus_spans": conspans,
            "word_consensus_spans": word_consensus_spans,
            "word_agreement": (round(word_agreement, 3)
                               if word_agreement is not None else None),
            "agreement": round(agreement, 3), "low_conf_regions": low,
            "report": "\n".join(lines)}


def verify_alignment(items, raw_vocal_spans=None, duration=None,
                     density_max=9.0, slow_rate=1.0, vad_wav=None):
    """通用对齐自检：逐条核对各项对齐异常。

items: [{start,end,lyric,whisper}, ...]（文字以我们的正确歌词为准，whisper 是窗内识别原文）。
raw_vocal_spans: whisper 原始人声窗 [(a,b),...]（用于检出「间隙含人声=对齐断裂」）。
vad_wav: 纯人声（去噪）wav 路径（算句内能量，与识别结果交叉，检出落在静音区的错位句）。
返回 {status(PASS/WARN/FAIL), errors, warnings, report}
"""

    items = sorted([it for it in items if it.get("lyric")],
                   key=lambda x: (x.get("start", 0), x.get("end", 0)))
    _rms_map, _ref = {}, 1e-9
    if vad_wav and os.path.isfile(vad_wav):
        _rl = _segment_rms(vad_wav, [(it.get("start", 0), it.get("end", 0)) for it in items])
        if _rl:
            _rms_map = {i: r for i, (a, b, r) in enumerate(_rl)}
            _ref = (max(r for a, b, r in _rl) * 0.5) or 1e-9
    errors, warnings = [], []

    for i, it in enumerate(items):
        s, e = float(it.get("start", 0)), float(it.get("end", 0))
        dur = round(e - s, 3)
        lyric = (it.get("lyric") or "").replace(" ", "").replace("　", "")
        wh = (it.get("whisper") or "").replace(" ", "").replace("　", "")
        nch = len(lyric)

        # ① 零长度 / 负长度窗口
        if dur <= 0.05:
            errors.append("第%d句 [%s] 零长度时间窗 (%.2f–%.2f)" % (i + 1, lyric[:12], s, e))
            continue

        # ⓪ 超出音频时长（硬伤）
        if duration and e > duration + 0.3:
            errors.append("第%d句 [%s] 时间窗超出音频时长 (%.2f > %.2f)"
                          % (i + 1, lyric[:12], e, duration))

        # ② 空识别（漏识）：时间段非空却没识别到任何文字 → 可能真在唱但被漏掉
        if not wh and dur >= 1.0:
            warnings.append("第%d句 [%s] whisper 空识别（漏识），但时间段 %.1f–%.1f 非空"
                            % (i + 1, lyric[:12], s, e))

        # ③ 内容相对时间段过长 / 过短（用户说的「内容明显超过时间段合理」）
        if nch >= 5 and dur > 0.1:
            rate = nch / dur
            min_dur = nch / 9.0  # 以 9 字/秒为快唱上限
            if dur < min_dur * 0.8 or rate > density_max:
                warnings.append("第%d句 [%s] 内容相对时间段过长（密度 %.1f 字/秒，时长 %.2f s，疑被压缩/与邻句错位）"
                                % (i + 1, lyric[:12], rate, dur))
            elif rate < slow_rate and dur > 9 and nch >= 3:
                warnings.append("第%d句 [%s] 时长明显偏长（%.1f s / %d 字，疑窗内混入邻句或静音）"
                                % (i + 1, lyric[:12], dur, nch))

        # ④ 副歌重复漂移：窗内识别内容其实是上一句或下一句
        if wh:
            simself = _sim_chars(wh, lyric)
            sim_prev = _sim_chars(wh, items[i - 1]["lyric"]) if i > 0 else 0.0
            sim_nxt = _sim_chars(wh, items[i + 1]["lyric"]) if i + 1 < len(items) else 0.0
            if simself < 0.3 and max(sim_prev, sim_nxt) > 0.6:
                errors.append("第%d句 时间窗漂移：窗内识别内容其实是%s一句（sim=%.2f）"
                              % (i + 1, "上一" if sim_prev >= sim_nxt else "下一", max(sim_prev, sim_nxt)))

        # ⑦ VAD 交叉（可选）：该句时间窗落在纯人声静音区（段内能量远低于演唱基准）→ 错位/幻觉
        if i in _rms_map and dur >= 3.0 and _rms_map[i] < _ref * 0.2:
            errors.append("第%d句 [%s] 时间窗内人声能量仅为演唱基准的 %.0f%%，"
                          "该句很可能识别错位/幻觉（落在静音区）"
                          % (i + 1, lyric[:12], _rms_map[i] / _ref * 100))

    # ⑤ 相邻重叠 / 间隙含人声（对齐断裂）
    spans = raw_vocal_spans or []
    for i in range(len(items) - 1):
        cur = items[i]["end"]
        nxt = items[i + 1]
        if nxt["start"] < cur - 1e-6:
            errors.append("第%d与%d句 时间窗重叠 (%.2f > %.2f)" % (i + 1, i + 2, cur, nxt["start"]))
        gap = nxt["start"] - cur
        if spans and gap > 2.0 and any(a < nxt["start"] and b > cur for a, b in spans):
            warnings.append("第%d→%d句 人声间隙 %.1f s 且含人声，疑似对齐断裂" % (i + 1, i + 2, gap))

    # ⑥ 结尾前仍有未覆盖人声
    if duration and spans:
        lst = items[-1]["end"]
        if lst < duration - 3 and any(b > lst + 1 for _, b in spans):
            warnings.append("结尾前 (%.1f–%.1f) 仍有未覆盖人声" % (lst, duration))

    status = "FAIL" if errors else ("WARN" if warnings else "PASS")
    lines = ["=== 对齐自检 %s（%d 句）===" % (status, len(items))]
    for x in errors:
        lines.append('  [错误] ' + x)
    for x in warnings:
        lines.append('  [警告] ' + x)
    if status == "PASS":
        lines.append('  ✓ 未发现上述问题（零长度/空识别/密度异常/副歌漂移/重叠/断裂）')
    return {"status": status, "errors": errors, "warnings": warnings,
            "report": "\n".join(lines)}


def cmd_verify_dir(out_dir, audio=None, duration=None, multi_pass=0):
    """对已有项目目录做通用对齐自检（不重跑主流程，不重新生成视频）。"""
    tl = load_timeline(os.path.join(out_dir, "歌词时间轴.json"))
    items = []
    if tl:
        for it in tl.get("lyric_lines", []):
            items.append({"start": it["start"], "end": it["end"],
                          "lyric": it["text"], "whisper": it.get("whisper", "")})
    spans = None
    if multi_pass and audio and os.path.isfile(audio):
        xrep = cross_check_whisper(audio, n=multi_pass, duration=duration, tmp_dir=out_dir)
        spans = xrep.get("consensus_spans")
        print(xrep["report"])
        json.dump(xrep, open(os.path.join(out_dir, "_xcheck.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    elif os.path.isfile(os.path.join(out_dir, "_transcript.txt")):
        raw = parse_transcript(os.path.join(out_dir, "_transcript.txt"))
        keep, _ = filter_vocal_segments(raw)
        spans = [(a, b) for a, b, _ in keep]
    # ★ VAD：优先 人声.wav（去噪纯人声），回退 audio（混合音乐，仅供参考）
    vw = os.path.join(out_dir, "人声.wav")
    if not os.path.isfile(vw) and audio and os.path.isfile(audio):
        vw = audio
    if os.path.isfile(vw):
        rep = estimate_vocal_activity(vw)
        if rep:
            json.dump(rep, open(os.path.join(out_dir, "_vocal_vad.json"), "w",
                                encoding="utf-8"), ensure_ascii=False, indent=2)
    rep = verify_alignment(items, raw_vocal_spans=spans, duration=duration,
                           vad_wav=vw if os.path.isfile(vw) else None)
    print(rep["report"])
    # 段级交叉（若时间轴含分段信息）
    if tl and tl.get("segments") and os.path.isfile(vw):
        _, vrep = cross_validate_segments_with_vad(tl["segments"], vw, auto_fix=False)
        for l in vrep:
            print("[VAD] " + l)
    with open(os.path.join(out_dir, "_align_verify.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    return rep


def main():
    import argparse
    ap = argparse.ArgumentParser(description="歌词精确对齐（whisper + 强制对齐 + 通用自检）")
    ap.add_argument("--mode", choices=["align", "verify"], default="align",
                    help="align=跑 whisper+对齐；verify=对已有项目目录做自检（不重跑、不重新生成视频）")
    ap.add_argument("--audio", help="音乐 wav（align 必填；verify 可选，用于多版交叉）")
    ap.add_argument("--lyrics", help="歌词文本（每行一句，[xxx] 行视为结构标记）")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--duration", type=float, default=None, help="音频真实时长（秒），用于结尾人声覆盖检查")
    ap.add_argument("--seg-bounds", default="", help='JSON：[[s,e],...] H3 分段边界')
    ap.add_argument("--karaoke", action="store_true")
    ap.add_argument("--multi-pass", type=int, default=0, help="whisper 多跑几版做交叉自检（0=不跑；建议 2~3）")
    ap.add_argument("--no-verify", dest="verify", action="store_false", help="关闭通用对齐自检")
    ap.set_defaults(verify=True)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if a.mode == "verify":
        cmd_verify_dir(a.out_dir, audio=a.audio, duration=a.duration, multi_pass=a.multi_pass)
        return

    if not a.audio or not a.lyrics:
        print("ERROR: align 模式需要 --audio 和 --lyrics", file=sys.stderr)
        sys.exit(1)
    lyrics = open(a.lyrics, encoding="utf-8").read()
    items = run_align(a.audio, lyrics, a.out_dir, out_name="字幕", karaoke=a.karaoke,
                      multi_pass=a.multi_pass, verify=a.verify, duration=a.duration)
    bounds = json.loads(a.seg_bounds) if a.seg_bounds else []
    if bounds:
        segs = classify_segments(bounds, items)
        write_timeline(os.path.join(a.out_dir, "歌词时间轴.json"),
                       os.path.basename(a.out_dir), a.duration or 0, bounds, items, segs)
        for s in segs:
            print('[seg %02d] %5.2f-%5.2f  %-6s vocal=%s ratio=%.2f win=%s  %s' % (
                s["index"], s["start"], s["end"], s["type"], s["vocal"], s["vocal_ratio"],
                s["vocal_windows"], ("｜".join(x["lyric"] for x in s["lyrics"]) or "-")))


if __name__ == "__main__":
    main()
