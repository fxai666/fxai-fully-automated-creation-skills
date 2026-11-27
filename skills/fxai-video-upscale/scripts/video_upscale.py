# -*- coding: utf-8 -*-
# =====================================================================
# 视频超分 · 本机 ComfyUI 工作流入口
# ---------------------------------------------------------------------
# 链路：源视频 → 分段 → FxAiVideoUpscale（自研 FlashVSR，任意倍数）→ 合并 → 保存成片
# 工作流：fxai-workflow/upscale-relayvsr.json（RelayVSR x2 原生节点，固定 2 倍，无精修）
# 提交 / 等终态 / 取产物全部走公共层 comfy_client.submit_workflow。
# =====================================================================

import argparse
import json
import math
import os
import subprocess
import sys
import time

sys.dont_write_bytecode = True

# 公共层：本脚本在 <技能>/scripts/ 下，往上两级是技能根
_WF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "fxai-workflow")
if _WF not in sys.path:
    sys.path.insert(0, _WF)
import comfy_client as C

SUBDIR_DEFAULT = "upscale"
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm")
SEG_MAX = 10.0        # 单段上限（秒）
SEG_MIN = 0.6         # 尾段合并阈值下限（秒），只用于预估段数
SCALE_OUT = 2.0       # RelayVSR x2 权重决定，固定 2 倍，不可调
# 16G 卡显存预算：单段「帧数 × 输出百万像素」≤ 240。锚点是 11-12 实测——3648×2112（7.7 Mpx）
# 每段 30 帧刚好跑完不 OOM（VSRDecode 一次解整段，显存随帧数线性涨）。小片自动放大单段帧数。
VRAM_FRAMES_MPX = 240
FRAMES_MIN = 8
FRAMES_MAX = 400
WH_FALLBACK = (960, 544)


def plan_segments(dur, per=SEG_MAX, min_tail=SEG_MIN):
    "预估分段：每段 per 秒；最后一段不足 min_tail 秒就并进倒数第二段。只用于打印段数，真实切分由 108 节点做。"
    if dur <= 0:
        return []
    n = max(1, int(math.ceil(dur / float(per) - 1e-9)))
    bounds = [min(per * i, dur) for i in range(n)] + [dur]
    if len(bounds) > 2 and (dur - bounds[-2]) < min_tail:
        bounds.pop(-2)
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def _probe(path, entries, ffprobe="ffprobe"):
    "读视频流字段，读不到返回空字典。"
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=" + entries, "-of", "json", path],
            stderr=subprocess.DEVNULL, timeout=60)
        return (json.loads(out.decode("utf-8", "ignore")).get("streams") or [{}])[0]
    except Exception:
        return {}


def probe_size(path):
    st = _probe(path, "width,height")
    w, h = int(st.get("width") or 0), int(st.get("height") or 0)
    return (w, h) if w > 0 and h > 0 else ()


def probe_fps(path):
    st = _probe(path, "r_frame_rate")
    try:
        num, _, den = str(st.get("r_frame_rate") or "").partition("/")
        v = float(num) / float(den or 1)
        return v if 0 < v < 1000 else 0.0
    except Exception:
        return 0.0


def probe_dur(path):
    "读视频时长（秒），流字段读不到再看容器。"
    st = _probe(path, "duration")
    try:
        v = float(st.get("duration") or 0)
        if v > 0:
            return v
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
            stderr=subprocess.DEVNULL, timeout=60)
        return float(json.loads(out.decode("utf-8", "ignore"))["format"]["duration"])
    except Exception:
        return 0.0


def mp4_ok(path):
    "MP4 完整性：文件头含 ftyp 且体积不是几百字节的空壳。"
    try:
        if os.path.getsize(path) < 10240:
            return False
        with open(path, "rb") as f:
            return b"ftyp" in f.read(16)
    except OSError:
        return False


def segment_seconds(fps, out_mpx):
    "分段时长：单段帧数按「帧数 × 输出百万像素 ≤ VRAM_FRAMES_MPX」算，再封顶 SEG_MAX 秒。"
    if fps <= 0:
        return SEG_MAX
    frames = max(FRAMES_MIN, min(FRAMES_MAX, int(VRAM_FRAMES_MPX / max(out_mpx, 0.01))))
    return min(SEG_MAX, frames / float(fps))


def estimate(src_dur, fps, out_mpx):
    "整片耗时估算（秒）：RelayVSR 单步采样，每帧约 0.05×输出百万像素 秒（下限 0.1）。"
    if src_dur <= 0 or fps <= 0:
        return 0.0
    return src_dur * fps * max(0.1, 0.05 * out_mpx)


