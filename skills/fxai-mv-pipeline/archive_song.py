# -*- coding: utf-8 -*-
"""按 SPEC 把 ComfyUI 产出物归档进某首歌的项目文件夹（语义化新结构，2026-09-28 起唯一标准）。\n\n新结构（每首歌 {WORKSPACE}/{歌名}/）：\n  歌曲.md        曲风 + 歌词\n  音乐.wav       音乐成品（根目录）\n  成片.mp4       最终 MV（带字幕，根目录）\n  字幕.ass/.srt  歌词字幕（根目录）\n  图片/          唯一子文件夹：人物1.png / 场景1.png …\n  meta.json / 运镜提示词.md  辅助文件（根目录）\n\n这是「从 ComfyUI 下载/复制」的额外备份层，不改动 ComfyUI 任何参数；本机从\nfxai-mv-pipeline/output 复制，远端改用 ComfyUI 下载接口，逻辑一致。\n\n用法：\n  python archive_song.py --song <歌名>          # 立即按新结构归档（产物须已在 fxai-mv-pipeline/output）\n  python archive_song.py --song <歌名> --wait   # 轮询直到最终 MV 就绪再归档\n"""
import sys, os, time, shutil, argparse

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
from produce_song import SPEC, WORKSPACE, cn

OUT_BASE = os.getcwd()  # 跟随调用者工作目录，不写死技能目录/output
LOG_PATH = os.path.join(WORKSPACE, "archive_%s.log")


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def archive(src, dst_dir, name):
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, name)
    if src and os.path.exists(src):
        shutil.copy2(src, dst)
        log("[archive] %s -> %s" % (os.path.basename(src), dst))
        return dst
    log("[archive] 跳过（源不存在）: %s" % src)
    return None


def write_md(song, SONG_DIR):
    spec = SPEC[song]
    md = "# %s\n\n## 曲风\n%s\n\n## 歌词\n%s\n" % (
        song, spec["style"].strip(), spec["lyrics"].strip())
    with open(os.path.join(SONG_DIR, "歌曲.md"), "w", encoding="utf-8") as f:
        f.write(md)
    log("[archive] 已写 %s/歌曲.md（曲风+歌词）" % song)


def backfill(song):
    """按新结构归档：从 fxai-mv-pipeline/output 拉原始产物。已存在则覆盖（幂等）。"""
    spec = SPEC[song]
    T = spec["tags"]
    SONG_DIR = os.path.join(WORKSPACE, song)
    IMG_DIR = os.path.join(SONG_DIR, "图片")
    os.makedirs(IMG_DIR, exist_ok=True)

    archive(os.path.join(OUT_BASE, T["music"] + ".wav"), SONG_DIR, "音乐.wav")
    # 多人物支持：T["char"] 可为列表（人物1.png/人物2.png…），向后兼容单字符串（人物1.png）
    _chars = T["char"] if isinstance(T["char"], (list, tuple)) else [T["char"]]
    for _ci, _cname in enumerate(_chars, 1):
        archive(os.path.join(OUT_BASE, _cname + ".png"), IMG_DIR, "人物%d.png" % _ci)
    for i, sc in enumerate(spec["scenes"], 1):
        archive(os.path.join(OUT_BASE, (T["scene"] % i) + ".png"),
                IMG_DIR, "场景%s.png" % cn(i))
    # 最终 MV：根目录已有 成片.mp4 则跳过（produce_song.py 已烧录字幕），否则用原始 mv
    if not os.path.exists(os.path.join(SONG_DIR, "成片.mp4")):
        archive(os.path.join(OUT_BASE, T["mv"] + ".mp4"), SONG_DIR, "成片.mp4")
    write_md(song, SONG_DIR)
    log("新结构归档完成：%s" % SONG_DIR)


def wait_until_ready(song, interval=60, timeout=10800):
    spec = SPEC[song]
    T = spec["tags"]
    SONG_DIR = os.path.join(WORKSPACE, song)
    sentinel = os.path.join(SONG_DIR, "成片.mp4") # 新结构完成标志
    fallback = os.path.join(OUT_BASE, T["mv"] + ".mp4") # 至少原始 mv 已生成
    music_src = os.path.join(OUT_BASE, T["music"] + ".wav")
    start = time.time()
    while True:
        if (os.path.exists(sentinel) or os.path.exists(fallback)) and os.path.exists(music_src):
            log("[wait] 产物就绪，开始归档 %s" % song)
            return
        if time.time() - start > timeout:
            log("[wait] 超时：%s" % song)
            return
        time.sleep(interval)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    # 歌名一律由调用方给（技能不内嵌任何具体歌曲）
    ap.add_argument("--song", required=True)
    ap.add_argument("--wait", action="store_true")
    args = ap.parse_args()
    if args.wait:
        wait_until_ready(args.song)
    backfill(args.song)
