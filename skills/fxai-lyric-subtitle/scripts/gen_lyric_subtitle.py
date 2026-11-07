# -*- coding: utf-8 -*-
'给视频/音频生成标准歌词字幕（SRT + ASS），可选烧录。\n\n流程：抽音轨 → whisper 提取分段时间戳 → 与我们生成的歌词做强制对齐（forced alignment）\n      → 生成 SRT/ASS → (可选) 用带 libass 的 ffmpeg 烧录。\n\n关键点（详见 SKILL.md）：\n- 烧录必须用**带 libass 的 ffmpeg**（conda 自带的 ffmpeg 无 libass，中文会烧成豆腐块）；凤希AI伴侣安装目录（固定目录名 `<盘>:\fxai`）自带的 ffmpeg 即带 libass，按全局配置 `software_install_dir` 自动定位，无需手写路径。\n- ASS 文件 UTF-8 无 BOM；FontName 用 Microsoft YaHei。\n- karaoke 的 \\k 单位为厘秒（1/100s）。\n\n歌词对齐设计（来自 MV 管线约定）：\n- whisper 只负责提供**时间点**；真正显示的歌词文字，一律以「我们自己生成的歌词」为准。\n- 对齐采用「分段（phrase）级」：whisper 的每段本就是一句自然乐句，把我们的每句歌词对齐到\n  它实际对应的那段，获取该段的时间区间。这样即使歌曲实际唱词与歌词有出入（如副歌改写），\n  歌词也会落在正确的乐句时间段上，伴奏段自然落在歌词之外。\n- 同时输出 `_alignment_record.json`：每行记录 {start, end, lyric(我们的词), whisper(听到的词)}，\n  方便后续人工做错别字校正复用。\n\n用法：\n  python gen_lyric_subtitle.py --video in.mp4 --lyrics lyrics.txt       --out-dir . --out-name sub --no-burn        # 只生成字幕\n  python gen_lyric_subtitle.py --video in.mp4 --out-name sub   # 生成并烧录 *_sub.mp4\n'






















import os, re, sys, json, argparse, subprocess, difflib

# 路径支持用户级全局部署配置 fxai_global.json 覆盖（安装时由 setup_env.py 写入
# ~/.workbuddy/fxai_global.json），无则回退默认（向后兼容）。
_this = os.path.dirname(os.path.abspath(__file__))
_SKILLS = os.path.dirname(os.path.dirname(_this))  # 技能包根目录（定位兄弟技能 fxai-mv-pipeline）
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


def _cfg(key, default):
    return _CFG.get(key, default)


# 软件目录（供自动定位 ffmpeg；用户只需提供 software_install_dir / software_data_dir）
_SOFT_INSTALL = _cfg("software_install_dir", "")
_SOFT_DATA = _cfg("software_data_dir", "")


def _detect_ffmpeg():
    """ffmpeg 默认随软件安装目录自带（带 libass）。"""
    if _SOFT_INSTALL:
        c = os.path.join(_SOFT_INSTALL, "ffmpeg", "ffmpeg.exe")
        if os.path.isfile(c):
            return os.path.normpath(c)
    return "ffmpeg" # 兜底：依赖 PATH 中的 ffmpeg


FFMPEG = _cfg("ffmpeg", "") or _detect_ffmpeg()

_WC = None


def _whisper_skill_dir():
    """定位 fxai-whisper-transcribe 技能目录（配置 whisper_skill 优先，不写死路径）。"""
    p = _cfg("whisper_skill", "")
    if p and os.path.isfile(os.path.join(p, "whisper_client.py")):
        return os.path.normpath(p)
    for rel in (os.path.join(_this, "..", "..", "fxai-whisper-transcribe"),
                os.path.join(_this, "..", "fxai-whisper-transcribe")):
        d = os.path.normpath(rel)
        if os.path.isfile(os.path.join(d, "whisper_client.py")):
            return d
    raise RuntimeError("找不到 fxai-whisper-transcribe 技能目录（whisper_client.py）")


