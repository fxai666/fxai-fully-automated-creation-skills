# -*- coding: utf-8 -*-
"""通用「一首歌 → 完整版 MV」生产脚本（fxai-mv-pipeline 技能内置，可复用）。

链路：音乐(YuE2，按歌词自动决定时长) -> 四视图人物 -> 多张纯场景图(横屏/竖屏依尺寸配置) -> 多参考完整 MV
      （每场景一套重写的 Ref2VA 六段式运镜提示词）-> 自动加标准字幕。

★★★ 创作时序（必守 · Lyric-First）★★★
1) 先生成音乐 → 拿到「真实音频时长」；
2) 【歌词先行对齐】立刻用 whisper 对整首音乐出时间轴，与我们写的歌词强制对齐：
   - 产出 字幕.srt / 字幕.ass（最终烧录用，**全流程只识别这一次**）
   - 产出 歌词时间轴.json（每句歌词的真实起止秒）
3) 用 H3 精确公式算「音频分段」（真实每段 9.41667s），再拿**真实分段时长**与字幕时间做严格交集：
   → 判定每个分段「有没有人在唱、段内第几秒唱到第几秒」→ 前奏/主歌/副歌/间奏/尾奏。
   用时间规则算出「场景图数 N」（短歌≤120s：N=分段数；长歌：N=分段数//2+1）。
4) 【关键】拿到分段表之后，才**逐段**写提示词（第 i 个分段 ↔ 第 i 条分镜，一一对应）：
   - 演唱段：写清切点时刻开口（`At 00:03.500, …`）、唇形与音频同步，歌词意象用该段真实唱到的词；
   - 前奏/间奏/尾奏：只写肢体表演与随拍律动，演唱/歌声/对口型一律不写（否则人物嘴巴乱动穿帮）。
   → 落盘为项目目录的「分镜剧本.md」（创作类文本一律 .md）。
5) 最后才生成 N 张场景图、逐段构建运镜提示词、渲染 MV、烧字幕（直接复用第 2 步的字幕）。

用法：
  python produce_song.py --song=<歌名> --phase music    # 只出音乐 + 歌词对齐 + 分镜剧本草稿，停下等逐条创作
  python produce_song.py --song=<歌名> --phase render   # 读已创作好的分镜剧本 → 出图 → MV → 烧字幕
  python produce_song.py --song=<歌名>                  # auto：有匹配剧本就渲染，否则自动铺草稿并一次跑完
  python produce_song.py --song=<歌名> --regen-subtitle # 人工发现字幕偏移/错字时，重跑识别并重合成
  （歌名也可由环境变量 SONG 提供；不用 --song 会直接报错，脚本内不写死任何歌）

★★★ 每首歌的数据都在「项目目录」里，脚本不内嵌任何歌曲内容（必守）★★★
歌再多脚本体积也不增长；加新歌 = 建项目目录 + 写这几个文件，**不改代码**。
  项目目录 {WORKSPACE}/{歌名}/
  ├ 歌曲.md      【输入·创作源】## 创作思路 / ## 曲风 / ## 穿搭 / ## 歌词
  ├ 场景素材.md  【输入】创意素材池：`## 素材 N｜名称` + img/sdesc/cam/camf/act
  │             （只提供场景氛围 + 各自运镜，**数量不由它决定**，由时间规则算）
  ├ 分镜剧本.md  【输入/输出】N 条最终分镜（唯一创作档，脚本回读解析）
  └ 制作.json    【输入·技术参数】characters / size(尺寸覆盖) / tags(输出子目录)

★★★ 项目目录与产出物归档约定（语义化命名 · 必守）★★★
每首歌一个以「歌名」命名的项目文件夹：{WORKSPACE}/{歌名}/
- 歌曲.md            ← 「## 创作思路」+「## 曲风」(style) +「## 穿搭」+「## 歌词」
- 音乐.wav           ← 生成的音乐成品（根目录，中文名）
- 成片_{W}x{H}.mp4    ← 最终 MV（带字幕），根目录，中文名+分辨率后缀（如 成片_1280x768.mp4 / 成片_768x1280.mp4）
- 字幕.ass / 字幕.srt ← 歌词字幕，根目录，中文名（不带分辨率后缀；音频相同可跨尺寸复用，单一文件）
- 图片（唯一子文件夹）：所有产出图备份
    ├── 人物1.png     （人物四视图/参考图，多人物顺延 人物2…）
    └── 场景1.png … 场景N.png（各场景图，按分镜顺序，统一阿拉伯数字编号：人物1~N、场景1~N）
- 这是「从 ComfyUI 下载/复制」的额外归档层，不改动 ComfyUI 任何参数

加新歌：① 建 {WORKSPACE}/{歌名}/；② 写 歌曲.md（曲风/穿搭/歌词，可加创作思路）+ 场景素材.md；
③ 需要时写 制作.json 指定 characters/size/tags；④ 在 {workspace}/config.json 的 characters 里配好人物参考图
（如 女主→avatar/000.png）；⑤ 跑 `python produce_song.py --song={歌名} --phase music`。
人物参考图、默认尺寸一律从配置读，不写死在代码里。
"""
# 运行时覆盖（临时、不落盘）：对话中临时要求改默认值/换人物时，由对话期改这里，不写回 config.json。
# 正常为空 {}；例：RUN_OVERRIDES = {"defaults": {"mv_width": 1080}, "characters": ["男主"]}
RUN_OVERRIDES = {}

import sys, os, json, shutil, wave, subprocess, datetime, math, re, hashlib

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
# H3 精确分段公式的单一真相源在公共件 fxai-audio-video（mv_pipeline 已把它加进 sys.path）
sys.path.insert(0, os.path.join(os.path.dirname(SKILL), "fxai-audio-video"))
import audio_video as _av
from mv_pipeline import (gen_music, gen_char_view, gen_scene_image, gen_mv,
                         url_to_rel, cleanup_song_tmp)
from mv_config import (load_config, get_character,
                       resolve_ref, cn)
# ★ 歌词精确对齐（Lyric-First）：音乐之后立刻对齐，供分镜创作判定「哪段在唱」
import lyrics_align
from lyrics_align import (run_align, classify_segments, write_timeline,
                          parse_transcript, filter_vocal_segments,
                          load_timeline, burn_subtitle, ffmpeg_path)

# 音乐工作区根：优先读环境变量 MUSIC_WORKSPACE（跨机器/换盘通用化），缺省回退默认
WORKSPACE = os.environ.get("MUSIC_WORKSPACE") or os.getcwd()

# ---------------------------------------------------------------------------
# ★★ 每首歌的创意数据一律放在「项目目录」里，脚本只放通用逻辑，不内嵌任何歌曲内容★★
# 歌再多，脚本体积也不增长；加新歌 = 建项目目录 + 写这几个文件，不改代码。
#
# 项目目录 = {WORKSPACE}/{歌名}/：
# 歌曲.md → ## 创作思路 / ## 曲风 / ## 穿搭 / ## 歌词 （创作源）
# 场景素材.md → 创意素材池：只提供「场景氛围 + 各自运镜」，数量不由它决定
# 分镜剧本.md → N 条最终分镜（唯一创作档，脚本回读）
# 制作.json → 技术参数：characters / size(尺寸覆盖) / tags(输出子目录)
# ---------------------------------------------------------------------------

def _md_slug(s):
    """歌名 → 适合做 ComfyUI 子目录名的 ASCII slug（中文名自动退化为稳定短哈希）。"""
    t = re.sub(r"[^0-9A-Za-z_-]", "", s)
    return t or ("s" + hashlib.md5(s.encode("utf-8")).hexdigest()[:6])


def parse_md_sections(md):
    """解析 Markdown 的 ## 段落 → {段名: 正文}"""
    out, cur, buf = {}, None, []
    for line in md.splitlines():
        if line.startswith("## "):
            if cur is not None:
                out[cur] = "\n".join(buf).strip()
            cur = line[3:].strip()
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf).strip()
    return out


def parse_md_blocks(md, heading):
    """解析 `## <heading> N｜名称` 块 → [{index, name, lyric, img, sdesc, cam, camf, act}]"""
    blocks, cur = [], None
    for line in md.splitlines():
        if line.startswith("## " + heading):
            cur = {"_h": line}
            blocks.append(cur)
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("歌词意象："):
            cur["lyric"] = s[len("歌词意象："):].strip()
        elif s.startswith("- ") and ":" in s:
            k, v = s[2:].split(":", 1)
            cur[k.strip()] = v.strip()
    out = []
    for i, b in enumerate(blocks, 1):
        name = b["_h"].split("｜", 1)[1].split("（")[0].strip() if "｜" in b["_h"] else ""
        item = {"index": i, "name": name, "lyric": b.get("lyric", "")}
        for k in ("img", "sdesc", "cam", "camf", "act"):
            if k in b:
                item[k] = b[k]
        out.append(item)
    return out


def _read_text(path):
    raw = open(path, "rb").read()
    return raw.decode("utf-8-sig", errors="replace")


def load_song_project(song_dir, song):
    """从项目目录读取这首歌的全部创意 + 技术参数。缺什么报什么，不静默兜底。"""
    song_md = os.path.join(song_dir, "歌曲.md")
    if not os.path.isfile(song_md):
        raise RuntimeError("项目缺少 歌曲.md（应含 ## 曲风 / ## 穿搭 / ## 歌词）：%s" % song_md)
    sec = parse_md_sections(_read_text(song_md))
    lyrics = sec.get("歌词", "").strip()
    if not lyrics:
        raise RuntimeError("歌曲.md 缺少「## 歌词」段：%s" % song_md)
    style = sec.get("曲风", "").strip()
    if not style:
        raise RuntimeError("歌曲.md 缺少「## 曲风」段：%s" % song_md)

    pool_path = os.path.join(song_dir, "场景素材.md")
    scenes = []
    if os.path.isfile(pool_path):
        scenes = [s for s in parse_md_blocks(_read_text(pool_path), "素材")
                  if "img" in s and "cam" in s]
    if not scenes:
        raise RuntimeError("项目缺少可用的 场景素材.md（至少 1 条含 img + cam 的素材）：%s" % pool_path)

    prod = {}
    prod_path = os.path.join(song_dir, "制作.json")
    if os.path.isfile(prod_path):
        try:
            prod = json.load(open(prod_path, encoding="utf-8")) or {}
        except Exception as e:
            raise RuntimeError("制作.json 解析失败：%s (%s)" % (prod_path, e))

    _t = prod.get("tags") or {}
    _slug = _md_slug(song)
    tags = {
        "music": _t.get("music") or ("song_music_%s" % _slug),
        "char": _t.get("char") or ("song_char_%s" % _slug),
        "scene": _t.get("scene") or ("song_scene_%s_%%d" % _slug),
        "mv": _t.get("mv") or ("song_mv_%s" % _slug),
    }
    return {
        "song": song,
        "concept": sec.get("创作思路", "").strip(),
        "style": style,
        # ★ 「## 音乐描述」= 直接喂给音乐模型的那段文本（可选）。
        #   M3 需要结构化 Caption（基本属性/人声/编曲三段），与给人读的「## 曲风」职责不同，
        #   故单独一段承载；缺省时回退用「## 曲风」。见 references/09-音乐生成-MiniMaxMusic3.md。
        "music_prompt": sec.get("音乐描述", "").strip(),
        "char_desc": sec.get("穿搭", "").strip(),
        # ★ 多人物各自穿搭：制作.json 的 char_outfits = {角色名: 穿搭}，优先于歌曲.md 的 ## 穿搭。
        #   双人/多人对戏时，每个角色各自写自己的穿搭。
        "char_outfits": prod.get("char_outfits") or {},
        # ★ 每首歌自定义「器乐段（前奏/间奏/尾奏）三段式表演」：制作.json 的 instrumental_beats
        #   = [[起,承,合], ...]；缺省回退内置 INSTRUMENTAL_BEATS（通用·户外自然主题）。
        #   室内/特定场景的歌（如棋室、书房）需自带一套贴合场景的动作，否则会套用山风/山脊等不搭的描述。
        "instrumental_beats": prod.get("instrumental_beats") or [],
        # ★ 音乐生成模型：制作.json 的 music_model = "yue2"（默认）/ "minimax3"（MiniMax Music 3）。
        #   minimax3 支持男女对唱（人声性别写在 Caption 的 Vocal Details），lyrics 用英文结构标签；
        #   yue2 无男女声标签，对唱只能靠风格描述碰运气。两套写法差异见
        #   references/08-音乐生成-Yue2.md 与 references/09-音乐生成-MiniMaxMusic3.md。
        "music_model": prod.get("music_model") or "",
        # ★ 音乐最长时长（秒）：仅 minimax3 生效（占位符 fxai_music_duration，模型可提前结束）。
        "music_duration": int(prod.get("music_duration") or 300),
        "lyrics": lyrics,
        "scenes": scenes,
        "camera_library": prod.get("camera_library") or [],
        "compose_variants": prod.get("compose_variants") or [],
        # ★ 项目级提示词覆盖点（可选）：让指定分镜跳过内置「站唱 / 中景特写」通用模板，
        #   用于骑乘、飞行动作等特殊表演。缺省 {} = 完全走原逻辑，不影响其它项目。
        "prompt_rules": prod.get("prompt_rules") or {},
        "characters": prod.get("characters") or [],
        "size": prod.get("size") or {},
        "tags": tags,
        "prop": prod.get("prop") or [],
    }


