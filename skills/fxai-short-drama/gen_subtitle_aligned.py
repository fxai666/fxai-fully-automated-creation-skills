# -*- coding: utf-8 -*-
"""用 whisper 的真实语音时间轴 + 正确台词文字 -> 对齐 SRT（可复用）。\n\n核心思想（用户 2026-09-28 定）：\n  whisper 只用来取「每段对白的真实起止时间」（时间刻度对齐），\n  字幕【文字】一律用我们的正确台词替换，不采用 ASR 识别文字\n  （H3 合成卡通音 ASR 很不稳，会把"凤希AI"识别成"凤夕AI"等错字）。\n\n时间精度两档：\n  --ts（句级，默认）：whisper 分句边界，含句间停顿，句间有长静音时字幕会"提前"。\n  --word-level（推荐）：whisper 词级时间戳，每句字幕 起=首词开始 / 止=末词结束，\n      能消除句间静音/气口导致的提前（ep4 第5句据此从 13.0s 修正到 20.05s）。\n\n两个输入必须段数一致，按行序一一对应：\n  --lyrics     正确台词纯文本，每行一句（按顺序，无时间戳）\n  --transcript whisper 输出文件（格式 `[start->end] 文本`，句级/词级产出的都一样）\n若不给 --transcript，则自动用 fxai-whisper-transcribe 技能跑 whisper 取时间（可选词级）。\n\n用法：\n  # 已有 whisper 时间轴\n  python gen_subtitle_aligned.py --lyrics lyrics.txt --transcript ts.txt --out out.srt\n  # 自动跑 whisper（句级）\n  python gen_subtitle_aligned.py --lyrics lyrics.txt --audio audio.wav --out out.srt\n  # 自动跑 whisper（词级，推荐）\n  python gen_subtitle_aligned.py --lyrics lyrics.txt --audio audio.wav --word-level --out out.srt\n"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import json

# H3 时长对齐公式的单一真相源在公共件 fxai-audio-video（与 MV / 数字人口播共用同一份）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "fxai-audio-video"))
import audio_video as _av

# whisper 环境由全局部署配置（~/.workbuddy/fxai_global.json）的
# software_install_dir / software_data_dir 推导，不写死本机绝对路径。
_THIS = os.path.dirname(os.path.abspath(__file__))
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
_SOFT_DATA = _CFG.get("software_data_dir", "")


# fxai-whisper-transcribe 技能目录（定位 whisper_client.py；可被配置 whisper_skill 覆盖）
DEFAULT_WHISPER_SKILL = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fxai-whisper-transcribe")
)

SEG_RE = re.compile(r"\[([\d.]+)->([\d.]+)\]\s*(.*)")


def sec2srt(sec):
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def load_lyrics(path):
    """正确台词纯文本，每行一句（去除空行）。"""
    lines = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                lines.append(ln)
    return lines


def load_transcript(path):
    """解析 whisper 输出：[start->end] 文本，只取时间，文字忽略。"""
    segs = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            m = SEG_RE.search(ln)
            if m:
                segs.append((float(m.group(1)), float(m.group(2))))
    return segs


_WC = None


def _whisper_client():
    """导入 whisper 客户端（全接口）；导入一次后缓存。"""
    global _WC
    if _WC is not None:
        return _WC
    import importlib.util
    d = _CFG.get("whisper_skill", "") or DEFAULT_WHISPER_SKILL
    p = os.path.join(d, "whisper_client.py")
    if not os.path.isfile(p):
        raise RuntimeError("找不到 whisper_client.py：%s" % p)
    spec = importlib.util.spec_from_file_location("fxai_whisper_client", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    _WC = m
    return m


def run_whisper(audio, out_ts, lang="zh", word_level=False):
    """走 whisper 服务接口取时间轴，返回 (segs_path, words_path)。

    词级时 words_path 含每个词的独立时间戳；segs_path 恒为每段一行 `[start->end]`。
    全程 HTTP，不起本地进程（地址读全局配置 whisper 段）。
    """
    wc = _whisper_client()
    words_path = out_ts + ".words"
    segs = wc.transcribe_segments(audio, language=lang, vad=True, condition=True)
    if word_level:
        # 同时写出：① 每个词一行（段数≠台词数时按字数比例切分）
        #          ② 每个段一行（段数恰好==台词数时直接用，最准）
        with open(out_ts, "w", encoding="utf-8") as f:
            f.write(wc.format_lines(segs, bounds_only=True))
        with open(words_path, "w", encoding="utf-8") as f:
            f.write(wc.format_lines(segs, words=True))
    else:
        with open(out_ts, "w", encoding="utf-8") as f:
            f.write(wc.format_lines(segs))
    return out_ts, words_path
    return out_ts, words_path


def load_words(path):
    '词级输出：每行 [start->end] 词文本，返回 [(start,end), ...]（时间轴按出现顺序）。\n剔除零/负时长词：faster-whisper 在音频尾部（静音/音乐）会吐出 end<=start 的幽灵词，\n它们集中在时间轴末端，会把按字数比例映射的最后几句台词挤压成零时长。'


    words = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            m = SEG_RE.search(ln)
            if m:
                s, e = float(m.group(1)), float(m.group(2))
                if e > s: # 跳过零/负时长幽灵词
                    words.append((s, e))
    return words


def char_weight(text):
    """按字数算权重：只数非空白、非标点的"实义字符"，标点/空格权重折半，防止标点抢时间。"""
    w = 0
    for ch in text:
        if ch.isspace():
            continue
        if ch in "，。！？、；：''（）…—,.!?;:":
            w += 0.3
        else:
            w += 1.0
    return w


def distribute_by_char(lyrics, words):
    '词级段数≠台词数时：按每句字数占比，把词时间轴切成不重叠的区间。\nTTS 按我们的台词匀速朗读，字数占比≈时长占比，每句锚在真实词时间上。'

    n = len(words)
    if n == 0:
        raise SystemExit("[对齐失败] whisper 词级未产出任何词时间戳。")
    weights = [char_weight(l) for l in lyrics]
    total = sum(weights)
    if total <= 0:
        raise SystemExit("[对齐失败] 台词权重为 0。")
    # 累积权重 -> 词下标边界
    bounds = []
    acc = 0.0
    for w in weights:
        bounds.append(acc)
        acc += w
    bounds.append(total) # 末尾
    entries = []
    for i, ly in enumerate(lyrics):
        s0 = int(round(bounds[i] / total * n))
        s1 = int(round(bounds[i + 1] / total * n))
        if s1 <= s0:
            s1 = s0 + 1
        if s0 >= n:
            s0 = n - 1
        if s1 > n:
            s1 = n
        t0 = words[s0][0]
        t1 = words[s1 - 1][1]
        # 兜底：极端情况下末词 end==start（理论上 load_words 已剔除，双保险）
        if t1 <= t0:
            if s1 < n:
                t1 = words[s1][1]
            else:
                t1 = t0 + 0.5
        entries.append((t0, t1, ly))
    # 强制单调不重叠：若后一句起点早于前一句终点，则推到前一句终点
    for i in range(1, len(entries)):
        if entries[i][0] < entries[i - 1][1]:
            entries[i] = (entries[i - 1][1], max(entries[i][1], entries[i - 1][1]), entries[i][2])
    return entries


def align_subtitles(lyrics, transcript_path=None, audio_path=None, out_path=None,
                    whisper_skill=DEFAULT_WHISPER_SKILL, lang="zh", word_level=False):
    """对齐并写 SRT。返回 (out_path, [(t0,t1,text), ...])。"""
    if not out_path:
        raise ValueError("out_path 必填")
    if transcript_path is None:
        assert audio_path, "需要 --transcript 或 --audio"
        transcript_path = out_path + (".whisper_wl.txt" if word_level else ".whisper_ts.txt")
        transcript_path, words_path = run_whisper(audio_path, transcript_path, lang, word_level)
    else:
        words_path = transcript_path + ".words"

    segs = load_transcript(transcript_path)
    if word_level and len(lyrics) != len(segs):
        # 词级段数≠台词数：用每个词的独立时间戳，按字数比例切分（TTS 匀速，最准）
        if not os.path.exists(words_path):
            raise SystemExit(
                f"[对齐失败] 词级段数 {len(segs)} ≠ 台词 {len(lyrics)}，且缺词级文件 {words_path}。\n"
                f"请用 --word-level 重新跑 whisper 以生成词时间戳。")
        words = load_words(words_path)
        print(f"[对齐] 正确台词 {len(lyrics)} 句 / whisper 段 {len(segs)} / 词 {len(words)}")
        print("[对齐] 段数不一致，改用词级时间轴 + 按字数比例切分（TTS 匀速）")
        entries = distribute_by_char(lyrics, words)
    elif len(lyrics) != len(segs):
        raise SystemExit(
            f"[对齐失败] 段数不一致：正确台词 {len(lyrics)} 句 vs whisper {len(segs)} 段。\n"
            f"请检查：whisper 是否把两句并成一段 / 拆成两段，或台词行数是否漏写。"
        )
    else:
        entries = [(t0, t1, lyrics[i]) for i, (t0, t1) in enumerate(segs)]
    with open(out_path, "w", encoding="utf-8") as f:
        for i, (t0, t1, text) in enumerate(entries, 1):
            f.write(f"{i}\n{sec2srt(t0)} --> {sec2srt(t1)}\n{text}\n\n")
    print(f"[对齐完成] {len(entries)} 条 -> {out_path}" + (" (词级)" if word_level else ""))
    for i, (t0, t1, text) in enumerate(entries, 1):
        print(f"  [{i:2d}] {sec2srt(t0)} --> {sec2srt(t1)}  {text}")
    return out_path, entries


def h3_actual_seconds(req_sec, fps=24):
    '请求 req_sec 秒 → H3 视频流实际生成秒数（向上对齐 17k+5 @24fps）。\n仅用于：后期段定位 / 镜头锚定对齐；工作流侧仍按 req_sec 传。\n公式本体在公共件 fxai-audio-video（与 MV / 数字人口播共用同一份）。'

    return _av.h3_actual_seconds(req_sec, fps)


def shot_boundaries(shots):
    """按 H3 算法累加每镜实际秒数，返回 [起, 止) 边界列表（长度 len(shots)+1）。"""
    b = [0.0]
    for s in shots:
        b.append(b[-1] + h3_actual_seconds(float(s.get("duration", 0))))
    return b


def _extract_d_lines(prompt):
    return [m.strip() for m in re.findall(r"<d>\[Chinese\](.*?)</d>", prompt, re.DOTALL)]


def align_subtitles_shotwise(shots, words_path, out_path, lang="zh"):
    '镜头锚定对齐（比全局比例更准，消除尾部漂移）。\n每镜：用 H3 公式算 [起,止) 窗口 → 取落在该窗口内的 whisper 词作为本镜真实语音区间 →\n按字数比例把本镜台词切进该区间。无词则退化为按窗口均分（仍锚在镜位）。\n返回 (out_path, entries)。'



    words = load_words(words_path)
    bounds = shot_boundaries(shots)
    entries = []
    for si, shot in enumerate(shots):
        lines = _extract_d_lines(shot.get("prompt", ""))
        if not lines:
            continue
        s0, s1 = bounds[si], bounds[si + 1]
        # 落在本镜窗口内的词（用中点判定）
        inw = [w for w in words if s0 <= (w[0] + w[1]) / 2.0 < s1]
        if inw:
            t0, t1 = inw[0][0], inw[-1][1]
            nw = len(inw)
            weights = [char_weight(l) for l in lines]
            tot = sum(weights) or 1.0
            acc = 0.0
            for li, ly in enumerate(lines):
                f0 = acc / tot
                acc += char_weight(ly)
                f1 = acc / tot
                i0 = min(nw - 1, int(round(f0 * nw)))
                i1 = min(nw, int(round(f1 * nw)))
                if i1 <= i0:
                    i1 = i0 + 1
                a = inw[i0][0]
                b = inw[i1 - 1][1]
                if b <= a:
                    b = a + 0.4
                entries.append((a, b, ly))
        else:
            # 退化：按窗口 + 字数比例均分（锚在镜位，防全局漂移）
            weights = [char_weight(l) for l in lines]
            tot = sum(weights) or 1.0
            acc = 0.0
            for ly in lines:
                a = s0 + acc / tot * (s1 - s0)
                acc += char_weight(ly)
                b = s0 + acc / tot * (s1 - s0)
                if b <= a:
                    b = a + 0.4
                entries.append((a, b, ly))
    # 单调不重叠
    for i in range(1, len(entries)):
        if entries[i][0] < entries[i - 1][1]:
            entries[i] = (entries[i - 1][1], max(entries[i][1], entries[i - 1][1]), entries[i][2])
    with open(out_path, "w", encoding="utf-8") as f:
        for i, (t0, t1, text) in enumerate(entries, 1):
            f.write(f"{i}\n{sec2srt(t0)} --> {sec2srt(t1)}\n{text}\n\n")
    print(f"[镜头锚定对齐] {len(entries)} 条 -> {out_path}")
    for i, (t0, t1, text) in enumerate(entries, 1):
        print(f"  [{i:2d}] {sec2srt(t0)} --> {sec2srt(t1)}  ({t1 - t0:5.2f}s)  {text}")
    return out_path, entries


def align_standard(shots, out_path, audio_path=None, transcript_path=None,
                   whisper_skill=DEFAULT_WHISPER_SKILL, lang="zh"):
    '字幕对齐标准动作（2026-09-28 定稿 · 一句话走完全套）：\n\n1. 取词级时间轴：给 audio_path 则自动跑 whisper 词级（产出 .words）；给 transcript_path 则直接用。\n2. load_words 已内置剔除零时长幽灵词。\n3. 用 H3 窗口把每镜台词锚定到所属镜头，按字数切分（消除全局尾部漂移）。\n文字一律来自 shots 的 <d> 台词真值，whisper 只借时间。返回 (out_path, entries)。'





    if transcript_path is None:
        assert audio_path, "需要 --audio 或 transcript_path"
        transcript_path = out_path + ".whisper_wl.txt"
        run_whisper(audio_path, transcript_path, lang, word_level=True)
    words_path = transcript_path + ".words"
    return align_subtitles_shotwise(shots, words_path, out_path)


def main():
    ap = argparse.ArgumentParser(description="whisper 取时间 + 正确台词替换 -> 对齐 SRT")
    ap.add_argument("--lyrics", help="正确台词纯文本，每行一句（无 --shots 时必填）")
    ap.add_argument("--shots", help="镜头定义 py 文件（定义 shots 列表，含 <d> 台词）；给了则走镜头锚定标准动作")
    ap.add_argument("--transcript", help="whisper 输出文件；不给则自动跑 whisper")
    ap.add_argument("--audio", help="音轨 wav；与 --transcript 二选一")
    ap.add_argument("--out", required=True, help="输出 SRT 路径")
    ap.add_argument("--whisper-skill", default=DEFAULT_WHISPER_SKILL,
                    help="fxai-whisper-transcribe 技能目录（一般不用传）")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--word-level", action="store_true",
                    help="词级时间戳（推荐）：每句字幕=首词开始/末词结束，消除句间静音带来的提前")
    args = ap.parse_args()

    if args.shots:
        # 标准动作：镜头锚定词级对齐
        ns = {}
        exec(compile(open(args.shots, encoding="utf-8").read(), args.shots, "exec"), ns)
        shots = ns.get("shots")
        if shots is None:
            raise SystemExit(f"[错误] {args.shots} 未定义 shots 变量")
        align_standard(shots, args.out, audio_path=args.audio,
                       transcript_path=args.transcript,
                       whisper_skill=args.whisper_skill, lang=args.lang)
    else:
        if not args.lyrics:
            raise SystemExit("[错误] 未给 --shots 时必须给 --lyrics")
        lyrics = load_lyrics(args.lyrics)
        align_subtitles(
            lyrics,
            transcript_path=args.transcript,
            audio_path=args.audio,
            out_path=args.out,
            whisper_skill=args.whisper_skill,
            lang=args.lang,
            word_level=args.word_level,
        )


if __name__ == "__main__":
    main()
