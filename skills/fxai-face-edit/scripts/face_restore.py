# -*- coding: utf-8 -*-
# =====================================================================
# 人脸精修 · 轻量链（本机 ComfyUI 工作流入口）
# ---------------------------------------------------------------------
# 链路：源视频 → 分段 → FxAiFaceRestoreLiteV2 逐段精修（全脸）→ 合并 → 保存成片
# 工作流：fxai-workflow/face-restore-lite.json（占位符版，任务 "facerestore" / 模型 "lite"）
#
# 与 face_edit.py 的分工：
#   face_edit.py   → 云端 FaceFusion REST（换脸 / 对嘴 / 单张老照片 / 抠像）
#   face_restore.py→ 本机 ComfyUI 分段工作流（整片视频逐段精修，长片首选，本机不计费）
#
# 提交 / 等终态 / 取产物全部走公共层 comfy_client.submit_workflow：
#   它按 prompt_id 定向监听 `fxai:task_saved` 广播，事件本身带 url（`video/<子目录>|文件名`），
#   直接就是下载令牌；监听起不来才回落 /history 轮询 + fxai 结果接口。
# =====================================================================

import argparse
import json
import math
import os
import subprocess
import sys
import time

# 公共层：本脚本在 <技能>/scripts/ 下，往上两级是技能根
_WF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "fxai-workflow")
if _WF not in sys.path:
    sys.path.insert(0, _WF)
import comfy_client as C

# 默认参考图：自有角色底图（服务端相对路径，由 ComfyUI 自己解析）
REF_DEFAULT = "avatar/000.png,avatar/001.png"
# 服务端保存子目录（成片落在这里，再由下载接口取回本机）
SUBDIR_DEFAULT = "face_restore"
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm")
# 分段时长（秒）：固定 10，与整条视频生成规范统一口径，不开放调节
SEG = 10
# 尾段下限（秒）：切完最后一段不足这个长度，就并进倒数第二段，不留碎尾
MIN_TAIL = 3.0
# 宽高兜底：只在 ffprobe 读不到源尺寸时才用（工作流占位符默认 960x544 会把竖屏压横）
WH_FALLBACK = (960, 544)


def plan_segments(dur, per=SEG, min_tail=MIN_TAIL):
    "分段口径：每段 per 秒；最后一段不足 min_tail 秒则并进倒数第二段。返回 [(起,止), …] 秒。"
    if dur <= 0:
        return []
    n = max(1, int(math.ceil(dur / float(per) - 1e-9)))
    bounds = [min(per * i, dur) for i in range(n)] + [dur]
    if len(bounds) > 2 and (dur - bounds[-2]) < min_tail:
        bounds.pop(-2)
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def probe_size(path, ffprobe="ffprobe"):
    "读源视频宽高。读不到返回 ()，由调用方决定兜底。"
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", path],
            stderr=subprocess.DEVNULL, timeout=30)
        st = (json.loads(out.decode("utf-8", "ignore")).get("streams") or [{}])[0]
        w, h = int(st.get("width") or 0), int(st.get("height") or 0)
        return (w, h) if w > 0 and h > 0 else ()
    except Exception:
        return ()


def probe_dur(path, ffprobe="ffprobe"):
    "读视频时长（秒）。读不到返回 0.0。"
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=duration", "-of", "json", path],
            stderr=subprocess.DEVNULL, timeout=30)
        st = (json.loads(out.decode("utf-8", "ignore")).get("streams") or [{}])[0]
        return float(st.get("duration") or 0)
    except Exception:
        try:
            out = subprocess.check_output(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", path], stderr=subprocess.DEVNULL, timeout=30)
            return float(json.loads(out.decode("utf-8", "ignore"))["format"]["duration"])
        except Exception:
            return 0.0



def probe_frames(path, ffprobe="ffprobe"):
    "读视频帧数。读不到返回 0。"
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-show_entries", "stream=nb_read_frames", "-of", "json", path],
            stderr=subprocess.DEVNULL, timeout=120)
        st = (json.loads(out.decode("utf-8", "ignore")).get("streams") or [{}])[0]
        return int(st.get("nb_read_frames") or 0)
    except Exception:
        return 0


