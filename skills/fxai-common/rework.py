# -*- coding: utf-8 -*-
"""单段返修（rework）公共件 —— **所有视频生成技能共用的唯一真相源**。

为什么要有这一份
----------------
2026-10-21 数字人口播出过一次事故：10 镜里只有第 7 镜人物侧脸，本该只重渲那 9.42 秒，
结果整片 88 s 重跑 70 分钟；更糟的是 `--start-index/--end-index` 传了还被主流程的
「已有产物→跳过」吃掉，参数形同虚设（见 `dh_video.py` 的 `rework` 判据）。

**这类错误靠"读规范时记住"守不住，必须做成程序行为。**
本模块就是那个程序行为，任何视频生成技能（数字人 / MV / 短剧 / 幻灯口播）都调它：

| 函数 | 作用 |
|---|---|
| `is_rework(start, end)` | 是否「单段返修」——显式给了索引且不是整片 `(0, -1)` |
| `changed_shots(proj, sb)` | 对比上次渲染签名，算出**这次改了哪几镜**（1 起） |
| `save_sig(proj, sb)` | 本次渲染用的分镜长什么样，落盘做签名 |
| `shot_timecode(n)` | 第 n 镜对应的时间码区间（定位用） |
| `refix_command(proj, shots)` | 直接给出该跑的返修命令，不用人去拼 |

判据（日志行，看到才算真在返修）
    `[rework] 返修模式：只重渲第 N 段`
    `[rework] 只改了第 N 镜 → 自动降级为单镜返修（整片不重跑）`
"""
import hashlib
import json
import os

# H3 单段时长（秒）。数字人 / MV / 短剧 / 幻灯都是按这个切片，改这里就够。
SEG_SECONDS = 9.41667

# 只有个别镜改动时，自动降级成「逐镜单段返修」的阈值。
# 超过这个数，逐镜排队反而比整片一次跑完慢，就退回整片。
AUTO_REWORK_MAX = 3

SIG_NAME = "_storyboard_sig.json"


# ---------------------------------------------------------------- 判定
def is_rework(start_index, end_index):
    """是否「单段返修」：显式给了索引，且不是整片模式 `(0, -1)`。

    ⚠️ 这个判断必须出现在**所有**「已有产物 → 跳过」之前。
    数字人曾经漏了这一步，参数传了照样跳过，成片一个字节没变。
    """
    if start_index is None or end_index is None:
        return False
    try:
        s, e = int(start_index), int(end_index)
    except (TypeError, ValueError):
        return False
    return not (s == 0 and e == -1)


def parse_shots(text, total=None):
    """`"3,7,9"` / `"3 7 9"` / `"3-6"` / `[3,7,9]` → 排好序去重的镜号列表（1 起）。

    质检发现「第 3、7、9 镜有问题」时，直接把这几镜拼成一处提交，
    逐镜手敲与整片重跑，两种做法都排除。
    """
    if text is None:
        return []
    if isinstance(text, (list, tuple, set)):
        raw = list(text)
    else:
        raw = []
        for chunk in str(text).replace("，", ",").replace("、", ",").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "-" in chunk:
                a, b = chunk.split("-", 1)
                try:
                    raw.extend(range(int(a), int(b) + 1))
                except ValueError:
                    continue
            else:
                try:
                    raw.append(int(chunk))
                except ValueError:
                    continue
    out = sorted({int(x) for x in raw if int(x) >= 1})
    if total is not None:
        out = [x for x in out if x <= int(total)]
    return out


# ---------------------------------------------------------------- 批量语义
# 服务端合并规则（2026-10-21 用户强调「技能里记录得很清楚」的那条）：
# 每一次单段渲染提交，服务端都会拿**已缓存的其它段**拼成完整成片返回。
# 所以：① 多段返修必须**串行**，并行提交会互相覆盖对方刚生成的段；
#      ② 中间那几次合并片都是半成品，**只认最后一段渲完的那一次**；
#      ③ 最后一段渲完之前，不许烧字幕、不许做封面、不许交付。
MERGE_NOTE = (
    "服务端合并规则：每渲完一段都会拿其它段的缓存拼回完整成片 →\n"
    "  · 多段返修必须串行，并行提交会互相覆盖；\n"
    "  · 中途那几次成片都是半成品，只认**最后一段渲完**的那一次；\n"
    "  · 全部渲完之前不烧字幕、不做封面、不交付。"
)