def resolve_song():
    """当前要生产的歌：--song=<名> / 位置参数 > 环境变量 SONG（不写死在脚本里）。"""
    for a in sys.argv[1:]:
        if a.startswith("--song="):
            return a.split("=", 1)[1].strip()
    pos = [a for a in sys.argv[1:] if not a.startswith("-")]
    if pos:
        return pos[0]
    s = os.environ.get("SONG", "").strip()
    if s:
        return s
    raise RuntimeError("未指定歌曲：用 --song=<歌名> 或环境变量 SONG；"
                       "项目目录 {WORKSPACE}/<歌名>/ 需含 歌曲.md 与 场景素材.md")


ACTIVE_SONG = resolve_song()

# 横屏：图片长边 1920（宽 1920 高 1080，16:9）
LAND_W, LAND_H = 1920, 1080

# ---------------------------------------------------------------------------
# 运镜库 / 构图变化库（供「按时间规则算出的 N 条分镜」逐条取用）
# 场景素材池被循环复用时，保证每条分镜的「运镜 + 画面构图」都不一样。
# 项目 制作.json 可用 camera_library / compose_variants 覆盖。
# ---------------------------------------------------------------------------
CAMERA_LIBRARY = [
    ("镜头从人物侧后方缓慢推近", "保持中近景，画面带轻微呼吸感"),
    ("镜头自左向右匀速平移", "横移中保持人物处于三分线"),
    ("镜头由低处缓缓抬升", "从地面植被升到人物半身"),
    ("镜头跟随人物缓慢前行跟拍", "步伐与镜头同步，轻微手持感"),
    ("镜头绕人物平稳环绕半圈", "环绕半径稳定，背景连续流动"),
    ("镜头从高处缓缓下摇", "由天空树冠落到人物身上"),
    ("镜头先快速推近再缓慢拉远", "推拉节奏贴合乐句起伏"),
    ("镜头固定，人物缓步走入画面", "静态机位，靠人物走位产生动感"),
    ("镜头贴近前景枝叶向前穿行", "前景虚化掠过，主体渐清晰"),
    ("镜头横向缓慢移动并带轻微仰角", "保持天空占比，人物略高"),
    ("镜头从人物背后绕行至正面", "绕行半圈，落定正面中景"),
    ("镜头缓慢后退拉远", "人物渐小，环境层次渐展"),
]

# 同一素材池场景被复用时追加的构图变化（只变取景/光线，不改场景本体语义）
COMPOSE_VARIANTS = [
    "广角全景构图", "低角度仰拍构图", "前景枝叶虚化构图",
    "高角度俯瞰构图", "侧逆光轮廓构图", "三分法留白构图", "近景细节构图",
]


# ---------------------------------------------------------------------------
# ★★★ 逐段提示词：演唱段 vs 器乐段（前奏/间奏/尾奏）必须分流 ★★★
# 由「歌词对齐」给出每段是否有人声、段内第几秒唱到第几秒，提示词据此写死时间轴。
# ---------------------------------------------------------------------------
# 器乐段禁用词：出现即会让模型生成张嘴/演唱动作
_SING_WORDS = ("演唱", "歌唱", "歌声", "对口型", "开口唱", "哼唱", "唱出", "清唱", "开口演唱")

# 器乐段（无人声）的人物动作库：靠肢体表演与随拍律动把画面做活，全程闭唇安静
# ★ 器乐段（无人声）人物动作库：走动（向镜头/左/右）+ 手部动作 + 比心等动态，
# 靠身体动作与随拍律动把画面做活。
# 🔴 不写负向禁令（「不点头 / 不歪头 / 不晃身」），负向词等于把该动作喂给模型，反而诱发点头。
INSTRUMENTAL_ACTS = [
    "她缓步向镜头方向走来，双手自然垂落身侧，发丝随山风轻扬，神情舒展安静",
    "她向画面左侧走出几步，衣角与发丝随风扬起，神情松弛",
    "她抬手轻轻拨开眼前垂落的枝叶，侧身让开，动作舒缓",
    "她侧身而立，任风把长发与裙摆吹起，双手在身前交叠，呼吸般轻微起伏",
    "她闭上双眼像在感受风与光，肩线随呼吸舒展，随后睁开眼，嘴角浮起一丝笑意",
    "她蹲下身拾起一片落叶举到胸前停留片刻，再轻轻摊开掌心让它飘走，动作细致温柔",
    "她背对镜头站定，随后缓缓转过身来，向镜头走出两步，神情沉静",
    "她伸手接住飘落的细碎光点，指尖轻转，向前轻迈半步，神情专注",
]

# ★ 器乐段「三段式表演序列」
# 每套写成 **起 / 承 / 合** 三个节拍，每段带：动作 + 表情 + 神态 + 与环境的互动，
# 让 9.4 秒里有层次、有呼吸、有情绪变化，而不是一个姿势撑到底。
# 走动（向镜头/左/右）+ 手部动作 + 比心等动态，靠身体动作撑满每个节拍；
# 保持「动作+表情+神态+环境互动」层次。头部动作与眼神描写一律不写（h3 册 01）。
INSTRUMENTAL_BEATS = [
    [  # 1 风·发丝
        "她立在风中，抬手将一缕被吹乱的长发别到耳后，肩线松弛，神情安静出神",
        "她缓步向镜头方向走来几步，衣角与发丝随山风扬起，双手自然垂落身侧，呼吸与风同频",
        "她停下脚步，双手交叠在身前、指尖轻轻相触，身体正对镜头，安静地定住",
    ],
    [  # 2 侧步·回望
        "她向画面左侧迈出一步，一只手轻提裙角、另一手自然垂着，神情专注柔和",
        "她俯身让指尖在身前虚虚一点又收回，肩背被天光映亮，嘴角放松、眼尾微弯",
        "她收回手抱在身前，换重心到另一只脚，身体转正对镜头，神情沉静温婉",
    ],
    [  # 3 张开双臂·接风
        "她向画面右侧走出两三步，抬手压了压被风吹起的发，长发与裙摆一起扬起，神情舒展",
        "她张开双臂像要把风接住，胸口随呼吸起伏，脸上是彻底放松的神情，脚步在原地轻轻踏节",
        "她慢慢放下手臂交叠在身前，风小下去，转过身面向镜头，安静地笑了笑",
    ],
    [  # 4 帽檐·横走
        "她向右侧挪了半步，草帽压低半张脸，指尖在身前轻轻交握",
        "她直起身，抬手扶了扶帽檐，迈步沿画面横向走去，衣角随步子轻摆",
        "她把手收回身前交握，身体转正对镜头，呼吸放缓，整个人安静地静下来",
    ],
    [  # 5 迎光·舒展
        "她慢慢向前走，天光把她的轮廓染亮，眉眼弯起",
        "她停下来迎着光闭上眼，深深吸气，双臂随呼吸缓缓打开又收回，脸上是舒展的神情",
        "她睁开眼转过身，发丝被风吹得扬起，眼里带着笑，然后拢住衣角继续向前走",
    ],
    [  # 6 举臂·接光
        "她双手不自觉地举到胸前、指尖轻触，像接住坠落的光，神情渐沉静",
        "她向画面左侧缓步走开几步，闭上眼，睫毛在脸上投下浅影，嘴角浮起很轻的笑",
        "她睁开眼转过身面向镜头，手从胸前放下垂在身侧，整个人融进天光里，安静地定格",
    ],
    [  # 7 转身·走来
        "她背对镜头站定，肩线随呼吸缓慢起伏，一只手轻轻搭在身侧，站姿松弛",
        "她像听见了什么，缓缓转过身来，向镜头走出两三步，发丝随转身扬起，神情从茫然渐定",
        "她完全转过来面对镜头，双手自然垂落，眉眼放松，最后安静停在这个姿势",
    ],
    [  # 8 指尖光点·比心
        "她伸出手接住飘过的细碎光点，指尖轻轻一转，神情专注又有点孩子气",
        "她把手举到胸前停留片刻，再松开让光点落下去，向前轻跳半步，笑意从眼底漫到嘴角",
        "她把手放下，向前走了一步，神情安静下来，最后轻轻合了下眼又睁开",
    ],
]


def has_singing_word(text):
    t = text or ""
    return any(w in t for w in _SING_WORDS)


# ★ 首尾安全区：视频片段实际生成时长会漂移，故每段**开头 1 秒**与**结尾 1 秒**
# 一律设为「过渡安全区」（中间才是演唱/表演区）：
# 段首 1s：承接上一段尾帧 → 闭唇、安静、不做口型（防止继承上一段的张嘴画面）
# 段尾 1s：提前收束 → 闭唇、动作渐停（防止本段尾帧的张嘴状态被下一段继承）
HEAD_GUARD = 1.0
TAIL_GUARD = 1.0


# —— 道具参考图生成规范（硬规则，所有歌曲统一，勿在 per-song 提示词里另写背景/承托物）——
# 纯白摄影棚背景 + 画面只有「道具本身」：承托物/台座/桌子/人物/场景/杂物一律不入画。
# 唯一例外：道具本身为白色或浅色（prop["light_prop"]=true）时，背景改用有对比度的
# 纯色（深灰/墨色），保证道具在图上可见。两条规则（背景 + 仅道具本身）都不可破。
PROP_BG_WHITE = (
    "背景为绝对的纯白色（摄影棚级纯白背景，无任何渐变、阴影、光斑或杂物，纯白到边缘）；"
    "画面中仅有这一件道具本身，无承托物、无台座、无桌子、无人物、无山水、无任何其他物体或元素；"
    "专业产品静物摄影质感，构图居中，道具清晰呈现于纯白之上。"
)
PROP_BG_CONTRAST = (
    "背景为与道具形成明显对比的纯色（深灰或墨色纯色背景，无任何渐变、阴影、光斑或杂物）；"
    "画面中仅有这一件道具本身，无承托物、无台座、无桌子、无人物、无山水、无任何其他物体或元素；"
    "专业产品静物摄影质感，构图居中，道具清晰呈现。"
)


def build_prop_render_prompt(prop):
    """按【道具参考图规范】构造生图提示词：纯白背景 + 仅道具本身（light_prop 时改对比背景）。

    prop：制作.json 的 prop 单项（name/desc 必填，prompt/light_prop 可选）。
    - prompt 写了也只作「道具本体描述」用；背景与「仅道具本身」由本函数强制追加，
      作者不必（也不应）在提示词里写背景/琴案/场景——写了也会被规范后缀覆盖语义。
    """
    base = (prop.get("prompt") or prop.get("desc") or "").strip()
    bg = PROP_BG_CONTRAST if prop.get("light_prop") else PROP_BG_WHITE
    return (base + bg).strip()