def mp4_ok(path):
    "MP4 完整性：文件头含 ftyp 且体积不是几百字节的空壳。"
    try:
        size = os.path.getsize(path)
        if size < 10240:
            return False
        with open(path, "rb") as f:
            head = f.read(16)
        return b"ftyp" in head
    except OSError:
        return False


def restore(video, out_dir="", ref="", seed=42, subdir="", timeout=7200,
            name="", url="", dry_run=False, width=0, height=0,
            auto_shutdown=False):
    """提交人脸精修任务并取回成片。

    参数：
      video    源视频路径（服务端可读；本机部署即本机绝对路径）
      out_dir  本地落点目录，缺省当前工作目录
      ref      参考图，逗号分隔的服务端相对路径；缺省用自有角色 000/001
      seed     随机种子，<1 即随机；缺省 42（与工作流作者存档一致，逐段稳定不闪）
      subdir   服务端保存子目录
      timeout  等终态上限秒数（分段精修耗时随片长线性增长）
      name     本地文件名，缺省沿用服务端返回的文件名
      url      指定 ComfyUI 地址（缺省走本机优先 → 配置 → 127.0.0.1:8188）
      dry_run  只打印参数不提交
      width/height  输出宽高，0 即按源视频取（读不到才用 960x544 兜底）
      auto_shutdown  跑完是否关机（默认 False：本机跑关掉自己没意义，云端按需开）
    返回：本地成片绝对路径（合并后的整片，不是某一段的中间产物）
    """
    out_dir = out_dir or os.getcwd()
    ref = ref or REF_DEFAULT
    subdir = subdir or SUBDIR_DEFAULT

    # 服务端按自己的根目录解析路径：本机存在的相对路径先转绝对，否则必报「找不到视频文件」
    if not os.path.isabs(video) and os.path.exists(video):
        video = os.path.abspath(video)

    # 输出宽高必须跟源一致：工作流占位符默认 960x544，竖屏素材会被拉横
    pw, ph = (width, height) if width and height else probe_size(video)
    if not (pw and ph):
        pw, ph = WH_FALLBACK
        print("[face-restore] ⚠️ ffprobe 读不到源尺寸，退回 %dx%d（可能拉伸画面，可 --width/--height 指定）"
              % (pw, ph), flush=True)

    # 分段口径（服务端按 10 秒切，尾段不足 MIN_TAIL 秒并入上一段）：先在本地算一遍做对照
    src_dur = probe_dur(video)
    if src_dur > 0:
        plan = plan_segments(src_dur)
        desc = " + ".join("%.2f" % (b - a) for a, b in plan)
        print("[face-restore] 分段口径: %s 秒（%d 段，每段 %.0f 秒；尾段 <%.0f 秒并入上一段）"
              % (desc, len(plan), SEG, MIN_TAIL), flush=True)

    wf_path = C.resolve_wf("facerestore")
    params = {
        "fxai_videoinput": video,
        "fxai_imageinput": ref,
        "fxai_outputdir": subdir,
        "fxai_seed": seed,
        "fxai_width": int(pw),
        "fxai_height": int(ph),
        "fxai_duration": SEG,
        "fxai_average_segment": True,
        "fxai_skip_initial": True,
        "fxai_include_tail": False,
        "fxai_segment_data": "[]",
        "fxai_start_index": 0,
        "fxai_auto_shutdown": bool(auto_shutdown),
    }

    if dry_run:
        print("[dry-run] 工作流: %s" % wf_path)
        print("[dry-run] 注入: %s" % json.dumps(params, ensure_ascii=False, indent=2))
        print("[dry-run] 落点: %s" % out_dir)
        return ""


    # 本机优先：本机 8188 探得通就用本机，不计费；探不通才由 require_comfyui 走云
    if not url:
        C.prefer_local()
    C.require_comfyui(url or None)
    base = C.comfy_url(url or "")

    print("[face-restore] 源视频: %s" % video, flush=True)
    print("[face-restore] 参考图: %s" % ref, flush=True)
    print("[face-restore] 输出尺寸: %dx%d，分段 %d 秒，跑完关机: %s"
          % (pw, ph, SEG, "是" if auto_shutdown else "否"), flush=True)
    print("[face-restore] 提交中（分段逐段精修，耗时随片长增长，请耐心等）…", flush=True)
    t0 = time.time()
    token = C.submit_workflow(wf_path, params, tag="face-restore",
                              kind="facerestore", timeout=timeout)
    cost = time.time() - t0
    print("[face-restore] 服务端产物令牌: %s（执行 %.0f 秒）" % (token, cost), flush=True)

    fname = name or os.path.basename(str(token).replace("\\", "/").split("|")[-1])
    if not fname.lower().endswith(VIDEO_EXT):
        fname += ".mp4"
    dst = os.path.join(out_dir, fname)
    C._download(token, dst, url=base)

    if not mp4_ok(dst):
        raise RuntimeError("下载完成但 MP4 不完整（无 ftyp 或体积过小），疑似半片：%s" % dst)

    # 只拦「只拿到部分段」这种真事故；帧级边界由分段器自己保证，这里不做逐帧对账
    out_dur = probe_dur(dst)
    if src_dur > 0 and out_dur > 0 and out_dur < src_dur * 0.95:
        raise RuntimeError(
            "成片时长明显短于源片：源 %.2f 秒 / 成片 %.2f 秒，只拿到了部分段。"
            "检查工作流 111 号节点的「结束索引」是否为 -1（传 0 只会跑第一段）：%s"
            % (src_dur, out_dur, dst))

    size_mb = os.path.getsize(dst) / 1024.0 / 1024.0
    print("[face-restore] 完成：%s（%.1f MB）" % (dst, size_mb), flush=True)
    print("[face-restore] 耗时：%.0f 秒" % cost, flush=True)
    return dst


