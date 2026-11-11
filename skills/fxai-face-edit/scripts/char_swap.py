# -*- coding: utf-8 -*-
# =====================================================================
# 角色替换 · H3 循环分段链（本机 ComfyUI 工作流入口）
# ---------------------------------------------------------------------
# 链路：源视频 → 分段 → MiniMax H3 图生视频换人（逐段）→ 影调/接缝 → 合并 → 成片
# 工作流：fxai-workflow/h3-char-swap-api.json（占位符版，任务 "charswap" / 模型 "h3"）
# 来源：作者存档《凤希AI-H3角色替换循环分段V1》
#
# 与同目录另两个入口的分工：
#   face_edit.py    → 云端 FaceFusion REST（单张图换脸 / 对嘴 / 老照片 / 抠像）
#   face_restore.py → 本机分段精修（只修脸，不改人物身份与服装）
#   char_swap.py    → 本机分段换人（整片人物身份 + 服装 + 发型一起换，保留原片动作与运镜）
#
# 提交 / 等终态 / 取产物全部走公共层 comfy_client.submit_workflow。
# =====================================================================

import argparse
import json
import os
import sys
import time

# 公共层：本脚本在 <技能>/scripts/ 下，往上两级是技能根
_HERE = os.path.dirname(os.path.abspath(__file__))
_WF = os.path.join(_HERE, "..", "..", "fxai-workflow")
for _p in (_HERE, _WF):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import comfy_client as C
import face_restore as FR  # 复用 probe_size / probe_dur / mp4_ok，同一套判据

# 参考角色图：服务端相对路径（fxai/image/ 下的 rel），多张用逗号分隔。
# 提示词里写的是 <Picture 1>，所以默认只给一张；给多张时第一张才是 <Picture 1>。
# 传本地图片路径也收 —— 提交前自动上传到服务端 fxai/image/<子目录>/，再换成相对路径。
REF_DEFAULT = "avatar/000.png"
# 服务端保存子目录（分段中间件 + 合并成片都落这里）
SUBDIR_DEFAULT = "h3_swap"
# 源视频上传子目录后缀：段目录只允许分段文件。
# 合并节点扫目录时按「文件名第一个数字」排序，源片 002_file.mp4 会和 002.mp4 撞键，
# 把尾段挤出合并列表，还会因参数不同让 concat 的 DTS 回退、后面各段时间戳全塌。
SRC_SUFFIX = "_src"
# 每段时长（秒）：与工作流分段器一致，4 秒一段
SEG = 4
# 最大边长：**不写死数字，按显存自动取档**（comfy_client.default_max_side）。
#   本机 ≤16G 卡 → 960；本机 ≥24G 卡 → 1280；线上实例（仙宫云等）→ 1280。
# 显式 --max-side 永远优先；None = 没指定，走自动档。档位低于源片属正常，不做自动抬档。
# 工作流按 64 倍数对齐：960 档在 720×1386 源上落 480×960，1280 档落 640×1280。
MAX_SIDE = None
# 成片时长下限比例：低于源片的这个比例，判定只跑出了部分段
DUR_RATIO = 0.95
# 成片时长上限比例：高于源片的这个比例，判定合并目录混进了非分段视频 / 时间戳错乱
DUR_MAX_RATIO = 1.15
# 影调对齐方式：增益 / 偏移 / 不启用。
#
# 默认「不启用」。亮度跨段累积的问题**不靠它解决** —— 影调是乘性增益，会连黑位一起压，
# 画面本来就偏暗的素材会越压越闷。真正断回路的是接线：`27 取末帧` 改从影调之后、
# 接缝校色**之前**取，这样接缝节点的平移进不到下一段的条件里。
# 详见 references/06-角色替换ComfyUI链.md 第六节。
#
# 影调按「背景亮度均匀、整体被压低」设计，遇到舞台光斑、夜景这类背景明暗不均的素材，
# 八方位采样块彼此差好几倍，比值没有意义，各段增益会乱跳（实测 0.43 / 0.95 / 0.72 都有），
# 压暗的段对比度跟着掉，前后就不一致。需要时用 --tone 增益 显式打开。
TONE_DEFAULT = "不启用"
TONE_CHOICES = ("增益", "偏移", "不启用")
# 认作「本地图片」的后缀（命中且文件存在才走上传）
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

# 默认换人提示词模板：assets/换人提示词-默认.txt（H3 三段式：subject_definitions / summary /
# detailed_description，与工作流存档同款）。
# 换了参考图不需要改模板——它靠 <Picture 1> / <Video 1> 指代，不写死人物描述。
PROMPT_FILE = os.path.normpath(os.path.join(_HERE, "..", "assets", "换人提示词-默认.txt"))


