# -*- coding: utf-8 -*-
# =====================================================================
# fxai-audio-video —— 音频驱动视频生成（公共件，MV 与数字人口播共用）
#
# 定位：给一段音频 + 参考图 + 提示词，按 MiniMax H3 的三种模式出视频。
# 音频是「唱歌」还是「说话」与流程无关——同一套提交/下载/尺寸/分段逻辑，
# 只有提示词措辞不同（唱歌写「演唱/歌声」，口播写「讲话/说话内容」），由 kind 区分。
#
# 两种用法：
#   · 多段音频（口播逐段 / MV 一整曲）→ gen_videos(auds, ...)，一段一条视频
#   · 单段音频 → gen_video(aud, ...)，一条视频
#
# 依赖（都不在本技能内）：
#   · fxai-workflow/comfy_client.py   共享 ComfyUI 客户端（提交/轮询/HTTP下载/尺寸封顶）
#   · fxai-workflow/*.json            mv-minimax-{single,multi,multiref}.json
#   · fxai-scene-gen/scene_gen.py     场景图（调用方自己委托，本模块不生成场景图）
# =====================================================================
import json
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILLS = os.path.dirname(_HERE)
for _dep in ("fxai-workflow", "fxai-common"):
    _p = os.path.join(_SKILLS, _dep)
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------- 共享客户端：镜像全部公开符号 ----------
import comfy_client as _C  # noqa: E402

_g = globals()
for _n in dir(_C):
    if not _n.startswith("__"):
        _g[_n] = getattr(_C, _n)

MODES = ("single", "multi", "multiref")
# 子目录 / 任务标签前缀：MV 用 mv_*，数字人口播用 dh_*（同一台 ComfyUI 各自归档）
PREFIX_MV = "mv"
PREFIX_SPOKEN = "dh"

# H3 多参考（multiref）标准输出尺寸：最大边 1280 < 1300 约束内。
# 🔴 默认**竖屏** 768×1280（口播绝大多数是竖屏投放）；要横屏用 multiref_landscape=True
# 换成 1280×768。两者都整除 32、都在 1300 约束内。single/multi 不在此列。
MULTIREF_SIZE = (768, 1280)            # 默认竖屏
MULTIREF_SIZE_LANDSCAPE = (1280, 768)  # 横屏（显式要求时）

# 🔴 主体定义极简（铁律 17，2026-11-12 全局统一，用户定稿）：
#   主体定义**只回答「这是什么」**——`<Subject N> 是 <Picture M> 中的<身份词>。` 一句到底；
#   保留类目清单（脸 / 发型 / 服装 / 建筑 / 陈设…）**一律写进「延续分析」的 fully_preserved**。
#   理由：主体定义里写满类目等于给模型第二遍外观指令，参考图会被文字覆盖（服装换色、款式漂）。
#   见 fxai-h3-video-prompt/references/04-通用技巧与自查.md「铁律 17 展开」。
KEEP_PERSON = "保留人物身份、发型发色、服装样式与颜色、裤装、鞋履。"
KEEP_SCENE = "保留场景的建筑、陈设与物品。"
KEEP_PROP = "保留道具的形制、材质与颜色。"
DEFAULT_CHAR_DESC = "人物"
DEFAULT_SCENE_DESC_SING = "场景"
DEFAULT_SCENE_DESC_SPEAK = "场景"
DEFAULT_CHAR_KEEP = KEEP_PERSON      # 兼容旧名
DEFAULT_SCENE_KEEP = KEEP_SCENE
DEFAULT_PROP_KEEP = KEEP_PROP


# =====================================================================
# 一、音频归一化
# =====================================================================
def resolve_audio(audio, upload_subdir="audio_input"):
    """把音频入参归一化成「服务端相对路径」（fxai_audioinput 要的就是这个）。

    - 本地文件（口播 wav 母版在项目 music/ 下）→ 先经 POST /fxai/audio/upload 传上去，
      返回服务端相对路径；
    - 已是服务端相对路径（MV 的音乐在服务端生成，形如 音乐生成/051_audio.wav）→ 原样返回。

    这样 MV 与口播都能直接把「自己手里的音频」传进来，调用方不用区分来源。
    """
    if not audio:
        raise ValueError("缺音频：audio 传本地音频路径，或服务端音频相对路径")
    if os.path.isfile(audio):
        rel = upload_audio(audio, subdir=upload_subdir)
        print("[audio] 已上传本地音频 -> %s" % rel, flush=True)
        return rel
    return audio


def _local_of(audio):
    """音频的本地可读路径；已经是服务端相对路径就返回它自己（读不到时长时容错为 None）。"""
    return audio if audio and os.path.isfile(audio) else None


# =====================================================================
# 一之二、H3 音频分段（精确公式，MV / 口播 / 短剧共用）
# =====================================================================
# MiniMax（H3）只接受满足「17k+5 帧」的时长（H3_FPS = 24）。
# 工作流里传「平均分段时长 = 10」，服务端并不是真切 10 秒——它先向下对齐到网格：
#   align_down_h3(240) = 17 * ((240-5) // 17) + 5 = 226 帧 → 226/24 = 9.41667 s
# 这就是「传 10 秒、实际 9.4 秒多点」。**必须照此算**，ceil(时长/10) 估算会偏
# （两者经常差 1 段，会让场景数 / 时间窗 / 逐段提示词对位全错）。
#
# 与节点 FxAiAudioSegmenterV2.select_segment(目标模型="MiniMax") 逐行同构。
H3_FPS = 24


