# -*- coding: utf-8 -*-
"""交付视频编码统一出口（跨技能共用，零第三方依赖）。

规格唯一来源：fxai-standard/references/12-交付视频编码规格.md。
码率数值只在本模块定义；产线只调函数，不得各自写 -b:v / -crf / -cq。
"""
import os
import subprocess
import tempfile

DELIVERY_TOTAL_KBPS = 2000          # 交付成片总比特率目标（视频 + 音频）
DELIVERY_AUDIO_BPS = 191678         # 源音轨 AAC 192k 的实测值；-c:a copy 时用
DELIVERY_MAXRATE_KBPS = 2200        # 单遍 ABR 的 VBV 峰值
DELIVERY_BUFSIZE_KBPS = 3600        # 单遍 ABR 的 VBV 缓冲
BITRATE_TOLERANCE = 0.10            # 验收容差 ±10%
MIN_APPLY_SECONDS = 30.0            # 短于此长度的片段/对比片不受交付码率约束


def delivery_video_bps(audio_bps=DELIVERY_AUDIO_BPS):
    """返回视频目标码率（bps）：总码率减去音轨码率。"""
    total = DELIVERY_TOTAL_KBPS * 1000
    if not 0 < audio_bps < total:
        raise ValueError("音轨码率必须落在 0 与 %d bps 之间，收到 %r"
                         % (total, audio_bps))
    return total - audio_bps


def delivery_venc(audio_copy=True, audio_bps=None, preset="medium", extra=None):
    """最终成片单遍编码参数列表（不含输入/输出路径）。

    直接替换产线里原有的 -crf / -cq 参数段；输出后调 assert_delivery_bitrate() 验收。
    """
    a = DELIVERY_AUDIO_BPS if audio_copy else (audio_bps or 192000)
    v = delivery_video_bps(a)
    args = ["-c:v", "libx264", "-preset", preset,
            "-b:v", "%dk" % (v // 1000),
            "-maxrate", "%dk" % DELIVERY_MAXRATE_KBPS,
            "-bufsize", "%dk" % DELIVERY_BUFSIZE_KBPS,
            "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    args += (["-c:a", "copy"] if audio_copy
             else ["-c:a", "aac", "-b:a", "%dk" % (a // 1000)])
    if extra:
        args += list(extra)
    return args


def decoded_bitrate(path, ffprobe=None):
    """ffprobe 实测 format.bit_rate（bps）；读不到返回 0。"""
    fp = ffprobe or os.environ.get("FXAI_FFPROBE") or "ffprobe"
    r = subprocess.run([fp, "-v", "error", "-show_entries", "format=bit_rate",
                        "-of", "default=nw=1:nk=1", str(path)],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    try:
        return int((r.stdout or "").strip().splitlines()[0])
    except (ValueError, IndexError, AttributeError):
        return 0


def assert_delivery_bitrate(path, ffprobe=None, tol=BITRATE_TOLERANCE):
    """验收门：总比特率落在容差内才放行，不过即停在交付前。"""
    kbps = decoded_bitrate(path, ffprobe) / 1000.0
    lo = DELIVERY_TOTAL_KBPS * (1 - tol)
    hi = DELIVERY_TOTAL_KBPS * (1 + tol)
    if not lo <= kbps <= hi:
        raise RuntimeError(
            "交付码率验收未过：%s 实测 %.0f kbps，要求 %.0f~%.0f kbps（目标 %d）"
            % (path, kbps, lo, hi, DELIVERY_TOTAL_KBPS))
    return kbps


def encode_delivery_2pass(src, dst, vf=None, ffmpeg=None, ffprobe=None,
                          cwd=None, audio_copy=True, audio_bps=None,
                          loglevel="warning", verify=True):
    """最终成片两遍定码率编码（偏差最小），完成后自动跑码率验收。

    vf 需要烧字幕时传 "subtitles=filename=字幕.ass"；cwd 必须是字幕所在目录。
    """
    ff = ffmpeg or os.environ.get("FXAI_FFMPEG") or "ffmpeg"
    if ffprobe is None and os.path.isabs(ff):
        _cand = os.path.join(os.path.dirname(ff),
                             "ffprobe.exe" if os.name == "nt" else "ffprobe")
        if os.path.isfile(_cand):
            ffprobe = _cand
    a = DELIVERY_AUDIO_BPS if audio_copy else (audio_bps or 192000)
    v = delivery_video_bps(a)
    passlog = os.path.join(tempfile.gettempdir(),
                           "fxai_delivery_2pass_%d" % os.getpid())
    base = [ff, "-y", "-hide_banner", "-loglevel", loglevel, "-i", str(src)]
    if vf:
        base += ["-vf", vf]
    base += ["-c:v", "libx264", "-preset", "medium", "-b:v", str(v),
             "-passlogfile", passlog]

    p1 = subprocess.run(base + ["-pass", "1", "-an", "-f", "null", "-"],
                        cwd=cwd, capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    if p1.returncode != 0:
        raise RuntimeError("交付编码 pass1 失败：\n" + (p1.stderr or "")[-1200:])
    tail = ["-pass", "2", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    tail += (["-c:a", "copy"] if audio_copy
             else ["-c:a", "aac", "-b:a", "%dk" % (a // 1000)])
    p2 = subprocess.run(base + tail + [str(dst)], cwd=cwd,
                        capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    if p2.returncode != 0:
        raise RuntimeError("交付编码 pass2 失败：\n" + (p2.stderr or "")[-1200:])
    if verify:
        assert_delivery_bitrate(dst, ffprobe)
    return dst
