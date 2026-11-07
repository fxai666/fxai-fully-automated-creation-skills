#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封面拼接（标题卡已抽成独立技能 fxai-title-card）

──── 职责（改版后）────
· 本脚本**只做封面**：写实 image/cover.png → 前拼静态封面帧 → concat(v=1:a=1)。
· 标题卡叠加统一在独立技能 `fxai-title-card`（能给任何视频叠字），本脚本只做封面。
· `overlay_titles()` 作旧调用兼容：内部转调 fxai-title-card 叠卡，再 stitch_cover。

──── 封面段参数 ────
🔴 封面段占几帧、音轨垫什么，**统一由
`fxai-poster/references/05-封面模板库.md`「封面段时长」规定**。
本脚本只把帧数换算成本产线的秒数（24fps），不另立一套标准、不写第二份数值。
· 音轨必须垫次声正弦，不得留空：全零采样会被视频号当「开头静音」做人声对齐，
  视频没裁、音频裁了 → 口型整体错位。垫法与电平映射见上面那册。
· 封面段与成片编码参数必须一致，否则 concat demuxer 拒收：
  768×1280 / 24fps / yuv420p / h264；音频 aac 24000Hz stereo。
"""
import argparse, json, os, re, subprocess, sys

# 封面段帧数：定案在 fxai-poster/references/05-封面模板库.md「封面段时长」，此处只换算。
COVER_FRAMES = 10
FPS = 24
COVER_SEC = COVER_FRAMES / float(FPS)
# 垫底音：次声正弦（不是粉噪——粉噪听得出「呼呼」，已否）。输入电平，编码后约 -73dB。
# 24000Hz 采样率下 AAC 对 25Hz 的低频损失比 05 册 48000Hz 实测更大（-56dB → 实测 -80.3dB），
# 已按映射表上调到 -50dB（编码后约 -68dB，落入合格区间 -80~-66）。
COVER_BED_HZ = 25
COVER_BED_DB = -50.0
# 拼完后实测量一遍：mean 落在这个区间才算合格。
# 上界 -66dB 是「听得见」的分界，下界 -80dB 是「近似数字静音」的分界。
COVER_BED_OK = (-80.0, -66.0)


def ffmpeg_bin():
    cand = []
    try:
        cfg = json.load(open(os.environ.get("FXAI_GLOBAL_CONFIG")
                             or os.path.expanduser("~/.workbuddy/fxai_global.json"),
                             encoding="utf-8"))
        sd = cfg.get("software_install_dir")
        if sd:
            cand.append(os.path.join(sd, "ffmpeg", "ffmpeg.exe"))
    except Exception:
        pass
    for c in cand:
        if os.path.isfile(c):
            return c
    return "ffmpeg"


def run(cmd, cwd=None):
    print("$", " ".join(str(c) for c in cmd[:7]), "...", flush=True)
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        print(p.stdout[-1500:]); print(p.stderr[-2500:])
        raise SystemExit("命令失败: %s" % cmd[0])
    return p


def title_card_scripts():
    """定位独立技能 fxai-title-card 的 scripts 目录。

    here = .../skills/<本技能>/scripts  →  上两级 = .../skills  →  同级技能目录
    """
    here = os.path.dirname(os.path.abspath(__file__))
    skills_root = os.path.dirname(os.path.dirname(here))
    cand = os.path.join(skills_root, "fxai-title-card", "scripts")
    if not os.path.isdir(cand):
        sys.exit("找不到 fxai-title-card 技能（标题卡实现已迁到该技能）：%s" % cand)
    return cand


def infocard_scripts():
    """定位独立技能 fxai-infocard 的 scripts 目录（信息贴片：半透明形状补充层）。

    信息贴片与标题卡是两个不同的技能：
      · 标题卡    fxai-title-card  顶部纯文字，跟节拍
      · 信息贴片  fxai-infocard    半透明形状（气泡/胶囊/方块），任意位置动态进出
    """
    here = os.path.dirname(os.path.abspath(__file__))
    skills_root = os.path.dirname(os.path.dirname(here))
    cand = os.path.join(skills_root, "fxai-infocard", "scripts")
    if not os.path.isdir(cand):
        sys.exit("找不到 fxai-infocard 技能（信息贴片实现在该技能）：%s" % cand)
    return cand


def check_cover_noise(path, sec=COVER_SEC):
    """量成片前 sec 秒的音量，确认封面音频「非静音但听不见」。

    返回 (mean_db, 是否合格)。区间外的两种可能都要报出来：
      · 太响（> -66dB）：安静环境下能听到，调低 COVER_BED_DB；
      · 太轻（< -80dB）：贴近数字静音 -91dB，视频号可能重新做人声起点对齐，口型会错位。
    """
    p = subprocess.run([ffmpeg_bin(), "-v", "info", "-t", "%.2f" % sec, "-i", path,
                        "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", p.stdout + p.stderr)
    if not m:
        print("[cover] ⚠️ 量不到封面音量，跳过自检", flush=True)
        return None, True
    mean = float(m.group(1))
    lo, hi = COVER_BED_OK
    ok = lo <= mean <= hi
    tip = "" if ok else (
        "偏响，安静环境能听见，调低 COVER_BED_DB" if mean > hi
        else "偏轻，接近数字静音，视频号可能重新对齐音画，调高 COVER_BED_DB")
    print("[cover] %s 封面音频 mean %.1f dB（合格区间 %.0f~%.0f）%s"
          % ("✅" if ok else "⚠️", mean, lo, hi, tip), flush=True)
    return mean, ok


def stitch_cover(project, with_titles, cover, out):
    """封面（垫次声正弦）+ concat(v=1:a=1) → 最终成片。只拼片头，不碰标题卡。

    🔴 封面段音频**不得留空**：anullsrc 出的是全零样本（-91dB 数字静音），视频号转码时
    会把它当「开头静音」做人声起点对齐——视频没裁、音频裁了，口型整体错位。
    垫什么、垫多少，规则见 `fxai-poster/references/05-封面模板库.md`「音轨」节。
    这里只做换算与实测，不合格由 check_cover_noise() 报出来，不必靠耳朵调。
    """
    FF = ffmpeg_bin()
    os.makedirs(os.path.join(project, "_tmp"), exist_ok=True)
    cover_clip = os.path.join(project, "_tmp", "cover_clip.mp4")
    run([FF, "-y", "-loop", "1", "-i", cover,
         "-f", "lavfi", "-t", "%.4f" % COVER_SEC,
         "-i", "sine=frequency=%d:sample_rate=24000" % COVER_BED_HZ,
         "-t", "%.4f" % COVER_SEC,
         "-af", "volume=%.1fdB,afade=t=out:st=%.3f:d=0.06" % (
             COVER_BED_DB, max(0.0, COVER_SEC - 0.06)),
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-r", "24", "-c:a", "aac", "-ar", "24000", "-ac", "2", "-shortest", cover_clip],
        cwd=project)
    run([FF, "-y", "-i", cover_clip, "-i", with_titles,
         "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
         "-map", "[v]", "-map", "[a]",
         "-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "20", "-b:v", "0",
         "-pix_fmt", "yuv420p", "-r", "24", "-c:a", "aac", "-ar", "24000", "-ac", "2", out],
        cwd=project)
    print("封面拼接完成 ->", out)
    check_cover_noise(out)
    return out


def overlay_titles(project, burned, titles_dir, cover, beat_times, out):
    """【兼容旧调用】叠标题卡（转调 fxai-title-card）+ 拼封面。

    新项目请直接用 `finalize.py`：它调 fxai-title-card 叠卡、再调本文件的 stitch_cover。
    """
    print("[deprecated] 叠卡已迁到 fxai-title-card，此处仅作兼容转调")
    sys.path.insert(0, title_card_scripts())
    import overlay_cards as OC  # noqa: E402

    beats = json.load(open(beat_times, encoding="utf-8"))["beats"]
    cards_path = os.path.join(titles_dir, "cards.json")
    if os.path.isfile(cards_path):
        cards = json.load(open(cards_path, encoding="utf-8"))
        for b, c in zip(beats, cards):
            b.setdefault("title", c.get("title", ""))
            b.setdefault("sub", c.get("sub", ""))
            b.setdefault("foot", c.get("foot", ""))
    for i, b in enumerate(beats, 1):
        b.setdefault("i", i)

    wt = os.path.join(project, "_tmp", "with_titles.mp4")
    OC.overlay(burned, titles_dir, beats, wt)
    return stitch_cover(project, wt, cover, out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    ap.add_argument("--burned", default=None, help="已烧字幕的中间成片")
    ap.add_argument("--titles-dir", default=None)
    ap.add_argument("--cover", default=None)
    ap.add_argument("--beat-times", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    proj = a.project
    burned = a.burned or os.path.join(proj, "成片_768x1280.mp4")
    titles_dir = a.titles_dir or os.path.join(proj, "image")
    cover = a.cover or os.path.join(proj, "image", "cover.png")
    beat_times = a.beat_times or os.path.join(proj, "_beat_times.json")
    out = a.out or os.path.join(proj, "成片_最终_768x1280.mp4")
    overlay_titles(proj, burned, titles_dir, cover, beat_times, out)


if __name__ == "__main__":
    main()
