# -*- coding: utf-8 -*-
# =====================================================================
# fxai-digital-human-video —— 数字人口播视频生成（技能主入口）
#
# 拿口播音频（fxai-voiceover 生成的 music/*.wav，或用户额外提供的任意音频）
# 生成数字人出镜讲话的视频，走 MiniMax H3 三模式，与 MV 同一套视频实现
# （公共件 fxai-audio-video），只是提示词措辞从「演唱」换成「口播讲话」。
#
# 两种音频模式（单人单口播默认走「合并模式」）：
#   · 合并模式（默认，单人单口播）：把 TTS 生成的每段音频先合并成一条完整音频，
#     再一次性喂给视频生成（single/multi/multiref 都按这条完整音频走，分段 / 场景提示词
#     写作全部基于完整音频，与 MV 那套一致）。--multi-audio 可关掉。
#   · 多音频模式（--multi-audio，多音频多人场景）：每条音频单独生成一条视频，
#     --concat 再拼成一条。非默认——逐段单独生成会让 H3 截断公式在每段边界错位。
#
# 产物落盘：
#   --project <项目名> 时用 output_layout 建目录，成片落 <项目根>/<项目名>/video/，
#     提示词存档与视频清单落 doc/，并回填 doc/delivery.json 的 video 段。
#   不带 --project 时退回到 --out-dir，同样写 doc 清单。
# =====================================================================
# >>> 交付码率统一出口（fxai-standard/references/12-交付视频编码规格.md）>>>
import os as _os
import sys as _sys
_VC_ROOT = _os.path.normpath(_os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), '../fxai-common'))
if _VC_ROOT not in _sys.path:
    _sys.path.insert(0, _VC_ROOT)
import video_encode as _venc
# <<< 交付码率统一出口 <<<
import argparse
import datetime
import glob
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILLS = os.path.dirname(_HERE)
for _dep in ("fxai-workflow", "fxai-audio-video", "fxai-scene-gen", "fxai-fourview-gen", "fxai-common"):
    _p = os.path.join(_SKILLS, _dep)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import audio_video as av  # noqa: E402  公共件：音频驱动 → 视频（与 MV 共用）
import fxai_common as fxc  # noqa: E402
import rework as rw  # noqa: E402  公共件：单段返修判定 + 分镜改动签名（全视频技能共用）
from scene_gen import gen_scene_image  # noqa: E402
from fourview_gen import gen_char_view  # noqa: E402  角色四视图（多参考人物图，与 MV 同一入口）

