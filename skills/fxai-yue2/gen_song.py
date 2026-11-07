# -*- coding: utf-8 -*-
"""fxai-yue2 —— 只用「风格(Style) + 歌词(Lyrics)」产出一首可复现的歌。\n\n后端是 Yue2（ComfyUI 自定义节点 YuE2GenerateABC / YuE2GenerateMusic），\n经公共客户端 fxai-workflow/comfy_client.py 提交，不依赖 mv-pipeline、不绑定任何项目。\n\n★ 本技能与 fxai-workflow 的分工：\n  fxai-workflow 只提供「提交工作流」的公共能力（地址/认证/下载/占位符替换），\n  不含任何音乐语义；音乐语义（Style/Lyrics/Mode/MaxDuration）全部由本模块定义。\n\n用法：\n  # 歌词内联（\n 分隔、带 [Verse] 之类的段落标签）\n  python gen_song.py --style "中文，温暖女声，民谣流行，88 BPM，木质吉他、钢琴"                      --lyrics "[Verse]\n一台电脑点亮凌晨的灯\n[Chorus]\n一个人 就是一支队伍"\n\n  # 长歌词走文件（推荐）\n  python gen_song.py --style-file s.md --lyrics-file l.md\n\n  # 指定歌名/输出目录/随机种子（同 seed + 同 style + 同 lyrics 可复现）\n  python gen_song.py --style-file s.md --lyrics-file l.md --tag <歌名> --out-dir 我的歌 --seed 20261001\n\n  # 产物落哪儿由调用方给：MV/公众号把自己的项目目录传给 --out-dir，这里只负责下载到位\n  python gen_song.py --style-file s.md --lyrics-file l.md --out-dir 我的歌\n\n  # 换用自定义工作流（宿主里含额外占位符时透传）\n  python gen_song.py --style-file s.md --lyrics-file l.md --wf music-yue2-api.json                      --extra-params \'{"fxai_maxduration":120}\'\n\n  # ★ 取产物契约：默认 --via download，全部经 HTTP 接口（/fxai/down/file）下载，\n  #   不读 ComfyUI 本地目录——ComfyUI 随时可能搬到另一台机器，本机路径会失效。\n  #   copy 只是本地快路径（需显式 --copy-from），不猜目录、不静默回退。\n  python gen_song.py --style-file s.md --lyrics-file l.md --via download\n\n输出：stdout 打一行 JSON（ok / wav / remote / via / seed / duration / style / lyrics 摘要），\n      同时打中文结果描述（风格·人声·结构·时长·是否截断）。\n"""
import argparse
import json
import os
import shutil
import sys
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.join(HERE, "fxai-workflow")
if not os.path.isdir(WF_DIR):   # 兜底：公共客户端与技能同级（兄弟目录）
    WF_DIR = os.path.join(os.path.dirname(HERE), "fxai-workflow")
if not os.path.isdir(WF_DIR):   # 再兜底：用户级技能目录
    WF_DIR = os.path.join(os.path.expanduser("~"), ".workbuddy", "skills", "fxai-workflow")
if WF_DIR not in sys.path:
    sys.path.insert(0, WF_DIR)

import comfy_client as cc   # noqa: E402


def read_text(arg, flag):
    """--xxx 给的是文件路径就读文件，否则当原文用。"""
    if not arg:
        return ""
    if os.path.isfile(arg):
        with open(arg, encoding="utf-8") as f:
            return f.read()
    return arg


def copy_from_disk(src, dst):
    """从已存在的成品复制（不覆盖、不删源、校验字节）。

    铁律：ComfyUI 侧的源文件保持原样（可重复取），目标已存在就换名。
    """
    if not os.path.isfile(src):
        return None
    dst = os.path.join(os.path.dirname(dst), os.path.basename(src))
    if os.path.isfile(dst):
        base, ext = os.path.splitext(dst)
        i = 1
        while os.path.isfile("%s_%d%s" % (base, i, ext)):
            i += 1
        dst = "%s_%d%s" % (base, i, ext)
    shutil.copy2(src, dst)   # 保留时间戳，源文件不动
    if os.path.getsize(src) != os.path.getsize(dst):
        return None
    return dst