def whisper_client():
    """导入 whisper 客户端（全接口）；导入一次后缓存。"""
    global _WC
    if _WC is not None:
        return _WC
    import importlib.util
    p = os.path.join(_whisper_skill_dir(), "whisper_client.py")
    spec = importlib.util.spec_from_file_location("fxai_whisper_client", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    _WC = m
    return m


def extract_audio(video, wav):
    subprocess.run([FFMPEG, "-y", "-i", video, "-ar", "16000", "-ac", "1",
                    "-c:a", "pcm_s16le", wav], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def whisper_ts(audio, transcript, words=False):
    """调 whisper 服务接口，写 `[start->end] 文本` 每行。

    words=True 出**逐字**时间戳（中文里 word 级≈字级）。
    服务地址由全局配置 whisper 段决定（本机 8185 / 线上），本脚本只发 HTTP。
    """
    wc = whisper_client()
    wc.write_transcript(audio, transcript, words=bool(words), language="zh")


def _audio_duration(path):
    """取音频时长秒（取不到返回 None）。"""
    try:
        out = subprocess.run([FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error",
                              "-show_entries", "format=duration",
                              "-of", "csv=p=0", path],
                             capture_output=True, text=True, check=True)
        return float(out.stdout.strip().split(",")[0])
    except Exception:
        return None

def parse_transcript(path):
    segs = []
    for line in open(path, encoding="utf-8"):
        m = re.match(r"\[([\d.]+)->([\d.]+)\]\s*(.*)", line.strip())
        if m:
            segs.append([float(m.group(1)), float(m.group(2)), m.group(3).strip()])
    return segs


def load_lyrics(path):
    if not path:
        return None
    return [l.strip() for l in open(path, encoding="utf-8")
            if l.strip() and not l.strip().startswith("[")]


def _sim(a, b):
    """两句（忽略空格）的字符相似度，用 difflib ratio，容忍局部错词。"""
    a2 = (a or "").replace(" ", "").replace("　", "")
    b2 = (b or "").replace(" ", "").replace("　", "")
    if not a2 or not b2:
        return 0.0
    return difflib.SequenceMatcher(None, a2, b2, autojunk=False).ratio()


_PUNCT = "，。！？、；：,.!?;: \u0009\u3000" \
         "\u2014\u2013\u2015\u2010\u2011\u2012\uff5e\u2026\u22ef~" \
         "\u300c\u300d\u300e\u300f\u300a\u300b\u3008\u3009\uff08\uff09\u0028\u0029\u005b\u005d\u3010\u3011\u007b\u007d\u3014\u3015" \
         "\u201c\u201d\u2018\u2019\u0060\u0022\u0027" \
         "\u00b7\u30fb\u2027\u3003"
_CN_DIGIT = {"零": "0", "〇": "0", "一": "1", "二": "2", "两": "2", "三": "3", "四": "4",
             "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}
_CN_UNIT = {"十": "10", "百": "100", "千": "1000"}
# 数字串（含中文数字与阿拉伯数字混排）
_NUM_RE = re.compile(r"[0-9零〇一二两三四五六七八九十百千]+")


def _cn_number_to_digits(t):
    """把一段数字串转成阿拉伯数字串，支持「一百九十八」→198、「二十」→20、「198」原样。

    正文常写中文数字、whisper 听成阿拉伯数字，不归一化就匹配不上，
    会把整句窗口算短。
    """
    def one(m):
        s = m.group(0)
        if s.isdigit():
            return s
        total, section, digit = 0, 0, None
        for ch in s:
            if ch in _CN_DIGIT:
                digit = int(_CN_DIGIT[ch])
            elif ch in _CN_UNIT:
                unit = int(_CN_UNIT[ch])
                if digit is None:
                    digit = 1          # 「十五」的十前面没有一
                section += digit * unit
                digit = None
            else:                     # 「万」等更大的单位，本处不覆盖，原样保留
                return s
        total += section + (digit or 0)
        return str(total)
    return _NUM_RE.sub(one, t)


def _norm_chars(t):
    """只保留实义字符（去标点空白、数字归一），用于跨 ASR 差异匹配。"""
    return _cn_number_to_digits("".join(c for c in t if c not in _PUNCT))


# 单条字幕的上限，**按画面方向分档**（字号随分辨率变，MV 的 ass_header 就是这么算的）：
#   竖屏 768x1280 / 字号 32 → 一行约 20 字 → 4.0 秒 / 16 字（16 字≈3.8 秒，与语速匹配）
#   横屏 1280x768  / 字号 38 → 一行约 28 字 → 6.0 秒 / 24 字（24 字≈5.8 秒）
# 横屏档位比竖屏更宽（24 字 / 6.0 秒）。
SUB_MAX_SEC = 4.0
SUB_MAX_CHARS = 16
SUB_MAX_SEC_LANDSCAPE = 6.0
SUB_MAX_CHARS_LANDSCAPE = 24
_SUB_PUNCT = "。！？；，、：…—"
# 字幕画面上不显示标点（只用于断句），显示更干净
_SHOW_STRIP = "。，、；：！？…—,.!?;: \u3000"


def sub_limits(w, h, max_sec=0, max_chars=0):
    """按画面方向取单条字幕上限；显式给了值就以传入的为准。"""
    land = w > h
    sec = max_sec if max_sec and max_sec > 0 else (
        SUB_MAX_SEC_LANDSCAPE if land else SUB_MAX_SEC)
    ch = max_chars if max_chars and max_chars > 0 else (
        SUB_MAX_CHARS_LANDSCAPE if land else SUB_MAX_CHARS)
    return float(sec), int(ch)


def split_for_subtitle(segs, max_sec=SUB_MAX_SEC, max_chars=SUB_MAX_CHARS):
    """把长句再切成短条：**优先按标点断，其次按字数硬断**；时间在原句窗口内按字数比例分配。

    不按固定秒数盲切——那样会切断句子。标点断出来的短条既不超屏，也跟得上说话。
    """
    out = []
    for item in segs:
        s0, e0, line = item[0], item[1], item[2]
        span = (e0 - s0) or 0.4
        parts, buf = [], ""
        for ch in line:
            buf += ch
            if ch in _SUB_PUNCT and len(_norm_chars(buf)) >= 4:
                parts.append(buf)
                buf = ""
        if buf.strip():
            parts.append(buf)
        chunks = []
        for pt in parts:
            cs = list(pt)
            while cs:
                cut = min(max_chars, len(cs))
                for i in range(min(max_chars, len(cs)) - 1, 0, -1):
                    if cs[i] in _SUB_PUNCT:
                        cut = i + 1
                        break
                chunks.append("".join(cs[:cut]).strip())
                cs = cs[cut:]
        # 显示时去掉标点（标点只参与断句，不上屏）
        chunks = ["".join(c for c in c if c not in _SHOW_STRIP) for c in chunks]
        chunks = [c for c in chunks if c]
        if not chunks:
            continue
        # 时间按字数**等比分配**，不做上限截断。
        # 截断会把时间"偷掉"：前面的条都被压到max_sec，剩余全落到末条，
        # 于是出现「10 个字只给 0.5 秒、最后一条 0.1 秒」这种错位。
        # 时长上限靠**切分**保证（上面按标点+字数切），不靠压缩时间。
        weights = [float(len(_norm_chars(c)) or 1) for c in chunks]
        total = sum(weights)
        t = s0
        for i, c in enumerate(chunks):
            dur = span * weights[i] / total
            if i == len(chunks) - 1:
                dur = e0 - t# 末条补齐到窗口末尾，不留缝
            if dur <= 0.05:
                dur = 0.05
            out.append((round(t, 2), round(t + dur, 2), c, ""))
            t += dur
    return out


def check_subtitle_timing(segs, total_sec=None, min_sec=0.6, max_sec=None):
    """校验字幕时间轴：递增不重叠、无空档、时长合理。

    字幕时间轴有问题直接抛错、不让落盘。
    """
    if not segs:
        raise ValueError("字幕为空")
    problems = []
    for i, item in enumerate(segs, 1):
        s0, e0, txt = item[0], item[1], item[2]
        d = e0 - s0
        if d <= 0:
            problems.append("第%d条时长非正(%.2f→%.2f) %r" % (i, s0, e0, txt[:12]))
        elif d < min_sec:
            problems.append("第%d条仅%.2f秒却装%d字 %r" % (i, d, len(_norm_chars(txt)), txt[:12]))
        elif max_sec and d > max_sec + 0.05:
            problems.append("第%d条%.2f秒超过上限 %r" % (i, d, txt[:12]))
        if i > 1 and s0 < segs[i - 2][1] - 1e-6:
            problems.append("第%d条与上一条重叠(%.2f < %.2f) %r"
                            % (i, s0, segs[i - 2][1], txt[:12]))
    # 覆盖率：字幕总时长 vs 音轨时长（允许尾部 1.5s 静音余量）
    if total_sec and segs[-1][1] < total_sec - 1.5:
        problems.append("字幕只到 %.2f 秒，音轨 %.2f 秒，尾部缺失" % (segs[-1][1], total_sec))
    if problems:
        raise ValueError("字幕时间轴有问题：\n  - " + "\n  - ".join(problems[:12]))
    return True


def align_chars_to_lines(lines, words):
    """**逐字**对齐：正文每个字直接取它自己的时间戳（中文 word 级≈字级）。

    比句级对齐更稳：句级靠字符顺序推进定位边界，ASR 多字/漏字会让边界漂移；
    逐字是每个字独立查表（重复字按出现顺序排队），单字错漏只影响那一个字，
    不会带偏整句，且对「ASR 多插/漏插字」免疫。

    words: [[start, end, text], …]（逐字）。返回 [(start, end, line, matched), …]。
    """
    from collections import defaultdict, deque
    table = defaultdict(deque)
    for st, en, tx in words:
        for ch in _norm_chars(tx):
            table[ch].append((st, en))
    out, missing = [], 0
    for line in lines:
        spans = []
        for ch in _norm_chars(line):
            if table[ch]:
                spans.append(table[ch].popleft())
            else:
                missing += 1
        if spans:
            out.append([spans[0][0], spans[-1][1], line, ""])
        else:
            out.append([None, None, line, ""])
    # 有整句没对上→交给调用方走句级/兜底，别硬给错时间
    if any(o[0] is None for o in out):
        return []
    if missing:
        print("[align] 逐字对齐：有 %d 个字未在 ASR 里找到（不影响时间定位）" % missing,
              file=sys.stderr)
    # 保证单调不重叠
    for i in range(1, len(out)):
        if out[i][0] < out[i - 1][1]:
            out[i][0] = out[i - 1][1]
            if out[i][1] <= out[i][0]:
                out[i][1] = out[i][0] + 0.3
    return [tuple(o) for o in out]


def align_speech_captions(lines, words, max_chars=16, max_sec=4.0, min_sec=0.6):
    """按正文标点分句，以全局单调编辑对齐映射实际语音时间戳。"""
    def normalized(text):
        if os.name == "nt":
            import ctypes
            buf = ctypes.create_unicode_buffer(len(text) * 2 + 2)
            size = ctypes.windll.kernel32.LCMapStringW(0x0804, 0x02000000, text, len(text), buf, len(buf))
            if size:
                text = buf[:size].rstrip("\x00")
        else:
            try:
                from opencc import OpenCC
                text = OpenCC("t2s").convert(text)
            except ImportError:
                pass
        return _norm_chars(text).lower()

    def clean(text):
        return "".join(c for c in text if c not in _PUNCT and not c.isspace())

    def chunks_of(text):
        phrases = [p for p in re.split(r"[。！？；，、：…—,.!?;:]+", text) if clean(p)]
        chunks = []
        for phrase in phrases:
            tokens = re.findall(r"[A-Za-z0-9]+|[^A-Za-z0-9]", clean(phrase))
            total_chars = sum(len(t) for t in tokens)
            n_chunks = max(1, (total_chars + max_chars - 1) // max_chars)
            limit = (total_chars + n_chunks - 1) // n_chunks
            phrase_chunks, buf = [], ""
            for token in tokens:
                if len(token) > max_chars:
                    raise ValueError("单个英文词或数字超过字幕字数上限：" + token)
                if buf and len(buf) + len(token) > limit:
                    phrase_chunks.append(buf)
                    buf = ""
                buf += token
            if buf:
                if phrase_chunks and len(buf) < 3:
                    previous = phrase_chunks[-1]
                    move = 3 - len(buf)
                    if len(previous) > move and all(not c.isascii() for c in previous[-move:]):
                        phrase_chunks[-1], buf = previous[:-move], previous[-move:] + buf
                phrase_chunks.append(buf)
            chunks.extend(phrase_chunks)
        return chunks

    chunks = [chunk for line in lines for chunk in chunks_of(line)]
    wanted = [normalized(c) for c in chunks]
    target = "".join(wanted)
    timed = []
    for st, en, text in words:
        norm = normalized(text)
        for i, ch in enumerate(norm):
            timed.append((ch, st + (en - st) * i / len(norm),
                          st + (en - st) * (i + 1) / len(norm)))
    source = "".join(w[0] for w in timed)
    if not target or not source:
        raise ValueError("正文或语音时间戳为空，无法对齐")

    # 编辑距离约束一对一、按顺序匹配；错漏字只影响局部。
    n, m = len(target), len(source)
    rows = [list(range(m + 1))]
    for i in range(1, n + 1):
        row = [i] + [0] * m
        prev = rows[-1]
        for j in range(1, m + 1):
            row[j] = min(prev[j - 1] + (target[i - 1] != source[j - 1]),
                         prev[j] + 1, row[j - 1] + 1)
        rows.append(row)
    quality = 1.0 - rows[n][m] / max(n, m)
    if quality < 0.70:
        raise ValueError("语音与正文匹配率不足：%.1f%%；检查素材版本" % (quality * 100))
    mapping = [None] * n
    i, j = n, m
    while i or j:
        if i and j and rows[i][j] == rows[i - 1][j - 1] + (target[i - 1] != source[j - 1]):
            mapping[i - 1] = j - 1
            i -= 1
            j -= 1
        elif j and rows[i][j] == rows[i][j - 1] + 1:
            j -= 1
        else:
            i -= 1

    captions, pos = [], 0
    for text, norm in zip(chunks, wanted):
        hits = [v for v in mapping[pos:pos + len(norm)] if v is not None]
        pos += len(norm)
        if not hits:
            raise ValueError("字幕短句没有实际时间锚点：" + text)
        st, en = timed[hits[0]][1], timed[hits[-1]][2]
        if en <= st:
            raise ValueError("字幕时间窗口为空：" + text)
        if en - st <= max_sec:
            captions.append([st, en, text, ""])
            continue
        # 超长短句按实际字时间选择切点，时间沿用原字时间。
        tokens = re.findall(r"[A-Za-z0-9]+|[^A-Za-z0-9]", text)
        local = mapping[pos - len(norm):pos]
        offset, group, group_start, group_end = 0, "", None, None
        for token in tokens:
            count = len(_norm_chars(token))
            token_hits = [v for v in local[offset:offset + count] if v is not None]
            offset += count
            if not token_hits:
                group += token
                continue
            ts, te = timed[token_hits[0]][1], timed[token_hits[-1]][2]
            if group_start is not None and te - group_start > max_sec and group:
                captions.append([group_start, group_end, group, ""])
                group, group_start, group_end = "", None, None
            if group_start is None:
                group_start = ts
            group += token
            group_end = te
        if group and group_start is not None:
            captions.append([group_start, group_end, group, ""])

    # 短条优先与相邻语义短句合并，保持实际语音首尾边界。
    k = 0
    while k < len(captions):
        cue = captions[k]
        if cue[1] - cue[0] < min_sec:
            merged = False
            for neighbor in (k + 1, k - 1):
                if not 0 <= neighbor < len(captions):
                    continue
                a, b = sorted((k, neighbor))
                left, right = captions[a], captions[b]
                if (len(left[2]) + len(right[2]) <= max_chars
                        and right[1] - left[0] <= max_sec
                        and right[0] - left[1] <= 0.5):
                    captions[a:b + 1] = [[left[0], right[1], left[2] + right[2], ""]]
                    k = max(0, a - 1)
                    merged = True
                    break
            if merged:
                continue
            # 在静音空隙延长显示，不改变实际语音匹配关系。
            lower = captions[k - 1][1] if k else 0.0
            upper = captions[k + 1][0] if k + 1 < len(captions) else words[-1][1] + 0.2
            cue[1] = min(upper, max(cue[1], cue[0] + min_sec))
            if cue[1] - cue[0] < min_sec:
                cue[0] = max(lower, cue[1] - min_sec)
        k += 1
    for k in range(1, len(captions)):
        if captions[k][0] < captions[k - 1][1]:
            captions[k - 1][1] = captions[k][0]
    print("[align] 单调语音对齐匹配率 %.1f%%，字幕 %d 条" % (quality * 100, len(captions)), flush=True)
    check_subtitle_timing(captions, max_sec=max_sec + 0.05, min_sec=min_sec - 0.005)
    return [tuple(c) for c in captions]


def parse_words(path):
    """读逐字时间戳文件，返回 [[start, end, text], …]。"""
    out = []
    for line in open(path, encoding="utf-8"):
        m = re.match(r"\[([\d.]+)->([\d.]+)\]\s*(.*)", line.strip())
        if m and m.group(3).strip():
            out.append([float(m.group(1)), float(m.group(2)), m.group(3).strip()])
    return out


def align_lyrics_spans(segments, lyrics):
    """把每句歌词对齐到**一个完整时间窗口**（它覆盖的所有 whisper phrase 的首尾）。

    whisper 常把一句切成多个 phrase。
    逐 phrase 显示会让字幕只显示半句、中途消失，因此按整句对齐：
      1) 把 lyrics 每行与 segments 做**顺序推进的字符级匹配**，确定该句吃掉哪些 phrase；
      2) 该句的窗口 = 第一个命中 phrase 的 start → 最后一个命中 phrase 的 end；
      3) 匹配不上的行（ASR 错字/漏字）用剩余时长均分兜底，不丢人。
    返回 [(start, end, line, matched_text), …]（start<end，按序不重叠）。
    """
    if not segments or not lyrics:
        return []
    full = "".join(_norm_chars(seg[2]) for seg in segments)
    bounds, pos = [], 0
    for seg in segments:                      # 每个 phrase 在 full 里的字符区间
        n = len(_norm_chars(seg[2]))
        bounds.append((pos, pos + n))
        pos += n

    out, cur = [], 0                          # cur = 已消费到的字符位置
    for line in lyrics:
        want = _norm_chars(line)
        if not want:
            continue
        # 顺序推进匹配：允许跳过 ASR 多出的字，但不允许回头
        ci, hit = 0, []
        while ci < len(want) and cur < len(full):
            if full[cur] == want[ci]:
                for k, (b0, b1) in enumerate(bounds):
                    if b0 <= cur < b1:
                        if not hit or hit[-1] != k:
                            hit.append(k)
                        break
                ci += 1
            cur += 1
        if hit:
            s0 = segments[hit[0]][0]
            e0 = segments[hit[-1]][1]
            matched = "".join(segments[k][2] for k in range(hit[0], hit[-1] + 1))
            out.append([s0, max(e0, s0 + 0.4), line, matched])
        else:
            out.append([None, None, line, ""])   # 留给兜底
    # 兜底：未匹配的行按剩余区间均分
    if any(o[0] is None for o in out):
        prev_end = 0.0
        total = segments[-1][1]
        nmiss = sum(1 for o in out if o[0] is None)
        slots = (total - prev_end) / float(nmiss) if nmiss else 0
        for o in out:
            if o[0] is None:
                o[0] = round(prev_end, 2)
                o[1] = round(prev_end + max(slots, 0.6), 2)
                prev_end = o[1]
    # 保证单调不重叠
    for i in range(1, len(out)):
        if out[i][0] < out[i - 1][1]:
            out[i][0] = round(out[i - 1][1], 2)
            if out[i][1] <= out[i][0]:
                out[i][1] = round(out[i][0] + 0.6, 2)
    return [tuple(o) for o in out]


def align_lyrics_to_segments(segments, lyrics):
    '把每句歌词强制对齐到 whisper 分段（phrase 级），返回 list of (start, end, line, whisper_raw)。\n\n策略（贪心单调 + 局部窗口，对歌词对齐最稳）：\n  - 逐句处理，每行只在「上一句分配位置之后的一个小窗口」内挑选相似度最高的分段；\n    这样既按歌曲顺序对齐，又不会因副歌重复/个别错词而全局平移。\n  - 伴奏/无人声段（与任何歌词相似度近 0）自然落在该窗口之外被跳过，其时间段不留字幕。\n  - 多句若命中同一分段，则按各句字数占该段比例切分时间段（切出互不重叠的区间）。\n  - 匹配总体过低时退化为按总时长均匀铺排。\n'








    if not segments or not lyrics:
        return []
    n, m = len(lyrics), len(segments)
    WINDOW = 5 # 每句最多向后看几段，限制跨度过大时的错位

    # 1) 贪心单调分配：每行挑窗口内最相似的分段
    seg_ptr = 0
    assign = [] # line i -> seg index
    for i in range(n):
        best_j = min(seg_ptr, m - 1)
        best_s = -1.0
        for j in range(seg_ptr, min(seg_ptr + WINDOW, m)):
            s = _sim(lyrics[i], segments[j][2])
            if s > best_s:
                best_s, best_j = s, j
        assign.append(best_j)
        seg_ptr = best_j + 1

    # 2) 解析时间：单段直接取区间；多行共享同段则按字数切分
    out = [None] * n
    seg_to_lines = {}
    for i, sj in enumerate(assign):
        seg_to_lines.setdefault(sj, []).append(i)
    for sj, idxs in seg_to_lines.items():
        s, e = segments[sj][0], segments[sj][1]
        lens = [max(1, len(lyrics[i].replace(" ", "").replace("　", ""))) for i in idxs]
        tot = sum(lens)
        cur = s
        for i, ln in zip(idxs, lens):
            dur = (e - s) * ln / tot
            out[i] = [cur, cur + dur, lyrics[i], segments[sj][2]]
            cur += dur
    # 3) 兜底
    fb = _even_split(segments, lyrics)
    for i in range(n):
        if out[i] is None:
            out[i] = fb[i]
    return out


def _even_split(source, lyrics):
    """兜底：把歌词按 source 时间轴总区间均匀铺开。source 为含 [start,end,...] 的序列。"""
    if not source or not lyrics:
        return []
    t0 = source[0][0]
    t1 = source[-1][1]
    n = len(lyrics)
    segs = []
    for i, ln in enumerate(lyrics):
        s = t0 + (t1 - t0) * i / n
        e = t0 + (t1 - t0) * (i + 1) / n
        segs.append([s, e, ln, ""])
    return segs


# 唱歌时 whisper 常见幻觉：前奏被误识为「作曲 李宗盛」、尾段变成「好/我河上床」语气词，
# 这些段与任何歌词极不相似，必须丢弃，否则会把真歌词挤到错误的时间轴。
_HALLU_RE = re.compile(r"作曲|作词|编曲|李宗盛|河上床|好$")


def _filter_hallucination(segments, lyrics):
    """丢弃幻觉/非歌词段：① 含署名/语气词关键词；② 过短(≤2字)；③ 与全部歌词相似度均<0.18。

    ★ 相似度是**辅助**判断，不是唯一依据：短句口语天然与整句相似度低，
      但它是真实说话内容，误删会整体错位。归一化文本命中任何一句歌词的
      子串即保留。
    """
    if not lyrics:
        return segments
    norm_lyrics = [_norm_chars(l) for l in lyrics]
    out = []
    for (s, e, t) in segments:
        tt = t.strip()
        if _HALLU_RE.search(tt):
            continue
        if len(tt) <= 2:
            continue
        nt = _norm_chars(tt)
        if nt and any(nt in L for L in norm_lyrics if L):
            out.append([s, e, t])      # 是正文的子串 → 真实内容，保留
            continue
        mx = max(_sim(lyrics[i], tt) for i in range(len(lyrics)))
        if mx < 0.18:
            continue
        out.append([s, e, t])
    return out


def srt_time(t):
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return ("%02d:%02d:%06.3f" % (h, m, s)).replace(".", ",")


def ass_time(t):
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return "%d:%02d:%05.2f" % (h, m, s)


ASS_HEADER = None  # 运行时由 ass_header(w, h) 生成（PlayRes 必须等于视频真实分辨率）


def _video_size(video, default=(1280, 768)):
    """ffprobe 读视频真实宽高。PlayResX/Y 必须等于它，否则竖屏（768x1280）libass
    按错误画布算坐标，字幕飘到画面外 / 压在人物脸上。"""
    probe = os.path.join(os.path.dirname(ffmpeg_bin_name()), "ffprobe.exe")
    if not os.path.isfile(probe):
        probe = "ffprobe"
    try:
        r = subprocess.run([probe, "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", video],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
        wh = (r.stdout or b"").decode("utf-8", "replace").strip().split(",")
        if len(wh) >= 2 and wh[0].isdigit() and wh[1].isdigit():
            return int(wh[0]), int(wh[1])
    except Exception:
        pass
    return default


def ffmpeg_bin_name():
    return FFMPEG


def _mv_ass_header(w, h):
    """复用 MV 的 ass_header（fxai-mv-pipeline/lyrics_align.py）—— 字幕样式唯一真相源。

    MV 默认款 = 薄荷青：微软雅黑加粗 + 柔和描边 2.5
    + 半透明投影 1.2 + 字距 0.8，字号 ∝ 短边（横 0.050 / 竖 0.042）、下边距 ∝ 高。
    MV 改了这边自动同步；导入不到才用下面的等价本地实现兜底。
    """
    try:
        mv_dir = os.path.join(_SKILLS, "fxai-mv-pipeline")
        if mv_dir not in sys.path:
            sys.path.insert(0, mv_dir)
        from lyrics_align import ass_header as _mv_header
        return _mv_header(w, h)
    except Exception:
        return _ass_header_local(w, h)


def _ass_header_local(w, h):
    """MV 样式的本地等价实现（导入不到 lyrics_align 时的兜底，参数与 MV 保持一致）。"""
    w, h = int(w) if w else 1280, int(h) if h else 768
    land = w >= h
    fs = max(20, int(round(min(w, h) * (0.050 if land else 0.042))))
    mv = max(24, int(round(h * 0.078)))
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: %d\nPlayResY: %d\nWrapStyle: 0\n\n" % (w, h)
        + "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Microsoft YaHei,%d,&H00F5FBE8,&H000000FF,&H003A3F0E,&H50000000,"
        "1,0,0,0,100,100,0.8,0,1,2.5,1.2,2,30,30,%d,1\n\n" % (fs, mv)
        + "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Text\n"
    )


def ass_header(w, h):
    return _mv_ass_header(w, h)


def build(segs, karaoke, header=None):
    srt = []
    for i, (s, e, txt, _raw) in enumerate(segs, 1):
        srt.append(str(i))
        srt.append("%s --> %s" % (srt_time(s), srt_time(e)))
        srt.append(txt)
        srt.append("")
    srt_text = "\n".join(srt)

    ev = []
    for s, e, txt, _raw in segs:
        if karaoke:
            n = max(1, len(txt))
            per = max(1, int(round((e - s) * 100.0 / n))) # \k 单位为厘秒
            kar = "".join("{\\k%d}%s" % (per, ch) for ch in txt)
            ev.append("Dialogue: 0,%s,%s,Default,,0,0,0,%s" % (ass_time(s), ass_time(e), kar))
        else:
            ev.append("Dialogue: 0,%s,%s,Default,,0,0,0,%s" % (ass_time(s), ass_time(e), txt))
    ass_text = (header or ass_header(1280, 768)) + "\n".join(ev) + "\n"
    return srt_text, ass_text


def main():
    ap = argparse.ArgumentParser(description="生成视频歌词字幕（SRT/ASS）+ 可选烧录")
    ap.add_argument("--video", help="输入视频")
    ap.add_argument("--audio", help="输入音频（与 --video 二选一）")
    ap.add_argument("--lyrics", help="正确歌词文本，每行一句（[section] 行忽略）")
    ap.add_argument("--asr-mode", choices=["char", "phrase"], default="char",
                    help="对齐用哪种时间戳：char=逐字（默认，口播更稳）/ phrase=句级。"
                         "两者都已验证足够准；MV 那套多次识别是给唱歌的，口播不需要")
    ap.add_argument("--sub-max-sec", type=float, default=0,
                    help="单条字幕最长秒数（默认 0 = 按画面方向自动：竖屏 %.1f / 横屏 %.1f）"
                         % (SUB_MAX_SEC, SUB_MAX_SEC_LANDSCAPE))
    ap.add_argument("--sub-max-chars", type=int, default=0,
                    help="单条字幕最多字数（默认 0 = 自动：竖屏 %d / 横屏 %d）"
                         % (SUB_MAX_CHARS, SUB_MAX_CHARS_LANDSCAPE))
    ap.add_argument("--transcript", help="已有 whisper 分段文件，跳过 whisper")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--out-name", default="subtitled")
    ap.add_argument("--karaoke", action="store_true", help="生成卡拉OK逐字变色（平均分配，不跟随实际演唱）")
    ap.add_argument("--no-burn", action="store_true", help="只生成字幕文件，不烧录")
    ap.add_argument("--ffmpeg", default=FFMPEG)
    a = ap.parse_args()

    os.makedirs(a.out_dir, exist_ok=True)
    if a.video and not a.audio:
        wav = os.path.join(a.out_dir, "_audio_16k.wav")
        extract_audio(a.video, wav)
        audio = wav
    elif a.audio:
        audio = a.audio
    else:
        print("ERROR: need --video or --audio", file=sys.stderr)
        sys.exit(1)

    lyrics = load_lyrics(a.lyrics)

    # ★ 口播 = 纯朗读、无伴奏无重复，识别率高、错字少，**不做 MV 那套多次识别**
    #   （9/11/13s 多版互校是为唱歌设计的：伴奏、器乐段、歌词重复）。
    #   单字错漏不影响句级窗口定位，只有「整段错」才会偏。
    #   质量靠「逐字时间戳 + 落盘前自检」保证，不靠重复识别。
    # 没有文案时字幕文字来自识别，用句级（逐字太碎，一条字幕只有 1 个字）
    asr_mode = a.asr_mode if lyrics else "phrase"
    transcript = a.transcript or os.path.join(a.out_dir, "_transcript.txt")
    words_file = os.path.join(a.out_dir, "_words.txt")
    if not a.transcript:
        whisper_ts(audio, words_file if asr_mode == "char" else transcript,
                   words=(asr_mode == "char"))

    segs_raw = parse_transcript(transcript) if os.path.isfile(transcript) else []
    if lyrics:
        segs = []
        if asr_mode == "char" and os.path.isfile(words_file):
            _vw, _vh = _video_size(a.video) if a.video else (1280, 768)
            _ms, _mc = sub_limits(_vw, _vh, a.sub_max_sec, a.sub_max_chars)
            segs = align_speech_captions(lyrics, parse_words(words_file), _mc, _ms)
            if segs:
                print("[align] 逐字时间戳对齐（%d 字级时间戳）" % len(parse_words(words_file)),
                      flush=True)
            else:
                print("[align] 逐字对齐未整句命中，降级到句级", flush=True)
        if not segs:
            if not segs_raw:
                whisper_ts(audio, transcript)
                segs_raw = parse_transcript(transcript)
            segs_raw = _filter_hallucination(segs_raw, lyrics)  # 丢弃幻觉/非正文段
            # 句级：一句 = 一个完整窗口（首 phrase.start → 末 phrase.end），
            # 不逐 phrase 显示，否则字幕只显示半句、中途消失。
            segs = align_lyrics_spans(segs_raw, lyrics)
        if segs:
            _vw, _vh = _video_size(a.video) if a.video else (1280, 768)
            _ms, _mc = sub_limits(_vw, _vh, a.sub_max_sec, a.sub_max_chars)
            print("[sub] 单条上限：%.1f 秒 / %d 字（%s）"
                  % (_ms, _mc, "横屏" if _vw > _vh else "竖屏"), flush=True)
            if asr_mode != "char":
                segs = split_for_subtitle(segs, _ms, _mc)
            # 时间轴不合格即报错，不落盘
            check_subtitle_timing(segs, _audio_duration(audio), max_sec=_ms + 0.05)
        else:
            print("WARN: 对齐失败，退化为均匀铺排", file=sys.stderr)
            segs = _even_split(segs_raw, lyrics)
        # 记录对照表（供后续错别字校正复用）
        rec = [{"start": round(s, 2), "end": round(e, 2),
                "lyric": txt, "whisper": raw} for (s, e, txt, raw) in segs]
        with open(os.path.join(a.out_dir, "_alignment_record.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
    else:
        # 没有文案：字幕文字直接来自语音识别，并把识别稿另存一份供人工校对改字
        if not segs_raw:
            whisper_ts(audio, transcript)
            segs_raw = parse_transcript(transcript)
        segs = [(s, e, t, "") for (s, e, t) in segs_raw]
        src = a.transcript or os.path.join(a.out_dir, "_transcript.txt")
        if os.path.isfile(src):
            with open(src, encoding="utf-8") as f:
                raw = f.read().strip()
            md = os.path.join(a.out_dir, "语音识别稿.md")
            with open(md, "w", encoding="utf-8") as f:
                f.write("# 语音识别稿（字幕文字来源）\n\n"
                        "> 本次没有提供文案，字幕文字由**语音识别**得出。"
                        "核对/改字后可用 `--lyrics` 重新生成字幕。\n\n```\n"
                        + raw + "\n```\n")
            print("[sub] 无文案：字幕文字来自语音识别，识别稿 -> %s" % md, flush=True)

    vw, vh = _video_size(a.video) if a.video else (1280, 768)
    header = ass_header(vw, vh)
    srt_text, ass_text = build(segs, a.karaoke, header)
    print("ASS: PlayRes %dx%d（MV 默认样式：薄荷青）" % (vw, vh), flush=True)

    srt_path = os.path.join(a.out_dir, a.out_name + ".srt")
    ass_path = os.path.join(a.out_dir, a.out_name + ".ass")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(srt_text)
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_text)
    print("WROTE:", srt_path, ass_path, flush=True)
    print("段数: %d | karaoke=%s" % (len(segs), a.karaoke), flush=True)

    if not a.no_burn and a.video:
        out = os.path.join(a.out_dir, a.out_name + ".mp4")
        # 必须 cwd 到 out-dir，用相对路径（Windows 盘符不转义）
        subprocess.run([a.ffmpeg, "-y", "-i", a.video,
                        "-vf", "subtitles=filename=%s" % os.path.basename(ass_path),
                        "-c:a", "copy", os.path.basename(out)],
                       cwd=a.out_dir, check=True)
        print("BURNED:", out, flush=True)


if __name__ == "__main__":
    main()