AUDIO_EXT = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".wma")


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_sec(s):
    try:
        s = float(s or 0)
    except Exception:
        return "-"
    return "%d:%02d" % (int(s // 60), int(round(s % 60)))


def _rel(root, p):
    try:
        return os.path.relpath(p, root).replace("\\", "/")
    except Exception:
        return p


def _pad(n):
    return "%02d" % n if n < 100 else "%03d" % n


# ---------- 读文案：提示词要契合这段在讲什么 ----------
def read_script(script):
    """读口播文案，返回 [(段号, 情感标签, 正文)]；空行分段，带 `用…的语气说<|endofprompt|>` 的拆出标签。"""
    with open(script, encoding="utf-8") as f:
        blocks = [b.strip() for b in re.split(r"\n\s*\n", f.read()) if b.strip()]
    out = []
    for i, b in enumerate(blocks, 1):
        tag, text = fxc.parse_line(b)
        out.append((i, tag, text))
    return out


def script_note(script, limit=3):
    """把文案前几段正文摘给提示词用：写场景/道具时要能对上这段在讲什么。"""
    if not script or not os.path.isfile(script):
        return ""
    try:
        segs = read_script(script)
    except Exception as e:
        print("[dh] 读文案失败（不影响生成）：%s" % e, flush=True)
        return ""
    if not segs:
        return ""
    print("[dh] 文案 %d 段（提示词请契合这些内容）" % len(segs), flush=True)
    for i, tag, text in segs[:limit]:
        print("   第%d段%s：%s" % (i, ("[%s]" % tag) if tag else "", text[:36]), flush=True)
    return " / ".join(t for _i, _tg, t in segs[:limit])


def script_gist(script, limit=12):
    """**全文**摘要（默认前 12 段）——场景图按整篇文章的意境来出，不只看开头几段。

    口播是整篇文章朗读（MV 是逐句歌词），场景图**不逐段对齐口播内容**，
    所以需要看整篇讲了什么，才能选出若干个彼此不同、且都贴合全文意境的场景。
    """
    if not script or not os.path.isfile(script):
        return ""
    try:
        segs = read_script(script)
    except Exception as e:
        print("[dh] 读文案失败（不影响生成）：%s" % e, flush=True)
        return ""
    if not segs:
        return ""
    texts = [t.rstrip("。！？!?") for _i, _tg, t in segs[:limit] if t.strip()]
    return "；".join(texts)


# ---------- H3 分段：一条音频内部会被切成多段，提示词条数要跟段数对齐 ----------
def audio_segs(auds, avg_seg=None):
    """逐条音频按 H3 精确公式切段，返回 [[(start, end), …], …]（与音频一一对应）。

    「一条长口播」≠「一条视频」：H3 会把每条音频内部再切成 9.41667 s 一段，
    提示词行数 / 场景图数量都得按这个段数来，写少了后面几段就复用第一条。
    avg_seg 缺省取工作流默认（10 → 实际 9.41667 s）。
    """
    out = []
    for i, p in enumerate(auds, 1):
        dur = av.audio_duration(p)
        if not dur:
            print("[dh] 第%d条音频读不到时长（%s），按单段处理" % (i, os.path.basename(p)), flush=True)
            out.append([])
            continue
        _secs, bounds = av.h3_segments(dur, avg_seg if avg_seg else 10.0)
        out.append(bounds)
        print("[dh] 第%d条音频 %s：%.2f 秒 → H3 切 %d 段（每段约 %.2f 秒）"
              % (i, os.path.basename(p), dur, len(bounds), (dur / len(bounds)) if bounds else 0), flush=True)
    return out


def script_segments(script, n_total, limit_each=1):
    """把文案按「段数」摊成逐段要点：第 i 个视频分段该讲什么。

    文案段数与 H3 分段数不一定相等（一条音频内部还会再切段），所以按顺序取用：
    文案段数不足就循环取用，不足 n_total 的用最后一段兜底。
    """
    if not script or not os.path.isfile(script) or n_total <= 0:
        return []
    try:
        segs = read_script(script)
    except Exception:
        return []
    if not segs:
        return []
    out = []
    for i in range(n_total):
        idx = min(i // max(1, limit_each), len(segs) - 1)
        _no, tag, text = segs[idx]
        out.append({"tag": tag, "text": text,
                    "note": ("[%s] " % tag if tag else "") + text})
    return out


# ---------- 音频收集：单音频 / 多音频 ----------
def collect_audios(a):
    """把 --audio（多条）/ --audio-dir（整个目录）/ --script（按序号推断同名音频）收成音频列表。

    排序统一按「文件名里的序号」→ 没有序号就按文件名，保证第 i 段对第 i 条。
    """
    auds = []
    for x in (a.audio or []):
        p = os.path.abspath(x)
        if not os.path.isfile(p):
            raise SystemExit("! 音频不存在：%s" % x)
        auds.append(p)
    if a.audio_dir:
        d = os.path.abspath(a.audio_dir)
        if not os.path.isdir(d):
            raise SystemExit("! 音频目录不存在：%s" % a.audio_dir)
        auds += [os.path.join(d, f) for f in sorted(os.listdir(d))
                 if f.lower().endswith(AUDIO_EXT)]
    if a.script and not auds:
        # 没显式给音频时，才用脚本名去 music/ 找同名音频（<脚本名>NN.wav）。
        # 已给 --audio/--audio-dir 时脚本只用于取文案内容，不做这层查找。
        base = os.path.basename(os.path.splitext(a.script)[0])
        cands = []
        for d in {os.path.dirname(os.path.abspath(a.script)),
                  os.path.join(os.path.dirname(os.path.abspath(a.script)), "..", "music"),
                  os.getcwd(), os.path.join(os.getcwd(), "music")}:
            cands += glob.glob(os.path.join(d, base + "*.wav"))
            cands += glob.glob(os.path.join(d, base + "*.mp3"))
        if not cands:
            raise SystemExit("! 按脚本 %s 没找到同名音频（期望 %sNN.wav）" % (a.script, base))
        auds += cands
    # 去重 + 按「序号优先、文件名次之」排序
    uniq = {os.path.normcase(p): p for p in auds}
    def _key(p):
        m = re.findall(r"(\d+)", os.path.basename(p))
        return (int(m[-1]) if m else 10 ** 9, os.path.basename(p))
    out = sorted(uniq.values(), key=_key)
    if not out:
        raise SystemExit("! 没有音频。给 --audio <文件>（可多条）/ --audio-dir <目录> / --script <口播文案>")
    print("[dh] 音频 %d 条：%s" % (len(out), "、".join(os.path.basename(p) for p in out[:8])
                                   + ("…" if len(out) > 8 else "")), flush=True)
    return out


# ---------- 场景图（委托 fxai-scene-gen；multiref 不出现主角本人，可有观众/背景人物） ----------
# 场景图数量规则
#
# 时间规则：时长 ≤ 120s → 段数（每段一张，零重复）；> 120s → 段数 // 2 + 1。
# multiref 至少 2 个**不同**场景（同场景跨段会跳帧）、上限 = 段数。
# 场景图不逐段对齐口播内容，按整篇文案给若干不同场景。
def scene_count_for(dur, seg, explicit=0, mode="multiref"):
    """算该生成几张场景图。explicit > 0 为用户显式指定（仍受 multiref 下限 2 约束）。"""
    seg = max(1, int(seg or 1))
    if dur <= 120:
        n = seg                      # 短片：每段一张，零重复
    else:
        n = seg // 2 + 1             # 长片：一半加一张
    if explicit and explicit > 0:
        n = explicit
    if mode == "multiref":
        # 多参考铁律：至少 2 个不同场景，且不超过段数
        n = min(seg, max(2, n))
        if seg < 2:
            print("[scene] ⚠️ 只有 1 段，无法做出多个场景（多场景需 ≥2 段）", flush=True)
    return max(1, n)


# 环境差异化修饰词（只写环境/光线/视角/构图，不写主角本人；不出现文字或字幕）。
# 一条描述被复用到多张图时按序加上这些修饰，保证每张场景图真的不同——否则同场景跨段会跳帧。
# 🔴 全部为「无前景遮挡」安全修饰——禁「前景遮挡/过肩/低机位从桌面向上拍」类写法
#    （multiref 注入后人物会被前景家具挡住，像沉进桌子里——实测穿帮）。
# 🔴 修饰词一律**不改动机位角度/高低**（统一平视眼高），只变光影/景深/色调——
#    否则多张场景图机位高低不一，相邻段切换会"跳"。
_SCENE_VARIANTS = (
    "平视中景，浅景深，主体环境居中",
    "侧向斜角构图，人物落位区开阔，层次靠背景纵深",
    "窗光斜洒，环境更通透",
    "视线高度平视，背景纵深明显",
    "正面平视，环境细节清晰，光线柔和",
    "广角全景，环境完整入画，层次分明",
)

# 🔴 人物落位区护栏（自动追加到每条场景提示词，白名单式写法）：
#    multiref 把人物注入场景，若镜头与落位区之间有家具/台面等前景物，
#    人物会被画成沉入家具（只剩头浮在桌面上）。场景图必须给人物留出
#    「身前无遮挡的开阔落位区」，家具只放背景与两侧；特写类空镜不注入人物。
_SCENE_GUARD = ("构图硬要求：画面中下部留出人物落位区，落位区身前与身侧没有任何遮挡物，"
                "桌台/沙发等家具只出现在背景与两侧；"
                "相机与落位区视线齐平，禁桌面俯拍特写与前景遮挡构图。"
                # 🔴 护栏只约束「身前无遮挡」，地面占比一律交给场景描述。
                # 护栏里写「画面下半部是开阔的空地面」会与场景描述里的「柜体铺到画面最下沿」
                # 互相打架：模型按空地图 → 地面占 55~70% → 中景腰线（77%）切在地面起始线上。
                )


# 场景图提示词
#
# 场景图只写环境（场景/光线/氛围/构图），不写主体——主体由 char_rel 注入，场景图带主体会
# 重复/冲突（双人、错位）。可出现背景人物（远景/虚化的听众、路人），不出现文字或字幕。
# 有文案时按**全文意境**追加一段"只取自然与环境意象"的限定；环境不预设清单，
# 室内/室外/森林/溪流/海边/屋顶/街市…任意地方都可以，只要贴合内容、彼此不同、氛围统一。
_SCENE_MOOD = ("画面环境呼应全文意境：「%s」，但只表现其中的自然与环境意象"
               "（光线、风、天光、水面、植被、建筑、街道等），保持空镜、不出现主体人物")


def _auto_scene_prompt(gist):
    """无 --scene-desc 时按全文意境自由配场景。"""
    if not gist:
        return ""
    return _SCENE_MOOD % gist


def _scene_prompts(descs, n, gist=""):
    """把用户给的场景描述铺成 n 条**互不相同**的场景图提示词。

    描述不够就循环取用并叠加差异化修饰（视角/光线/构图），确保多张图不雷同。
    人物边界：**不出现主角本人**（主角由 multiref 的 char_rel 注入，场景图再出现会双人穿帮）；
    可以有**背景人物**（远景/虚化的听众、路人，不抢戏），但**不出现主体本人**；一律**不出现文字或字幕**。
    """
    base = descs or [""]
    out = []
    for i in range(n):
        d = base[i % len(base)]
        if not d:
            d = _auto_scene_prompt(gist) or (
                "自然主义写实，浅景深，一处光线通透、层次安静的实景环境，"
                "氛围统一、背景简洁不抢戏，不出现文字")
        if i >= len(base):
            # 描述被复用 → 换修饰词，保证每张图真的不一样
            d = "%s，%s" % (d, _SCENE_VARIANTS[i % len(_SCENE_VARIANTS)])
        out.append("%s，%s" % (d, _SCENE_GUARD))
    return out


# ---------- 角色四视图（多参考人物图；规则与 MV 一致） ----------
def make_charview(a, timeout=None):
    """生成人物四视图，返回服务端相对路径（多参考的 <Picture 1>）。

    ★ 与 MV 同规则：**只写穿搭，面貌由参考图锁定**（提示词只写穿搭，五官脸型全交给参考图）；
      参考图直接用全局头像库路径，原路径引用即可。
    multiref 专用——single/multi 把人物画进场景图，不需要四视图。
    """
    ref = av.REF_WOMAN if a.role in ("woman", "女", "女性") else (
        av.REF_MAN if a.role in ("man", "男", "男性") else a.role)
    desc = (a.char_desc or "").strip() or av.DEFAULT_CHAR_DESC
    tag = "%s_%s_char" % (a.project_tag, a.name or "口播")
    # 🔴 四视图命中本地缓存直接复用：单镜返修里重出四视图要多花 6 分钟，
    # 而形象不变时重出一遍没有收益。
    # 缓存命中时**走上传**而不是复用旧 rel —— 服务端文件名恒为 000.png，
    # 不同项目会互相覆盖，上传本项目的这张才算数。
    cached = os.path.join(av.OUT_DIR, tag + ".png")
    if (not getattr(a, "force", False)) and os.path.isfile(cached):
        try:
            from comfy_client import upload_image
            rel = upload_image(cached, subdir=a.charview_subdir,
                               filename="%s.png" % tag, overwrite=True)
            if rel and not str(rel).startswith(a.charview_subdir):
                rel = "%s/%s" % (a.charview_subdir, rel)
            print("[dh] 角色四视图复用缓存：%s -> %s（换形象用 --force）" % (cached, rel),
                  flush=True)
            return rel
        except Exception as e:                                   # noqa: BLE001
            print("[dh] 四视图缓存上传失败，改回重新生成：%s" % e, flush=True)
    _p, rel = gen_char_view(desc, ref=ref, views=4,
                            tag=tag,
                            subdir=a.charview_subdir, num=0,
                            timeout=timeout or a.charview_timeout)
    print("[dh] 角色四视图（穿搭：%s）-> %s" % (desc[:24], rel), flush=True)
    return rel

def _sync_scene_to_project(a, cached, idx):
    """把场景图从缓存取回项目 image/scene_NN.png。

    🔴 为什么要程序做：`gen_scene_image` 生成后只落在服务端 output 与本地缓存
    `~/.workbuddy/fxai_cache/`，视频任务只消费**服务端相对路径**，所以项目 `image/`
    一直是空的——人以为"图没生成"，其实图在缓存里。靠规范写「记得拷」必漏，
    这里做成自动行为：
      · 本地没有 → 拷
      · 本地已有 → 跳过（省 IO，也不覆盖人工修过的图）
      · --force  → 覆盖（重新生成就该换掉旧图）
    """
    import shutil
    try:
        import output_layout as ol
        root = ol.layout(a.project)["root"] if a.project else (a.out_dir or ".")
    except Exception:
        root = a.out_dir or "."
    dst = os.path.join(root, "image", "scene_%02d.png" % idx)
    if os.path.isfile(dst) and not a.force:
        print("[scene] 本地已有 %s，跳过取回" % dst, flush=True)
        return dst
    if not (os.path.isfile(cached) and os.path.getsize(cached) > 1024):
        print("[scene] ⚠️ 缓存里没有可拷的文件：%s" % cached, flush=True)
        return None
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(cached, dst)
        print("[scene] 已取回本地 -> %s" % dst, flush=True)
        return dst
    except OSError as e:
        print("[scene] ⚠️ 取回本地失败（%s）：%s" % (e, dst), flush=True)
        return None


def _scenes_newer_than(dst0, root):
    """成片比场景图旧 → 场景图换过了，旧成片作废。

    场景图重出后（改了场景描述、从特效空间换成实景），画面必须跟着换。
    只凭「合并_xxx.mp4 存在」就跳过渲染，会拿旧画面配新场景，改了等于没改。
    """
    try:
        t0 = os.path.getmtime(dst0)
    except OSError:
        return False
    img_dir = os.path.join(root, "image")
    if not os.path.isdir(img_dir):
        return False
    for f in glob.glob(os.path.join(img_dir, "scene_*.png")):
        try:
            if os.path.getmtime(f) > t0 + 1:
                return True
        except OSError:
            continue
    return False


def make_scenes(a, n, tag, note="", gist=""):
    """生成 n 张场景图，返回 (相对路径列表, 描述列表)。mode=single 只用第 1 张。

    场景描述**按文案内容写**：给了 --scene-desc 用它；没给就把文案摘要打出来提醒要按内容定场景，
    并且仍会用一个「不抢戏」的通用场景兜底（不会静默拿一个与内容无关的场景糊过去）。
    超时给足（队列拥堵时一张图可能排队十几分钟），已生成的图由 scene_gen 复用，重跑不重复烧图。

    🔴 每张生成后**自动取回项目 `image/scene_NN.png`**（本地没有才拷，`--force` 覆盖），
    见 `_sync_scene_to_project()`。
    """
    if a.scene_ref:
        rels = [s for s in re.split(r"[,;]", a.scene_ref) if s.strip()]
        # 场景描述按 `|` 拆开后与场景图**一一对应**；只给一条时所有段共用，
        # 一条都不给则该段不带场景描述（靠参考图本身说话）。
        descs = [d.strip() for d in (a.scene_desc or "").split("|") if d.strip()]
        if len(descs) == 1:
            descs = descs * len(rels)
        elif descs and len(descs) != len(rels):
            print("[dh] ⚠️ 场景描述 %d 条 / 场景图 %d 张，按较短的一边循环取用"
                  % (len(descs), len(rels)), flush=True)
            descs = [descs[i % len(descs)] for i in range(len(rels))]
        print("[dh] 用现成场景图 %d 张（不生成）" % len(rels), flush=True)
        return rels, (descs or [""] * len(rels))
    if n <= 0:
        return [], []
    if not a.scene_desc:
        print("[dh] 没给 --scene-desc：场景按**整篇文案意境**自由决定（任何地方都可以，"
              "只要贴合内容、彼此不同、氛围统一）", flush=True)
        if gist:
            print("[dh] 全文意境依据：%s" % gist[:100], flush=True)
        else:
            print("[dh] ⚠️ 没有文案可依据，场景只能用中性兜底；给 --script 或 --scene-desc 更贴内容", flush=True)
    descs = [d.strip() for d in (a.scene_desc or "").split("|") if d.strip()]
    prompts = _scene_prompts(descs, n, gist=gist)
    role = None if a.mode == "multiref" else (a.role or "woman")
    rels = []
    failed = []
    for i in range(n):
        try:
            p, rel = gen_scene_image(prompts[i], role=role, w=a.width, h=a.height,
                                     tag="%s_scene%d" % (tag, i + 1), subdir=a.scene_subdir,
                                     num=i, timeout=a.scene_timeout, force=a.force)
        except Exception as e:
            # 单张失败**不中断整批**：队列里若有一个任务卡住，等它超时会把后面全部拖死。
            # 这里记下失败的序号继续往下，收尾统一提示；重跑时已成功的图直接复用。
            print("[scene] ⚠️ 第 %d/%d 张失败：%s" % (i + 1, n, e), flush=True)
            failed.append(i + 1)
            rels.append(None)
            continue
        print("[dh] 场景图 %d/%d -> %s | %s" % (i + 1, n, rel, prompts[i][:40]), flush=True)
        _sync_scene_to_project(a, p, i + 1)
        rels.append(rel)
    if failed:
        print("[scene] ⚠️ 共 %d 张没出来：%s。再跑一次只补这几张（已成功的复用缓存）"
              % (len(failed), "、".join(str(x) for x in failed)), flush=True)
    return rels, prompts


# ---------- 项目目录 ----------
def open_project(a):
    """返回 (项目名, 视频目录, doc 目录, 项目根)。"""
    name = (a.project or "").strip()
    if not name:
        d = os.path.abspath(a.out_dir)
        os.makedirs(d, exist_ok=True)
        return "", d, d, d
    import output_layout as ol
    L = ol.layout(name)
    # 交付物扁平化：视频/字幕/音频/清单一律放**项目根目录**，不建 video/ music/ doc/ 分层，
    # 方便一眼看全；图片（场景图/四视图）仍走 fxai_cache 与 image/。
    root = L["root"]
    print("[dh] 项目目录（交付物在根目录）：%s" % root, flush=True)
    return name, root, root, root


def _video_item(root, r):
    v = r.get("video") or ""
    return {"index": r.get("index"), "type": "video",
            "audio": _rel(root, r["audio"]) if r.get("audio") else "",
            "file": _rel(root, v), "path": os.path.abspath(v) if v else "",
            "seconds": r.get("seconds"),
            "duration": _fmt_sec(r.get("seconds")),
            "size_kb": round(os.path.getsize(v) / 1024.0, 1) if v and os.path.isfile(v) else None,
            "server_rel": r.get("rel") or "", "reused": bool(r.get("skipped"))}


def write_delivery(name, root, doc_dir, items, joined, mode, auds):
    """写 doc/视频清单（delivery.video.json + 视频清单.md），并回填 delivery.json 的 video 段。"""
    os.makedirs(doc_dir, exist_ok=True)
    total = sum(float(i.get("seconds") or 0) for i in items)
    doc = {"project": name, "project_root": os.path.abspath(root), "generated_at": _now(),
           "mode": mode, "audio_count": len(auds),
           "summary": {"count": len(items), "total_seconds": round(total, 2),
                       "total_duration": _fmt_sec(total),
                       "merged_file": _rel(root, joined) if joined else ""},
           "items": items}
    vp = os.path.join(doc_dir, "delivery.video.json")
    with open(vp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)

    # 回填音频阶段写的 delivery.json（把 video 占位换成真实清单）
    dp = os.path.join(doc_dir, "delivery.json")
    if os.path.isfile(dp):
        try:
            with open(dp, "r", encoding="utf-8") as f:
                base = json.load(f)
            base["video"] = {"dir": "video/", "count": len(items), "items": items,
                             "mode": mode,
                             "merged_file": _rel(root, joined) if joined else "",
                             "note": "由 fxai-digital-human-video 生成"}
            with open(dp, "w", encoding="utf-8") as f:
                json.dump(base, f, ensure_ascii=False, indent=2)
            print("[dh] 已回填 %s 的 video 段" % dp, flush=True)
        except Exception as e:
            print("[dh] 回填 delivery.json 失败（视频本身已生成）：", e)

    mp = os.path.join(doc_dir, "视频清单.md")
    lines = ["# 数字人口播视频清单 —— %s" % (name or "口播"), "",
             "- 生成时间：%s" % doc["generated_at"],
             "- 模式：%s（%s）" % (mode, {"single": "单场景", "multi": "多场景",
                                        "multiref": "多参考"}[mode]),
             "- 音频：%d 条" % len(auds),
             "- 视频：%d 条，合计 %s（%.2f 秒）" % (len(items), _fmt_sec(total), total), ""]
    if joined:
        lines.append("- 已拼成一条：%s" % _rel(root, joined))
    lines += ["", "| # | 音频 | 视频 | 时长 | 大小 | 说明 |", "|---|------|------|------|------|------|"]
    for i in items:
        lines.append("| %s | %s | %s | %s | %s KB | %s |"
                     % (i["index"], i["audio"], i["file"], i["duration"],
                        i["size_kb"] if i["size_kb"] is not None else "-",
                        "（已有产物，复用）" if i["reused"] else ""))
    with open(mp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("[dh] 视频清单：%s / %s" % (vp, mp), flush=True)
    return vp


# ---------- 音频模式：合并 / 多音频 ----------
def _merge_audio(a, auds, root):
    """合并模式：把多条音频合成一条完整音频，落 <项目根>/music/<name>_完整.wav。"""
    music_dir = root
    os.makedirs(music_dir, exist_ok=True)
    dst = os.path.join(music_dir, "口播音频.wav")
    return av.concat_audios(auds, dst)


def _run_multi_audio(a, auds, size, video_dir, doc_dir, note, seg_prompts):
    """多音频模式：每条音频单独生成一条视频，--concat 再拼。

    返回 (results, joined, src_auds)。注意：逐段单独生成会让 H3 截断公式在每段边界错位，
    所以这不是单人单口播的默认路径（默认走合并模式）。
    """
    # H3 分段：每条音频内部还会被切成 9.41667 s 一段，提示词行数/场景图按这个段数来
    segs_per_audio = audio_segs(auds, a.avg_seg)
    n_total = max([len(b) for b in segs_per_audio] or [0]) or len(auds)
    print("[dh] 逐段渲染单元共 %d 个" % n_total, flush=True)

    osz = out_size(a, size)
    a.project_tag = _project_tag(a, root)
    # 场景图数量：与合并模式同一条时间规则（多参考至少 2 个不同场景、上限=段数）
    total_dur = sum(av.audio_duration(p) or 0.0 for p in auds) or 0.0
    n_scene = scene_count_for(total_dur, n_total, a.scene_count, a.mode)
    print("[dh] 场景图 %d 张（时间规则：总时长 %.1fs / 共 %d 段）"
          % (n_scene, total_dur, n_total), flush=True)
    scene_rels, scene_descs = make_scenes(a, n_scene, a.project_tag + "_" + a.name, note=note,
                                          gist=script_gist(a.script))

    # 没给 --seg-prompts 时按文案自动生成逐段提示词：第 i 段 ↔ 第 i 段文案
    if not seg_prompts and (a.script or note):
        texts = script_segments(a.script, n_total) if a.script else []
        seg_prompts = build_seg_prompts(a, scene_descs, texts, n_total)
        if seg_prompts:
            print("[dh] 已按文案自动生成 %d 条逐段提示词" % len(seg_prompts), flush=True)

    kw = dict(size=size, timeout=a.timeout, char_desc=a.char_desc, kind="speak",
              prefix=av.PREFIX_SPOKEN, audio_subdir="dh_audio", model=a.model,
              prompt_out=os.path.join(doc_dir, "口播提交提示词.json"),
              start_index=a.start_index, end_index=a.end_index,
              multiref_landscape=a.multiref_landscape)

    if a.mode == "multiref":
        kw.update(char_rel=[av.REF_WOMAN] if a.role in ("woman", "女", "女性") else
                  ([av.REF_MAN] if a.role in ("man", "男", "男性") else [a.role]),
                  scene_ref=scene_rels, scene_desc=scene_descs or None, seg_prompts=seg_prompts)
    elif a.mode == "multi":
        kw.update(scene_ref=a.scene_subdir, seg_prompts=seg_prompts)
    else:  # single：一张含人物场景图
        kw.update(char_rel=None, scene_ref=scene_rels[0] if scene_rels else None,
                  seg_prompts=seg_prompts)

    results = av.gen_videos(auds, a.mode, name=a.name, out_dir=video_dir,
                            skip_existing=not a.force, **kw)

    joined = None
    if a.concat and len(results) > 1:
        tone_kw = {"seam": a.seam, "mode": "增益" if a.tone else "不启用",
                   "ref": a.tone_ref or None, "strength": a.tone_strength,
                   "max_offset": a.seam_offset}
        joined = av.concat_videos([r["video"] for r in results],
                                  os.path.join(video_dir, "合并_%dx%d.mp4" % (osz[0], osz[1])),
                                  tone=(tone_kw if (a.seam or a.tone) else None))
        print("[dh] 完整口播：%s（%s）" % (joined, _fmt_sec(av.probe_duration(joined))), flush=True)
    return results, joined, auds


# ---------- 烧录字幕（交付物 #5 #6）----------
def _burn_subtitles(video, video_dir, size, lyrics=None):
    """烧录字幕：抽音轨→whisper→ video/字幕.ass/.srt（无分辨率后缀），
    再用带 libass 的 ffmpeg 烧成 video/成片_{W}x{H}.mp4（-c:a copy 保留原音频）。"""
    import subprocess
    script = os.path.join(_SKILLS, "fxai-lyric-subtitle", "scripts", "gen_lyric_subtitle.py")
    sub = "字幕"
    cmd = [sys.executable, script, "--video", video, "--out-dir", video_dir,
           "--out-name", sub, "--no-burn"]
    if lyrics and os.path.isfile(lyrics):
        # --script 是带情感标签的 TTS 脚本；字幕只显示念出来的正文，
        # 所以先剥掉 `用…的语气说<|endofprompt|>。` 这段标签再交给对齐器。
        plain = os.path.join(video_dir, "_字幕正文.txt")
        with open(lyrics, encoding="utf-8") as f:
            blocks = [b.strip() for b in re.split(r"\n\s*\n", f.read()) if b.strip()]
        with open(plain, "w", encoding="utf-8") as f:
            for b in blocks:
                f.write(fxc.parse_line(b)[1].strip() + "\n")
        cmd += ["--lyrics", plain]
    subprocess.run(cmd, check=True)
    # ★ 字幕只显示念出来的**正文**。出现情感标签或 <|endofprompt|> 就是错的，当场报错，
    #   不让带标签的字幕落盘（错字幕比没字幕更糟）。
    for ext in (".srt", ".ass"):
        fp = os.path.join(video_dir, sub + ext)
        with open(fp, encoding="utf-8") as f:
            body = f.read()
        if fxc.MARK in body or "endofprompt" in body or "的语气说" in body:
            raise SystemExit("[dh] 字幕里出现情感标签（%s），字幕必须只用正文；"
                             "请检查 --lyrics 传入的是无标签正文" % fp)
    ass = os.path.join(video_dir, sub + ".ass")
    out = os.path.join(video_dir, "成片_%dx%d.mp4" % (size[0], size[1]))
    subprocess.run([av.ffmpeg_libass(), "-y", "-i", video,
                    "-vf", "subtitles=filename=%s" % os.path.basename(ass)]
                   + _venc.delivery_venc(audio_copy=True)
                   + [os.path.basename(out)], cwd=video_dir, check=True)
    return out, ass


# ---------- 主流程 ----------
def build_parser():
    ap = argparse.ArgumentParser(
        description="数字人口播视频生成（音频 → MiniMax 三模式视频，与 MV 同一套实现）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="用法：\n"
               "  # 单人单口播（默认合并模式）：多段音频先合并成一条，再一次性生成整条口播\n"
               "  python dh_video.py --project P --audio P/music/口播01.wav --audio P/music/口播02.wav --scene '书房，暖光'\n"
               "  # 多参考（四视图人物图 + 场景图），只重渲第 3 段\n"
               "  python dh_video.py --project P --audio-dir P/music --mode multiref --start-index 2 --end-index 2\n"
               "  # 多音频模式：每条音频单独生成一条视频再拼接\n"
               "  python dh_video.py --project P --script P/doc/口播文案.txt --multi-audio --concat\n")
    ap.add_argument("--project", help="项目名：成片落 <项目根>/<项目名>/video/，清单落 doc/")
    ap.add_argument("--audio", action="append", help="口播音频（本地路径或服务端相对路径）；可给多条 = 多段")
    ap.add_argument("--audio-dir", help="音频目录：按序号排序逐段生成")
    ap.add_argument("--script", help="口播文案（按同名序号去 music/ 找各段音频，多音频模式）")
    ap.add_argument("--mode", default="multiref", choices=["single", "multi", "multiref"],
                    help="single 单场景 / multi 多场景 / multiref 多参考（默认 multiref）")
    ap.add_argument("--role", default="woman", help="人物参考：woman|man|自定义相对路径（single/multi 把人物画进场景）")
    ap.add_argument("--char-desc", help="人物穿搭外貌描述（multiref 写进提示词）")
    ap.add_argument("--scene-desc", help="场景描述；多场景用 | 分隔（如 '书房，暖光|阳台，晨光'）")
    ap.add_argument("--scene-ref", help="用现成场景图（相对路径，多张逗号分隔），给了就不生成场景图")
    ap.add_argument("--scene-count", type=int, default=0,
                    help="生成几张场景图（默认 0 = 按时间规则自动算：≤120s 每段一张、>120s 段数//2+1，"
                         "且 multiref 至少 2 个不同场景、上限=段数；一般不用手工指定）")
    ap.add_argument("--avg-seg", type=float, default=10.0,
                    help="H3「平均分段时长」入参（默认 10，实际每段 9.41667 秒；一般不用改）")
    ap.add_argument("--scene-subdir", default="dh_scene", help="场景图服务端保存子目录")
    ap.add_argument("--charview", dest="charview", action="store_true", default=True,
                    help="multiref 先生成角色四视图作人物图（默认开，与 MV 同规则）")
    ap.add_argument("--no-charview", dest="charview", action="store_false",
                    help="跳过四视图，直接用全局头像库作人物图")
    ap.add_argument("--charview-subdir", default="dh_charview", help="四视图服务端保存子目录")
    ap.add_argument("--charview-timeout", type=int, default=3600, help="四视图生成超时秒（默认 3600）")
    ap.add_argument("--seg-prompts", help="逐段运镜提示词文件（空行分段，一段一行；给了就按它逐段命中）")
    ap.add_argument("--size", default="portrait_9x16",
                    choices=["portrait_9x16", "portrait_3x4", "landscape_3x4"],
                    help="single/multi 模式尺寸：portrait_9x16 竖屏 1080x1920（默认）/ portrait_3x4 / landscape_3x4；multiref 不取此值")
    ap.add_argument("--multiref-landscape", action="store_true",
                    help="multiref 输出横屏 1280×768（默认竖屏 768×1280，口播一般是竖版；"
                         "要横版才加这个开关）")
    ap.add_argument("--multi-audio", action="store_true",
                    help="多音频模式：每条音频单独生成一条视频再拼接（默认关；单人单口播请用合并模式，"
                         "先合并成完整音频再一次性生成，否则 H3 截断公式会在每段边界错位）")
    ap.add_argument("--width", type=int, default=1080, help="场景图宽度（默认 1080）")
    ap.add_argument("--height", type=int, default=1920, help="场景图高度（默认 1920）")
    ap.add_argument("--name", default="口播", help="视频文件名前缀（多段自动补 _01 _02）")
    ap.add_argument("--concat", action="store_true", help="多段视频按顺序拼成一条完整口播")
    ap.add_argument("--seam", dest="seam", action="store_true", default=True,
                    help="拼接时做接缝色彩对齐（默认开）：把每段首帧贴到上一段末帧，"
                         "消掉段间跳色。分段生成必然跳色——模型没有跨段记忆，"
                         "实测接缝处掉 1.6~2.0 灰阶，是正常帧间变化的近 3 倍")
    ap.add_argument("--no-seam", dest="seam", action="store_false", help="关闭接缝对齐（段间差超 30 灰阶会自动不接）")
    ap.add_argument("--tone", action="store_true",
                    help="拼接时额外做影调对齐（乘法增益，把每段背景拉回参考色阶，修「发闷」）")
    ap.add_argument("--tone-ref", default="", help="影调参考片（源片）；不给就以第一段为锚")
    ap.add_argument("--tone-strength", type=float, default=1.0, help="影调强度 0-1（默认 1）")
    ap.add_argument("--seam-offset", type=float, default=12.0, help="接缝单通道最大偏移灰阶（默认 12）")
    ap.add_argument("--burn", action="store_true", help="生成后自动烧录字幕：video/字幕.ass → video/成片_{W}x{H}.mp4")
    ap.add_argument("--timeout", type=int, default=7200, help="单段渲染超时秒（默认 7200）")
    ap.add_argument("--scene-timeout", type=int, default=1200,
                    help="单张场景图超时秒（默认 1200；超时只跳过这一张，其余继续，重跑补缺的）")
    ap.add_argument("--model", help="视频工作流模型（默认取全局配置，单模型 minimax 一般不用给）")
    ap.add_argument("--start-index", type=int, help="multiref 单段重渲：起始段索引（0 起）")
    ap.add_argument("--end-index", type=int, help="multiref 单段重渲：结束段索引（与 start 相同 = 只重该段）")
    ap.add_argument("--out-dir", default=".", help="不建项目目录时的视频输出目录（默认当前目录）")
    ap.add_argument("--force", action="store_true", help="已有该段视频也重渲（默认跳过）")
    ap.add_argument("--scene-only", action="store_true",
                    help="只出场景图、不渲染视频：先把 12 张图看对了再跑正片，返工不浪费 GPU")
    ap.add_argument("--storyboard", help="分镜表 JSON（模型设计，见 references/09）。"
                                        "给了就逐段按表里的景别/运镜/动作出提示词")
    ap.add_argument("--no-local", action="store_true",
                    help="跳过本机探测，强制走线上容器（不碰本机盘、不烧本机 GPU）")
    ap.add_argument("--json", action="store_true", help="末尾输出 JSON（供其它技能解析）")
    return ap


# 逐段运镜库（口播）：多条备选按段取用（i % len）。
# 🔴 H3 multiref 把场景背景**逐段重算**——一旦运镜去「平移/抬升/环绕」，背景就被反复
#    重算扭曲，表现为突然闪烁/跳跃（实测穿帮）。所以**生成层**运镜只保留「固定」与
#    「极缓推近」两种，都在改焦段、不改机位角度。
#    真正的机位运动（拉远 / 横移 / 摇 / 手持呼吸感）走后期 `scripts/camera_move.py`，
#    那是在成片像素上做亚像素变换，背景一次都不用重算（见 08 册 4.4）。
SPEAK_CAMERAS = (
    "镜头保持完全固定的中景，构图稳定不晃，背景完全静止",
    "镜头极缓慢地轻微推近，景别仍保持中景，幅度极小、速度极慢，背景保持完全静止",
    "镜头从中景极缓推近一点点后停定，落幅比起幅稍紧，全程画面稳定不摇晃",
    "镜头保持固定中景，机位略偏人物侧前方，画面稳定，背景静止",
    "镜头极缓慢地极小幅轻微推近，推到位后保持不动，背景环境完全静止",
    "镜头固定中景，构图稳定，人物在画面中略偏一侧，背景静止",
)

# 肢体语言库（口播）：每段一个**具体**表演节拍（i % len）。
# 🔴 判据不是「动作小不小」，而是「换没换类型」：原地手势反复用同一套，观众看到的是
#    一个人在原地比划；位移、朝向、躯干俯仰、坐站这几类轮着来，才有活人感。
#    位移一律**慢、短、有明确终点**（08 册 4.2 位移边界），写「来回走」会被放大成晃动。
# ⚠️ 表演决策归模型，脚本只给提示词规范（重要，别往这里加动作）：
#    人物在场景里**怎么站、怎么动、什么时候走**，由模型看完场景图和提示词后自己决定。
#    脚本的职责只有一件事：给出规范（要有自然手势 / 重心会换 / 脸始终朝镜头 / 可以走位）。
#    多条目轮换等于用脚本替模型做表演决策，而且「侧身 / 转身」类条目会让口播变成侧脸念稿。
#    → 只留**一条中性规范**。具体动作写在分镜表 `motion` / `action` 里。
SPEAK_ACTS = (
    "她站在画面中边讲边做自然的解释手势，幅度与快慢跟着语气走，"
    "重心在两腿之间自然转移，面部始终朝向镜头。",
)

# 开场镜专用动作（第 0 段）：入场必须有位移，
# 一上来就站定念稿，观众的第一印象就是「这个人会不会动」。
# 只留「走进来」这一类——不允许任何带朝向变化的入场（侧身转进画面会在首帧就侧脸）。
ENTRY_ACTS = (
    "她一边开口讲，一边从画面外侧缓步走入，走到画面中略偏中心的位置停住，随即抬手向镜头示意一下。",
    "她一边开口讲，一边从画面深处的远处朝镜头走来，起幅是全身入画，"
    "走近到腰部以上的中景距离停住，边走边讲。",
)

# 场景互动动作库：**只在完全没给分镜表**时兜底，按场景描述里最先出现的实物匹配。
# 原则同上：只写「用手边的东西」，不写「转到哪一侧、面向哪里」——朝向一律交给模型，
# 且一律正对镜头。所以这里全部是「抬手示意 / 走近半步 / 低头看一眼」这类不转身的动作。
SCENE_INTERACTIONS = (
    (("显示器", "屏幕", "双屏", "笔电", "笔记本", "电脑"),
     ("她抬手指向身侧的屏幕，边讲边在画面上虚点两下，手收回身前继续讲。",
      "她抬手顺着身侧屏幕的界面划一条线，然后收回手势对镜头解释。",
      "她朝身侧的屏幕走近半步，抬手指着屏上的位置讲，视线回到镜头。")),
    (("白板",),
     ("她抬手指着身侧白板上的流程图讲，手指沿途点过两个节点后收回，视线回到镜头继续讲。",
      "她朝白板走近半步停下，抬手在板面上虚画一笔，手收回身前对镜头讲。")),
    (("操作台", "主机", "线缆", "机柜", "指示灯", "螺丝刀", "设备"),
     ("她抬手朝身侧的设备示意，指了指指示灯的位置，手收回身前继续讲。",
      "她朝操作台走近半步，抬手在台面上方虚点两下，直起身对镜头说明。")),
    (("手册", "书架", "本子", "书"),
     ("她抬手从身侧拿起一本手册翻开举到胸前示意，合上后继续对镜头讲。",
      "她朝书架走近半步，抬手点着架上一排手册逐一点过，视线回到镜头。")),
    (("便签", "板墙", "留言板"),
     ("她抬手指向身侧的便签墙，点着其中一张讲，手收回身前。",
      "她朝便签墙走近半步停下，抬手揭下一张便签举到胸前示意。")),
    (("窗", "城市", "绿植", "天际线"),
     ("她抬手朝身侧的窗示意一下，指尖朝外点一点，随即收回手势对镜头讲。",
      "她朝窗边走近半步，抬手朝窗外一比，视线回到镜头继续讲。")),
)


def pick_act(scene_desc, i):
    """按场景里**最先出现**的实物选互动动作。

    按位置匹配而不是按固定顺序：一段里常同时出现「窗」「笔电」「书架」，
    谁写在最前面就是这段的主体，动作就跟谁走（「身后是窗与城市天际线…左侧有笔电」
    该指窗外，不该指屏幕）。场景里没有可指认的东西时才退回通用比划。
    """
    d = scene_desc or ""
    best, best_pos = None, len(d) + 1
    for keys, acts in SCENE_INTERACTIONS:
        for k in keys:
            p = d.find(k)
            if 0 <= p < best_pos:
                best, best_pos = acts, p
    return best[i % len(best)] if best else SPEAK_ACTS[i % len(SPEAK_ACTS)]


def _project_tag(a, root):
    """缓存/服务端命名前缀：必须含项目名，否则不同项目同名 tag 会互相复用产物。"""
    name = (getattr(a, "project", "") or "").strip()
    if not name:
        name = os.path.basename(os.path.abspath(a.out_dir or root or ".")) or "口播"
    return re.sub(r"[^\w\-一-鿿]+", "_", name)[:40]


def out_size(a, size):
    """**视频真实输出尺寸**——交付文件名（合并_/成片_）一律用它；single/multi 的 size 只用于传给工作流。

    multiref 走固定标准尺寸：竖屏 768×1280（默认）/ 横屏 1280×768；
    single/multi 才用传入的 size（默认竖屏 1080×1920）。
    """
    if a.mode == "multiref":
        return av.MULTIREF_SIZE_LANDSCAPE if a.multiref_landscape else av.MULTIREF_SIZE
    return size


def load_storyboard(path):
    """读模型设计的分镜表。表在 → 逐段用表里的景别/运镜/动作；表不在 → 退回通用轮换。"""
    if not path:
        return None
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        print("[storyboard] ⚠️ 读取失败，退回通用轮换：%s" % e, flush=True)
        return None
    shots = d.get("shots") or []
    if not shots:
        print("[storyboard] ⚠️ 表里没有 shots，退回通用轮换", flush=True)
        return None
    print("[storyboard] 已载入 %d 镜（模型设计）" % len(shots), flush=True)
    return shots


_AUDIT_BAD = ("点头", "抬头", "低头", "转头", "回头", "歪头", "仰头", "摇头",
              "探头", "扭头", "颔首", "扬起下巴", "甩头", "偏头", "点一下头",
              "表情灵动", "随内容变化", "自然切换", "肩颈", "颈肩", "呼吸有轻微起伏",
              # 身体转向（2026-10-21 事故）：模型只执行「转出去」，不执行「转回来」，
              # 成片出现侧脸口播。口播只允许走近/走远/左右横移，脸始终朝镜头。
              "侧身", "侧转", "转身", "转体", "背对", "背朝", "面向屏幕", "朝向屏幕",
              # 躯干倾斜 → 被模型翻译成点头/仰头（2026-10-21 第二轮事故）：
              # 「上身前压一点」「上身后仰」「肩线高低错开」「肩头略收」在腰以上构图里，
              # 最直接的视觉表现就是头和肩一起上下动 —— 用户看到的就是"还在点头"。
              # 躯干只写「换重心 / 站稳」，绝不写前后倾与肩线高低。
              "上身前倾", "上身向前", "上身后仰", "上身后靠", "上身轻轻前压",
              "肩线", "肩头", "耸肩", "缩脖",
              # 冻结词 → 人被定住，连嘴也不动（"根本不开口"的直接嫌疑）
              "停住不动", "定住不动", "僵住", "一动不动")


def _audit_prompts(prompts):
    """渲染前审计：提示词里若还留着头部动作 / 面部节拍，**烧 GPU 之前**就报出来。

    词表以 `scripts/preflight.py` 为权威（那边可扩展、还有白名单项），
    这里是最后一道哨：分镜写回旧写法、公共模板被改回去，都能在渲染前发现。
    2026-10-21 的「人物呆板」事故就是提示词里带着「表情灵动随内容变化 /
    肩颈随呼吸起伏」跑完整片，跑了 70 分钟才发现。
    """
    hits = []
    for i, p in enumerate(prompts, 1):
        w = [x for x in _AUDIT_BAD if x in p]
        if w:
            hits.append("第%d段 %s" % (i, "/".join(w)))
    if hits:
        print("[prompt] ⚠️ 提示词里仍有头部动作/面部节拍：%s" % "；".join(hits[:4]), flush=True)
        print("[prompt]    这些词会让头一直动；先跑 scripts/preflight.py 定位再渲染", flush=True)
    else:
        print("[prompt] ✓ 提示词无头部动作/面部节拍（%d 段）" % len(prompts), flush=True)


_TURN_BAD = ("侧身", "侧转", "转身", "转体", "背对", "背朝", "回身", "侧过身",
             "转回正面", "面向屏幕", "朝向屏幕", "侧面环境", "面向侧面")
# 分句被删空时的兜底：一句绝对安全的原地讲解
_ACT_SAFE = ("她站在画面中自然讲解，双手随话语做解释手势，"
             "重心在两腿之间自然转移，面部始终朝向镜头。")


def _strip_turn(act, idx=None):
    """删掉动作描述里带「转向 / 侧身」的**整个分句**。

    为什么要整句删而不是改写：H3 不理解「转出去再转回来」这种往返指令，
    只按最后一个姿态定格 —— 实测第 7 镜写「身体侧转约三十度面向屏幕方向，
    停稳后上半身转回面向镜头」，成片里就是约 1.5 秒侧脸口播，嘴型对不上观众。
    这类描述**整句删除，换说法同样违规**。
    位移留给「走近 / 走远 / 左右横移」，姿态一律正对镜头。
    """
    if not act:
        return act
    segs = re.split(r"(?<=[；;，,。])", str(act))
    keep = [s for s in segs if s.strip() and not any(w in s for w in _TURN_BAD)]
    dropped = [s.strip() for s in segs if s.strip() and any(w in s for w in _TURN_BAD)]
    out = re.sub(r"[；;，,]{2,}", "；", "".join(keep)).strip()
    if dropped and idx is not None:
        print("[storyboard] 第 %s 镜删掉转向描写：%s" % (idx, " / ".join(dropped)[:60]),
              flush=True)
    if len(out.strip("；;，,。 ")) < 4:
        if idx is not None:
            print("[storyboard] 第 %s 镜动作删空，改用安全兜底" % idx, flush=True)
        return _ACT_SAFE
    return out


# 「在说话」的判定词：入场句 / 走位句里没有这些，就说明这一镜把时间轴全给了位移
_SPEAK_WORDS = ("讲", "说", "开口", "口型", "嘴部", "嘴")


def _ensure_speaking(s, variant="move"):
    """🔴 入画即开口：入场 / 走位句必须自带「在说话」。

    音频是整段 `fully_copy` 的，画面第一帧声音就已经在响。写成纯位移
    （`从画面外侧走入，走到桌前站定`）模型就演成走进来、站好、再开口——
    实测开场两三秒嘴是闭的。这里在句子末尾补一句说话状态，并补上标点
    （自定义 entry 常不带标点，会和后面的动作链粘连成一串）。

    🔴 `variant` 决定补哪一句，**入场句与位移句必须补不同的措辞**：
    一镜的 entry 与 motion 会拼进同一条动作链，两处补同一串＝重复指令
    （权重摊薄、第二句被当成独立动作），会被 preflight ★11 直接拦下。
      - variant="entry" → 「一路开口讲着走入」
      - variant="move"  → 「口型逐字跟随音频变化」（后退句用「嘴型随话语持续开合」）
    """
    s = (s or "").strip()
    if not s:
        return s
    if any(w in s for w in _SPEAK_WORDS):
        # 已经写了说话，只补标点防粘连
        return s if s.endswith(("。", "；", "！", "？")) else s + "；"
    s = s.rstrip("；;，,。！？!? ")
    if variant == "entry":
        return "%s，一路开口讲着走入；" % s
    if any(w in s for w in ("后退", "向后", "退到", "退开")):
        return "%s，嘴型随话语持续开合；" % s
    # 措辞避开公共模板里的「口型逐字跟随 <Audio 1> 中的说话内容变化」，
    # 否则补进去的句子又和模板重复一遍（同一件事写两遍）。
    return "%s，唇形随话语持续开合；" % s


def _tune_prompt(p, shot_kind, expr):
    """按这一镜的主动作类型收束提示词（只动提示词字符串，不动工作流）。

    - kind="move"：这是**走位镜**，人物这一镜的主任务是「在场景里移动」。
      公共动作链里的「双手跟着说话自然动」和「身体缓慢移动、换重心、侧身」
      必须收掉：手势与位移同时给，模型只会给一个「小幅站位偏移」的中间解
      （08 册 4.2.1 实测）。稳定规则里「有强调的大手势」那半句同理要换掉。
    - expr 给了就把通用占位「表情自然专注」换成这一镜自己的**静态**表情
      （面带微笑 / 眼睛看向镜头 这类，不写动作节拍）。
    """
    core = av._NARR["speak"]["action_core"]
    if shot_kind == "move":
        # 🔴 2026-10-21：旧版这里只留「嘴唇与 <Audio 1> 精准同步开合」一句，
        # 强度太弱（等于「对齐一下」而不是「一直在说」），4 个走位镜出片后
        # 人物几乎不动嘴 —— 用户原话「有些场景里面他根本都不开口了」。
        # 走位镜拿掉的是手势，**不是说话**：讲话指令必须和手势镜同等强度。
        core_move = ("从第一个字到最后一个字持续讲话，嘴部全程保持明显开合，"
                     "口型逐字跟随 <Audio 1> 中的说话内容变化，语速与音频完全一致；")
        p = p.replace(core, core_move)
        p = p.replace(
            "人物动作连贯自然，幅度有大有小（有强调的大手势，也有放松的小动作），",
            "人物移动连续、匀速、幅度稳定，")
    if expr:
        p = p.replace("表情自然专注", expr)
    return p


def build_seg_prompts(a, scene_descs, texts, n_total):
    """按文案自动生成逐段提示词：第 i 段 ↔ 第 i 段文案，场景/道具跟着内容走。

    - multiref → Ref2VA 六段式（av.build_ref2va_prompt）
    - single / multi → FL2VA 三段式（av.build_fl2va_prompt + av.fl2va_body）

    **给了 `--storyboard` 就照分镜表走**：景别、运镜、人物动作全部取表里的，
    创意决策交给模型，代码只拼提示词。表不在时才用固定轮换兜底。
    """
    if n_total <= 0:
        return []
    shots = load_storyboard(getattr(a, "storyboard", None))
    pool = scene_descs or [""]
    n_pool = len(pool)
    out = []
    for i in range(n_total):
        sh = shots[i] if (shots and i < len(shots)) else None
        t = texts[i] if (texts and i < len(texts)) else None
        # 分镜表自带这一镜的文案，优先级最高：它是按 H3 分段对齐过的，
        # 而按文案句子逐条取用会在「句子数 > 分段数」时丢掉尾部内容。
        if sh and sh.get("text"):
            t = {"text": sh["text"]}
        # 第 i 段用第 (i % n_pool) 张场景图的描述——场景图有多张，提示词必须对应上，
        # 否则生成 N 张图却每段都写第 1 张，等于白生成。
        base_scene = pool[i % n_pool] or ""
        # 🔴 场景描述**只写环境**，台词不往里拼。
        # 旧实现是 `scene = 场景 + "；她正在讲：<台词>"`，而 scene 落进 <Subject 2>
        # （场景背景图）的定义 → 模型读到「S1 没台词、S2 在讲」，与动作链里的
        # 「持续讲话」指令打架，实测成片嘴是闭的。台词走 lines 挂回 <Subject 1>。
        scene = base_scene
        line_txt = (t["text"].rstrip("。！？!?") if t else None)
        if a.mode == "multiref":
            entry = None
            shot_kind = "gesture"
            expr = None
            if sh:
                camera = sh.get("camera") or SPEAK_CAMERAS[i % len(SPEAK_CAMERAS)]
                size = sh.get("size") or "中景"
                entry = sh.get("entry") or (ENTRY_ACTS[0] if i == 0 else None)
                shot_kind = sh.get("kind") or "gesture"
                expr = sh.get("expression")
                # 🔴 分镜表里的 `motion` 是**身体轨迹**（09 册要求每镜必写）。
                # 旧实现只读 `action`，motion 写了也进不了提示词 → 人物躯干与脚
                # 全程不动，只剩头和手在动，观感极机械（实测）。
                # 这里按 `kind` 二选一：一镜只做一种主动作。
                #   kind="move"    → 只给 motion（走位），不叠开放手势，
                #                    否则模型会在「自由手势」和「定向位移」之间取中间解；
                #   kind="gesture" → motion 在前（姿态/朝向）、手势在后。
                motion = (sh.get("motion") or "").strip()
                action = (sh.get("action") or "").strip()
                if shot_kind == "move":
                    # 🔴 走位镜的位移句必须自带「讲」：纯位移（走到…/退到…站定）会把整段
                    # 时间轴占满，模型按「先移动到位、再开口」演 —— 实测后退镜退着退着
                    # 就不说了。位移句排在说话句之后，且必须自己再点一次「边走/边退边讲」。
                    motion = _ensure_speaking(motion)
                    act = motion or action or SPEAK_ACTS[i % len(SPEAK_ACTS)]
                else:
                    act = "；".join([x for x in (motion, action) if x]) \
                        or SPEAK_ACTS[i % len(SPEAK_ACTS)]
            else:
                camera = SPEAK_CAMERAS[i % len(SPEAK_CAMERAS)]
                act = pick_act(base_scene, i)
                size = "中景"
                # 开场镜必须有入场动作：第一印象决定观众觉得这个人「会不会动」
                entry = ENTRY_ACTS[0] if i == 0 else None
            # 转向清洗：内置动作库 / 分镜表里残留的「侧身 / 转身」一律按分句删掉。
            # 放在这里而不是改字符串常量，是为了连兜底库和被复用的旧分镜一起兜住。
            act = _strip_turn(act, i + 1)
            if entry and any(w in str(entry) for w in _TURN_BAD):
                entry = _strip_turn(entry) or None
                if not entry or len(entry.strip("；;，,。 ")) < 4:
                    entry = ENTRY_ACTS[0]
            # 🔴 入场句自带「讲」+ 补标点：分镜表里写的 entry 常常是纯位移且不带句末标点，
            # 不带标点会与后面的动作链粘连成一串（「站定从第一个字…」），语义权重被稀释。
            if entry:
                entry = _ensure_speaking(entry, variant="entry")
            if act and not act.endswith(("。", "；", "！", "？")):
                act += "。"
            # 走位镜必须看得见腿：中景只到腰，走位物理上不可见（02 册「走位三件套」）。
            # 这里做程序兜底，不靠人记 —— 判据同 scripts/preflight.py 的 ★2。
            if shot_kind == "move" and not any(z in str(size) for z in ("中全景", "全景", "远景")):
                print("[storyboard] 第 %d 镜是走位镜，景别「%s」看不见腿，已自动改用中全景"
                      % (i + 1, size), flush=True)
                size = "中全景"
            p = av.build_ref2va_prompt(
                char_descs=[a.char_desc] if a.char_desc else None,
                scene_desc=scene, kind="speak",
                camera=camera, act_extra=act, act_mode="extra",
                face_mode="front", shot_size=size, entry=entry,
                lines=line_txt)
            p = _tune_prompt(p, shot_kind, expr)
            # 自查：这一镜的 motion 必须真的出现在提示词里，否则等于没写
            probe = (motion or action or "")[:10]
            if probe and probe not in p:
                print("[prompt] ⚠ 第 %d 镜的 motion 未进提示词：%s" % (i + 1, probe),
                      flush=True)
            out.append(p)
        else:
            # FL2VA 只有一段正文、没有 Subject 结构，台词就跟着**人物描述**走，
            # 不拼进场景描述（与 multiref 分支同一个规矩：台词归人，不归场景）。
            _cd = a.char_desc
            if line_txt:
                _cd = "%s，她正在讲：%s" % (a.char_desc, line_txt)
            body = av.fl2va_body(_cd, scene,
                                 act=("她边说边用手比划讲解，身体自然移动、重心随讲解转移，"
                                      "面部始终朝向镜头；" if t else ""),
                                 kind="speak")
            out.append(av.build_fl2va_prompt(pics=1, duration=0, body=body, kind="speak"))
    _audit_prompts(out)
    return out


def _auto_rework(shots):
    """只改了几镜时：逐镜跑单段返修（子进程调自己），不整片重跑。

    这是「本来只改一镜，却又整片重抖 70 分钟」的**自动闸门**——
    判据是程序算出来的（`fxai-common/rework.changed_shots` 比对分镜签名），
    不是让 Agent 凭印象决定。要整片重来请显式 `--force`。
    """
    import subprocess
    argv = sys.argv[1:]
    cleaned, skip = [], False
    for x in argv:
        if skip:
            skip = False
            continue
        if x in ("--start-index", "--end-index"):
            skip = True
            continue
        if x.startswith("--start-index=") or x.startswith("--end-index="):
            continue
        cleaned.append(x)
    me = os.path.abspath(__file__)
    for n in shots:
        i = int(n) - 1
        cmd = [sys.executable, me] + cleaned + ["--start-index", str(i), "--end-index", str(i)]
        print("[rework] ▶ 返修第 %d 镜（索引 %d）" % (n, i), flush=True)
        rc = subprocess.call(cmd)
        if rc != 0:
            print("[rework] ✗ 第 %d 镜返修失败（rc=%d），停下别继续" % (n, rc), flush=True)
            return rc
    print("[rework] ✅ 单镜返修全部完成（%d 镜，其余段走服务端缓存）" % len(shots), flush=True)
    return 0


def main():
    a = build_parser().parse_args()
    auds = collect_audios(a)
    name, video_dir, doc_dir, root = open_project(a)
    size = av.SIZE[a.size]
    osz = out_size(a, size)   # 真实输出尺寸（multiref 固定竖/横，single/multi 才用 size）
    a.project_tag = _project_tag(a, root)   # 缓存/服务端命名带项目名，跨项目产物各自独立
    if a.no_local:
        av.prefer_cloud()  # 强制走线上：ComfyUI 地址切到云端容器，不碰本机
    else:
        av.prefer_local()  # 本机优先：本机 ComfyUI 探得通就不烧云端 GPU

    note = script_note(a.script)

    seg_prompts = None
    if a.seg_prompts:
        with open(a.seg_prompts, encoding="utf-8") as f:
            seg_prompts = [b.strip() for b in re.split(r"\n\s*\n", f.read()) if b.strip()]
        print("[dh] 逐段提示词 %d 条（来自 %s）" % (len(seg_prompts), a.seg_prompts), flush=True)

    # ---------- 选音频模式 ----------
    if a.multi_audio:
        # 多音频模式：每条音频单独生成一条视频，--concat 再拼
        results, joined, src_auds = _run_multi_audio(a, auds, size, video_dir, doc_dir, note, seg_prompts)
    else:
        # 合并模式（默认，单人单口播）：先合并成一条完整音频，再一次性生成。
        # 分段 / 场景提示词写作全部基于这条完整音频，与 MV 那套一致；
        # 「每条音频单独生成」会让 H3 截断公式在每段边界错位，所以走合并成一条完整音频。
        merged = _merge_audio(a, auds, root)
        merged_dur = av.audio_duration(merged) or 0.0
        # 完整音频被 H3 切成 9.41667 s 一段，提示词行数/场景图按这个段数来
        _secs, bounds = av.h3_segments(merged_dur, a.avg_seg)
        n_total = len(bounds) or 1
        print("[dh] 完整音频 %.2f 秒 → H3 切 %d 段（每段约 %.2f 秒）；逐段渲染单元共 %d 个"
              % (merged_dur, n_total, (merged_dur / n_total) if n_total else 0.0, n_total), flush=True)

        n_scene = scene_count_for(merged_dur, n_total, a.scene_count, a.mode)
        print("[dh] 场景图 %d 张（时间规则：时长 %.1fs / 共 %d 段；多参考至少 2 个不同场景、"
              "上限=段数；不逐段对齐口播内容，按整篇文案给若干不同场景）"
              % (n_scene, merged_dur, n_total), flush=True)
        scene_rels, scene_descs = make_scenes(a, n_scene, a.project_tag + "_" + a.name, note=note,
                                              gist=script_gist(a.script))
        if a.scene_only:
            ok = [r for r in scene_rels if r]
            print("[dh] --scene-only：场景图 %d/%d 就绪，本次不渲染视频。"
                  "确认画面对了再跑一遍（去掉 --scene-only，场景图直接复用）"
                  % (len(ok), len(scene_rels)), flush=True)
            return 0 if len(ok) == len(scene_rels) else 2

        # 没给 --seg-prompts 时按文案自动生成逐段提示词：第 i 段 ↔ 第 i 段文案
        # 🔴 给了 --storyboard 也必须构建：旧条件只看 `a.script or note`，
        # 于是「只传 --storyboard、没传 --script」时静默走通用模板，
        # 分镜表全部白写（景别/走位/表情一条都没进提示词，实测踩过）。
        if not seg_prompts and (a.script or note or getattr(a, "storyboard", None)):
            texts = script_segments(a.script, n_total) if a.script else []
            seg_prompts = build_seg_prompts(a, scene_descs, texts, n_total)
            if seg_prompts:
                print("[dh] 已按分镜/文案生成 %d 条逐段提示词" % len(seg_prompts), flush=True)
        elif getattr(a, "storyboard", None):
            print("[dh] ⚠ 已给 --storyboard 但未生成逐段提示词（请检查分镜表是否可读）", flush=True)

        kw = dict(size=size, timeout=a.timeout, char_desc=a.char_desc, kind="speak",
                  prefix=av.PREFIX_SPOKEN, audio_subdir="dh_audio", model=a.model,
                  prompt_out=os.path.join(doc_dir, "口播提交提示词.json"),
                  start_index=a.start_index, end_index=a.end_index,
                  multiref_landscape=a.multiref_landscape)

        # 🔴 落盘「这次是怎么跑的」（2026-10-21 补）：
        # 单镜返修（scripts/refix.py）必须**原样复现**这次的参数，否则人物/场景会变。
        # 靠人记「上次 char-desc 写的是什么」迟早出错，这里程序记。
        try:
            import json as _json
            _run = {"project": a.project, "audio": os.path.basename(merged),
                    "mode": a.mode, "role": a.role, "char_desc": a.char_desc,
                    "scenes_txt": "scenes.txt",
                    "storyboard": os.path.basename(a.storyboard or ""),
                    "size": a.size, "multiref_landscape": bool(a.multiref_landscape),
                    "scene_count": len(scene_descs or []),
                    "saved_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S")}
            open(os.path.join(doc_dir, "_run_args.json"), "w", encoding="utf-8").write(
                _json.dumps(_run, ensure_ascii=False, indent=2))
        except Exception:                                        # noqa: BLE001
            pass

        if a.mode == "multiref":
            # 多参考：**四视图人物图在前 + 场景图在后**（不出现主角本人，可有观众/背景人物），与 MV 同一规则
            if a.charview:
                char_rel = [make_charview(a)]
            else:
                char_rel = [av.REF_WOMAN if a.role in ("woman", "女", "女性") else
                            (av.REF_MAN if a.role in ("man", "男", "男性") else a.role)]
                print("[dh] --no-charview：直接用头像库 %s 作人物图" % char_rel[0], flush=True)
            kw.update(char_rel=char_rel,
                      scene_ref=scene_rels, scene_desc=scene_descs or None, seg_prompts=seg_prompts)
        elif a.mode == "multi":
            # 多场景：给服务端一个场景图目录，它按音频分段轮换
            kw.update(scene_ref=a.scene_subdir, seg_prompts=seg_prompts)
        else:  # single：一张含人物场景图
            kw.update(char_rel=None, scene_ref=scene_rels[0] if scene_rels else None,
                      seg_prompts=seg_prompts)

        # 一次生成整条口播：合并音频内部由工作流按 H3 切段，段数与 seg_prompts 对齐
        print("[dh] 合并模式：完整音频一次性生成（%s）" % a.mode, flush=True)
        merged_name = "合并_%dx%d.mp4" % (osz[0], osz[1])
        dst0 = os.path.join(video_dir, merged_name)
        # 🔴 返修模式：「已有产物 → 跳过」的判定必须先过 `is_rework`。
        # 只要显式给了索引且不是整片 `(0,-1)`，一律视为返修并强制重渲 ——
        # 否则合并成片一存在就直接「跳过」，参数传了等于没传，成片一个字节不变。
        # 判据放在公共件 `fxai-common/rework.py`，所有视频技能共用同一套，别在本地重写。
        rework = rw.is_rework(a.start_index, a.end_index)
        if rework:
            print("[rework] 返修模式：只重渲第 %d 段（服务端会拿其它段的缓存合并成完整片）"
                  % (a.start_index + 1), flush=True)
        else:
            # 整片模式但成片已存在 → 先算「这次到底改了哪几镜」。
            # 只改 1~3 镜时**自动降级**成逐镜单段返修：这是「本来只改一镜却整片重跑」
            # 的程序闸门，不靠人记规范。真要整片重来请显式 --force。
            sb_p = a.storyboard
            proj_dir = os.path.dirname(os.path.dirname(os.path.abspath(sb_p))) if sb_p else None
            if (not a.force) and sb_p and proj_dir and os.path.isfile(dst0):
                ch = rw.changed_shots(proj_dir, sb_p)
                if ch and len(ch) <= rw.AUTO_REWORK_MAX:
                    print("[rework] 只改了第 %s 镜 → 自动降级为单镜返修（整片不重跑）"
                          % "、".join(map(str, ch)), flush=True)
                    print("[rework] 想整片重来请加 --force", flush=True)
                    return _auto_rework(ch)
        if (not a.force) and (not rework) and os.path.isfile(dst0) \
                and not _scenes_newer_than(dst0, root):
            print("[video] 完整口播已有产物，跳过：%s" % dst0, flush=True)
            results = [{"index": 1, "audio": merged, "video": dst0, "rel": "",
                        "seconds": av.probe_duration(dst0), "skipped": True}]
        else:
            if (not a.force) and (not rework) and os.path.isfile(dst0):
                print("[video] 场景图比成片新（换过场景），重新渲染", flush=True)
            dst, rel = av.gen_video(a.mode, merged, tag=a.name, out_dir=video_dir, **kw)
            if os.path.abspath(dst) != os.path.abspath(dst0):
                os.replace(dst, dst0)
                dst = dst0
            results = [{"index": 1, "audio": merged, "video": dst, "rel": rel,
                        "seconds": merged_dur, "skipped": False}]
        joined = None
        if a.concat:
            print("[dh] 合并模式：音频已合并为一条，成片即完整口播，无需再 concat", flush=True)
        src_auds = [merged]

    items = [_video_item(root, r) for r in results if r.get("video")]
    write_delivery(name, root, doc_dir, items, joined, a.mode, src_auds)
    if a.burn:
        _bv = os.path.join(video_dir, "合并_%dx%d.mp4" % (osz[0], osz[1]))
        if os.path.isfile(_bv):
            out, _ = _burn_subtitles(_bv, video_dir, osz, lyrics=a.script)
            print("[dh] 已烧录字幕成片：%s" % out, flush=True)
        else:
            print("[dh] 未找到合并成片，跳过烧录：%s" % _bv, flush=True)

    # 记下这次用的分镜签名 —— 下次「改了哪几镜」才算得出来（否则只能整片重跑）。
    # 必须放在**成功路径末尾**：中途失败就返回了，不会把错误状态记成基线。
    if a.storyboard and os.path.isfile(a.storyboard):
        try:
            _pd = os.path.dirname(os.path.dirname(os.path.abspath(a.storyboard)))
            rw.save_sig(_pd, a.storyboard, kind=a.mode)
        except Exception as _e:                                  # noqa: BLE001
            print("[rework] 分镜签名未落盘（不影响出片）：%s" % _e, flush=True)

    print("[dh] 完成：%d 段视频 -> %s" % (len(items), video_dir), flush=True)
    if a.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[dh] 已中断", flush=True)
        sys.exit(130)