def resolve_prop_rels(song_dir, props):
    """取/生成「道具参考图」（如某首歌里用到的某件道具），返回 [(rel, desc), ...]（与 props 顺序一致）。

    - props：制作.json 的 prop 列表，每项为 {name, desc(提示词里声明的道具描述),
      prompt(可选·仅道具本体描述), tag, subdir, num, light_prop(可选)}；
      subdir/num 决定服务端落盘目录与固定文件名（同名覆盖）。
    - 生图提示词由 build_prop_render_prompt 按【道具参考图规范】统一构造
      （纯白背景 + 仅道具本身，light_prop 时改对比背景），不依赖 per-song 手写的背景描述。
    - 优先读 _assets.json 的 prop_rels（预览阶段已写入，或上次完整渲染已生成）；
      缺失的才按 props 现生成，并写回 _assets.json，供 --mv-only 复用。
    - rel 格式 = "subdir/NNN.png"（与 scene_rels/char_rels 一致），直接进 multiref 图片列表。
    """
    cache = os.path.join(song_dir, "_assets.json")
    d = json.load(open(cache, encoding="utf-8")) if os.path.isfile(cache) else {}
    existing = list(d.get("prop_rels") or [])
    rels = list(existing)
    for p in props[len(rels):]:
        _tag = p.get("tag") or ("prop_%d" % abs(hash(p.get("name", "prop"))))
        _sub = p.get("subdir") or "song_ref_prop"
        _num = int(p.get("num", 0))
        _pr = build_prop_render_prompt(p)
        if not _pr:
            rels.append("")
            continue
        _dst, _rel = gen_scene_image(_pr, role=None, w=1080, h=1920,
                                     tag=_tag, subdir=_sub, num=_num, force=False)
        rels.append(_rel)
    if len(rels) > len(existing):
        d["prop_rels"] = rels
        json.dump(d, open(cache, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    descs = [p.get("desc", "") for p in props]
    return [(rels[i], descs[i]) for i in range(len(props))]


def resolve_existing_assets(song_dir, n_scenes, log_names=None):
    """--mv-only 用：复用已生成好的人物图/场景图，跳过生图。

    优先级：① 读缓存 _assets.json；② 从运行日志里的 prompt_id 反查 fxai 结果（最准）；
    ③ 兜底：列服务端目录取最后 N 个文件。
    返回 (char_rels, [scene_rel, ...], [prop_rel, ...])；char_rels 为人物图相对路径列表（支持多人物），找不到返回 ([], [], [])。

⚠️ 默认扫描目录下所有 *.log 并按修改时间倒序（最新优先），确保任何命名约定下都能复用已生成的图。
"""
    if log_names is None:
        import glob as _g
        log_names = [os.path.basename(p) for p in
                     sorted(_g.glob(os.path.join(song_dir, "*.log")),
                            key=os.path.getmtime, reverse=True)]
    cache = os.path.join(song_dir, "_assets.json")
    d = {}  # ★ 兜底初始化：缓存不存在时，反查流程末尾写缓存会引用 d，未初始化会 UnboundLocalError
    if os.path.isfile(cache):
        try:
            d = json.load(open(cache, encoding="utf-8"))
            if d.get("char_rels") and len(d.get("scene_rels") or []) >= n_scenes:
                return list(d["char_rels"]), list(d["scene_rels"])[:n_scenes], list(d.get("prop_rels") or [])
        except Exception:
            pass
    import urllib.request
    pids = {"char": [], "scene": []}
    for name in log_names:
        p = os.path.join(song_dir, name)
        if not os.path.isfile(p):
            continue
        try:
            txt = open(p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for line in txt.splitlines():
            m = re.search(r"\[(charview\d+|scene):[^\]]+\] queued ([0-9a-f\-]{8,})", line)
            if not m:
                continue
            pids["char" if m.group(1).startswith("charview") else "scene"].append(m.group(2))
    if not (pids["char"] and pids["scene"]):
        return None, [], []
    base = json.load(open(os.path.join(os.path.expanduser("~"), ".workbuddy",
                                       "fxai_global.json"), encoding="utf-8")).get("comfy_url",
                                                                                   "http://127.0.0.1:8188")
    def query(ids):
        out = []
        for i in ids:
            try:
                req = urllib.request.Request(
                    base.rstrip("/") + "/fxai/tasks/result",
                    data=json.dumps({"prompt_ids": i}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=15) as r:
                    d = json.loads(r.read().decode("utf-8"))
                u = (d.get(i) or {}).get("url")
                if u:
                    out.append(url_to_rel(u))
            except Exception:
                pass
        return out
    char_rels = query(pids["char"])  # 所有 charview 任务的产物（多人物时多个）
    _seen_c, _uniq_c = set(), []
    for r in char_rels:  # 去重并保持顺序
        if r and r not in _seen_c:
            _seen_c.add(r)
            _uniq_c.append(r)
    char_rels = _uniq_c
    scene_rels = query(pids["scene"])
    seen, uniq = set(), []
    for r in scene_rels:  # 去重并保持顺序
        if r and r not in seen:
            seen.add(r)
            uniq.append(r)
    scene_rels = uniq[:n_scenes]
    if char_rels and len(scene_rels) >= n_scenes:
        json.dump({"char_rels": char_rels, "scene_rels": scene_rels,
                   "prop_rels": d.get("prop_rels") or []},
                  open(cache, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return char_rels, scene_rels, (d.get("prop_rels") or [])


def _parse_sing_plan(sing, k, L):
    """解析「演唱者计划」→ [(subject_idx, start, end), ...]（相对秒，演唱区）。

★ 双人对唱用：写在 分镜剧本.md 的 `- sing:` 行。调用点已把「角色名」转成 Subject 序号。
  "2"                        → 整段 <Subject 2> 开口唱
  "2(0.00-4.50) 1(4.50-9.42)" → 段内切换：前 4.5s S2 唱、后段 S1 唱（精确到秒）
  "1 2"（都不带时间）          → 演唱区内两人依次均分
  ""（空）                     → 默认 <Subject 1> 整段（向后兼容单人物歌）
"""
    da, db = HEAD_GUARD, max(HEAD_GUARD + 0.5, round(L - TAIL_GUARD, 2))
    s = (sing or "").strip()
    if not s:
        return [(1, da, db)]
    toks = re.findall(r"(\d+)\s*(?:\(\s*([\d.]+)\s*-\s*([\d.]+)\s*\))?", s)
    toks = [(int(a), b, c) for (a, b, c) in toks]
    if not toks:
        return [(1, da, db)]
    timed = [(a, float(b), float(c)) for (a, b, c) in toks if b]
    if not timed:  # 都没带时间 → 在演唱区内均分
        n = len(toks)
        span = (db - da) / n
        return [(max(1, min(k, t[0])), round(da + i * span, 2), round(da + (i + 1) * span, 2))
                for i, t in enumerate(toks)]
    out = []
    for a, b, c in timed:
        out.append((max(1, min(k, a)), max(da, b), min(db, c)))
    return out or [(1, da, db)]


def build_segment_prompt(char_descs, sdesc, cam, camf, act, seg, next_seg=None, prop_descs=None,
                         inst_beats=None, prompt_rules=None):
    """★ 按「该音频分段的真实人声状态」生成 Ref2VA 六段式提示词，支持多人物。

char_descs: list[str]，每元素 = 一个人物的**身份词**（`人物` / `年轻女子`），
            🔴 不传外观类目、不传具体颜色款式（铁律 17：类目归延续分析）；
            与多参考图片列表（人物图在前、场景图在后）一一对应：
            <Picture 1>~<Picture K> = 人物1~K，<Picture K+1> = 场景。
seg: 来自 lyrics_align.classify_segments 的分段信息
     {index, start, end, len, type, vocal, vocal_windows, lyrics}
- vocal=True  ：写明切点时刻起开口演唱（`At 00:03.500, …`）、唇形与音频同步，段首 1 秒过渡时闭唇。
- vocal=False ：全段无人声 → 明确「不演唱、不开口、双唇闭合」，改靠肢体表演做画面动感。
"""
    if not isinstance(char_descs, list):
        char_descs = [char_descs]
    k = len(char_descs)
    scene_idx = k + 1
    L = float(seg.get("len") or 0)
    wins = seg.get("vocal_windows") or []
    stype = seg.get("type") or ("演唱" if seg.get("vocal") else "间奏")
    act = (act or "").strip()
    # 段首过渡（承接上一段尾帧）：所有段的前 1 秒都闭唇安静，继承上一段的张嘴画面会穿帮
    # ★ prompt_rules 可按项目覆盖（骑乘段不该写「安静站定」）；缺省完全保持原文。
    _rules = prompt_rules or {}
    head = ((_rules.get("head_text") or "").strip()
            or "段首过渡（承接上一段尾帧）：人物双唇自然闭合、安静站定，只有轻微呼吸与肩线起伏，不做口型；")

    # —— subject_definitions（人物1~K + 场景K+1）——
    # 🔴 铁律 17：主体定义只写「<Picture N> 中的<身份词>。」，类目清单归下面的延续分析
    subj_defs = []
    for i, cd in enumerate(char_descs, 1):
        subj_defs.append("<Subject %d> 是 <Picture %d> 中的%s。"
                         % (i, i, _av.person_identity(cd, default="人物")))
    subj_defs.append("<Subject %d> 是 <Picture %d> 中的%s。"
                     % (scene_idx, scene_idx, _av.subject_identity(sdesc, default="场景")))
    # ★ 道具参考（某件道具）：恒定追加在场景之后，<Picture/Subject> 编号 = scene_idx+1 起，
    #   与 audio_video.build_multiref_lines._row_imgs 把 prop_rels 追加在每行图片列表末尾保持一致。
    _prop_descs = prop_descs or []
    for _j, _pd in enumerate(_prop_descs, 1):
        _p = scene_idx + _j
        subj_defs.append("<Subject %d> 是 <Picture %d> 中的%s。"
                         % (_p, _p, _av.subject_identity(_pd, default="道具")))
    # —— retention_analysis（保留类目清单写在这里）——
    retain = []
    for i, cd in enumerate(char_descs, 1):
        retain.append("<Subject %d> (appears in [Shot 1]): fully_preserved - %s" % (i, _av.KEEP_PERSON))
    retain.append("<Subject %d> (appears in [Shot 1]): fully_preserved - %s" % (scene_idx, _av.KEEP_SCENE))
    for _j, _pd in enumerate(_prop_descs, 1):
        _p = scene_idx + _j
        retain.append("<Subject %d> (appears in [Shot 1]): fully_preserved - %s" % (_p, _av.KEEP_PROP))

    people = "、".join("<Subject %d>" % i for i in range(1, k + 1))
    pics = "、".join("<Picture %d>" % i for i in range(1, k + 1))
    _prop_pics = "、".join("<Picture %d>" % (scene_idx + _j) for _j in range(1, len(_prop_descs) + 1))
    _prop_note = (" %s 作为道具参考，需完整保留其形制与质感。" % _prop_pics) if _prop_descs else ""

    if seg.get("vocal"):
        # ★ 演唱者计划：默认 <Subject 1>；双人对唱时按 seg["sing"] 指定（支持段内秒级切换）
        plan = _parse_sing_plan(seg.get("sing"), k, L)
        _singers = []
        for _sj, _a, _b in plan:
            if _sj not in _singers:
                _singers.append(_sj)
        _sing_ref = "、".join("<Subject %d> (S%d)" % (sj, sj) for sj in _singers)
        audio_desc = ("<Audio 1> 是本段对应的原声音轨（含清晰人声演唱与伴奏），"
                      "作为 %s 的音色参考和目标视频的完整最终音轨。" % _sing_ref)
        _duet = "（本段为对唱，%s 分别开口）" % _sing_ref if len(_singers) > 1 else ""
        summary = ("[reference generation + audio reuse] 目标视频展示 %s 在 <Subject %d> 中演唱。"
                   "%s 作为人物外观参考，<Picture %d> 作为场景背景参考，<Audio 1> 作为原声音频。"
                   "本段为「%s」，音频中有人声演唱%s。%s" % (
                       people, scene_idx, pics, scene_idx, stype, _duet, _prop_note))
        # 按演唱者计划写成明确时间轴（段首/尾 1s 仍是过渡安全区）
        _parts = []
        _cur = HEAD_GUARD
        # ★ 内置模板是「站唱」表演（轻步移动 / 原地轻踏 / 中景做手势），会把骑乘、飞行等
        #   特殊 act 覆盖成步行。项目可用 prompt_rules 覆盖；缺省完全保持原文，其它项目不受影响。
        _pre_tail = ((_rules.get("pre_sing_text") or "").strip()
                     or "双唇仍保持闭合，只调整呼吸、肩线随乐句轻移，双脚在原地轻轻踏节；")
        _perf_tail = ((_rules.get("sing_tail") or "").strip()
                      or ("身形随乐句在画面中轻步移动（向镜头 / 向侧方 / 原地轻踏），"
                          "双手随乐句在身前做出柔和而富有表现力的手势，肩线与重心随乐句轻轻换向；"))
        for _sj, _a, _b in plan:
            if _a > _cur + 0.05:
                _parts.append("%s （起唱前）：%s" % (_av.fmt_at(_cur), _pre_tail))
            _parts.append(
                "%s （演唱）：<Subject %d> (S%d) 开口演唱，唇形与 <Audio 1> 中的人声精准同步开合，%s，%s"
                % (_av.fmt_at(_a), _sj, _sj, act or "表情安静专注", _perf_tail))
            _cur = _b
        if _cur < round(L, 2):
            _parts.append("%s （收束）：最后一个音符落下后双唇轻轻闭合，动作放缓，定格为柔和宁静的神情。"
                          % _av.fmt_at(_cur))
        timeline = head + "\n" + "\n".join(_parts)
        soundscape = "来自 <Audio 1> 的复制人声演唱与伴奏贯穿全程。"
    else:
        audio_desc = ("<Audio 1> 是本段对应的原声伴奏音轨（纯器乐，**没有人声演唱**），"
                      "作为目标视频的完整最终音轨直接复用。")
        summary = ("[reference generation + audio reuse] 目标视频展示 %s 在 <Subject %d> 中。"
                   "%s 作为人物外观参考，<Picture %d> 作为场景背景参考，<Audio 1> 作为原声音频。"
                   "本段为「%s」，音频是纯器乐、没有人声，人物全程不演唱。%s" % (people, scene_idx, pics, scene_idx, stype, _prop_note))
        # 器乐段：只出现肢体表演与口型闭合状态。整段拆成【起/承/合】三段表演，每段都写动作+表情+神态。
        _idx = int(seg.get("index", 1)) - 1
        _beats_lib = inst_beats or INSTRUMENTAL_BEATS  # ★ 优先用本歌自带器乐段动作（制作.json instrumental_beats）
        beats = _beats_lib[_idx % len(_beats_lib)]
        _a = HEAD_GUARD
        _b = round(max(HEAD_GUARD + 0.5, L - TAIL_GUARD), 2)
        _span = max(0.6, _b - _a)
        _t1 = round(_a + _span * 0.36, 2)
        _t2 = round(_a + _span * 0.70, 2)
        tail = ("%s （段尾过渡）：动作渐停、双唇保持闭合，安静定格，把画面交给下一段。"
                % _av.fmt_at(_b))
        timeline = (
            head + "\n"
            + ("%s （全段无人声，不演唱、不开口、不做口型，双唇始终自然闭合）——【起】%s；\n"
               % (_av.fmt_at(_a), beats[0]))
            + ("%s ——【承】%s；\n" % (_av.fmt_at(_t1), beats[1]))
            + ("%s ——【合】%s，动作随乐句渐缓；\n" % (_av.fmt_at(_t2), beats[2]))
            + tail
        )
        soundscape = "来自 <Audio 1> 的复制纯器乐伴奏与环境声音贯穿全程，没有人声。"

    # ★ 项目级覆盖：主体/坐骑数量锁定 + 取景规则。缺省 = 原「中景构图」文案。
    _scene_line = (((_rules.get("scene_rules") or "").strip() + "\n")
                   if (_rules.get("scene_rules") or "").strip() else "")
    _frame_tail = ((_rules.get("frame_rule") or "").strip()
                   or "画面始终保持人物略偏中心的中景构图。")
    return (
        "subject_definitions:\n"
        + "\n".join(subj_defs) + "\n" + audio_desc + "\n"
        "\nsummary:\n" + summary + "\n"
        "\nretention_analysis:\n"
        + "\n".join(retain)
        + "\n<Audio 1>: fully_copy - <Audio 1> 作为目标视频的完整最终音轨直接复用。\n"
        "\ndetailed_description:\n"
        "目标视频采用电影感、自然主义的音乐视频风格。\n"
        + _scene_line
        + ("[Shot 1] 本段共 %.2f 秒，严格按以下时间轴表演：\n%s\n" % (L, timeline))
        + cam + "，" + camf + "，" + _frame_tail + "\n"
        + (("视频中出现的道具须严格参照 %s 等道具参考图，形制与质感保持一致，不可凭空改动。\n"
            % _prop_pics) if _prop_descs else "")
        + "\noverall_soundscape:\n" + soundscape + "\n"
        "\nnon_diegetic_music:\n"
        "无"
    )


def build_scene_prompt(char_descs, sdesc, cam, camf, act, seg=None):
    """兼容旧调用：未传分段信息时按「演唱段」处理。char_descs 可传 list 或单值。"""
    if not isinstance(char_descs, list):
        char_descs = [char_descs]
    if seg is None:
        seg = {"index": 1, "len": 10.0, "type": "演唱", "vocal": True,
               "vocal_windows": [(1.0, 10.0)]}
    return build_segment_prompt(char_descs, sdesc, cam, camf, act, seg)


def audio_duration(wav_path):
    try:
        with wave.open(wav_path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ★★★ MiniMax / H3 精确分段算法（公共件 fxai-audio-video，本文件只做转发）★★★
# 与节点 FxAiAudioSegmenterV2.select_segment(目标模型="MiniMax") 逐行同构。
# 传「平均分段时长 = 10」时节点先向下对齐到 17k+5 网格：240 帧 → 226 帧 → 9.41667 s/段。
# **分段数一律用本公式算，`ceil(时长/10)` 估算会差 1 段**，场景数/时间窗/歌词对位会全错。
# 公式本体见 fxai-audio-video/audio_video.py：h3_align_down / h3_segments / h3_segment_count。
# ---------------------------------------------------------------------------
H3_FPS = 24


def h3_align_down(frames):
    """最大的 17k+5 <= frames（转调公共件）。"""
    return _av.h3_align_down(frames)


def h3_segments(duration, avg_seg=10.0, fps=H3_FPS):
    """一条音频按 H3 规则切段 → (每段秒数 list, 每段 (start,end) 边界 list)（转调公共件）。"""
    return _av.h3_segments(duration, avg_seg, fps)


def h3_segment_count(duration, avg_seg=10.0):
    """只要分段数（转调公共件）。"""
    return _av.h3_segment_count(duration, avg_seg)


def split_lyrics_by_windows(lyrics, n, weights=None):
    """把歌词按权重近似分配到 n 个时间窗，返回每窗的歌词文本。

weights=None → 按字数权重；传入窗长列表 → 按「时间占比」分配（更准，推荐）。
★ 用途：写完音乐、算出场景数 N 之后，给每一条分镜提供「这段时间大概唱到哪句」，
  以便逐段对照歌词意境写场景与运镜（真实时间戳由后面 whisper 字幕阶段给出）。
"""
    lines = [l.strip() for l in lyrics.splitlines()
             if l.strip() and not l.strip().startswith("[")]
    if not lines:
        return [""] * n
    if weights and len(weights) == n and sum(float(x) for x in weights) > 0:
        w = [max(1e-6, float(x)) for x in weights]  # 按真实窗长占比分配（推荐）
    else:
        w = [1.0] * n  # 退化：均分
    total = float(sum(w))
    out, buf, acc, idx = [], [], 0.0, 0
    # 歌词侧累计权重（按字数）
    lw = [max(1, len(l)) for l in lines]
    ltot = float(sum(lw))
    cum = 0.0
    for i in range(n):
        cum += w[i]
        target = (cum / total) * ltot  # 该窗结束时应唱到的字数位置
        while idx < len(lines) and (acc + lw[idx]) <= target + 1e-6 and (len(buf) == 0 or acc < target):
            buf.append(lines[idx])
            acc += lw[idx]
            idx += 1
        if not buf and idx < len(lines):  # 该窗至少分到一句
            buf.append(lines[idx])
            acc += lw[idx]
            idx += 1
        out.append(" / ".join(buf))
        buf = []
    # 余下未分配的歌词并入最后一窗
    if idx < len(lines):
        out[-1] = (out[-1] + " / " + " / ".join(lines[idx:])).strip(" /")
    return out


def build_storyboard(spec, n_scenes, seg_info, duration=None, scene_overrides=None):
    """★ 核心：**第 i 条分镜 ↔ 第 i 个音频分段，一一对应**。

场景图仍按时间规则只出 n_scenes 张，但 **提示词逐段独立**（行数 = 分段数），
使「前奏/间奏不演唱」「某段从切点时刻开唱」能精确写进每一段。

seg_info: lyrics_align.classify_segments 的结果（含 type / vocal / vocal_windows / lyrics）
- 前 n_scenes 条：带 img（要真正生成场景图），可加构图变体让画面条条不同。
- 之后的条：img 标为「复用场景X」，只写该段自己的运镜与动作。
- 无人声段（前奏/间奏/尾奏）：act 一律换成器乐动作库的肢体表演，演唱类描写不写进 act。
"""
    pool = spec["scenes"]
    cam_lib = spec.get("camera_library") or CAMERA_LIBRARY
    variants = spec.get("compose_variants") or COMPOSE_VARIANTS

    # 场景图索引 j(0起) → 该图的 (img, sdesc, name)：前 n_scenes 张按素材池循环铺
    # scene_overrides：旧剧本（按场景创作的 N 条）传进来，直接继承已创作内容，不浪费创作
    scene_table = []
    for j in range(n_scenes):
        base = pool[j % len(pool)]
        img = base["img"]
        if j >= len(pool):  # 素材池被循环复用 → 追加构图变体，防止雷同
            img = img + "，" + variants[(j - len(pool)) % len(variants)]
        item = {"img": img, "sdesc": base["sdesc"], "name": base["name"],
                "cam": base["cam"], "camf": base["camf"], "act": base["act"]}
        o = scene_overrides[j] if (scene_overrides and j < len(scene_overrides)) else None
        if o:
            for k in ("img", "sdesc", "cam", "camf", "act", "name"):
                v = (o.get(k) or "").strip()
                if v and not v.startswith("复用场景"):
                    item[k] = v
        scene_table.append(item)

    # ★ 歌词感知场景图：有真实歌词的片段，场景图提示词贴近该段歌词的
    # 环境意象；纯器乐段 / 无唱词段（歌词意象为空）→ 保持素材池主题自由发挥。
    # 多参考铁律：场景图必须空镜，故明确「只取自然/环境意象、不出现人物」。
    if not scene_overrides:  # 仅自动生成时注入；人工已创作的 img 尊重其原意
        _seg_by_scene = {}
        for _i, _sg in enumerate(seg_info, 1):
            _j = (_i - 1) % n_scenes
            if _sg.get("vocal") and _sg.get("lyrics"):
                _seg_by_scene.setdefault(_j, _sg)  # 每个场景图取首个有歌词的演唱段
        for _j, _sg in _seg_by_scene.items():
            _lyr = " / ".join(x.get("lyric", "") for x in _sg.get("lyrics", [])).strip()
            if _lyr and 0 <= _j < len(scene_table):
                scene_table[_j]["img"] = (
                    scene_table[_j]["img"]
                    + "；画面环境呼应此段歌词意境：「" + _lyr
                    + "」，但只表现其中的自然与环境意象（花田、光线、微风、天空、水面等），"
                    + "保持空镜、不出现任何人物")

    out = []
    for i, sg in enumerate(seg_info, 1):
        ref = (i - 1) % n_scenes  # 该段引用的场景图索引（0 起）
        tb = scene_table[ref]
        if i <= n_scenes:
            img = tb["img"]  # 前 N 条：这张图要真正生成
        else:
            img = "复用场景%s（无需修改）" % cn(ref + 1)
        if i <= len(pool):
            cam, camf = tb["cam"], tb["camf"]  # 素材自带的原创运镜
        else:
            cam, camf = cam_lib[(i - 1) % len(cam_lib)]
        if sg.get("vocal"):
            act = tb["act"]
        else:
            # ★ 器乐段草稿动作：优先用本歌自带 instrumental_beats（双人/贴合场景），
            #   缺省才回退内置单人动作库（通用·户外自然主题）。
            #   取该组「起」拍作为草稿 act；完整【起/承/合】在提示词层展开。
            _ib = spec.get("instrumental_beats") or []
            if _ib:
                _grp = _ib[(i - 1) % len(_ib)]
                if isinstance(_grp, (list, tuple)) and _grp:
                    act = _grp[0]
                elif isinstance(_grp, str) and _grp.strip():
                    act = _grp
                else:
                    act = INSTRUMENTAL_ACTS[(i - 1) % len(INSTRUMENTAL_ACTS)]
            else:
                act = INSTRUMENTAL_ACTS[(i - 1) % len(INSTRUMENTAL_ACTS)]
        lyric = " / ".join(x["lyric"] for x in sg.get("lyrics", [])) \
            or ("（%s·无唱词）" % (sg.get("type") or "器乐"))
        out.append({
            "index": i,
            "start": sg.get("start", 0),
            "end": sg.get("end", 0),
            "len": sg.get("len", 0),
            "type": sg.get("type", ""),
            "vocal": bool(sg.get("vocal")),
            "vocal_windows": sg.get("vocal_windows") or [],
            "scene_ref": ref + 1,  # 引用的场景图序号（1 起）
            "lyric": lyric,
            "name": tb["name"],
            "img": img,  # 场景图提示词（前 N 条用于生图）
            "sdesc": tb["sdesc"],
            "cam": cam,  # 该段运镜（主）
            "camf": camf,  # 该段运镜细化
            "act": act,  # 该段人物动态（器乐段已换为肢体表演）
        })
    return out


def render_storyboard_md(scenes, song, duration, seg, seg_sec, seg_secs=None, n_scenes=None, n_chars=1):
    """把 N 条分镜渲染成 Markdown 创作档（★ 2026-09-29：创作类文本一律 .md）。

★ 两套模板，按角色数自动切换（互不干扰）：
  - 单人（n_chars≤1）：基础字段 img/sdesc/cam/camf/act —— 与旧版完全一致。
  - 双人/多人（n_chars>1）：额外输出 `- sing:` 演唱者字段 + 对唱规范说明（见 references/07-双人对唱.md）。

结构（脚本可回读解析）：
  ## 分镜 <i>｜<场景名>（<start>s - <end>s）
  歌词意象：…
  - img / sdesc / cam / camf / act [ / sing（双人模板）]：…
"""
    per_seg = h3_align_down(round(seg_sec * H3_FPS)) / float(H3_FPS) if seg_sec else 0.0
    n_scenes = n_scenes or len(scenes)
    lines = [
        "# 分镜剧本 · %s" % song,
        "",
        "- 歌曲时长：%.2f s" % (duration or 0),
        "- 音频分段（H3 精确）：%d 段｜请求每段 %ss，服务端向下对齐为 **%.5fs**（%d 帧 @ %dfps，17k+5 网格）" % (
            seg, seg_sec, per_seg, h3_align_down(round(seg_sec * H3_FPS)), H3_FPS),
        "- 分段时长序列：%s" % (", ".join("%.2f" % x for x in (seg_secs or [])) or "-"),
        "- 场景图：%d 张（时间规则下限 与 场景素材.md 规划数 取大 → max(下限, 规划)，已是规划上限）→ 逐段循环引用" % n_scenes,
        "- 歌词对齐：字幕.ass / 歌词时间轴.json（whisper 强制对齐，音乐出来即生成，成片直接复用）",
        "",
        "> 铁律：第 i 条分镜 = 第 i 个音频分段，**一一对应**（lines_data 行数 = 分段数）。",
        "> 「是否演唱」由该段内是否命中字幕决定：前奏/间奏/尾奏只写肢体表演与随拍律动，演唱、歌声、对口型都不写。",
        "> 每段的首 1s 与尾 1s 是过渡安全区（实际切片会漂移），提示词里已自动处理，创作时不必再写。",
    ]
    if n_chars > 1:
        lines += [
            "> ★ 本档为【对唱模板】（%d 人）：`- sing:` 指定谁开口 —— 写角色名（如 `女主`/`男主`）表示整段由他唱；" % n_chars,
            ">   段内切换写 `男主(0.00-4.50) 女主(4.50-9.42)`（秒为**段内相对秒**，可精确到 0.01s）。留空则默认第一个角色整段唱。",
            ">   完整规范见 references/07-双人对唱.md。",
        ]
    else:
        lines.append("> 本档为【独唱模板】（1 人）：演唱段默认由该角色开口，无需 sing 字段。")
    lines.append("")
    for sc in scenes:
        lines.append("## 分镜 %d｜%s（%.2fs - %.2fs）" % (
            sc.get("index", 0), sc.get("name", ""),
            sc.get("start", 0), sc.get("end", 0)))
        lines.append("段落类型：%s（%s）" % (sc.get("type", ""), "有人声" if sc.get("vocal") else "无人声"))
        _w = sc.get("vocal_windows") or []
        lines.append("段内人声：%s" % (
            "、".join("%.2fs-%.2fs" % (a, b) for a, b in _w) if _w else "无"))
        lines.append("引用场景图：%s" % sc.get("scene_ref", ""))
        lines.append("歌词意象：%s" % sc.get("lyric", ""))
        lines.append("")
        for key in ("img", "sdesc", "cam", "camf", "act"):
            lines.append("- %s: %s" % (key, sc.get(key, "")))
        if n_chars > 1:  # ★ 对唱模板才输出演唱者字段（独唱模板与旧版完全一致）
            lines.append("- sing: %s" % sc.get("sing", ""))
        lines.append("")
    return "\n".join(lines) + "\n"


def load_storyboard(path):
    """读取已写好的分镜剧本（Markdown 创作档，Agent/人工逐条创作后回读解析）。"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return None
    blocks, cur = [], None
    for line in text.splitlines():
        if line.startswith("## 分镜"):
            cur = {"_heading": line}
            blocks.append(cur)
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("歌词意象："):
            cur["lyric"] = s[len("歌词意象："):].strip()
        elif s.startswith("段落类型："):
            _t = s[len("段落类型："):].strip()
            cur["type"] = _t.split("（")[0].strip()
            cur["vocal"] = ("无人声" not in _t)
        elif s.startswith("段内人声："):
            _w = s[len("段内人声："):].strip()
            cur["vocal_windows"] = []
            if _w and _w != "无":
                for part in _w.replace("，", "、").split("、"):
                    try:
                        a, bb = part.split("-")
                        cur["vocal_windows"].append(
                            (float(a.rstrip("s")), float(bb.rstrip("s"))))
                    except Exception:
                        pass
        elif s.startswith("引用场景图："):
            try:
                cur["scene_ref"] = int(s[len("引用场景图："):].strip())
            except Exception:
                pass
        elif s.startswith("- ") and ":" in s:
            k, v = s[2:].split(":", 1)
            cur[k.strip()] = v.strip()
    out = []
    for i, b in enumerate(blocks, 1):
        try:
            idx = int(b["_heading"].replace("## 分镜", "").split("｜")[0].strip())
        except Exception:
            idx = i
        name, start, end = "", 0.0, 0.0
        if "｜" in b["_heading"]:
            tail = b["_heading"].split("｜", 1)[1]
            name = tail.split("（")[0].strip()
            try:
                se = tail.split("（", 1)[1].rstrip("）").split(" - ")
                start = float(se[0].rstrip("s"))
                end = float(se[1].rstrip("s"))
            except Exception:
                pass
        if not all(k in b for k in ("img", "sdesc", "cam", "camf", "act")):
            continue  # 字段不全视为未创作完，跳过（上层会重建）
        out.append({
            "index": idx, "start": start, "end": end,
            "len": round(end - start, 2),
            "type": b.get("type", ""), "vocal": bool(b.get("vocal", True)),
            "vocal_windows": b.get("vocal_windows") or [],
            "scene_ref": b.get("scene_ref") or idx,
            "lyric": b.get("lyric", ""), "name": name,
            "img": b["img"], "sdesc": b["sdesc"],
            "cam": b["cam"], "camf": b["camf"], "act": b["act"],
            "sing": b.get("sing", ""),  # ★ 演唱者标注（双人对唱）：如「男主」或「男主(0.00-4.50) 女主(4.50-9.42)」
        })
    return out or None


def sanitize(name):
    for ch in '/\\:*?"<>|':
        name = name.replace(ch, "_")
    return name


def archive(src, dst_dir, name):
    """把 ComfyUI 本地成品（下载/复制来源）归档进项目文件夹。源不存在则跳过。"""
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, name)
    if src and os.path.exists(src):
        shutil.copy2(src, dst)
        print("[archive] %s -> %s" % (os.path.basename(src), dst))
        return dst
    print("[archive] 跳过（源不存在）: %s" % src)
    return None


def step(msg):
    print("\n========== %s ==========" % msg, flush=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # 运行阶段：music=只出音乐+分镜草稿后停下（等逐段创作）；render=读已创作分镜出图+MV+字幕；
    # auto（默认）=有匹配剧本就渲染，否则一次性跑完（草稿自动铺）。
    PHASE = "auto"
    MV_SEGMENT = None  # 0 起索引；非 None = 只重渲该段（隐含 --mv-only，跳过全曲字幕烧录）
    for _a in sys.argv[1:]:
        if _a.startswith("--phase="):
            PHASE = _a.split("=", 1)[1]
        elif _a.startswith("--mv-segment="):
            try:
                MV_SEGMENT = int(_a.split("=", 1)[1])
            except ValueError:
                raise RuntimeError("--mv-segment= 需要整数（0 起段索引，故事板第 N 段 → N-1）")
        elif _a in ("music", "render", "auto"):
            PHASE = _a
    SONG_DIR = os.path.join(WORKSPACE, ACTIVE_SONG)
    if not os.path.isdir(SONG_DIR):
        raise RuntimeError("项目目录不存在：%s（需含 歌曲.md 与 场景素材.md）" % SONG_DIR)
    spec = load_song_project(SONG_DIR, ACTIVE_SONG)  # ★ 一切歌曲数据都从项目目录读
    T = spec["tags"]
    # ★ 项目级提示词覆盖：只有列进 segments 的分镜才套用骑乘/特殊表演规则（1 起镜号）
    _PR = spec.get("prompt_rules") or {}
    RULE_SEGMENTS = set()
    for _x in (_PR.get("segments") or []):
        try:
            RULE_SEGMENTS.add(int(_x))
        except (TypeError, ValueError):
            pass
    IMG_DIR = os.path.join(SONG_DIR, "图片")  # ★ 唯一子文件夹：所有产出图
    os.makedirs(IMG_DIR, exist_ok=True)

    # ★ 从工作区配置 config.json 读取人物与尺寸（用户/UI 维护，不写死）
    cfg = load_config(WORKSPACE)
    # 本项目尺寸覆盖（制作.json 的 size：竖屏/横屏差异属于项目数据，不写死在脚本里）
    if spec.get("size"):
        cfg["defaults"].update(spec["size"])
    # 合并运行时覆盖（仅内存，不写回配置；对话临时要求才填 RUN_OVERRIDES）
    if RUN_OVERRIDES.get("defaults"):
        cfg["defaults"].update(RUN_OVERRIDES["defaults"])
    # 人物：运行时覆盖 > 项目 制作.json 指定 > 配置首个启用人物
    song_chars = (RUN_OVERRIDES.get("characters")
                  or spec.get("characters")
                  or [n for n, c in cfg["characters"].items() if c.get("enabled", True)])
    if not song_chars:
        raise RuntimeError("未配置《%s》的人物：请在 config.json 的 characters 配好，或在项目的 制作.json 指定 characters" % ACTIVE_SONG)
    lead = song_chars[0]
    lead_char = get_character(cfg, lead)
    REF = resolve_ref(lead_char.get("ref_image")) if lead_char else None
    if not REF:
        raise RuntimeError("人物「%s」未配置 ref_image（参考图相对路径，如 avatar/000.png）" % lead)
    # 人物参考图（type=face 面部特征参考图）：面貌五官由参考图本身复刻，配置 desc 只作标记、不进提示词
    FACE_MARK = (lead_char.get("desc") if lead_char else "") or ""
    # 当歌穿搭（每歌不同，来自 歌曲.md 的「## 穿搭」）：发型/妆容/服装
    OUTFIT_DESC = spec.get("char_desc", "")
    # ★ 多人物各自穿搭（制作.json 的 char_outfits）：{角色名: 穿搭}。缺省则回退 config.char_desc / OUTFIT_DESC。
    CHAR_OUTFITS = spec.get("char_outfits") or {}
    # ★ 本歌自带「器乐段三段式表演」（制作.json instrumental_beats）；空则用内置通用库。
    INST_BEATS = spec.get("instrumental_beats") or []
    # 四视图 / 场景运镜 / MV 合成：文字只描述穿搭，面貌由参考图锁定（不写任何五官脸型，防止污染形象）
    CHAR_DESC = OUTFIT_DESC
    CHAR_FULL = OUTFIT_DESC
    print("[config] 人物面貌以参考图为准（%s），提示词只写当歌穿搭" % FACE_MARK)
    # 多人物定义：每个 enabled 角色 → (name, cfg, ref, 当歌穿搭 desc)；文件名由配置给出，
    # 归档时按 人物1.png / 人物2.png … 动态编号（与场景图规则一致）。
    char_defs = []
    for _cn in song_chars:
        _cc = get_character(cfg, _cn)
        if not _cc:
            print("[warn] 人物「%s」在 config.json 中不存在，跳过" % _cn)
            continue
        _ref = resolve_ref(_cc.get("ref_image"))
        if not _ref:
            raise RuntimeError("人物「%s」未配置 ref_image（参考图相对路径，如 avatar/000.png）" % _cn)
        # 每人各自的当歌穿搭：优先用配置里的 char_desc，否则共用整首歌的 OUTFIT_DESC（歌曲.md ## 穿搭）。
        # ★ 配置里的 desc 只是「面貌以参考图为准」的标记（SKILL.md:318），提示词只从 char_desc / OUTFIT_DESC 取值——
        # 它与生成四视图时用的提示词（当歌穿搭）是两回事。所以这里只取 char_desc / OUTFIT_DESC，
        # 不取 _cc.get("desc")。并加防御：万一 char_desc 误写成标记文案，直接回退 OUTFIT_DESC。
        # 这是 MV / 多参考全局铁律，详见 SKILL.md 经验速查 #13（人物描述必须写实际穿搭）。
        _char_desc = (CHAR_OUTFITS.get(_cn) or _cc.get("char_desc") or OUTFIT_DESC)
        if _char_desc and ("仅标记" in _char_desc or "以参考图为准" in _char_desc or "复刻五官" in _char_desc):
            _char_desc = OUTFIT_DESC
        char_defs.append((_cn, _cc, _ref, _char_desc))
    if not char_defs:
        raise RuntimeError("未解析到任何有效人物（请检查 config.json characters 的 ref_image）")
    LAND_W = cfg["defaults"]["landscape_width"]
    LAND_H = cfg["defaults"]["landscape_height"]
    MV_W = cfg["defaults"]["mv_width"]
    MV_H = cfg["defaults"]["mv_height"]

    # 方向文案按实际尺寸自动判定（竖屏/横屏/正方），写死「横屏」会误导
    def _orient(w, h):
        if h > w:
            return "竖屏"
        if w > h:
            return "横屏"
        return "正方"
    LAND_ORIENT = _orient(LAND_W, LAND_H)
    MV_ORIENT = _orient(MV_W, MV_H)
    print("[config] 尺寸：场景图 %dx%d(%s)，MV %dx%d(%s)" % (
        LAND_W, LAND_H, LAND_ORIENT, MV_W, MV_H, MV_ORIENT))
    print("[config] 歌曲《%s》使用人物：%s (ref=%s)" % (ACTIVE_SONG, "、".join(song_chars), REF))

    # ★ 歌曲.md 现在是「输入源」（创作思路/曲风/穿搭/歌词都从这里读），脚本只读不写。
    lyrics_path_root = os.path.join(SONG_DIR, "歌曲.md")
    print("[lyrics] 读取项目歌曲文件 ->", lyrics_path_root)

    LYRICS = spec["lyrics"]
    STYLE = spec["style"]
    # ★ 实际喂给音乐模型的提示词：优先用「## 音乐描述」（M3 的结构化 Caption），否则回退「## 曲风」。
    MUSIC_PROMPT = spec.get("music_prompt") or STYLE
    # SCENES 在音乐生成后、按时长计算场景数时赋值（见下方「场景图数量按音乐时长定」）

    results = {}
    started = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1) 音乐（Yue2 按歌词自动决定时长，工作流锁 360s 上限，本管线不传任何时长参数）→ 根目录 音乐.wav
    # ★ 渲染阶段（--phase render / --reuse-music）必须复用已有音乐：重新生成会因 Yue2 非确定性
    # 导致时长变化，进而让「时间规则算出的 N」与已创作好的分镜剧本对不上。
    MUSIC_REF_PATH = os.path.join(SONG_DIR, "_music_ref.json")
    _ref = None
    try:
        with open(MUSIC_REF_PATH, encoding="utf-8") as f:
            _ref = json.load(f)
    except Exception:
        _ref = None
    _music_wav = os.path.join(SONG_DIR, "音乐.wav")
    _reuse = ((PHASE == "render") or ("--reuse-music" in sys.argv)) \
        and bool(_ref) and os.path.isfile(_music_wav)
    if _reuse:
        music = _music_wav
        aud_rel = _ref.get("aud_rel")
        dur = audio_duration(music) or _ref.get("duration")
        step("1/6 复用已有音乐（不重新生成，保证时长与分镜剧本一致）")
        print("[music] 复用 %s（服务端相对路径 %s，时长 %.1f s）" % (music, aud_rel, dur or 0))
    else:
        # ★ 音乐模型默认规则（用户定，与分镜模板同步）：
        #   独唱（单人）→ 默认 Yue2；对唱（多人）→ 默认 MiniMax Music 3（M3 才支持可控对唱）。
        #   制作.json 的 music_model 显式指定时优先生效（可覆盖本默认）。
        _explicit_model = spec.get("music_model") or ""
        _music_model = _explicit_model or ("minimax3" if len(song_chars) > 1 else "yue2")
        step("1/6 生成音乐（%s%s）" % (
            _music_model, "" if _explicit_model else "·按%s默认" % ("对唱" if len(song_chars) > 1 else "独唱")))
        music, aud_rel = gen_music(MUSIC_PROMPT, LYRICS, mode="full", model=_music_model,
                                   duration=int(spec.get("music_duration") or 300),
                                   tag=T["music"], subdir="音乐生成", timeout=3600)
        dur = audio_duration(music)
        try:
            with open(MUSIC_REF_PATH, "w", encoding="utf-8") as f:
                json.dump({"aud_rel": aud_rel, "local": music, "duration": dur},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        archive(music, SONG_DIR, "音乐.wav")  # ★ 归档：音乐 -> 根目录
        results["music"] = music
        results["aud_rel"] = aud_rel
        results["actual_duration"] = dur
        print("[music] 实际时长 ≈ %.1f s" % (dur or 0))

    # ★★★ 场景图数量：时间规则是「下限」，场景素材.md 里实际规划了 N 个场景就按 N 张生成 ★★★
    # 短歌（≤2分钟）→ 每段一张零重复；长歌（>2分钟）→ 至少「一半+1」张。
    # 若 场景素材.md 规划了更多场景（N ≥ 下限），按 N 张出，用户已规划的镜头一个都不压缩。
    # 即 n_total = max(时间规则下限, len(场景素材))。
    SEG_SEC = cfg["defaults"].get("audio_segment_seconds", 10)
    song_dur = dur or 0.0
    # ★ 分段数必须用「MiniMax/H3 精确算法」算，`ceil(时长/10)` 会差 1 段：
    # 传 10s → 节点向下对齐成 226 帧 = 9.41667s，尾段不足阈值并回上一段，段数与服务端一致。
    seg_secs, seg_bounds = h3_segments(song_dur, SEG_SEC)
    seg = len(seg_secs) if seg_secs else max(1, math.ceil(song_dur / SEG_SEC))
    if song_dur <= 120:
        min_scenes = seg  # 短歌：每段一张，零重复
    else:
        min_scenes = seg // 2 + 1  # 长歌：一半加一张，前面场景最多循环一次
    SCENES_SRC = spec["scenes"]
    # ★ 时间规则只是下限，规划了 N 个场景就出 N 张（max 取大，规划镜头一个都不压缩）
    n_total = max(min_scenes, len(SCENES_SRC))
    _real = ("%.5fs" % seg_secs[0]) if seg_secs else "?"
    print("[segments] 音乐≈%.2fs → H3精确分段 %d 段（请求%gs，实际每段%s/%d帧@%dfps）" % (
        song_dur, seg, SEG_SEC, _real, h3_align_down(round(SEG_SEC * H3_FPS)), H3_FPS))
    print("[scenes] 下限%d 张（时间规则）→ 取 max(下限, 规划%d) = 生成 %d 张场景图" % (min_scenes, len(SCENES_SRC), n_total))

    # ★★★ 1.4/6 人声分离 + 神经网络去噪（Lyric-First 前置）★★★
    # 音乐出来后、歌词识别之前：先用服务端模型工作流分离出纯净人声 → 下载 →
    # ffmpeg arnndn（RNNoise）去噪 → 拿「去噪人声」去喂 whisper。
    # 任一环节失败（技能未装 / ComfyUI 离线 / 分离报错）则回退用原音乐，不阻断主流程。
    def _vocal_for_align(audio_rel, song_dir):
        """分离+去噪，返回用于 whisper 对齐的本地音频；分离/去噪失败返回 None（调用方回退用原音乐）。"""
        _dn = os.path.join(song_dir, "人声.wav")
        if os.path.isfile(_dn):  # 已跑过就复用（省一次服务端算力）
            print("[vocal] 复用已有去噪人声：%s" % _dn)
            return _dn
        try:
            _sk = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                "..", "fxai-vocal-separate"))
            if _sk not in sys.path:
                sys.path.insert(0, _sk)
            import importlib
            m = importlib.import_module("separate_vocals")
        except Exception as e:
            print("[vocal] 跳过人声分离（未安装 fxai-vocal-separate 技能）：%s" % e)
            return None
        try:
            # 分离出来的原始人声是中间件（_ 开头，收尾清理）；去噪后的就是交付物「人声.wav」
            r = m.separate_vocals(audio_rel, song_dir,
                                  vocal_name="_人声.wav", denoise_name="人声.wav")
        except Exception as e:
            print("[vocal] 人声分离失败，回退用原音乐对齐：%s" % e)
            return None
        p = r.get("denoised") or r.get("vocal")
        if p and os.path.isfile(p):
            print("[vocal] 歌词识别改用：%s" % p)
            return p
        return None

    ALIGN_AUDIO = music
    if lyrics_align._G.get("vocal_separation", True):
        step("1.4/6 人声分离 + 神经网络去噪（为歌词识别准备纯净人声）")
        ALIGN_AUDIO = _vocal_for_align(aud_rel, SONG_DIR) or music

    # ★★★ 1.5/6 歌词先行对齐（Lyric-First）★★★
    # 音乐一出来就跑 whisper：出真实时间轴 → 与歌词强制对齐 → 判定每个分段「有没有人在唱、
    # 段内第几秒唱到第几秒」→ 前奏/间奏/尾奏只写肢体表演，演唱类描写不写（否则人物跟着音乐张嘴，穿帮）。
    # ★ 字幕全流程只识别这一次：成片阶段直接烧录这里的 字幕.ass，whisper 只识别这一次。
    SUB_ASS = os.path.join(SONG_DIR, "字幕.ass")
    TL_PATH = os.path.join(SONG_DIR, "歌词时间轴.json")
    REGEN_SUB = ("--regen-subtitle" in sys.argv)
    tl = None if REGEN_SUB else load_timeline(TL_PATH)
    if tl and tl.get("segments") and len(tl["segments"]) == seg and os.path.isfile(SUB_ASS):
        seg_info = tl["segments"]
        for _s in seg_info:  # 回读时统一字段名（text → lyric）
            _s["lyrics"] = [dict(x, lyric=x.get("text", "")) for x in _s.get("lyrics", [])]
        lyric_items = tl.get("lyric_lines") or []
        step("1.5/6 复用已有歌词对齐（字幕.ass + 歌词时间轴.json）")
    else:
        step("1.5/6 歌词精确对齐（whisper 时间轴 → 与歌词强制对齐）")
        # 已有转写就复用，不重跑 whisper（whisper 没变，重跑纯浪费时间）
        _trs0 = os.path.join(SONG_DIR, "_transcript.txt")
        # ★ video_size 传 MV 真实分辨率：ASS 的 PlayResX/Y 必须等于视频尺寸，
        # 否则竖屏（768x1280）会按横屏画布算坐标，字幕位置错乱。
        lyric_items = run_align(ALIGN_AUDIO, LYRICS, SONG_DIR, out_name="字幕",
                                transcript=_trs0 if os.path.isfile(_trs0) else None,
                                video_size=(MV_W, MV_H))
        # ★ 人声判定用「whisper 原始时间刻度」做交集：
        # 不用歌词强制对齐结果（后者会把真唱段当幻觉丢掉，误判成无人声）。
        _trs = os.path.join(SONG_DIR, "_transcript.txt")
        _vsj = os.path.join(SONG_DIR, "_vocal_spans.json")
        _vocal_spans = None
        if os.path.isfile(_vsj):
            try:
                _vocal_spans = [tuple(x) for x in json.load(open(_vsj, encoding="utf-8"))]
            except Exception:
                _vocal_spans = None
        if _vocal_spans is None and os.path.isfile(_trs):
            _raw = parse_transcript(_trs)
            _lyr_lines = [it.get("lyric") or it.get("text") or "" for it in lyric_items]
            _keep, _drop = filter_vocal_segments(_raw, _lyr_lines, duration=song_dur)
            _vocal_spans = [(a, b) for a, b, _ in _keep]
            print("[align] whisper 原始 %d 段 → 人声 %d 段（剔除幻觉 %d：%s）" % (
                len(_raw), len(_keep), len(_drop),
                "、".join("%.0fs %s" % (d[0], d[2][:12]) for d in _drop[:4]) or "-"))
        seg_info = classify_segments(seg_bounds, lyric_items, vocal_spans=_vocal_spans)
        # ★ VAD 交叉验证：纯人声段内能量 vs whisper 人声窗（自动降级伪人声窗段）
        _vw = os.path.join(SONG_DIR, "人声.wav")
        if os.path.isfile(_vw):
            seg_info, _vad_rep = lyrics_align.cross_validate_segments_with_vad(
                seg_info, _vw, auto_fix=True)
            for _l in _vad_rep:
                print("[VAD] " + _l)
            if _vad_rep:
                json.dump({"reports": _vad_rep},
                          open(os.path.join(SONG_DIR, "_vad_xcheck.json"), "w",
                               encoding="utf-8"), ensure_ascii=False, indent=2)
        write_timeline(TL_PATH, ACTIVE_SONG, song_dur, seg_bounds, lyric_items, seg_info)
        if _vocal_spans:  # 存原始人声段，便于排查/复算
            try:
                _tl = json.load(open(TL_PATH, encoding="utf-8"))
                _tl["vocal_spans"] = [list(x) for x in _vocal_spans]
                json.dump(_tl, open(TL_PATH, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=2)
            except Exception:
                pass
    _v = [s for s in seg_info if s.get("vocal")]
    print("[align] 字幕 %d 句｜分段 %d 段：演唱 %d 段 / 器乐 %d 段" % (
        len(lyric_items), len(seg_info), len(_v), len(seg_info) - len(_v)))
    for s in seg_info:
        print('   [%02d] %6.2f-%6.2f %-8s %s | %s' % (
            s["index"], s["start"], s["end"], s.get("type", ""),
            ("唱 " + "、".join("%.1f-%.1f" % tuple(w) for w in (s.get("vocal_windows") or [])))
            if s.get("vocal") else "无人声",
            ("｜".join(x.get("lyric", "") for x in s.get("lyrics", [])) or "-")))

    # ★ 独立歌词交付档 歌词.md（用户要求：交付清单里单独存一份歌词，后缀 .md）
    #   文本来自歌曲.md 的「## 歌词」，附真实时间轴（来自本次对齐结果），便于交付/查阅。
    try:
        _ly_md = os.path.join(SONG_DIR, "歌词.md")
        _buf = ["# 《%s》歌词" % ACTIVE_SONG, "",
                "- **时长**：%.2fs" % song_dur,
                "- **说明**：时间轴见 `歌词时间轴.json`，字幕见 `字幕.srt` / `字幕.ass`。",
                "", "## 歌词全文", "", "```", (LYRICS or "").strip(), "```", ""]
        if lyric_items:
            _buf += ["## 时间轴", "", "| # | 起 | 止 | 歌词 |", "|---|----|----|------|"]
            for _i, _it in enumerate(lyric_items, 1):
                _buf.append("| %d | %.2fs | %.2fs | %s |" % (
                    _i, _it.get("start", 0.0), _it.get("end", 0.0),
                    _it.get("lyric") or _it.get("text") or ""))
            _buf.append("")
        with open(_ly_md, "w", encoding="utf-8", newline="\n") as _f:
            _f.write("\n".join(_buf))
        print("[lyrics] 已写出独立歌词档：%s" % _ly_md)
    except Exception as _e:
        print("[warn] 写出 歌词.md 失败（不影响主流程）：%s" % _e)

    # ★★ 分镜剧本（Lyric-First：第 i 条 ↔ 第 i 个音频分段，一一对应）★★
    # 剧本条数 = 分段数 seg；场景图仍只有 n_total 张，按「引用场景图」循环复用。
    # ★ 创作类文本一律 .md：分镜剧本.md 为唯一创作档，脚本回读解析。
    STORYBOARD_PATH = os.path.join(SONG_DIR, "分镜剧本.md")
    old_plan = load_storyboard(STORYBOARD_PATH)
    if old_plan and len(old_plan) == seg and PHASE != "music":
        scenes_plan = old_plan
        print("[storyboard] 使用已创作的逐段分镜剧本（%d 条 = 分段数）：%s" % (
            len(scenes_plan), STORYBOARD_PATH))
    else:
        # 旧剧本若按「场景数」创作（N 条），其 img/cam/camf/act 直接继承为场景表，创作一点不丢
        overrides = None
        if old_plan and len(old_plan) == n_total:
            overrides = old_plan
            _bak = os.path.join(SONG_DIR, "分镜剧本_按场景%d条_备份.md" % n_total)
            try:
                shutil.copy2(STORYBOARD_PATH, _bak)
            except Exception:
                pass
            print("[storyboard] 旧剧本按场景创作（%d 条）→ 备份为 %s，其内容复用为 %d 张场景图的 img/cam" % (
                n_total, os.path.basename(_bak), n_total))
        elif old_plan:
            print("[warn] 分镜剧本 %d 条 ≠ 分段数 %d → 按分段重建" % (len(old_plan), seg))
        scenes_plan = build_storyboard(spec, n_total, seg_info, duration=song_dur,
                                       scene_overrides=overrides)
        with open(STORYBOARD_PATH, "w", encoding="utf-8") as f:
            f.write(render_storyboard_md(scenes_plan, ACTIVE_SONG, song_dur, seg, SEG_SEC,
                                         seg_secs=seg_secs, n_scenes=n_total,
                                         n_chars=len(char_defs)))  # ★ >1 走【对唱模板】
        print("[storyboard] 已生成逐段分镜剧本草稿（%d 条 = 分段数）：%s" % (
            len(scenes_plan), STORYBOARD_PATH))
        if PHASE == "music":
            print("\n[STOP] 音乐 + 歌词对齐完成。请打开 分镜剧本.md，逐条改写 img / cam / camf / act，")
            print('       （无人声段的 act 必须是不开口的肢体表演）')
            print("""       改完后再运行：python produce_song.py --phase render
""", flush=True)
            return
    SCENES = scenes_plan

    MV_ONLY = ("--mv-only" in sys.argv) or (MV_SEGMENT is not None)  # 只重渲 MV：复用已生成好的图，跳过生图；--mv-segment 隐含 MV_ONLY

    # 2) 四视图纯人物 → 图片/人物1.png
    if MV_ONLY:
        step("2/6 复用已有人物图（--mv-only，不重新生图）")
        char_rels, _srels, _ = resolve_existing_assets(SONG_DIR, n_total)
        scene_rels = list(_srels)
        scene_descs = [sc["sdesc"] for sc in SCENES[:n_total]]
        if not char_rels or len(scene_rels) < n_total:
            print("[error] --mv-only 但未找到足够素材（人物 %s / 场景 %d，需要 %d）" % (
                char_rels, len(scene_rels), n_total))
            return
        print("[assets] 人物 %s ｜场景 %d 张：%s" % (", ".join(char_rels), len(scene_rels),
                                                ", ".join(x.split("/")[-1] for x in scene_rels)))
        results["char_rels"] = char_rels
        char_rel = char_rels[0] if char_rels else None  # 兼容旧字段：lead 人物图
        results["char_rel"] = char_rel
        results["scene_rels"] = scene_rels
    else:
        step("2/6 四视图纯人物（%d 个角色）" % len(char_defs))  # → 图片/人物1.png … 人物N.png
        # ★ 服务端落盘目录一律语义化、不带序号：目录 song_charview，角色序号写在文件名上
        # （节点 fxai_filename_index → num，启用文件名覆盖=True，num=0/1 → 000.png/001.png），
        # 与场景图 song_ref_scene + num=i-1 同一套范式。本地归档再按 人物1.png / 人物2.png 编号。
        char_rels = []
        for _idx, (_cn, _cc, _ref, _desc) in enumerate(char_defs, 1):
            _char, _rel = gen_char_view(_desc, ref=_ref, views=4,
                                        tag="%s_%d" % (T["char"], _idx),
                                        subdir="song_charview", num=_idx - 1)
            char_rels.append(_rel)
            archive(_char, IMG_DIR, "人物%d.png" % _idx)  # ★ 归档：四视图 -> 图片/人物N.png
        results["char_rels"] = char_rels
        char_rel = char_rels[0] if char_rels else None  # 兼容旧字段：lead 人物图
        results["char_rel"] = char_rel

    # 3) 纯场景图（数量按时长标准算，循环到 n_total）→ 图片/场景1.png … 场景N.png
    step("3/6 %s纯场景图 %dx%d (%d 张)" % (LAND_ORIENT, LAND_W, LAND_H, n_total))
    if MV_ONLY:
        print('       --mv-only：复用已有 %d 张场景图，跳过生图' % len(scene_rels))
    else:
        scene_rels, scene_descs, scene_prompts = [], [], []
        # 防呆：场景图提示词里写死的构图方向（横屏宽幅/竖屏构图）必须与本次生图尺寸一致，
        # 否则会出现「竖屏尺寸 + 横屏构图描述」的自相矛盾，画面会被裁切变形。
        _wrong = ("横屏" if LAND_ORIENT == "竖屏" else "竖屏")
        # ★ 只生成前 n_total 张（其余分镜循环引用这些图），一张图服务多条分镜
        for i, sc in enumerate(SCENES[:n_total], 1):
            if _wrong in sc.get("img", ""):
                print("[warn] 场景%d 提示词含「%s」但与本次%s尺寸 %dx%d 不符，请改提示词" % (
                    i, _wrong, LAND_ORIENT, LAND_W, LAND_H), flush=True)
            # num=i-1：第 i 张场景图固定写死为 <序号>.png，重跑同名覆盖，目录里永远只有 n_total 张
            src, rel = gen_scene_image(sc["img"], role=None, w=LAND_W, h=LAND_H,
                                       tag=T["scene"] % i,
                                       subdir="song_ref_scene", num=i - 1)
            archive(src, IMG_DIR, "场景%s.png" % cn(i))  # ★ 归档：场景图 -> 图片/场景N.png
            scene_rels.append(rel)
            scene_descs.append(sc["sdesc"])
            scene_prompts.append((i, sc.get("name", ""), sc["img"], sc.get("sdesc", "")))
        results["scene_rels"] = scene_rels

        # ★ 场景提示词落盘
        # 记录每张场景图「实际送入 ComfyUI」的提示词（= sc["img"]），multiref 下为「空镜·无人物」；
        # 有歌词的演唱段已含「画面环境呼应此段歌词意境」，器乐/无词段为「主题自由发挥」。
        _sp_path = os.path.join(SONG_DIR, "场景提示词.md")
        with open(_sp_path, "w", encoding="utf-8") as f:
            f.write("# 场景提示词 · %s\n\n" % ACTIVE_SONG)
            f.write("> 每张场景图生图时**实际送入 ComfyUI** 的提示词（共 %d 张，与 `图片/场景1~%d.png` 一一对应）。\n" % (len(scene_prompts), len(scene_prompts)))
            f.write("> multiref 模式：场景图为「空镜·不含人物」，人物由参考图在 MV 阶段注入；\n")
            f.write("> 有歌词的演唱段已追加「画面环境呼应此段歌词意境」，器乐/无词段按主题自由发挥。\n\n")
            for _i, (_n, _name, _img, _sdesc) in enumerate(scene_prompts, 1):
                _tag = "（歌词呼应）" if "画面环境呼应" in _img else "（主题自由）"
                f.write("## 场景%d · %s %s\n\n" % (_i, _name, _tag))
                if _sdesc:
                    f.write("环境概述：%s\n\n" % _sdesc)
                f.write("**生图提示词：**\n\n> %s\n\n" % _img)
        print("[场景提示词] 已落盘：%s（%d 张）" % (_sp_path, len(scene_prompts)))

    # ★ 道具参考图（如某首歌用到的某件道具）：取/生成并写回 _assets.json，供 multiref 作为额外参考图。
    PROPS = spec.get("prop") or []
    _prop_pairs = resolve_prop_rels(SONG_DIR, PROPS)
    prop_rels = [r for r, _ in _prop_pairs if r]
    prop_descs = [d for r, d in _prop_pairs if r]
    results["prop_rels"] = prop_rels
    if prop_rels:
        print("[assets] 道具参考图 %d 张：%s" % (len(prop_rels),
                                                ", ".join(x.split("/")[-1] for x in prop_rels)))

    # 4) 逐段一套 Ref2VA 六段式运镜提示词（★ 按该段有没有人声分流：演唱段 vs 器乐段）
    step("4/6 逐段构建 Ref2VA 运镜提示词（%d 段）" % len(SCENES))
    # 多人物：每人穿搭描述列表（与图片列表顺序一致：人物1~K + 场景）。单人物时为 [OUTFIT_DESC]。
    char_descs_all = [d for (_, _, _, d) in char_defs]
    # ★ 对唱：角色名 → Subject 序号（第 1 个角色=1、第 2 个=2…）。
    #   把 分镜剧本.md 的 `- sing: 男主(0.00-4.50) 女主(4.50-9.42)` 转成 `2(0.00-4.50) 1(4.50-9.42)`。
    _name2subj = {}
    for _i2, (_cn2, _cc2, _ref2, _d2) in enumerate(char_defs, 1):
        _name2subj[_cn2] = _i2

    def _norm_sing(_s):
        _s = (_s or "").strip()
        for _nm, _sj in _name2subj.items():
            _s = _s.replace(_nm, str(_sj))
        return _s

    ref2va = []
    for _i, sc in enumerate(SCENES):
        _nxt = SCENES[_i + 1] if (_i + 1) < len(SCENES) else None
        _seg = {
            "index": sc.get("index", _i + 1),
            "len": sc.get("len") or round(sc.get("end", 0) - sc.get("start", 0), 2),
            "type": sc.get("type", ""),
            "vocal": sc.get("vocal", True),
            "vocal_windows": sc.get("vocal_windows") or [],
            "sing": _norm_sing(sc.get("sing")),  # ★ 演唱者（角色名→Subject 序号）
            "lyrics": [],
        }
        _nseg = None
        if _nxt is not None:
            _nseg = {"vocal": _nxt.get("vocal", True)}
        _hit = int(sc.get("index", _i + 1)) in RULE_SEGMENTS
        ref2va.append(build_segment_prompt(char_descs_all, sc["sdesc"], sc["cam"], sc["camf"],
                                           sc["act"], _seg, next_seg=_nseg, prop_descs=prop_descs,
                                           inst_beats=INST_BEATS,
                                           prompt_rules=_PR if _hit else None))
    with open(os.path.join(SONG_DIR, "运镜提示词.md"), "w", encoding="utf-8") as f:
        f.write("# 运镜提示词 · %s\n\n" % ACTIVE_SONG)
        f.write("> 每个音频分段一套 H3 Ref2VA 六段式提示词（行数 = 分段数，一一对应）。\n")
        f.write("> 无人声段（前奏/间奏/尾奏）已强制「不演唱、不开口、双唇闭合」，改为肢体表演。\n\n")
        for i, (sc, p) in enumerate(zip(SCENES, ref2va), 1):
            f.write("## 分镜 %d：%s（%.1fs - %.1fs）｜%s（%s）\n\n" % (
                i, sc.get("name", ""), sc.get("start", 0), sc.get("end", 0),
                sc.get("type", ""), "有人声" if sc.get("vocal") else "无人声"))
            if sc.get("lyric"):
                f.write("歌词意象：%s\n\n" % sc["lyric"])
            f.write("```\n" + p + "\n```\n\n")

    # 5) 多参考完整 MV（分辨率 = MV_W×MV_H）
    # ★ seg_prompts：行数 = 分段数，第 i 个音频分段精确命中第 i 条提示词（不再循环复用同一条）
    step("5/6 多参考完整 MV（%s %dx%d + 逐段运镜提示词 %d 条）" % (
        MV_ORIENT, MV_W, MV_H, len(ref2va)))
    mv, mv_rel = gen_mv("multiref", aud_rel,
                        char_rel=results.get("char_rels") or [char_rel], scene_ref=scene_rels,
                        char_descs=char_descs_all, scene_desc=scene_descs,
                        seg_prompts=ref2va,
                        prompt_out=os.path.join(SONG_DIR, "MV提交提示词.json"),
                        size=(MV_W, MV_H),
                        tag=T["mv"], timeout=21600,
                        start_index=MV_SEGMENT, end_index=MV_SEGMENT,
                        prop_rel=prop_rels)
    results["mv"] = mv

    # 6) 烧字幕 → 根目录 成片_{W}x{H}.mp4
    # ★★ 字幕只识别一次：直接用 1.5 步生成的 字幕.ass 烧录，
    # whisper 只跑一次（音乐没变，时间戳必然一致）。
    # 只有人工检查发现字幕偏移/错字时，才加 --regen-subtitle 重跑识别并重合成。
    # ★ 命名铁律：成片带分辨率后缀（成片_{W}x{H}.mp4），字幕不带后缀（跨尺寸复用）。
    # ★ --mv-segment 模式：只重渲了单段，mv 是该段裸片（时间从 0 起），全曲字幕跳过不烧
    # （烧了时间错位）。直接存为 成片_段N_{W}x{H}.mp4，由用户按 SKILL #14 归位流程拼回整曲。
    if MV_SEGMENT is not None:
        _res_tag = "%dx%d" % (MV_W, MV_H)
        _seg_name = "成片_段%d_%s" % (MV_SEGMENT, _res_tag)
        _seg_mp4 = os.path.join(SONG_DIR, "%s.mp4" % _seg_name)
        try:
            shutil.copy2(mv, _seg_mp4)
        except Exception as _e:
            print("[warn] 复制单段成片失败，源文件留于：%s（%s）" % (mv, _e))
            _seg_mp4 = mv
        print("[mv-segment] 已重渲第 %d 段（0 起索引）-> %s" % (MV_SEGMENT, _seg_mp4))
        print("[mv-segment] 归位：用 ffmpeg 按分段边界替换 成片_%s.mp4 对应时段，再对整曲烧 字幕.ass" % _res_tag)
        results["mv_segment"] = _seg_mp4
        return
    # ★ 保留原片（干净无字幕）为 合并_<W>x<H>.mp4：日后只重烧字幕时直接复用，无需重渲 MV。
    # （清理中间件 _SONG_KEEP_PREFIX 已含「合并_」前缀，此文件会被保留；但前提是先生成它。）
    _merge_path = os.path.join(SONG_DIR, "合并_%dx%d.mp4" % (MV_W, MV_H))
    try:
        shutil.copy2(mv, _merge_path)
        print("[mv] 已保留原片（无字幕）：%s" % _merge_path)
    except Exception as _e:
        print("[warn] 保留原片失败（不影响成片）：%s" % _e)

    step("6/6 烧字幕（复用已生成的 字幕.ass）")
    _res_tag = "%dx%d" % (MV_W, MV_H)
    _mv_name = "成片_%s" % _res_tag
    _out_mp4 = os.path.join(SONG_DIR, "%s.mp4" % _mv_name)
    if not os.path.isfile(SUB_ASS):
        raise RuntimeError("缺少字幕文件 %s：请先跑 --phase music（或加 --regen-subtitle）生成歌词对齐" % SUB_ASS)
    burn_subtitle(SUB_ASS, mv, _out_mp4)
    print("[subtitle] 已烧录：%s（字幕来源 字幕.ass）" % _out_mp4)
    results["mv_sub"] = _out_mp4
    results["ass"] = SUB_ASS
    results["srt"] = os.path.join(SONG_DIR, "字幕.srt")

    # 整理成品 meta（根目录）
    step("整理成品 -> " + SONG_DIR)
    meta = {
        "song": ACTIVE_SONG,
        "style": STYLE,
        "lyrics_file": os.path.relpath(lyrics_path_root, WORKSPACE),
        "lyrics": LYRICS,
        "mode": "full",
        "actual_duration_sec": dur,
        "orientation": "%s (%s)" % (
            "portrait" if MV_H > MV_W else ("landscape" if MV_W > MV_H else "square"), MV_ORIENT),
        "video_resolution": "%dx%d (多参考 925 节点)" % (MV_W, MV_H),
        "image_resolution": "%dx%d (%s)" % (LAND_W, LAND_H, LAND_ORIENT),
        "mv_mode": "multiref (Ref2VA 全参考 + 原音频)",
        "characters": song_chars,
        "storyboard": "分镜剧本.md",
        "scenes": [{"index": sc.get("index", i), "name": sc.get("name", ""),
                    "start": sc.get("start", 0), "end": sc.get("end", 0),
                    "lyric": sc.get("lyric", "")}
                   for i, sc in enumerate(SCENES, 1)],
        "subtitle": "标准 ASS 字幕（whisper 强制对齐，音乐阶段生成一次，成片直接烧录）",
        "lyrics_align": {
            "timeline": "歌词时间轴.json",
            "segments": len(SCENES),
            "vocal_segments": len([s for s in SCENES if s.get("vocal")]),
            "instrumental_segments": len([s for s in SCENES if not s.get("vocal")]),
            "note": "是否演唱由该分段内是否命中字幕决定；器乐段提示词强制不写演唱/口型",
        },
        "outputs": {
            "歌曲": "歌曲.md",
            "音乐": "音乐.wav",
            "人声": "人声.wav",
            "成片": "成片_%dx%d.mp4" % (MV_W, MV_H),
            "字幕": ["字幕.ass", "字幕.srt"],
            "图片": ["人物%d.png" % (i + 1) for i in range(len(char_defs))] + ["场景%s.png" % cn(i) for i in range(1, len(SCENES) + 1)],
            "运镜提示词": "运镜提示词.md",
            "歌词": "歌词.md",
            "歌词时间轴": "歌词时间轴.json",
        },
    }
    with open(os.path.join(SONG_DIR, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("\n========== 全部完成 ==========", flush=True)
    print("歌曲目录：" + SONG_DIR, flush=True)
    print("歌词文件：" + lyrics_path_root, flush=True)
    print("实际时长 ≈ %.1f s" % (dur or 0), flush=True)

    # 生成完成后清理中间件/临时件：只保留约定交付物（SKILL.md「产出物归档与清理」约定）
    _rem, _kept = cleanup_song_tmp(SONG_DIR)
    if _rem:
        print("已清理临时件(%d)：%s" % (len(_rem), "、".join(_rem)), flush=True)
    else:
        print("无需清理（目录已是纯交付物）", flush=True)


if __name__ == "__main__":
    main()