def upscale(video, out_dir="", seed=42,
            subdir="", timeout=14400, name="", url="", dry_run=False,
            width=0, height=0, auto_shutdown=False):
    """提交视频超分任务并取回成片。

    参数：
      video    源视频路径（服务端可读；本机部署即本机绝对路径）
      out_dir  本地落点目录，缺省当前工作目录
      seed     随机种子，缺省 42
      subdir   服务端保存子目录
      timeout  等终态上限秒数
      name     本地文件名，缺省 <源文件名>_up2x.mp4
      url      指定 ComfyUI 地址（缺省走本机优先 → 配置 → 127.0.0.1:8188）
      dry_run  只打印参数不提交
      width/height  生成控制器宽高，0 即按源视频取
      auto_shutdown  跑完是否关机（默认 False）
    返回：本地成片绝对路径（合并后的整片）
    """
    out_dir = out_dir or os.getcwd()
    subdir = subdir or SUBDIR_DEFAULT
    scale = SCALE_OUT

    if not os.path.isabs(video) and os.path.exists(video):
        video = os.path.abspath(video)

    pw, ph = (width, height) if width and height else probe_size(video)
    if not (pw and ph):
        pw, ph = WH_FALLBACK
        print("[upscale] ⚠️ ffprobe 读不到源尺寸，退回 %dx%d（可 --width/--height 指定）"
              % (pw, ph), flush=True)
    fps = probe_fps(video) or 24.0

    out_w, out_h = int(round(pw * scale)), int(round(ph * scale))
    out_mpx = out_w * out_h / 1e6
    seg = segment_seconds(fps, out_mpx)
    print("[upscale] 输出尺寸: %dx%d（%.2f Mpx，源 %dx%d ×%g 固定）"
          % (out_w, out_h, out_mpx, pw, ph, scale), flush=True)
    print("[upscale] 分段口径: 每段 %.2f 秒（%g fps → %d 帧，按 %.1f Mpx 显存预算封顶）"
          % (seg, fps, int(round(seg * fps)), out_mpx), flush=True)

    src_dur = probe_dur(video)
    if src_dur > 0:
        plan = plan_segments(src_dur, seg)
        eta = estimate(src_dur, fps, out_mpx)
        print("[upscale] 片长 %.2f 秒 → %d 段，整片约 %.0f 秒（%.1f 分钟）"
              % (src_dur, len(plan), eta, eta / 60), flush=True)

    wf_path = C.resolve_wf("upscale", "relay")
    params = {
        "fxai_videoinput": video,
        "fxai_outputdir": subdir,
        "fxai_seed": int(seed),
        "fxai_width": int(pw),
        "fxai_height": int(ph),
        "fxai_duration": seg,
        "fxai_average_segment": True,
        "fxai_skip_initial": True,
        "fxai_include_tail": False,
        "fxai_segment_data": "[]",
        "fxai_start_index": 0,
        "fxai_end_index": -1,
        "fxai_auto_shutdown": bool(auto_shutdown),
    }

    if dry_run:
        print("[dry-run] 工作流: %s" % wf_path)
        print("[dry-run] 注入: %s" % json.dumps(params, ensure_ascii=False, indent=2))
        print("[dry-run] 落点: %s" % out_dir)
        return ""

    if not url:
        C.prefer_local()
    C.require_comfyui(url or None)
    base = C.comfy_url(url or "")

    print("[upscale] 源视频: %s" % video, flush=True)
    print("[upscale] 链路: 分段 → RelayVSR ×%g → 合并" % scale, flush=True)
    print("[upscale] 提交中（分段逐段跑，耗时随片长线性增长）…", flush=True)
    t0 = time.time()
    token = C.submit_workflow(wf_path, params, tag="upscale", kind="upscale", timeout=timeout)
    cost = time.time() - t0
    print("[upscale] 服务端产物令牌: %s（执行 %.0f 秒）" % (token, cost), flush=True)

    if not name:
        stem = os.path.splitext(os.path.basename(video))[0]
        name = "%s_up%sx.mp4" % (stem, ("%g" % scale))
    fname = name if name.lower().endswith(VIDEO_EXT) else name + ".mp4"
    dst = os.path.join(out_dir, fname)
    C._download(token, dst, url=base)

    if not mp4_ok(dst):
        raise RuntimeError("下载完成但 MP4 不完整（无 ftyp 或体积过小），疑似半片：%s" % dst)

    out_dur = probe_dur(dst)
    if src_dur > 0 and out_dur > 0 and out_dur < src_dur * 0.95:
        raise RuntimeError(
            "成片时长明显短于源片：源 %.2f 秒 / 成片 %.2f 秒，只拿到了部分段。"
            "检查工作流 111 号节点的「结束索引」是否为 -1：%s" % (src_dur, out_dur, dst))

    print("[upscale] 完成：%s（%.1f MB）" % (dst, os.path.getsize(dst) / 1048576.0), flush=True)
    print("[upscale] 耗时：%.0f 秒（%.2f 秒/帧，按 %.0f 帧计）"
          % (cost, cost / max(1.0, out_dur * fps), out_dur * fps), flush=True)
    return dst


def main():
    ap = argparse.ArgumentParser(
        description="视频超分（本机 ComfyUI 分段工作流）：RelayVSR x2 原生节点，固定 2 倍放大")
    ap.add_argument("video", help="源视频路径（服务端可读）")
    ap.add_argument("--seed", type=int, default=42, help="随机种子（默认 42）")
    ap.add_argument("--width", type=int, default=0, help="生成控制器宽（默认按源视频取）")
    ap.add_argument("--height", type=int, default=0, help="生成控制器高（默认按源视频取）")
    ap.add_argument("--auto-shutdown", action="store_true", help="跑完自动关机（默认关）")
    ap.add_argument("--subdir", default="", help="服务端保存子目录（默认 %s）" % SUBDIR_DEFAULT)
    ap.add_argument("--out-dir", default="", help="本地落点目录（默认当前目录）")
    ap.add_argument("--name", default="", help="本地文件名（默认 <源名>_up2x.mp4）")
    ap.add_argument("--timeout", type=int, default=14400, help="等终态上限秒数（默认 14400）")
    ap.add_argument("--url", default="", help="指定 ComfyUI 地址（默认本机优先）")
    ap.add_argument("--dry-run", action="store_true", help="只打印要提交的参数，不真跑")
    a = ap.parse_args()

    try:
        p = upscale(a.video, out_dir=a.out_dir,
                    seed=a.seed, subdir=a.subdir, timeout=a.timeout, name=a.name,
                    url=a.url, dry_run=a.dry_run, width=a.width, height=a.height,
                    auto_shutdown=a.auto_shutdown)
    except Exception as e:
        print("失败：%s" % e, flush=True)
        raise SystemExit(1)
    if p:
        print(p)


if __name__ == "__main__":
    main()