def main():
    ap = argparse.ArgumentParser(
        description="人脸精修轻量链（本机 ComfyUI 分段工作流）：整片视频逐段修脸")
    ap.add_argument("video", help="源视频路径（服务端可读）")
    ap.add_argument("--ref", default="", help="参考图，逗号分隔（默认 avatar/000.png,avatar/001.png）")
    ap.add_argument("--seed", type=int, default=42, help="随机种子，<1 即随机（默认 42）")
    ap.add_argument("--width", type=int, default=0, help="输出宽（默认按源视频取）")
    ap.add_argument("--height", type=int, default=0, help="输出高（默认按源视频取）")
    ap.add_argument("--auto-shutdown", action="store_true",
                    help="跑完自动关机（默认关；云端跑完想省机时费再加）")
    ap.add_argument("--subdir", default="", help="服务端保存子目录（默认 %s）" % SUBDIR_DEFAULT)
    ap.add_argument("--out-dir", default="", help="本地落点目录（默认当前目录）")
    ap.add_argument("--name", default="", help="本地文件名（默认沿用服务端返回名）")
    ap.add_argument("--timeout", type=int, default=7200, help="等终态上限秒数（默认 7200）")
    ap.add_argument("--url", default="", help="指定 ComfyUI 地址（默认本机优先）")
    ap.add_argument("--dry-run", action="store_true", help="只打印要提交的参数，不真跑")
    a = ap.parse_args()

    try:
        p = restore(a.video, out_dir=a.out_dir, ref=a.ref, seed=a.seed,
                    subdir=a.subdir, timeout=a.timeout, name=a.name,
                    url=a.url, dry_run=a.dry_run, width=a.width, height=a.height,
                    auto_shutdown=a.auto_shutdown)
    except Exception as e:
        print("失败：%s" % e, flush=True)
        raise SystemExit(1)
    if p:
        print(p)


if __name__ == "__main__":
    main()