def plan_batch(shots, per_shot_min=9.0, head_min=3.0, quiet=False):
    """打印「这几镜怎么修」的串行计划，返回顺序执行用的镜号列表。

    用户口径原话：**「如果三段都有问题，就三段都提交返修，这三段返修完了再去做后面的事。」**
    所以这里给出的永远是串行顺序表，不是并行、也不是整片重跑。
    """
    seq = parse_shots(shots)
    if not seq:
        return []
    if not quiet:
        print("[rework] 批量返修计划：%d 镜（第 %s 镜），串行执行"
              % (len(seq), "、".join(map(str, seq))), flush=True)
        print("         预计 %.0f~%0.f 分钟（每镜约 %.0f 分钟，四视图/场景图命中缓存更快）"
              % (head_min + per_shot_min * len(seq),
                 head_min + per_shot_min * len(seq) * 1.6, per_shot_min), flush=True)
        for k, n in enumerate(seq, 1):
            t0, t1 = shot_timecode(n)
            print("         %d/%d  第 %d 镜  %.2f~%.2f s" % (k, len(seq), n, t0, t1), flush=True)
        print("         " + MERGE_NOTE.replace("\n", "\n         "), flush=True)
    return seq


def shot_timecode(shot_no, seg=SEG_SECONDS):
    """第 shot_no 镜（1 起）→ (起, 止) 秒。"""
    t0 = (int(shot_no) - 1) * seg
    return t0, t0 + seg