def h3_align_down(frames):
    """最大的 17k+5 <= frames（与节点 align_down_h3 同构）。"""
    frames = int(frames)
    if frames < 5:
        return 5
    return 17 * ((frames - 5) // 17) + 5


def h3_align_up(frames):
    """最小的 17k+5 >= frames（单镜「请求 N 秒 → 实际生成多少秒」用）。"""
    frames = int(frames)
    if frames <= 5:
        return 5
    return 17 * ((frames - 5 + 16) // 17) + 5


def h3_segments(duration, avg_seg=10.0, fps=H3_FPS):
    """一条音频按 H3 规则切段，返回 (每段秒数 list, 每段 (start, end) 边界 list)。

    - duration: 音频总时长（秒）。
    - avg_seg: 工作流里的「平均分段时长」（0/None 时取默认 226 帧 ≈ 9.41667 s）。
    - fps: 帧率，H3 固定 24。
    时长取不到（非正数）返回 ([], [])。
    """
    try:
        duration = float(duration or 0)
    except (TypeError, ValueError):
        return [], []
    if duration <= 0:
        return [], []
    total_frames = round(duration * fps)
    frames_per_seg = h3_align_down(round(avg_seg * fps)) if avg_seg else 226
    if frames_per_seg <= 0:
        return [], []
    num_full = total_frames // frames_per_seg
    tail = total_frames - num_full * frames_per_seg
    # 尾段太短（avg_seg>10 → 5s 阈值，否则 3s）就并回上一段
    min_thr = int((5.0 if avg_seg and avg_seg > 10.0 else 3.0) * fps)
    if 0 < tail < min_thr and num_full > 0:
        num_full -= 1
        tail += frames_per_seg
    frames = [frames_per_seg] * num_full
    if tail > 0:
        frames.append(tail)
    secs = [round(f / float(fps), 5) for f in frames]
    bounds, cur = [], 0.0
    for s in secs:
        bounds.append((round(cur, 2), round(cur + s, 2)))
        cur += s
    return secs, bounds


def h3_segment_count(duration, avg_seg=10.0):
    """只要分段数（最常用：决定写几条逐段提示词 / 备几张场景图）。"""
    secs, _bounds = h3_segments(duration, avg_seg)
    return len(secs)


def h3_actual_seconds(req_sec, fps=H3_FPS):
    """「请求 req_sec 秒」→ H3 视频流**实际生成**的秒数（向上对齐 17k+5 @24fps）。

    只用于事后按时间点定位第几镜 / 镜头锚定；回传工作流的是段数与边界。
    """
    x = max(5, round(float(req_sec or 0) * fps))
    return h3_align_up(x) / float(fps)


def audio_duration(audio, default=0.0):
    """音频时长（秒）：本地文件用 ffprobe 探测；已是服务端相对路径或探测失败返回 default。

    分段计算的唯一输入就是它——**先拿时长再算分段**；文件数、字数、标称秒数都不是分段依据。
    """
    if not audio:
        return default
    try:
        d = probe_duration(_local_of(audio))
    except Exception:
        d = None
    return d if d else default


# =====================================================================
# 二、提示词构造（Ref2VA 六段式）
# =====================================================================
# kind 只影响「怎么描述人在干什么」：sing=演唱，speak=口播讲话。
# 其余六段结构、图片顺序（人物在前场景在后）、音频整体复用策略完全一致。
# 口播（speak）从 0 秒起连续讲述，不写演唱式前奏吸气 / 间奏停顿（与「音频从头就在说」对齐）。
_NARR = {
    "sing": {
        "label": "原声演唱音轨",
        "scene_role": "作为人物演唱的环境。",
        "summary_act": "演唱",
        "style": "目标视频采用电影感、自然主义的音乐视频风格。",
        "action": ("At 00:02.000, 她开始演唱，嘴唇与 <Audio 1> 中的歌声精准同步开合，"
                   "身形随乐句在画面中轻步移动（向镜头 / 向侧方 / 原地轻踏），"
                   "双手随乐句在身前做出柔和而富有表现力的手势；"),
        "pre": "她轻轻吸气，仿佛在回忆旋律；",
        "end": "结尾随最后一个音符自然放松，定格为柔和宁静的微笑。",
        "soundscape": "来自 <Audio 1> 的复制环境声音与歌声贯穿全程。",
    },
    "speak": {
        "label": "原声口播音轨",
        "scene_role": "作为人物对镜讲述的环境。",
        "summary_act": "对镜讲述",
        "style": "目标视频采用自然主义、干净克制的口播镜头风格，浅景深，人物清晰、背景轻微虚化。",
        # 手势与语义绑定：H3 在「幅度克制 + 原地不动」的约束下只剩头能动，
        # 于是用连续点头填充画面（实测：整段无规律点头）。解法是把身体动作给足，
        # 让人有事可做；「别点头」属于负向指令，容易反噬。
        # 🔴 头部运动一律不写：写「句尾轻点一次」等于给模型下发「点头」指令，
        # 生成出来就是一边说一边点头（实测，观感很假）。头交给模型自己处理。
        # 🔴 面部与颈肩的**持续节拍**也不写（「表情灵动随内容变化 / 自然切换」
        # 「肩颈放松，随呼吸有轻微起伏」）：这两句等于给模型下发「脸上不停做小动作」
        # 的指令，出来就是头部持续微动、观感机械（实测）。表情只写**静态状态**。
        # 🔴 2026-10-21：说话句必须写成「持续讲话 + 嘴部明显开合 + 逐字跟随」。
        # 旧写法只有「嘴唇与 <Audio 1> 精准同步开合」，强度太弱，配上每段都有的
        # 「面带微笑」，模型就把嘴闭合维持微笑，出片观感是"人根本不动嘴"（实测）。
        "action": ("从第一句话起，双手就跟着说话内容自然地动起来："
                   "强调时抬手、铺陈时摊开、说到身边的东西时伸手示意，"
                   "具体姿势由话语自己带出来，幅度与快慢跟着语气走，像人在面前聊天；"
                   "身体随着讲解自然换重心，整个人是松的；"
                   "从第一个字到最后一个字持续讲话，嘴部全程保持明显开合，"
                   "口型逐字跟随 <Audio 1> 中的说话内容变化，语速与音频完全一致；"),
        # 动作链拆两段：core 是必留的稳定/表情部分，gesture 是「空比划」。
        # 场景里有东西可指认时，用 act_extra 的互动动作替换 gesture——
        # 否则那句泛化的「手随话语比划」会把「侧身指屏幕」稀释掉，人物还是站着念稿。
        "action_core": ("从第一句话起，双手就跟着说话内容自然地动，"
                        "具体姿势由话语自己带出来，幅度与快慢跟着语气走；"
                        "身体随着讲解自然换重心，整个人是松的；"
                        "从第一个字到最后一个字持续讲话，嘴部全程保持明显开合，"
                        "口型逐字跟随 <Audio 1> 中的说话内容变化，语速与音频完全一致；"),
        "action_gesture": ("双手从第一句话起就随话语自然做解释手势，"
                           "具体姿势由她自己发挥，幅度有大有小、跟着语气走，"),
        # 非开场段：人本来就在场景里，别写成「刚进来」。
        # 🔴 2026-10-21：「直接开口讲述」强度不够——H3 是图生视频，首帧 = 参考图（嘴闭合），
        # 不写死首帧状态，模型就从闭嘴插值，出片前两三秒嘴是闭的（实测，整条视频都有这毛病）。
        # 起手句必须钉死「画面亮起时嘴已经在动」，且**不带 At 切点**（带切点 = 允许那之前不说）。
        "pre": "画面亮起时她已经在说话，嘴部正在开合，声音从第一个字起就没停；",
        # 开场镜专用：入场要有动作与位移；一上来就站定念稿，观众看不到这个人会动。
        # 🔴 位移与说话必须同一句：写成「走入 + 站定」两段，模型就演成走进来、站好、再开口。
        "entry": "她一边开口讲，一边从画面外侧缓步走入，走到画面中略偏中心的位置停住，全程边走边讲，嘴部不停开合；",
        "end": "结尾随最后一个字自然收住，最后一个字说完再闭嘴。",
        "soundscape": "来自 <Audio 1> 的清晰人声与说话内容原样贯穿全程，不加背景音乐与混响。",
    },
}


def _narr(kind):
    if kind not in _NARR:
        raise ValueError("kind 只能是 %s，实际 %r" % ("/".join(sorted(_NARR)), kind))
    return _NARR[kind]


def fmt_at(seconds):
    """秒 → 切点写法 `At 00:03.500,`（MM:SS.mmm）。"""
    total_ms = int(round(float(seconds) * 1000))
    m, rem = divmod(total_ms, 60000)
    s_, ms = divmod(rem, 1000)
    return "At %02d:%02d.%03d," % (m, s_, ms)


def _join_pre_act(pre, act):
    """起手句与动作链拼接：动作链以 `At` 切点开头时换行，让切点落在行首。"""
    pre = (pre or "").rstrip()
    act = (act or "").lstrip()
    if act.startswith("At "):
        return pre + "\n" + act
    return pre + act


def _trigger_line(words):
    """触发词行（独占一行，末尾带换行）；words 为空返回 ""。

    规范唯一权威来源：fxai-h3-video-prompt/references/07-触发词挂载规范.md
    （camera motion 运镜总开关 / prfight1 打斗 / prfin1 终结 / prslow1 慢镜终结）。
    """
    if not words:
        return ""
    if isinstance(words, str):
        words = [w.strip() for w in words.split(",") if w.strip()]
    return ", ".join(words) + "\n"


# 景别 → 具体构图约束。只写「中景」两个字，模型会自己收成胸像近景（实测第 2 段整段
# 变成大特写，人物占满画面、手势出不了画、背景全虚）；写清「头顶留多少、人物占画面
# 多高、背景看得见多少」才落得住。改这里等于改所有走 build_ref2va_prompt 的镜头。
# 🔴 景别只写「中文名 + H3 官方景别术语 + 身体部位范围」，**不写量化比例**。
# 旧版写过「约占画面高度四分之三 / 五分之三」「头顶留出六分之一空白」这类自造比例：
# H3 官方提示词指南里，与构图有关的量化只有运镜的 small/large amplitude，
# **主体占画面高度几分之几没有任何官方依据**（2026-10-22 查证），写了也不会被执行，
# 反而挤占提示词预算。官方实际使用的景别词是 medium-wide shot / medium shot /
# close-up / close shot 这些标准术语——所以中文名后面挂上对应英文术语。
# 构图要精确，官方给的正道是**用参考图锚定**（position and framing established by
# <Picture N>），不是写百分比。
SHOT_SPEC = {
    "远景": "远景（wide shot）：环境是主体，人物全身在画面中且较小",
    "全景": "全景（full shot）：人物全身完整进入画面，头顶与脚底各留出空白",
    "中全景": "中全景（medium-wide shot）：人物从头顶到大腿以上进入画面，环境完整可见",
    "中景": "中景（medium shot）：人物从头顶到腰部以上进入画面，身后环境清楚可辨",
    "近景": "近景（close-up）：人物从头顶到胸口进入画面，面部清晰",
    "特写": "特写（extreme close-up）：面部充满画面，以头顶到下巴为主",
}


def subject_identity(desc, default="人物"):
    """从传入描述里取「身份词」，用于主体定义句（铁律 17）。

    调用方可以传两种东西：
    - 纯身份词（`古装年轻女子` / `场景`）→ 原样用；
    - 旧写法「身份词 + 类目清单」（`古装年轻女子，脸、发型、服装…`）→ 只取第一个逗号前的身份词，
      类目清单丢弃（它归「延续分析」，不进主体定义）。
    """
    d = str(desc or "").strip()
    if not d:
        return default
    head = d.replace("，", ",").split(",")[0].strip()
    for t in ("全部来自", "来自 <Picture"):
        if t in head:
            head = head.split(t)[0]
    head = head.strip("。；;，, 、")
    return head or default


# 旧名兼容（produce_song 等调用方仍按 subject_keep_body 调用）
subject_keep_body = subject_identity

# 身份词里至少要有一个「人」的痕迹，否则说明调用方传的是穿搭 / 类目（如 `白色长裙，长发`），
# 主体定义会写出「<Subject 1> 是 <Picture 1> 中的白色长裙。」这种病句 —— 兜底回「人物」。
PERSON_HINTS = ("人", "者", "女", "男", "角色", "主角")


def person_identity(desc, default="人物"):
    """人物主体定义用的身份词：取到的是穿搭/类目就回退成 default。"""
    head = subject_identity(desc, default="")
    if head and any(w in head for w in PERSON_HINTS):
        return head
    return default


def build_ref2va_prompt(char_descs, scene_desc, audio_label=None, kind="sing",
                        camera=None, act_extra=None, prop_descs=None,
                        trigger_words=None, act_mode="default", face_mode="front",
                        shot_size="中景", entry=None, lines=None):
    """按 MiniMax H3 Ref2VA（全参考）规范生成六段式提示词，支持多人物。

    - char_descs: list[str]，每元素一个人物的**身份词**（`古装年轻女子` / `人物`）。
      🔴 铁律 17：主体定义只回答「这是什么」，**不写外观类目**（脸/发型/服装/颜色全归延续分析）；
      传成「身份词，脸、发型…」这种旧写法也能用，本函数只取第一个逗号前的身份词。
      顺序与人物图列表一致：<Picture 1>~<Picture K> = 人物1~K，<Picture K+1> = 场景。
    - scene_desc: 场景**身份词**（如 `场景` / `临水木构戏台`，对应 <Picture K+1>）。
    - audio_label: 音频身份说明，缺省按 kind 取（sing=原声演唱音轨 / speak=原声口播音轨）。
    - kind: "sing"（演唱，MV 用）| "speak"（口播讲话，数字人用）。
    - camera: 该段运镜覆盖（缺省用「缓慢轻微推近」；逐段轮换不同运镜，每段镜头各不一样）。
    # act_extra: 该段**专属**肢体语言/表演，追加在 kind 默认动作链之后（逐段轮换，动作不呆板）。
    # act_mode="extra" 时用它**替换**默认的「手随话语空比划」，只保留稳定站位与表情部分——
    # 场景里有屏幕/白板/设备可指认时，人物才会真的去指、去看，而不是站在画面中间空讲。
    - 音频为原音频参考：overall_soundscape 只声明沿用 <Audio 1>，non_diegetic_music 写「无」。
    - trigger_words: 可选，触发词（str 逗号分隔 / list）。给了就独占一行插在
      `detailed_description:` 标题的下一行（本函数不做自动判定，由调用方显式给）。
      规则见 fxai-h3-video-prompt/references/07-触发词挂载规范.md。
    - entry: 可选，**开场镜**的入场描述，替换该镜默认的 `pre`（缺省 pre 是「直接开口」）。
      只给第 0 段用；每段都写入场会让每次硬切都像重新进门。
    - lines: 可选，**这段的台词**。给了就挂进 `<Subject 1>`（人物）的定义里，
      **不进** `scene_desc`。🔴 台词挂到场景主体上会让模型读成「场景在说话」。
    """
    if not isinstance(char_descs, list):
        char_descs = [char_descs]
    nar = _narr(kind)
    label = audio_label or nar["label"]
    k = len(char_descs)
    scene_idx = k + 1
    # 🔴 台词只能挂在「人」身上，不得挂在场景上。
    # 曾经把台词拼进 scene_desc（dh_video.py 旧实现），而 scene_desc 落进 <Subject 2>
    # （场景背景图）的定义 → 模型读到的是「S1 没台词、S2 在讲一整段话」，
    # 与 detailed_description 里的讲话指令互相打架，实测成片嘴是闭的。
    # 场景图就是环境，台词归人物：<Subject 1> 是正在说话的那个。
    line_txt = (str(lines).strip() if lines else "")
    if line_txt:
        line_txt = line_txt.rstrip("。！？!?")
    subj_defs = []
    for _i, _cd in enumerate(char_descs, 1):
        # 铁律 17：主体定义只写「<Picture N> 中的<身份词>。」，外观类目全归延续分析
        _d = "<Subject %d> 是 <Picture %d> 中的%s。" % (
            _i, _i, person_identity(_cd, default=DEFAULT_CHAR_DESC))
        if line_txt and _i == 1:
            _d = "%s她是画面里正在讲话的人，讲的内容是：%s。" % (_d, line_txt)
        subj_defs.append(_d)
    # 场景同样只写身份词（`场景` / `临水木构戏台`），不留「作为…的环境」这类补充说明。
    subj_defs.append("<Subject %d> 是 <Picture %d> 中的%s。"
                     % (scene_idx, scene_idx, subject_identity(scene_desc, default="场景")))
    # ★ 道具参考（如某件道具）：恒定追加在场景之后，<Picture/Subject> 编号 = scene_idx+1 起，
    #   与 build_multiref_lines._row_imgs 把 prop_rels 追加在每行图片列表末尾保持一致。
    _prop_descs = prop_descs or []
    for _j, _pd in enumerate(_prop_descs, 1):
        _p = scene_idx + _j
        subj_defs.append("<Subject %d> 是 <Picture %d> 中的%s。"
                         % (_p, _p, subject_identity(_pd, default="道具")))
    subj_defs.append("<Audio 1> 是视频中的%s，作为 <Subject 1> (S1) 的音色参考和目标视频的完整最终音轨。"
                     % label)
    people = "、".join("<Subject %d>" % i for i in range(1, k + 1))
    pics = "、".join("<Picture %d>" % i for i in range(1, k + 1))
    subj_block = "\n".join(subj_defs) + "\n"
    # 保留类目清单写在这里（铁律 17）：主体定义只说「是什么」，这里说「保住哪几类」。
    retain_block = "\n".join(
        "<Subject %d> (appears in [Shot 1]): fully_preserved - %s" % (i, KEEP_PERSON)
        for i in range(1, k + 1))
    retain_block += ("\n<Subject %d> (appears in [Shot 1]): fully_preserved - %s\n"
                     % (scene_idx, KEEP_SCENE))
    for _j, _pd in enumerate(_prop_descs, 1):
        _p = scene_idx + _j
        retain_block += ("<Subject %d> (appears in [Shot 1]): fully_preserved - %s\n"
                         % (_p, KEEP_PROP))
    retain_block += "<Audio 1>: fully_copy - <Audio 1> 作为目标视频的完整最终音轨直接复用。\n"
    _prop_pics = "、".join("<Picture %d>" % (scene_idx + _j) for _j in range(1, len(_prop_descs) + 1))
    summary = ("[reference generation + audio reuse] 目标视频展示 %s 在 <Subject %d> 中%s。"
               "%s 作为人物外观参考，<Picture %d> 作为场景背景参考，<Audio 1> 作为原声音频。"
               "%s\n"
               % (people, scene_idx, nar["summary_act"], pics, scene_idx,
                  (" %s 作为道具参考，需完整保留其形制与质感。" % _prop_pics) if _prop_descs else ""))
    cam = ("镜头以缓慢的轻微推近（slow push-in）跟随人物，幅度小、速度慢。"
           if not camera else (camera if camera.endswith(("。", "！", "？")) else camera + "。"))
    # 负向约束：H3 容易把"自然走动/转身"放大成来回快速晃动、人物或镜头左右摇摆。
    # 只约束**镜头**与「来回反复」，人物幅度那半句给足——写「幅度克制」会让模型
    # 只剩头能动，退化成整段无规律点头（实测）。
    stable_rule = ("整段为**单个稳定镜头**：镜头不摇晃、不做快速推拉；"
                   "人物动作连贯自然，幅度有大有小（有强调的大手势，也有放松的小动作），"
                   "不出现来回反复的晃动或抖动。")
    if act_extra and act_mode == "extra":
        act_text = nar.get("action_core", nar["action"]) + str(act_extra)
    else:
        act_text = nar["action"] + (("%s" % act_extra) if act_extra else "")
    # 开场镜已经用 entry 写了「走入 → 停住」，动作链里的「可缓慢移动两三步」要收掉：
    # 一个镜头里两次位移会让模型走出多余的路径（实测：走进去又飘回来）。
    if entry:
        act_text = act_text.replace(
            "身体可缓慢移动两三步并停住（有明确终点），重心在两腿之间自然转移；",
            "走到位后重心落稳，在两腿之间自然转移；")
    # 🔴 face_mode="free" 也**不写**「可短暂侧身示意」：
    # 提示词里只要出现「侧身」，模型就理解成「身体转到侧面」，且不执行「转出去再转回来」
    # 这类往返指令 —— 出来是侧脸口播，嘴型对不上观众。
    # 口播口径：脸和身体始终正面朝向镜头，要「动」就让人物走近/走远、左右横移。
    face = ("面部始终正面朝向镜头，身体与镜头的夹角不超过五度"
            if face_mode == "free" else "面部正面朝向镜头")
    pre_text = entry or nar["pre"]
    # 入场镜的位置描述要跟着变：她已经「走入并停住」，再写「站在画面略偏中心」会自相矛盾
    pos_text = "入场后停在画面略偏中心位置" if entry else "站在画面略偏中心位置"
    shot_spec = SHOT_SPEC.get(shot_size or "中景", "%s镜头" % (shot_size or "中景"))
    # 🔴 铁律 17 配套：详细描述**不复述外观**，只用编号引用 —— 复述一次等于又下一遍文字指令，
    # 参考图的服装/发型会被这段文字重新覆盖（2026-11-12 实测）。
    detail = ("[Shot 1] %s，以 <Subject %d> 作为场景背景。<Subject 1> (S1) %s，"
              "%s，表情自然专注。%s%s %s%s\n"
              % (shot_spec, scene_idx, pos_text, face,
                 stable_rule, cam, _join_pre_act(pre_text, act_text), nar["end"]))
    if _prop_descs:
        detail += ("视频中出现的道具须严格参照 %s 等道具参考图，形制与质感保持一致，不可凭空改动。\n"
                   % _prop_pics)
    return ("subject_definitions:\n" + subj_block
            + "\nsummary:\n" + summary
            + "\nretention_analysis:\n" + retain_block
            + "\ndetailed_description:\n" + _trigger_line(trigger_words) + nar["style"] + "\n" + detail
            + "\noverall_soundscape:\n" + nar["soundscape"]
            + "\nnon_diegetic_music:\n无")


def build_multiref_lines(char_rels, scene_rels, char_descs=None, scene_descs=None,
                         ref2va_prompts=None, seg_prompts=None, kind="sing",
                         prop_rels=None):
    """多参考模式：构建 mv-minimax-multiref.json 的 lines_data（fxai_prompt）JSON 字符串。

    ★ 图片顺序：人物图在前、场景图在后 → <Picture 1>~<Picture K> = 人物1~K，
      <Picture K+1> = 场景图；提示词里 <Subject 1>~<Subject K> = 各人物，<Subject K+1> = 场景。
    - 每行 = [int序号, 该行 Ref2VA 六段式提示词, "人物图1,...,场景图N"]。
    - seg_prompts 优先（逐段模式：行数 = 音频分段数，第 i 段精确命中第 i 行）；
      否则按场景数生成（服务端按音频分段循环取值）。
    纯函数，不改动任何工作流 dict。
    """
    if not isinstance(char_rels, list):
        char_rels = [char_rels]
    if not char_rels:
        char_rels = [REF_WOMAN]
    n_people = len(char_rels)
    if not isinstance(char_descs, list):
        char_descs = [char_descs] if char_descs else None
    if char_descs is None:
        char_descs = [DEFAULT_CHAR_DESC] * n_people
    while len(char_descs) < n_people:
        char_descs.append(char_descs[-1])

    if not scene_rels:
        scene_rels = [""]
    if not isinstance(scene_rels, list):
        scene_rels = [scene_rels]
    default_scene = DEFAULT_SCENE_DESC_SING if kind == "sing" else DEFAULT_SCENE_DESC_SPEAK
    if not scene_descs:
        scene_descs = [default_scene] * len(scene_rels)
    elif not isinstance(scene_descs, list):
        scene_descs = [scene_descs]
    while len(scene_descs) < len(scene_rels):
        scene_descs.append(default_scene)

    def _row_imgs(srel):
        # ★ 道具参考图（如某件道具）恒定追加在所有行末尾：图片顺序 = 人物(K) + 场景(1) + 道具(N)。
        #   与 build_ref2va_prompt / produce_song.build_segment_prompt 的 <Subject> 编号保持一致。
        return ",".join(char_rels + ([srel] if srel else []) + (prop_rels or []))

    rows = []
    if seg_prompts:
        n_scene = len(scene_rels)
        for i, prompt in enumerate(seg_prompts):
            rows.append([10, prompt, _row_imgs(scene_rels[i % n_scene])])
    else:
        for i, (srel, sdesc) in enumerate(zip(scene_rels, scene_descs)):
            prompt = (ref2va_prompts[i] if (ref2va_prompts and i < len(ref2va_prompts))
                      else build_ref2va_prompt(char_descs, sdesc, kind=kind))
            rows.append([10, prompt, _row_imgs(srel)])
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def build_multiline_prompt(seg_prompts=None, ref2va_prompts=None):
    """多场景模式（mv-minimax-multi.json）的 lines_data（fxai_prompt）JSON 字符串。
    每行 = [int序号, 提示词]；优先 seg_prompts，其次 ref2va_prompts，都没有则空列表。"""
    rows = [[10, p] for p in (seg_prompts or ref2va_prompts or [])]
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


# =====================================================================
# 三、FL2VA 提示词（单场景 / 多场景）
# =====================================================================
def _fl2va_seconds(duration):
    """有效时长格式化成官方要求的两位小数；拿不到时长按 0.00。"""
    try:
        return "%.2f" % float(duration or 0)
    except (TypeError, ValueError):
        return "0.00"


def build_fl2va_prompt(pics=1, duration=0, body="", kind="sing"):
    """FL2VA（首尾帧）+ 音频参考的三段式提示词，供单场景 / 多场景模式用。

    官方格式硬约束（不可改）：
    - 首行是对齐指令，**必须是提示词第一行**，空一行再进三段核心字段；
    - 核心字段固定三个：integrated_multimodal_description / overall_soundscape / non_diegetic_music；
    - 时长 `S.SS` 保留两位小数。

    - pics: 参考图张数（首帧 1 张；首尾帧 2 张时 pics=2 并按官方写法给出两处对齐）。
    - duration: 有效时长（秒），写进对齐指令。
    - body: `[Shot 1] …` 正文。**要契合这一段文案在讲什么**（场景/道具/表演都写在这里）。
    - kind: "sing" 演唱 | "speak" 口播讲话，决定口型与声音措辞。
    """
    nar = _narr(kind)
    d = _fl2va_seconds(duration)
    if int(pics) >= 2:
        align = ("How the reference pictures align with the target video — Picture 1 (from Shot 1) "
                 "aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) "
                 "aligns with the %s-second mark of the target video." % d)
    else:
        align = ("How the reference pictures align with the target video — Picture 1 (from Shot 1) "
                 "aligns with the 0.00-second mark of the target video.")
    return (align + "\n\n"
            + "integrated_multimodal_description: " + (body or "[Shot 1] ")
            + "\n\noverall_soundscape: " + nar["soundscape"]
            + "\n\nnon_diegetic_music: 无")


# 🔴 台词/音频挂到场景主体上就翻车：模型读成「场景在说话」，与动作链里的口型同步
# 指令打架，实测人物整段闭嘴。这些词出现在场景主体的定义句里即为违规。
_BIND_BAD_ON_SCENE = ("她正在讲", "他在讲", "正在讲：", "讲的内容", "唱的内容",
                      "唱的是", "台词", "<Audio", "音色参考")


def audit_subject_binding(prompt, require_lines=False):
    """🔴 校验主体归属：台词与音频必须挂在**人物**主体上，场景主体一个字都不得沾。

    H3 全局规矩（见 `fxai-h3-video-prompt/references/02-Ref2VA多参考.md` 3.3），
    口播 / MV / 短剧凡走 Ref2VA 六段式的都适用。

    - prompt: 单段六段式提示词文本。
    - require_lines: True 时额外要求**人物主体**上必须带台词（口播逐段都该有）。

    返回 `(ok, problems)`；`problems` 是字符串列表，空列表即通过。
    """
    if not prompt:
        return False, ["提示词为空"]
    try:
        block = prompt.split("subject_definitions:", 1)[1].split("summary:", 1)[0]
    except IndexError:
        return False, ["不是六段式提示词（缺 subject_definitions）"]
    scene_lines, char_lines, audio_lines = [], [], []
    for ln in block.splitlines():
        ln = ln.strip()
        if not ln.startswith("<Subject"):
            if ln.startswith("<Audio"):
                audio_lines.append(ln)
            continue
        if "场景背景图" in ln or "道具参考图" in ln:
            scene_lines.append(ln)
        else:
            char_lines.append(ln)
    problems = []
    for ln in scene_lines:
        tag = "场景" if "场景背景图" in ln else "道具"
        for w in _BIND_BAD_ON_SCENE:
            if w in ln:
                problems.append("%s主体上挂了台词/音频（出现「%s」）——台词与音频只归人物" % (tag, w))
                break
    for ln in audio_lines:
        # <Audio 1> …作为 <Subject N> 的音色参考：N 必须落在人物主体上。
        import re as _re
        m = _re.search(r"作为\s*<Subject\s*(\d+)>", ln)
        if not m:
            problems.append("音频没指明挂在哪个主体上（应写「作为 <Subject 1> 的音色参考」）")
            continue
        n = int(m.group(1))
        # 场景主体编号 = 人物数 + 1；这里拿不到人物数，用「有没有人物主体行」兜底：
        # 人物行都带「中的人物」，其最大编号即人物数。
        nums = []
        for cl in char_lines:
            mm = _re.match(r"<Subject\s*(\d+)>", cl)
            if mm:
                nums.append(int(mm.group(1)))
        if nums and n > max(nums):
            problems.append("音频挂在了第 %d 号主体（非人物）上，应挂人物主体" % n)
    if require_lines and char_lines:
        if not any(("讲的内容" in c or "唱的内容" in c) for c in char_lines):
            problems.append("人物主体上没有台词（口播每段都应写明她在讲什么）")
    return (not problems), problems


def fl2va_body(char_desc, scene_desc, act="", kind="sing"):
    """FL2VA 正文（`integrated_multimodal_description` 的 `[Shot 1]` 部分）拼装器。

    写的是**首尾帧之间发生什么**，不是把画面重描一遍：主体状态 → 动作链 → 运镜 → 落点。
    - char_desc / scene_desc: 这段文案该有的**身份词**（`人物` / `场景` / `开放式书房`）。🔴 铁律 17：
      不传外观类目、不传具体颜色款式（写了会覆盖参考图）；留空则用中性占位。
    - act: 这一段的具体表演（口播写讲话与手势，MV 写演唱与乐句动作）；留空则用 kind 的默认动作链。
    - kind: "sing" | "speak"。

    与 Ref2VA 同一条负向约束：单个稳定镜头，镜头不摇晃、人物不来回快速晃动。
    """
    nar = _narr(kind)
    stable_rule = ("整段为单个稳定镜头：镜头不摇晃、不做快速推拉；"
                   "人物动作连贯自然，幅度有大有小，不出现来回反复的晃动或抖动。")
    return ("[Shot 1] %s，%s。%s%s%s"
            % (char_desc or "画面中的人物", scene_desc or "画面中的场景",
               stable_rule, _join_pre_act(nar["pre"], (act or nar["action"])), nar["end"]))


# =====================================================================
# 三、尺寸
# =====================================================================
def max_edge(src, want):
    """single/multi 模式的「最大边长」：封顶 MAX_EDGE(1300) 且只缩不放，按 ALIGN_BASE 对齐。"""
    return safe_max_edge(src, want=min(int(want), MAX_EDGE))


# =====================================================================
# 四、视频生成
# =====================================================================
def _archive_prompt(prompt_out, mode, raw):
    """把「实际提交给 ComfyUI 的 fxai_prompt」落盘，供事后回顾真实提交数据。失败不影响渲染。"""
    if not prompt_out:
        return
    try:
        parsed = None
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
        with open(prompt_out, "w", encoding="utf-8") as f:
            json.dump({"mode": mode, "submitted_fxai_prompt": raw,
                       "lines_data_parsed": parsed if isinstance(parsed, list) else None},
                      f, ensure_ascii=False, indent=1)
        print("[video] 已存档实际提交提示词 ->", prompt_out)
    except Exception as e:
        print("[video] 存档提示词失败（不影响渲染）：", e)


def gen_video(mode, aud_rel, char_rel=None, scene_ref=None,
              size=SIZE["portrait_9x16"], tag=None, timeout=3600,
              char_desc=None, scene_desc=None, ref2va_prompts=None,
              seg_prompts=None, char_descs=None, subdir=None, prompt_out=None,
              start_index=None, end_index=None, model=None,
              kind="sing", prefix=PREFIX_MV, audio_subdir="audio_input", out_dir=None,
              multiref_landscape=False, prop_rel=None, allow_no_outfit=False):
    """用一段音频生成一条视频（MiniMax 三模式）。

    - mode: "single" 单场景（一张参考图）| "multi" 多场景（一个图片目录，服务端轮换）
            | "multiref" 多参考（人物图在前场景图在后，逐段提示词）。
    - aud_rel: 音频。本地文件会自动上传成服务端相对路径，服务端相对路径原样使用。
    - kind: "sing" 演唱（MV）| "speak" 口播讲话（数字人）——只改提示词措辞。
    - prefix: 子目录与任务标签前缀（MV=mv、数字人=dh），决定服务端归档目录。
    - 尺寸：single/multi 走「最大边长」(fxai_width) 并封顶 MAX_EDGE=1300 且只缩不放；
            multiref 不走 max_edge，固定给 fxai_width/fxai_height：**竖屏 768×1280（默认）**/
            横屏 1280×768（multiref_landscape=True），最大边 1280 < 1300 约束内，且均整除 32
            过 FxAIGeneratorController 的「长宽对齐基数」后不变。
    - out_dir: 视频下载落盘目录，缺省落公共缓存 OUT_DIR。
    返回 (本地视频路径, 服务端相对路径)。
    """
    if mode not in MODES:
        raise ValueError("mode 只能是 %s，实际 %r" % ("/".join(MODES), mode))
    require_comfyui()
    tag = tag or ("%s_%s_%d" % (prefix, mode, int(time.time())))
    w, h = int(size[0]), int(size[1])
    char_rel = char_rel or REF_WOMAN
    aud_rel = resolve_audio(aud_rel, upload_subdir=audio_subdir)
    subdir = subdir or ("%s_%s" % (prefix, mode))
    base = {"fxai_audioinput": aud_rel, "fxai_outputdir": subdir, "fxai_seed": _seed_rand()}

    if mode == "single":
        prompt = (seg_prompts[0] if seg_prompts else "") or (ref2va_prompts[0] if ref2va_prompts else "")
        if not prompt:
            # 没给提示词就按 FL2VA 首尾帧格式现编一条（single 走 FL2VA，见 fxai-h3-video-prompt 第 2 节）
            prompt = build_fl2va_prompt(pics=1, duration=probe_duration(_local_of(aud_rel)),
                                        body=fl2va_body(char_desc, scene_desc, kind=kind), kind=kind)
        params = dict(base, fxai_prompt=prompt, fxai_imageinput=scene_ref or char_rel)
        params["fxai_width"] = max_edge(scene_ref or char_rel, want=max(w, h))
        # 只有确实用人物参考（没被 scene_ref 覆盖成场景图）时才校验穿搭；纯场景参考免检
        if not scene_ref:
            check_outfit(prompt, char_rel, allow_no_outfit)
        url = submit_workflow(resolve_wf("single", model), params,
                              tag="%s_single:%s" % (prefix, tag), kind="single", timeout=timeout)
    elif mode == "multi":
        lines = build_multiline_prompt(seg_prompts, ref2va_prompts)
        if lines in ("[]", ""):
            # multi 也是 FL2VA：每行一条 [序号, FL2VA 三段式提示词]
            rows = [[10, build_fl2va_prompt(pics=1, duration=0,
                                            body=fl2va_body(char_desc, scene_desc, kind=kind),
                                            kind=kind)]]
            lines = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        params = dict(base, fxai_prompt=lines, fxai_imageinput=scene_ref)
        params["fxai_width"] = max_edge(scene_ref, want=max(w, h))
        url = submit_workflow(resolve_wf("multi", model), params,
                              tag="%s_multi:%s" % (prefix, tag), kind="multi", timeout=timeout)
    else:  # multiref
        char_rels = char_rel if isinstance(char_rel, list) else ([char_rel] if char_rel else [])
        scene_rels = scene_ref if isinstance(scene_ref, list) else [scene_ref]
        scene_descs = scene_desc if isinstance(scene_desc, list) else ([scene_desc] if scene_desc else None)
        _char_descs = char_descs if isinstance(char_descs, list) else ([char_desc] if char_desc else None)
        prop_rels = prop_rel if isinstance(prop_rel, list) else ([prop_rel] if prop_rel else [])
        lines = build_multiref_lines(char_rels, scene_rels, char_descs=_char_descs,
                                     scene_descs=scene_descs, ref2va_prompts=ref2va_prompts,
                                     seg_prompts=seg_prompts, kind=kind, prop_rels=prop_rels)
        # 人物图在前：引用角色底图时必写穿搭+发型（char_descs 本就是穿搭描述，正常都会过）
        check_outfit(lines, (char_rels[0] if char_rels else char_rel), allow_no_outfit)
        # 多参考固定标准尺寸：默认竖屏 768×1280，横屏（multiref_landscape）1280×768。
        # 与 single/multi 的「最大边长」逻辑无关，不取传入的 size（默认竖屏 1080×1920，太重）。
        _mw, _mh = (MULTIREF_SIZE_LANDSCAPE if multiref_landscape else MULTIREF_SIZE)
        params = dict(base, fxai_prompt=lines, fxai_width=_mw, fxai_height=_mh)
        # 按段重渲：start_index=end_index=同一值 → 只重渲该段（索引 0 起）。默认不传 → 渲染全部段。
        if start_index is not None and end_index is not None:
            params["fxai_start_index"] = int(start_index)
            params["fxai_end_index"] = int(end_index)
        url = submit_workflow(resolve_wf("multiref", model), params,
                              tag="%s_multiref:%s" % (prefix, tag), kind="multiref", timeout=timeout)

    _archive_prompt(prompt_out, mode, params.get("fxai_prompt", ""))
    ext = os.path.splitext(str(url).split("|")[-1])[1] or ".mp4"
    dst = os.path.join(out_dir or OUT_DIR, tag + ext)
    _download(url, dst)
    print("[video:%s] DONE ->" % mode, dst)
    return dst, url_to_rel(url)


def gen_videos(auds, mode, name="口播", out_dir=None, timeout=3600, size=SIZE["portrait_9x16"],
               skip_existing=True, force_indices=None, **kw):
    """多段音频逐段生成视频：第 i 段音频 → 第 i 条视频，串行提交。

    - auds: 音频列表（本地路径或服务端相对路径都认）。
    - name: 文件名前缀，产物 <name>_01.mp4 / <name>_02.mp4 …（零填充两位）。
    - skip_existing: 已有该段视频则跳过不重渲（默认开）。
    - force_indices: 要**强制重渲**的段号（1 起）。单段返修时用它——
      否则「已有产物→跳过」会把返修请求吃掉，这是 2026-10-21 数字人口播踩过的坑
      （`--start-index` 传了却被跳过，成片一个字节没变）。判据统一走 `fxai-common/rework.py`。
    - 其余参数原样传给 gen_video。
    返回 [{"index", "audio", "video", "rel", "seconds", "skipped"}...]
    """
    if isinstance(auds, str):
        auds = [auds]
    auds = [a for a in (auds or []) if a]
    if not auds:
        raise ValueError("没有音频可生成")
    out_dir = out_dir or OUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    force_set = set(int(x) for x in (force_indices or []))
    results = []
    for i, a in enumerate(auds, 1):
        tag = "%s_%02d" % (name, i) if len(auds) > 1 else name
        hit = [f for f in os.listdir(out_dir) if f.startswith(tag + ".") and f.endswith(".mp4")]
        if skip_existing and hit and (i not in force_set):
            dst = os.path.join(out_dir, hit[0])
            print("[video] %d/%d 已有产物，跳过：%s" % (i, len(auds), dst), flush=True)
            results.append({"index": i, "audio": a, "video": dst, "rel": "",
                            "seconds": probe_duration(dst), "skipped": True})
            continue
        if i in force_set:
            print("[rework] 第 %d 段强制重渲（单段返修，不看已有产物）" % i, flush=True)
        print("[video] %d/%d 提交：%s" % (i, len(auds), a), flush=True)
        dst, rel = gen_video(mode, a, tag=tag, timeout=timeout, size=size,
                             out_dir=out_dir, **kw)
        results.append({"index": i, "audio": a, "video": dst, "rel": rel,
                        "seconds": probe_duration(dst), "skipped": False})
    return results


# =====================================================================
# 五、媒体工具
# =====================================================================
def ffmpeg_bin():
    """ffmpeg 可执行文件：全局配置 video.ffmpeg > PATH 里的 ffmpeg。"""
    c = _cfg("ffmpeg", "") or _cfg("video_ffmpeg", "")
    if c and (os.path.isfile(c) or shutil_which(c)):
        return c
    w = shutil_which("ffmpeg")
    return w or "ffmpeg"


def ffmpeg_libass():
    """**带 libass 的 ffmpeg**（烧字幕专用）。

    conda 自带的 ffmpeg 多数没编 libass，`subtitles` 滤镜直接报
    "No such filter: 'subtitles'"，中文还会烧成豆腐块。优先用凤希AI伴侣自带的
    `<software_install_dir>/ffmpeg/ffmpeg.exe`（gyan full build，含 libass），
    退回全局配置 video.ffmpeg，最后才退 PATH。**烧字幕一律用这个，`ffmpeg_bin()` 只用于无字幕的场景。**
    """
    inst = _cfg("software_install_dir", "")
    if inst:
        for nm in ("ffmpeg.exe", "ffmpeg"):
            c = os.path.join(inst, "ffmpeg", nm)
            if os.path.isfile(c):
                return os.path.normpath(c)
    return ffmpeg_bin()


def shutil_which(name):
    import shutil as _s
    return _s.which(name)


def probe_duration(path):
    """视频/音频时长（秒）。取不到返回 None。"""
    exe = shutil_which("ffprobe")
    if not exe or not path or not os.path.isfile(path):
        return None
    try:
        out = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", path],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        txt = (out.stdout or b"").decode("utf-8", "replace").strip()
        return round(float(txt), 2) if txt else None
    except Exception:
        return None


def concat_videos(paths, dst, tone=None):
    """把多段视频按顺序拼成一条（ffmpeg concat demuxer，-c copy 不重编码）。
    参数与编码一致时无损、极快。返回 dst。

    tone —— 拼之前是否先做「影调 / 接缝对齐」（见 fxai-common/video_tone.py）：
      None / False  不做，保持老行为（默认）
      True          接缝对齐 + 影调对齐，用默认参数
      dict          透传给 video_tone.align 的参数，例如
                    {"mode": "不启用", "seam": True, "max_offset": 20}
    分段生成必然段间跳色（模型没有跨段记忆，实测接缝处掉 1.6~2.0 灰阶，
    是正常帧间变化的近 3 倍），所以多段口播/MV 建议开。
    """
    paths = [p for p in (paths or []) if p and os.path.isfile(p)]
    if not paths:
        raise ValueError("没有可拼接的视频")
    os.makedirs(os.path.dirname(os.path.abspath(dst)) or ".", exist_ok=True)

    if tone:
        import video_tone  # 延迟导入：没装 numpy/PIL 也不影响不校正的调用
        kw = tone if isinstance(tone, dict) else {}
        tone_dir = os.path.join(os.path.dirname(os.path.abspath(dst)), "_tone")
        paths = video_tone.align(paths, outdir=tone_dir, exe=ffmpeg_bin(), **kw)

    lst = os.path.join(os.path.dirname(os.path.abspath(dst)), "_concat.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for p in paths:
            f.write("file '%s'\n" % os.path.abspath(p).replace("\\", "/").replace("'", "'\\''"))
    try:
        r = subprocess.run([ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0", "-i", lst,
                            "-c", "copy", dst], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=1800)
        if r.returncode != 0 or not os.path.isfile(dst):
            raise RuntimeError((r.stderr or b"").decode("utf-8", "replace")[-800:])
    finally:
        if os.path.isfile(lst):
            os.remove(lst)
    print("[video] 已拼接 %d 段 -> %s" % (len(paths), dst), flush=True)
    return dst


def _audio_fmt(path):
    """探测音频流格式签名 (codec_name, sample_rate, channels, bits_per_sample)，取不到返回 None。"""
    exe = shutil_which("ffprobe")
    if not exe or not path or not os.path.isfile(path):
        return None
    try:
        out = subprocess.run([exe, "-v", "error", "-show_entries",
                              "stream=codec_name,sample_rate,channels,bits_per_sample",
                              "-of", "json", path],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        j = json.loads((out.stdout or b"").decode("utf-8", "replace"))
        st = (j.get("streams") or [None])[0]
        if not st:
            return None
        return (st.get("codec_name"), st.get("sample_rate"),
                st.get("channels"), st.get("bits_per_sample"))
    except Exception:
        return None


def concat_audios(auds, dst, ar=32000, ac=1):
    """把多段音频按顺序合并成一条完整音频。

    - auds: 音频文件路径列表（wav/mp3/... 都认）。
    - dst: 合并输出路径（建议 .wav）。
    - 单人单口播场景：把 TTS 生成的每段音频先合并成一条完整音频，再整体喂给视频生成，
      分段 / 场景提示词写作全部基于这条完整音频（与 MV 那套一致）。「每条音频单独生成」
      是另一套多音频模式（--multi-audio），会让 H3 截断公式在每段边界错位，不是默认。

    合并策略（按格式自动选）：
    - 各段格式完全一致（编码/采样率/声道/位深都相同，TTS 输出常态）：直接 concat demuxer
      -c copy 无损直连，秒级、不重编码、保留原始采样率（如 CosyVoice 的 24kHz）。
    - 格式不一致：回退 filter_complex concat，统一重采样到 ar/ac（本机 ffmpeg 构建不认
      -ar 作输入选项，故放 filter 里做），保证合并成功。
    单文件直接复制；多文件返回合并后的 dst。
    """
    auds = [p for p in (auds or []) if p and os.path.isfile(p)]
    if not auds:
        raise ValueError("没有可合并的音频")
    os.makedirs(os.path.dirname(os.path.abspath(dst)) or ".", exist_ok=True)
    if len(auds) == 1:
        if os.path.normcase(os.path.abspath(auds[0])) == os.path.normcase(os.path.abspath(dst)):
            print("[audio] 单音频且已是目标文件，直接复用：%s" % dst, flush=True)
            return dst
        import shutil as _s
        _s.copyfile(auds[0], dst)
        print("[audio] 单音频，已复制为完整音频 -> %s" % dst, flush=True)
        return dst
    # 格式一致 -> 无损直连（-c copy）
    fmts = [_audio_fmt(p) for p in auds]
    same = all(f and f == fmts[0] for f in fmts)
    if same:
        lst = os.path.join(os.path.dirname(os.path.abspath(dst)), "_aconcat.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for p in auds:
                f.write("file '%s'\n" % os.path.abspath(p).replace("\\", "/").replace("'", "'\\''"))
        try:
            r = subprocess.run([ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0", "-i", lst,
                                "-c", "copy", dst], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=1800)
            if r.returncode != 0 or not os.path.isfile(dst):
                raise RuntimeError((r.stderr or b"").decode("utf-8", "replace")[-800:])
        finally:
            if os.path.isfile(lst):
                os.remove(lst)
        print("[audio] 已直连合并 %d 段 -> %s（%s %sHz %sch，无损 -c copy）"
              % (len(auds), dst, fmts[0][0], fmts[0][1], fmts[0][2]), flush=True)
        return dst
    # 格式不一致 -> 重采样后合并
    inputs = []
    for p in auds:
        inputs += ["-i", p]
    head = ""
    for i in range(len(auds)):
        head += "[%d:a]aresample=%d,aformat=channel_layouts=mono[%da];" % (i, ar, i)
    filt = head + "%sconcat=n=%d:v=0:a=1[out]" % (
        "".join("[%da]" % i for i in range(len(auds))), len(auds))
    r = subprocess.run([ffmpeg_bin(), "-y"] + inputs + ["-filter_complex", filt,
                        "-map", "[out]", "-ar", str(ar), dst],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800)
    if r.returncode != 0 or not os.path.isfile(dst):
        raise RuntimeError((r.stderr or b"").decode("utf-8", "replace")[-800:])
    print("[audio] 已重采样合并 %d 段 -> %s（%.1f kHz %dch）" % (len(auds), dst, ar / 1000.0, ac), flush=True)
    return dst


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("fxai-audio-video 已加载；gen_video / gen_videos / build_ref2va_prompt / "
          "concat_videos 可用（kind=sing 演唱 / kind=speak 口播）。")
