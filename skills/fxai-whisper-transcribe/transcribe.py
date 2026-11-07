# -*- coding: utf-8 -*-
"""本地音频转文本技能 (whisper / faster-whisper, large-v3)。

运行环境（不写死）：含 faster_whisper 的 python 由全局部署配置
~/.workbuddy/fxai_global.json 的 software_data_dir 推导
（<软件数据目录>/conda/env/whisper/python.exe，独立 conda env），跨机器/换盘无需改代码。
默认走 CUDA 第二张卡（CUDA_VISIBLE_DEVICES=1），与 ComfyUI 同卡策略一致。
"""












import os
import sys
import argparse
import json
import tempfile

# 读取全局部署配置（安装时由 setup_env.py 写入 ~/.workbuddy/fxai_global.json），
# 模型缓存目录统一由 software_data_dir 推导，本机绝对路径不进代码。
def _load_fxai_cfg():
    p = os.environ.get("FXAI_GLOBAL_CONFIG",
                       os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json"))
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


_FXAI = _load_fxai_cfg()
_DATA = _FXAI.get("software_data_dir", "")
# whisper 程序安装目录：模型在它的 model/ 子目录，**用哪个模型由程序自己定**，
# 全局配置只管「程序在哪」，模型名称 / 模型目录不进配置。
_PROGRAM_DIR = (_FXAI.get("whisper_dir") or "").strip()

# 模型缓存候选目录（按优先级）：
# 环境变量 HF_HOME(<HF_HOME>/hub) > <数据目录>/models/whisper > <数据目录>/whisper
# 本机绝对路径不进代码；HF_HOME 为用户标准 Hugging Face 缓存根目录。
def _model_cache_candidates():
    c = []
    # 标准环境变量 HF_HOME（Hugging Face 缓存根目录，模型在 <HF_HOME>/hub）。
    # 跨机器自动适配：用户可把 HF_HOME 指向自己的缓存盘，无需写死绝对路径。
    hf_home = os.environ.get("HF_HOME", "")
    if hf_home:
        c.append(os.path.join(hf_home, "hub"))
    # 根变量推导：<数据目录>/models/whisper、<数据目录>/whisper
    if _DATA:
        c.append(os.path.join(_DATA, "models", "whisper"))
        c.append(os.path.join(_DATA, "whisper"))
    return c


# 首选缓存目录（供外部 import：gen_subtitle_aligned 等会引用 MODEL_CACHE）
_CANDS = _model_cache_candidates()
MODEL_CACHE = (_CANDS[0] if _CANDS
               else os.path.join(_DATA, "whisper") if _DATA
               else os.path.join(tempfile.gettempdir(), "whisper_models"))
# 已缓存的本地模型仓库（在线兜底用；离线优先走下方本地快照目录）
LOCAL_REPO = "Systran/faster-whisper-large-v3"
DEFAULT_SIZE = "large-v3"
# 快照目录名（HF 缓存布局 models--<组织>--<仓库>）
_REPO_DIRS = ("models--Systran--faster-whisper-large-v3",)
# 指定使用的显卡：第二张卡
CUDA_DEVICE = "0"  # 2026-10-09 GPU1 掉卡（nvidia-smi 报 GPU is lost，需重启恢复），whisper 临时改用 GPU0


def local_model_dir():
    '解析本地 large-v3（满血版）模型目录（含 model.bin），离线可用。\n优先 whisper 程序目录 <whisper_dir>/model；否则在各候选缓存目录找 models--Systran--faster-whisper-large-v3 快照。'

    if _PROGRAM_DIR:
        d = os.path.join(_PROGRAM_DIR, "model")
        if os.path.isfile(os.path.join(d, "model.bin")):
            return d
    for base in _model_cache_candidates():
        for repo_dir in _REPO_DIRS:
            b = os.path.join(base, repo_dir)
            if not os.path.isdir(b):
                continue
            # 1) 读 refs/main 指向的快照
            ref = os.path.join(b, "refs", "main")
            if os.path.isfile(ref):
                rev = open(ref, "r", encoding="utf-8").read().strip()
                d = os.path.join(b, "snapshots", rev)
                if os.path.isfile(os.path.join(d, "model.bin")):
                    return d
            # 2) 退而求其次：snapshots 下第一个含 model.bin 的目录
            snap = os.path.join(b, "snapshots")
            if os.path.isdir(snap):
                for rev in sorted(os.listdir(snap)):
                    d = os.path.join(snap, rev)
                    if os.path.isfile(os.path.join(d, "model.bin")):
                        return d
    return None


def resolve_device(prefer=None):
    if prefer:
        return prefer
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _audio_duration(audio_path):
    """音频时长（秒）：优先 ffprobe，其次用 faster-whisper 解码兜底（不写死路径）。"""
    import shutil as _sh
    import subprocess as _sp
    exe = _sh.which("ffprobe")
    if exe:
        try:
            r = _sp.run([exe, "-v", "error", "-show_entries", "format=duration",
                         "-of", "csv=p=0", audio_path],
                        capture_output=True, text=True)
            v = float((r.stdout or "").strip())
            if v > 0:
                return v
        except Exception:
            pass
    try:
        from faster_whisper.audio import decode_audio
        a = decode_audio(audio_path, sampling_rate=16000)
        return a.shape[0] / 16000.0
    except Exception:
        return 0.0


def build_clip_timestamps(duration, seg):
    '把音频按 seg 秒**硬切**成 [(0,seg),(seg,2seg),...]（末段截到 duration）。\n\n硬切（clip_timestamps）实现真分段：切点不同 → 边界效应不同 → 两版可交叉校验；VAD 对连续伴奏的歌曲无效，故不用于分段。\n'






    seg = float(seg)
    clips, t = [], 0.0
    while t < duration - 1e-6:
        clips.append((round(t, 2), round(min(t + seg, duration), 2)))
        t += seg
    return clips or [(0.0, round(duration, 2))]


def transcribe(audio_path, model=None, language="zh", device=None, timestamps=False,
               vad=False, word_ts=False, beam=5, condition=True, max_speech=None,
               clip_seg=None, duration=None):
    "把音频转成文本。\n\naudio_path: wav/mp3/m4a 等 ffmpeg 能解的格式\nmodel: 留空则自动定位本地缓存的 large-v3 快照目录；\n       也可传 'large-v3' / 本地快照路径 / HF 仓库 id\nlanguage: 'zh' / 'en' / None(自动)\ndevice: 'cuda' / 'cpu' / None(自动探测，默认 cuda 第二张卡)\ntimestamps: True 则返回带时间轴的片段\nword_ts: True 则返回逐词时间戳（每行 [start->end] 词），用于强制对齐歌词\nbeam: beam_size（默认 5；多版交叉校验时可用不同 beam）\ncondition: 是否 condition_on_previous_text（歌里关掉可减少幻觉复读）\nmax_speech: 【不推荐】VAD 单段语音最大时长——对歌曲无效，见 build_clip_timestamps 说明\nclip_seg: 按该秒数**硬切**音频分段转写（如 9 / 10），时间戳仍为整首绝对时间。\n          分段长度不同跑两版即互为校验。vad 会被忽略。\nduration: 音频时长（秒）；给了就不重复探测。\n"















    from faster_whisper import WhisperModel

    if not os.path.isfile(audio_path):
        raise FileNotFoundError(audio_path)

    if model is None:
        model = local_model_dir() or LOCAL_REPO

    device = resolve_device(device)

    # 走 CUDA 时锁定第二张卡（与 ComfyUI 同策略）
    if device == "cuda":
        os.environ["CUDA_VISIBLE_DEVICES"] = CUDA_DEVICE

    compute_type = "float16" if device == "cuda" else "int8"

    # 本地快照目录直读，离线运行；仅当 model 是仓库 id 时才可能走网络兜底
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ["HF_HUB_CACHE"] = MODEL_CACHE

    m = WhisperModel(
        model,
        device=device,
        compute_type=compute_type,
        cpu_threads=max(4, os.cpu_count() or 4),
        download_root=os.path.join(MODEL_CACHE, "dl"),
    )

    kwargs = dict(language=language, beam_size=int(beam),
                  condition_on_previous_text=bool(condition))
    if clip_seg:
        dur = float(duration or 0) or _audio_duration(audio_path)
        if dur > 0:
            clips = build_clip_timestamps(dur, clip_seg)
            # 提供 clip_timestamps 时 faster-whisper 会忽略 vad_filter；
            # 词级时间戳仍是整首的绝对时间（内部已把 clip 偏移加回）。
            kwargs["clip_timestamps"] = ",".join("%.2f,%.2f" % c for c in clips)
            print("[clip] 硬切分段 %.1fs → %d 段（音频 %.2fs）" % (
                float(clip_seg), len(clips), dur))
    elif vad:
        kwargs["vad_filter"] = True
        if max_speech:
            kwargs["vad_parameters"] = {"max_speech_duration_s": float(max_speech)}
    if word_ts:
        kwargs["word_timestamps"] = True

    segments, info = m.transcribe(audio_path, **kwargs)
    if word_ts:
        lines = []
        for s in segments:
            for w in getattr(s, "words", []) or []:
                wtext = (getattr(w, "word", "") or "").strip()
                if not wtext:
                    continue
                lines.append("[%.2f->%.2f] %s" % (w.start, w.end, wtext))
        return "\n".join(lines)
    if timestamps:
        return "\n".join("[%.1f->%.1f] %s" % (s.start, s.end, s.text.strip()) for s in segments)
    return "".join(s.text for s in segments).strip()


def main():
    ap = argparse.ArgumentParser(description="本地 whisper 音频转文本")
    ap.add_argument("audio", help="音频文件路径")
    ap.add_argument("--model", default=None, help="本地快照路径 / 模型尺寸 / HF仓库id，默认本地 large-v3-turbo")
    ap.add_argument("--lang", default="zh", help="语言，默认 zh；auto 用 None")
    ap.add_argument("--device", default=None, help="cuda / cpu")
    ap.add_argument("--ts", action="store_true", help="输出带时间轴")
    ap.add_argument("--words", action="store_true", help="输出逐词时间戳（每行 [start->end] 词），用于歌词强制对齐")
    ap.add_argument("--vad", action="store_true", help="启用 VAD 过滤静音段（对歌曲无效，勿用于分段落）")
    ap.add_argument("--max-speech", type=float, default=None,
                    help="【不推荐】VAD 单段语音最大时长；歌曲无效，分段请用 --clip-seg")
    ap.add_argument("--clip-seg", type=float, default=None,
                    help="按该秒数硬切音频分段转写（如 9/10），时间戳仍为整首绝对时间")
    ap.add_argument("--dur", type=float, default=None, help="音频时长（秒），配合 --clip-seg 省一次探测")
    ap.add_argument("--beam", type=int, default=5, help="beam_size，默认 5")
    ap.add_argument("--no-condition", action="store_true",
                    help="关掉 condition_on_previous_text（歌里可减少幻觉复读）")
    ap.add_argument("--out", default=None, help="输出文本文件路径")
    a = ap.parse_args()

    lang = None if a.lang in ("auto", "none", "None") else a.lang
    text = transcribe(a.audio, model=a.model, language=lang, device=a.device,
                      timestamps=a.ts, vad=a.vad, word_ts=a.words,
                      beam=a.beam, condition=not a.no_condition,
                      max_speech=a.max_speech, clip_seg=a.clip_seg, duration=a.dur)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("SAVED %s (chars=%d)" % (a.out, len(text)))
    else:
        print(text)


if __name__ == "__main__":
    main()