def shot_no_of(seconds, seg=SEG_SECONDS):
    """秒数落在第几镜（1 起）—— 用户说「62 秒那里不对」时用它定位。"""
    return int(float(seconds) // seg) + 1


# ---------------------------------------------------------------- 签名
def _sig_path(proj):
    return os.path.join(proj, "doc", SIG_NAME)


def _shots_of(sb_path):
    try:
        with open(sb_path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:                                            # noqa: BLE001
        return []
    if isinstance(d, list):
        return d
    for k in ("shots", "storyboard", "list"):
        if isinstance(d.get(k), list):
            return d[k]
    return []


def _shot_hash(sh):
    return hashlib.md5(json.dumps(sh, ensure_ascii=False, sort_keys=True)
                       .encode("utf-8")).hexdigest()[:12]


def load_sig(proj):
    """上次渲染时的分镜签名：{"count": N, "shots": ["hash"...]}。"""
    try:
        with open(_sig_path(proj), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:                                            # noqa: BLE001
        return {}


def save_sig(proj, sb_path, kind=None):
    """渲染**之后**调用：把这次用到的分镜记下来。"""
    shots = _shots_of(sb_path)
    if not shots:
        return {}
    sig = {"count": len(shots), "kind": kind,
           "shots": [_shot_hash(s) for s in shots]}
    os.makedirs(os.path.join(proj, "doc"), exist_ok=True)
    with open(_sig_path(proj), "w", encoding="utf-8") as f:
        json.dump(sig, f, ensure_ascii=False, indent=1)
    return sig


def changed_shots(proj, sb_path):
    """这次改了哪几镜（1 起）。没有历史签名时返回 []（无从比较，不拦）。

    这是「本来只改一镜，却又整片重跑」的**程序闸门**：
    改了哪几镜是能算出来的，就不该让人凭印象决定整片重来与否。
    """
    old = load_sig(proj).get("shots") or []
    new = [_shot_hash(s) for s in _shots_of(sb_path)]
    if not old or not new:
        return []
    out = []
    for i, h in enumerate(new):
        if i >= len(old) or old[i] != h:
            out.append(i + 1)
    return out


# ---------------------------------------------------------------- 建议
def refix_command(proj, shots, python=None):
    """给出该跑的返修命令（sh 技能里的 refix.py 统一入口）。"""
    import sys
    py = python or sys.executable
    skill = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "fxai-digital-human-video", "scripts", "refix.py")
    proj_name = os.path.basename(os.path.abspath(proj))
    shots = list(shots or [])
    if len(shots) == 1:
        return '"%s" "%s" --project "%s" --shot %d' % (py, skill, proj_name, shots[0])
    return "（共 %d 镜要返修）%s" % (
        len(shots), "  ".join('"%s" "%s" --project "%s" --shot %d' % (py, skill, proj_name, s)
                              for s in shots))


def explain(proj, sb_path, whole_run=True):
    """打印「你其实只需要返修这几镜」。返回改动镜号列表（1 起）。"""
    changed = changed_shots(proj, sb_path)
    if not changed:
        return []
    if len(changed) > AUTO_REWORK_MAX:
        print("[rework] 分镜改动 %d 镜（>%d），整片重渲更快，放行"
              % (len(changed), AUTO_REWORK_MAX), flush=True)
        return changed
    print("[rework] 只改了第 %s 镜 → 本来只需要单段返修，不必整片重跑"
          % "、".join(map(str, changed)), flush=True)
    print("[rework] 建议：%s" % refix_command(proj, changed), flush=True)
    return changed


# ---------------------------------------------------------------- 拼回成片
def splice_segment(full_video, seg_video, shot_no, out=None, seg=SEG_SECONDS,
                   keep_audio_from_full=True, crf=18):
    """把「单段重生成的裸片」按时间码替换回完整成片。

    这是短剧 / MV / 幻灯共同的缺口：
      · 数字人 multiref 由**服务端**自动把新段与其它段合并（不需要本函数）；
      · 短剧 `--single-shot`、MV `--mv-segment`、幻灯某一段，产物都只是**那一段裸片**
        （时间从 0 起），这一步必须靠本函数拼回，否则交付的是缺段成片。
    本函数把这一步做成一次调用，音轨默认沿用原片（新段音频与原片同源，不需要重混）。

    seg_video 时长应≈ `seg`；短了就按实际时长替换（区间边界以新段为准，免生黑帧）。
    """
    import shutil
    import subprocess

    if not (os.path.isfile(full_video) and os.path.isfile(seg_video)):
        raise FileNotFoundError("成片或新段不存在：%s / %s" % (full_video, seg_video))
    t0, t1 = shot_timecode(shot_no, seg)
    # 新段真实时长：比标称短的时候以真实为准，否则 t1 之后会漏音/黑帧
    seg_dur = _probe_duration(seg_video) or seg
    t1 = t0 + seg_dur
    total = _probe_duration(full_video) or 0
    if t1 > total:
        t1 = total
    out = out or os.path.join(
        os.path.dirname(os.path.abspath(full_video)),
        "%s_修%d%s" % (os.path.splitext(os.path.basename(full_video))[0], int(shot_no),
                       os.path.splitext(full_video)[1]))

    vf = ("[0:v]trim=start=0:end=%.6f,setpts=PTS-STARTPTS[v0];"
          "[1:v]scale=%d:%d:force_original_aspect_ratio=decrease,"
          "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1[v1];"
          "[0:v]trim=start=%.6f,setpts=PTS-STARTPTS[v2];"
          "[v0][v1][v2]concat=n=3:v=1:a=0[v]")
    w, h = _probe_size(full_video) or (0, 0)
    if not (w and h):
        raise RuntimeError("读不到成片尺寸：%s" % full_video)
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-i", full_video, "-i", seg_video,
           "-filter_complex", vf % (t0, w, h, w, h, t1),
           "-map", "[v]"]
    if keep_audio_from_full:
        cmd += ["-map", "0:a?", "-c:a", "copy"]
    cmd += ["-c:v", "libx264", "-crf", str(crf), "-pix_fmt", "yuv420p", out]

    print("[rework] 拼回成片：第 %d 镜 [%.2f ~ %.2f s] 用新段替换"
          % (shot_no, t0, t1), flush=True)
    rc = subprocess.call(cmd)
    if rc != 0:
        raise RuntimeError("ffmpeg 拼回失败（rc=%d）" % rc)
    print("[rework] ✅ 新成片 -> %s（原片未动）" % out, flush=True)
    return out


def _probe_duration(path):
    try:
        import subprocess
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", path], capture_output=True, text=True)
        return float((r.stdout or "0").strip() or 0)
    except Exception:                                            # noqa: BLE001
        return 0


def _probe_size(path):
    try:
        import subprocess
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
                           capture_output=True, text=True)
        w, h = (r.stdout or "").strip().split(",")[:2]
        return int(w), int(h)
    except Exception:                                            # noqa: BLE001
        return 0, 0