def default_prompt():
    """读内置模板。文件缺失直接报错，别让空提示词跑出一版废片。"""
    if not os.path.isfile(PROMPT_FILE):
        raise RuntimeError("缺少默认提示词模板：%s" % PROMPT_FILE)
    with open(PROMPT_FILE, encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        raise RuntimeError("默认提示词模板是空的：%s" % PROMPT_FILE)
    return text


def read_prompt(prompt=""):
    """取提示词：空 → 内置模板；以 @ 开头或指向已存在的文件 → 读文件；否则当正文。"""
    if not prompt:
        return default_prompt()
    if prompt.startswith("@"):
        prompt = prompt[1:]
        if not os.path.isfile(prompt):
            raise ValueError("提示词文件不存在：%s" % prompt)
    elif os.path.isfile(prompt):
        with open(prompt, encoding="utf-8") as f:
            text = f.read().strip()
        if not text:
            raise ValueError("提示词文件是空的：%s" % prompt)
        return text
    return prompt.strip() or default_prompt()


def is_local_image(item):
    """这一项是「本机上的图片文件」吗（而不是服务端相对路径）。"""
    return item.lower().endswith(IMG_EXT) and os.path.isfile(item)


def upload_refs(ref, subdir):
    """把参考图统一成服务端相对路径。

    本机文件 → 上传到服务端 fxai/image/<subdir>/，返回 `<subdir>/<名>`；
    已经是服务端写法（`avatar/000.png`）→ 原样返回。多张逗号分隔时逐张处理。
    上传接口在服务端；调用前必须先连上 ComfyUI。
    """
    out = []
    for item in str(ref or "").split(","):
        item = item.strip()
        if not item:
            continue
        if is_local_image(item):
            name = C.upload_image(item, subdir=subdir)
            rel = name if str(name).startswith(subdir) else "%s/%s" % (subdir, name)
            print("[char-swap] 参考图已上传：%s → %s" % (item, rel), flush=True)
            out.append(rel)
        else:
            out.append(item)
    return ",".join(out) or ref


def is_local_base(base):
    """目标 ComfyUI 是不是本机（本机部署时服务端能直接读本地绝对路径）。"""
    b = str(base or "")
    return "127.0.0.1" in b or "localhost" in b


def upload_video_if_remote(video, subdir, base):
    """目标在远端时先把源视频传上去，返回服务端相对路径。

    本机部署不传：服务端就是本机，绝对路径直接能读，传一遍纯属浪费带宽。
    远端必须传：分段器只认「服务端能读到的路径」，本机绝对路径在云端不存在。

    源片传进 `<subdir>_src` 而不是 `<subdir>`：后者是分段目录，合并节点会扫整个目录，
    源片混进去会被当成一段参与拼接（见 SRC_SUFFIX 注释）。
    """
    if is_local_base(base) or not os.path.isfile(video):
        return video
    rel = C.upload_video(video, subdir=subdir + SRC_SUFFIX)
    print("[char-swap] 源视频已上传：%s → %s" % (video, rel), flush=True)
    return rel


def char_swap(video, out_dir="", ref="", prompt="", seed=42, max_side=MAX_SIDE,
              subdir="", duration=SEG, start_index=0, end_index=-1,
              auto_shutdown=False, timeout=14400, name="", url="", tone="",
              dry_run=False):
    """提交角色替换任务并取回成片。

    参数：
      video     源视频路径（服务端可读；本机部署即本机绝对路径）
      out_dir   本地落点目录，缺省当前工作目录
      ref       参考角色图：服务端相对路径（avatar/000.png），或**本机图片路径**（提交前自动上传）；
                多张逗号分隔，第一张才是 <Picture 1>。缺省 avatar/000.png
      prompt    换人提示词正文，或提示词文件路径（也认 @文件）；缺省用内置模板
      seed      随机种子，<1 即随机；缺省 42（逐段稳定，不易闪）
      max_side  最大边长。**不给就按显存自动取档**：本机 ≤16G 卡 960、≥24G 卡 1280、
                线上实例 1280；显式传数字则完全按传的来（与源片大小无关，低于源片属正常）
      subdir    服务端保存子目录
      duration  每段时长（秒），缺省 4
      start_index / end_index  段索引区间，end_index=-1 即跑完全部段
      auto_shutdown  跑完是否关机（默认 False：本机跑关自己没意义，云端按需开）
      tone      影调对齐方式：增益 / 偏移 / 不启用。缺省「不启用」
                （亮度跨段累积靠接线断，不靠它；见 references/06 第六节）
      timeout   等终态上限秒数（分段换人耗时随片长线性增长，默认 4 小时）
      name      本地文件名，缺省沿用服务端返回名
      url       指定 ComfyUI 地址（缺省走本机优先 → 配置 → 127.0.0.1:8188）
      dry_run   只打印参数不提交
    返回：本地成片绝对路径（合并后的整片，不是某一段的中间产物）
    """
    out_dir = out_dir or os.getcwd()
    ref = ref or REF_DEFAULT
    subdir = subdir or SUBDIR_DEFAULT
    tone = tone or TONE_DEFAULT
    if tone not in TONE_CHOICES:
        raise ValueError("--tone 只能是 %s，收到：%s" % (" / ".join(TONE_CHOICES), tone))
    text = read_prompt(prompt)

    # 长边档位：没显式给就按显存自动取（本机 ≤16G→960、≥24G→1280、线上→1280）。
    # 不是「源片多大就跑多大」：档位只决定能力上限，低于源片属正常，要更清就显式抬高。
    if not max_side:
        max_side = C.default_max_side(url)

    # 服务端按自己的根目录解析路径：本机存在的相对路径先转绝对，否则必报「找不到视频文件」
    if not os.path.isabs(video) and os.path.exists(video):
        video = os.path.abspath(video)

    src_dur = FR.probe_dur(video)
    if src_dur > 0:
        n = max(1, int(round(src_dur / float(duration) + 0.4999)))
        print("[char-swap] 源片 %.2f 秒，按每段 %.0f 秒切约 %d 段（实际段数以服务端分段器为准）"
              % (src_dur, duration, n), flush=True)

    wf_path = C.resolve_wf("charswap")
    params = {
        "fxai_videoinput": video,
        "fxai_imageinput": ref,
        "fxai_prompt": text,
        "fxai_outputdir": subdir,
        "fxai_seed": seed,
        "fxai_width": int(max_side),
        "fxai_duration": float(duration),
        "fxai_segment_data": "[]",
        # 分段器口径与工作流存档一致：不跳首段、保留尾段、平均分段
        "fxai_skip_initial": False,
        "fxai_include_tail": True,
        "fxai_average_segment": True,
        "fxai_start_index": int(start_index),
        "fxai_end_index": int(end_index),
        "fxai_auto_shutdown": bool(auto_shutdown),
        "fxai_tone_mode": tone,
    }

    if dry_run:
        show = dict(params)
        show["fxai_prompt"] = text[:60] + ("…（共 %d 字）" % len(text) if len(text) > 60 else "")
        local_refs = [x.strip() for x in str(ref).split(",") if is_local_image(x.strip())]
        if local_refs:
            show["fxai_imageinput"] = "%s（本地文件 %d 张，提交时自动上传到服务端 %s/）" \
                                      % (ref, len(local_refs), subdir)
        if url and not is_local_base(url) and os.path.isfile(video):
            show["fxai_videoinput"] = "%s（远端 %s：提交时自动上传到 %s/）" \
                                      % (video, url, subdir + SRC_SUFFIX)
        print("[dry-run] 工作流: %s" % wf_path)
        print("[dry-run] 注入: %s" % json.dumps(show, ensure_ascii=False, indent=2))
        print("[dry-run] 落点: %s" % out_dir)
        return ""

    # 本机优先：本机 8188 探得通就用本机，不计费；探不通才由 require_comfyui 走云
    if not url:
        C.prefer_local()
    C.require_comfyui(url or None)
    base = C.comfy_url(url or "")
    # --url 指定云端时，把全局地址一并切过去：上传、下载都按这个地址走
    C.COMFY = base

    # 素材统一成服务端能读的写法：远端先上传，本机则原样（绝对路径服务端能读）
    video = upload_video_if_remote(video, subdir, base)
    ref = upload_refs(ref, subdir)
    params["fxai_videoinput"] = video
    params["fxai_imageinput"] = ref

    print("[char-swap] 服务端: %s" % base, flush=True)
    print("[char-swap] 源视频: %s" % video, flush=True)
    print("[char-swap] 参考角色图: %s" % ref, flush=True)
    print("[char-swap] 最大边长 %d，每段 %.0f 秒，段区间 %s~%s，影调 %s，跑完关机: %s"
          % (max_side, duration, start_index, end_index, tone,
             "是" if auto_shutdown else "否"), flush=True)
    print("[char-swap] 提示词 %d 字" % len(text), flush=True)
    print("[char-swap] 提交中（逐段换人，耗时随片长增长，请耐心等）…", flush=True)

    t0 = time.time()
    token = C.submit_workflow(wf_path, params, tag="char-swap",
                              kind="charswap", timeout=timeout)
    cost = time.time() - t0
    print("[char-swap] 服务端产物令牌: %s（执行 %.0f 秒）" % (token, cost), flush=True)

    fname = name or os.path.basename(str(token).replace("\\", "/").split("|")[-1])
    if not fname.lower().endswith(FR.VIDEO_EXT):
        fname += ".mp4"
    dst = os.path.join(out_dir, fname)
    C._download(token, dst, url=base)

    if not FR.mp4_ok(dst):
        raise RuntimeError("下载完成但 MP4 不完整（无 ftyp 或体积过小），疑似半片：%s" % dst)

    out_dur = FR.probe_dur(dst)
    if src_dur > 0 and out_dur > 0 and out_dur < src_dur * DUR_RATIO:
        raise RuntimeError(
            "成片时长明显短于源片：源 %.2f 秒 / 成片 %.2f 秒，只拿到了部分段。"
            "检查 ⓪ 生成控制器（40）的「结束索引」是否为 -1，或 --end-index 是否传了 0：%s"
            % (src_dur, out_dur, dst))
    # 反向也要卡：段目录混进源片/参考片时，concat -c copy 会因参数不同让 DTS 回退，
    # 后续各段时间戳塌成同一个值 —— 成片不但慢放，尾段还会整段丢失。
    if src_dur > 0 and out_dur > src_dur * DUR_MAX_RATIO:
        raise RuntimeError(
            "成片时长异常长于源片：源 %.2f 秒 / 成片 %.2f 秒。典型原因是段目录里混进了"
            "非分段视频（源片 / 参考片），或各段参数不一致让时间戳错乱。"
            "确认 ⑧ 合并节点（23）扫的目录里只有 000.mp4 这类纯数字分段：%s"
            % (src_dur, out_dur, dst))

    size_mb = os.path.getsize(dst) / 1024.0 / 1024.0
    print("[char-swap] 完成：%s（%.1f MB）" % (dst, size_mb), flush=True)
    print("[char-swap] 耗时：%.0f 秒" % cost, flush=True)
    return dst


def main():
    ap = argparse.ArgumentParser(
        description="角色替换 H3 循环分段链（本机 ComfyUI）：整片人物/服装替换，保留原片动作与运镜")
    ap.add_argument("video", help="源视频路径（服务端可读）")
    ap.add_argument("--ref", default="",
                    help="参考角色图：服务端相对路径或本机图片（自动上传），逗号分隔"
                         "（默认 %s）" % REF_DEFAULT)
    ap.add_argument("--prompt", default="",
                    help="换人提示词正文，或提示词文件路径（默认内置 body_swap 模板）")
    ap.add_argument("--seed", type=int, default=42, help="随机种子，<1 即随机（默认 42）")
    ap.add_argument("--max-side", type=int, default=MAX_SIDE,
                    help="最大边长；不给则按显存自动（本机≤16G→960、≥24G→1280、线上→1280）")
    ap.add_argument("--duration", type=float, default=SEG, help="每段时长秒（默认 %d）" % SEG)
    ap.add_argument("--start-index", type=int, default=0, help="从第几段开始（默认 0）")
    ap.add_argument("--end-index", type=int, default=-1, help="到第几段结束，-1 即全部（默认 -1）")
    ap.add_argument("--auto-shutdown", action="store_true",
                    help="跑完自动关机（默认关；云端跑完想省机时费再加）")
    ap.add_argument("--subdir", default="", help="服务端保存子目录（默认 %s）" % SUBDIR_DEFAULT)
    ap.add_argument("--tone", default="", choices=TONE_CHOICES,
                    help="影调对齐方式（默认 %s）。背景明暗不均的素材用「不启用」" % TONE_DEFAULT)
    ap.add_argument("--out-dir", default="", help="本地落点目录（默认当前目录）")
    ap.add_argument("--name", default="", help="本地文件名（默认沿用服务端返回名）")
    ap.add_argument("--timeout", type=int, default=14400, help="等终态上限秒数（默认 14400）")
    ap.add_argument("--url", default="", help="指定 ComfyUI 地址（默认本机优先）")
    ap.add_argument("--dry-run", action="store_true", help="只打印要提交的参数，不真跑")
    a = ap.parse_args()

    try:
        p = char_swap(a.video, out_dir=a.out_dir, ref=a.ref, prompt=a.prompt,
                      seed=a.seed, max_side=a.max_side, subdir=a.subdir,
                      duration=a.duration, start_index=a.start_index,
                      end_index=a.end_index, auto_shutdown=a.auto_shutdown,
                      timeout=a.timeout, name=a.name, url=a.url, tone=a.tone,
                      dry_run=a.dry_run)
    except Exception as e:
        print("失败：%s" % e, flush=True)
        raise SystemExit(1)
    if p:
        print(p)


if __name__ == "__main__":
    main()