def find_latest_wav(roots, since_ts):
    """在候选根目录里挑「本次任务之后生成」的最新 wav。"""
    best = None
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dp, _dirs, fns in os.walk(root):
            for fn in fns:
                if not fn.lower().endswith(".wav"):
                    continue
                p = os.path.join(dp, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_mtime >= since_ts - 10 and (best is None or st.st_mtime > best[1]):
                    best = (p, st.st_mtime)
    return best[0] if best else None


def check_wav(path):
    """取到产物后做一次完整性校验（wav：RIFF/WAVE 头 + 非空 + 体积下限）。

    走接口下载时同样要卡——远端 ComfyUI 下载中途断了当场报错重跑，
    0 字节 wav 直接判失败，不往下做转码、剪片、上传素材。"""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError as e:
        raise SystemExit("✗ 产物读不到: %s (%s)" % (path, e))
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise SystemExit("✗ 产物不是有效 wav（文件头 %r）：%s —— 下载不完整，重跑一次" % (head[:8], path))
    size = os.path.getsize(path)
    if size < 1024:
        raise SystemExit("✗ 产物过小（%d 字节）：%s —— 下载不完整，重跑一次" % (size, path))
    return True


def wav_seconds(path):
    try:
        with wave.open(path, "rb") as w:
            return round(w.getnframes() / float(w.getframerate()), 2)
    except Exception:
        return 0.0


def parse_sections(lyrics):
    """把歌词里的段落标签抽出来，用于生成中文结果描述（不依赖外部模型解析）。"""
    tags, order = [], []
    for line in (lyrics or "").splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if s not in tags:
                tags.append(s)
                order.append(s)
    return order


def build_desc(style, lyrics, seconds, mode, truncated):
    secs = parse_sections(lyrics)
    parts = []
    if style:
        parts.append("风格描述：%s" % style.strip().replace("\n", " "))
    if secs:
        parts.append("歌曲结构：%s（歌词按段落标签分段）" % " → ".join(secs))
    if seconds:
        parts.append("实际时长约 %.1f 秒" % seconds)
    if truncated:
        parts.append("⚠️ 达到 max_duration 上限被截断")
    return "；".join(parts) + "。"


def main():
    ap = argparse.ArgumentParser(add_help=True, description="fxai-yue2：风格+歌词 → 一首歌（Yue2/ComfyUI）")
    ap.add_argument("--style", help="音乐风格（语言/流派/人声/速度/乐器），可用 \\n 或多行")
    ap.add_argument("--lyrics", help="歌词（含 [Verse]/[Chorus] 段落标签）")
    ap.add_argument("--style-file", help="风格从文件读（UTF-8）")
    ap.add_argument("--lyrics-file", help="歌词从文件读（UTF-8）")
    ap.add_argument("--mode", default="full", choices=["full", "melody", "off"],
                    help="ABC 规划模式：full=完整（旋律+和弦，默认）/ melody=只规划旋律 / off=不规划")
    ap.add_argument("--max-duration", type=int, default=0,
                    help="最大时长（秒）。内置工作流把 max_duration 锁在 360s，"
                         "要改时长请用 --wf 指定自带占位符的自定义工作流并配合 --extra-params")
    ap.add_argument("--seed", type=int, default=0, help="随机种子；0 / 负数 = 随机（同 seed 可复现）")
    ap.add_argument("--tag", default="", help="结果文件名前缀，默认用时间戳")
    ap.add_argument("--out-dir", default="", help="本地保存目录，默认 fxai-workflow/output；由调用方给，本技能不归档")
    ap.add_argument("--via", choices=["download", "auto", "copy"], default="download",
                    help="download(默认)=走 HTTP 接口下载（ComfyUI 在任意机器上都适用，技能默认走这条）；"
                         "auto=先接口下载，失败才回退本地成品；"
                         "copy=只按 --copy-from 找本地成品复制，找不到直接报错（不猜目录、不静默回退下载）")
    ap.add_argument("--copy-from", default="", help='仅 copy/auto 回退时用：本地成品候选根目录，多个用逗号分隔；'
                                                    '也可走环境变量 YUE2_COPY_FROM。★ 填挂载盘上的成品目录，ComfyUI 产出目录由服务端管理')
    ap.add_argument("--workflow", default="", help="工作流文件名（在 fxai-workflow 内），默认 music-yue2.json")
    ap.add_argument("--extra-params", default="", help='额外占位符 JSON，如 \'{"fxai_maxduration":120}\'')
    ap.add_argument("--timeout", type=int, default=3600, help="等待 ComfyUI 出结果的最长时间（秒）")
    ap.add_argument("--no-desc", action="store_true", help="只输出 JSON，不打中文描述")
    args = ap.parse_args()

    style = read_text(args.style or args.style_file, "--style")
    lyrics = read_text(args.lyrics or args.lyrics_file, "--lyrics")
    if not style.strip() or not lyrics.strip():
        ap.error("Style 与 Lyrics 都必填（--style/--style-file 与 --lyrics/--lyrics-file）")

    extra = {}
    if args.extra_params:
        extra = json.loads(args.extra_params)

    seed = args.seed if args.seed and args.seed > 0 else cc._seed_rand()
    tag = args.tag or ("song_%s" % time.strftime("%Y%m%d_%H%M%S"))

    # 落盘位置由调用方给（本技能是中间件，不自己定项目目录）
    out_dir = args.out_dir or cc.OUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    wf_path = os.path.join(WF_DIR, args.workflow or "music-yue2.json")
    if not os.path.isfile(wf_path):
        ap.error("找不到工作流 %s（可用 --workflow 指定）" % wf_path)

    cc.require_comfyui()

    params = {
        "fxai_prompt": style,    # node53 → 链接进 node42/45 的 style
        "fxai_lyrics": lyrics,   # node54 → 链接进 node42/45 的 lyrics
        "fxai_seed": seed,
    }
    params.update(extra)

    t_start = time.time()
    print("[yue2] 提交任务 %s（seed=%s, mode=%s）" % (tag, seed, args.mode), flush=True)
    url = cc.submit_workflow(wf_path, params, tag="yue2:" + tag, kind="music", timeout=args.timeout)

    # ★ 取产物：默认（也是唯一通用路径）＝ 走 HTTP 接口下载（/fxai/down/file）。
    # 本地复制只作快路径，且必须显式给 --copy-from；ComfyUI 产出目录由脚本按 --copy-from 决定，不自动去猜
    # —— ComfyUI 随时可能搬到另一台机器，本机目录在远端场景下一定不存在。
    dst = None
    via_used = args.via
    roots = [r.strip() for r in (args.copy_from or os.environ.get("YUE2_COPY_FROM", "") or "").split(",")
             if r and r.strip()]
    if args.via in ("copy", "auto") and roots:
        src = find_latest_wav(roots, t_start)
        if src:
            dst = copy_from_disk(src, os.path.join(out_dir, tag + ".wav"))
            if dst:
                print("[yue2] 本地快路径命中: %s -> %s" % (src, dst), flush=True)
        elif args.via == "copy":
            raise SystemExit("✗ via=copy 但在候选目录里没找到本次新产出的 wav：%s\n"
                             "  · 想走接口下载就用 --via download（默认）；"
                             "  · 确实用本地成品请确认 --copy-from 路径对不对。" % " | ".join(roots))
    elif args.via in ("copy", "auto"):
        print("[yue2] via=%s 但未指定 --copy-from，跳过本地查找" % args.via, flush=True)

    if not dst:
        dst = os.path.join(out_dir, tag + ".wav")
        cc._download(url, dst)
        via_used = "download"
    check_wav(dst)
    seconds = wav_seconds(dst)
    print("[yue2] DONE -> %s（%.1fs）" % (dst, seconds), flush=True)

    result = {
        "ok": True,
        "tag": tag,
        "wav": dst,
        "remote": cc.url_to_rel(url),
        "via": via_used,
        "seed": seed,
        "mode": args.mode,
        "max_duration": args.max_duration,
        "duration": seconds,
        "style": style.strip(),
        "lyrics_chars": len(lyrics),
        "sections": parse_sections(lyrics),
    }
    print("RESULT " + json.dumps(result, ensure_ascii=False))
    if not args.no_desc:
        print("[描述] " + build_desc(style, lyrics, seconds, args.mode,
                                    bool(args.max_duration and seconds >= args.max_duration - 1)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
