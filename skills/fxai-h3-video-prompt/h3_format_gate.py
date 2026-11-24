# -*- coding: utf-8 -*-
"""H3 六段式提示词格式门禁（短剧 / 漫剧 / 多参考 Ref2VA）——**白名单式**。

判定方式：每一段都有一份模板结构（正则 / 枚举白名单），**只有整段、整行匹配模板才放行**；
只认模板命中，不做反向词表穷举。模板即规范，见
`fxai-short-drama/references/01-提示词规范.md` 的「六段白名单模板」。

六段逐段校验：

  段① 主体定义  _audit_subject_defs
      白名单句式：`<Subject N> 是 <Picture M> 中的<身份词>。`
      身份词只取自白名单枚举，编号从 1 连续、不重复定义。
      另允许 `<Audio N>` 行：绑定说话人时写成 `… <Subject N> (Sx)`，Sx 与详细描述一致。
  段② 摘要      _audit_summary
      白名单结构：`[reference generation] <站位句>。目标视频…，收在…。`
  段③ 延续分析  _audit_retention
      白名单句式（按类目枚举，一字不错）：人物 / 场景 / 道具三套整行模板。
  段④ 详细描述  _audit_detail
      白名单结构：风格句 + `[Shot 1]` 正文；主体写 `<Subject N>` 全称；
      说话人写 `<Subject N> (Sx)`，不得出现裸 `(Sx)`；台词只写 `<d>[Chinese]…</d>`；
      正文不直接引 `<Picture N>`；首事件不带时刻；编号稳定且连续。
  段⑤ 整体音效  _audit_soundscape
      白名单：非空、只写声音（命中声音类白名单词）、不夹台词、不带 `(Sx)`。
  段⑥ 非叙事音乐 _audit_music
      白名单：非空；写「无 / N/A / 不使用」或命中音乐构件白名单词。
  跨段          _audit_cross_section
      延续分析覆盖主体定义里每个 `<Subject N>`；摘要 / 详细描述不出现未定义主体。

语言：正文默认允许中文；`allow_chinese=False` 时退回「四段正文须全英文、中文只进 <d>」。

入口：`audit_short_drama_six_segment(shots)`、`audit_ref2va_six_segment(prompts)`
均返回 list[str]，空列表 = 通过。调用方按 fail-closed 处理：加载失败也算不通过。

本文件是门禁的**唯一事实源**（2026-11-24 依编译件行为反向重建，源码在一次蓝屏写盘中丢失）。
语义改动后与 `_backup/h3_format_gate.pyc.good-*` 做差分回归，确认违规集合一致；维护流程见
`references/09-门禁模块维护.md`。
"""

import re

# ---------------------------------------------------------------- 段名与别名

SEG_ORDER = [
    "主体定义",
    "摘要",
    "延续分析",
    "详细描述",
    "整体音效",
    "非叙事音乐",
]
SEG_HEADERS = SEG_ORDER

# 英文段名（Ref2VA：MV / 口播）→ 中文段名
SEG_ALIAS = {
    "subject_definitions": "主体定义",
    "summary": "摘要",
    "retention_analysis": "延续分析",
    "detailed_description": "详细描述",
    "overall_soundscape": "整体音效",
    "non_diegetic_music": "非叙事音乐",
}

# ---------------------------------------------------------------- 段① 主体定义

_IDENT_WHITELIST = (
    "男子", "女子", "老人", "小孩", "男孩", "女孩",
    "男人", "女人", "人物", "场景", "道具", "动物",
)

# 长词优先（「女子」优先于「女」类前缀匹配）
_SUBJ_DEF_LINE = re.compile(
    "^<Subject (\\d+)> 是 <Picture (\\d+)> 中的(%s)。$"
    % "|".join(sorted(_IDENT_WHITELIST, key=len, reverse=True))
)

# 场景 / 道具这类不会开口的主体
_NON_VOCAL_KINDS = ("场景", "道具")

# 专属人 / 动物才做得出的动作动词（「人做得出、屏幕桌椅做不出」标准维护）
_HUMAN_ACTION_VERBS = (
    "[站坐躺趴跪靠倚走跑跳蹲扑抿咳喘哼唱吻抱摸踢]"
    "|立在|站立|伫立|直立|立正|立着|点头|摇头|回头|转身|俯身|侧身|耸肩|摊手|摆手|挥手"
    "|招手|伸手|皱眉|挑眉|眯眼|瞪眼|眨眼|开口|张嘴|闭嘴|说话|低语"
)

# 主体后面紧跟人形动作 = 拟人句（前面是「把 / 将」时是宾语，放行）
_SUBJ_THEN_HUMAN_VERB = re.compile(
    "(?<![把将])<Subject (\\d+)>\\s*(?=" + _HUMAN_ACTION_VERBS + ")"
)

# 宽松主体定义句式：抓「身份词位置写了外观 / 衣着」的越界写法
_SUBJ_DEF_LOOSE = re.compile("^<Subject (\\d+)> 是 <Picture (\\d+)> 中的(.{2,12})。$")
_LOOK_WORDS = (
    "色", "纱", "绸", "缎", "绣", "纹", "绒", "棉", "麻", "皮", "丝", "链",
    "裙", "衣", "衫", "裤", "袍", "鞋", "帽", "发", "眉", "眼", "脸", "唇",
    "瘦", "胖", "美", "帅", "温柔", "清冷", "年轻", "苍老", "俊", "秀气",
)

# 音频定义行 / 说话人绑定
_AUDIO_DEF_LINE = re.compile("^<Audio (\\d+)>")
_AUDIO_BIND = re.compile("<Subject (\\d+)>\\s*\\(S(\\d+)\\)")

_PIC_LIST = "<Picture \\d+>(?:、<Picture \\d+>)*"

# ---------------------------------------------------------------- 段② 摘要

_SUMMARY_REQUIRED = (
    ("任务类型标签", re.compile("^\\[reference generation[^\\]]*\\]")),
    ("目标视频句", re.compile("目标视频(?:是|展示|采用).+?收在")),
)
_SUMMARY_PIC_ROLE_OPT = re.compile("%s\\s*作为[^。；]*参考" % _PIC_LIST)
_STATION_MIN_CHARS = 8

# ---------------------------------------------------------------- 段③ 延续分析

_PRESERVED_KINDS = (
    ("人物", re.compile(
        "^<Subject (\\d+)> \\(appears in \\[[^\\]]*\\]\\): fully_preserved - "
        "人物身份、五官发型、以及参考图中的全部穿着与配件完全沿用 <Picture \\d+>；"
        "不新增、不替换图里没有的任何服饰细节。$")),
    ("场景", re.compile(
        "^<Subject (\\d+)> \\(appears in \\[[^\\]]*\\]\\): fully_preserved - "
        "场景环境、背景与画面中已有元素完全沿用 <Picture \\d+>；"
        "不新增参考图里没有的任何内容。$")),
    ("道具", re.compile(
        "^<Subject (\\d+)> \\(appears in \\[[^\\]]*\\]\\): fully_preserved - "
        "道具形制、材质与颜色完全沿用 <Picture \\d+>；"
        "不新增参考图里没有的任何部件。$")),
)
_PRESERVED_AUDIO = re.compile("^<Audio (\\d+)>: fully_copy - .+$")

# ---------------------------------------------------------------- 段④ 通用正则

_AT_RE = re.compile("At \\d{2}:\\d{2}\\.\\d{3}")
_D_TAG_RE = re.compile("<d>")
_D_LANG_RE = re.compile("<d>\\s*\\[(?:Chinese|English|中文|英文)\\]")
_ALIAS_REF = re.compile("\\(S(\\d+)\\)")
_DETAIL_SUBJ = re.compile("<Subject (\\d+)>")
_PICTURE_REF = re.compile("<Picture\\s*(\\d+)>")

# 触发词 / 首帧钉死 / 面部表情 / 双人眼神互动
_TRIGGER_CAM_RE = re.compile("^camera motion\\b")
_FIRST_FRAME_RE = re.compile("(第一帧|首帧|画面开头)")
_EXPR_RE = re.compile(
    "(笑|笑意|眼神|目光|神情|眉眼|嘴角|眼眶|抿唇|挑眉|抬眼|垂眼|眯眼|对视|相视|视线|眉眼弯|撇嘴|皱眉|扬眉)"
)
_GAZE_RE = re.compile("(对视|相视|视线|看向他|看向她|看他|看她|目光|迎上)")

# ---------------------------------------------------------------- 段⑤ 整体音效

_SOUND_WHITELIST = (
    "声", "响", "鸣", "噪", "嗡", "哗", "砰", "啪", "呼", "嘶",
    "滴答", "心跳", "蜂鸣", "回响", "喘息", "摩擦",
)

# ---------------------------------------------------------------- 段⑥ 非叙事音乐

_MUSIC_NONE = re.compile("^(?:无|none|n/?a|不使用|不加|没有|留空)\\s*[。.]?$", re.I)
_MUSIC_WHITELIST = (
    "旋律", "和弦", "节奏", "鼓", "贝斯", "钢琴", "弦乐", "管弦", "合成", "电子",
    "铺底", "单音", "拨弦", "音色", "打击乐", "渐强", "渐弱", "速度", "拍子", "琶音",
    "低音", "长音", "混响", "和声", "节拍", "音垫", "琶音",
)

# ---------------------------------------------------------------- 语言策略

_ENGLISH_SECTIONS = ("摘要", "详细描述", "整体音效", "非叙事音乐")
_CJK_RE = re.compile("[一-鿿]")


# ================================================================ 工具


def _strip_d_tags(text):
    """去掉 `<d>…</d>` 对白标签（含其中文），门禁只看标签外的正文。"""
    return re.sub("<d>.*?</d>", "", text, flags=re.S)


def _split_sections(prompt):
    """按段名行切六段，返回 (dict 段名->正文, dict 段名->行号)。

    段名必须**独立成整行**（允许冒号后缀）。这样正文里偶然提到「摘要」不会误切。
    中文段名（短剧）与英文段名（Ref2VA：MV / 口播）等价，统一归一化成中文键。
    """
    lines = prompt.splitlines()
    idx = {}
    for j, t in enumerate(lines):
        key = t.strip().rstrip(":：")
        key = SEG_ALIAS.get(key, key)
        if key in SEG_ORDER and key not in idx:
            idx[key] = j
    secs = {}
    order = sorted(idx.items(), key=lambda kv: kv[1])
    for k, (name, j) in enumerate(order):
        end = order[k + 1][1] if k + 1 < len(order) else len(lines)
        secs[name] = "\n".join(lines[j + 1:end]).strip()
    return secs, idx


def _nonempty_lines(body):
    return [x.strip() for x in body.splitlines() if x.strip()]


# ================================================================ 段① 主体定义


def _audit_subject_defs(i, body, ctx):
    """段① 白名单：`<Subject N> 是 <Picture M> 中的<身份词>。` + `<Audio N>` 绑定行。"""
    bad = []
    if not body:
        return ["镜%d 「主体定义」段为空——白名单句式：`<Subject N> 是 <Picture M> 中的<身份词>。`，身份词只能取 %s"
                % (i, " / ".join(_IDENT_WHITELIST))]
    seen = {}
    kinds = {}
    audio_spk = {}
    for line in _nonempty_lines(body):
        am = _AUDIO_DEF_LINE.match(line)
        if am:
            bind = _AUDIO_BIND.search(line)
            if _ALIAS_REF.search(line) and not bind:
                bad.append("镜%d 「主体定义」<Audio %s> 行里的 (Sx) 没挂到主体上——"
                           "音频对应某个说话人时必须写成 `<Subject N> (Sx)`（官方 §2.4）"
                           % (i, am.group(1)))
            if bind:
                # 主体 → 说话人编号（音频只借号，不重新编号）
                audio_spk[int(bind.group(1))] = int(bind.group(2))
            continue
        m = _SUBJ_DEF_LINE.match(line)
        if not m:
            # Ref2VA 分支允许场景写场所名（宽松句式），但身份词位置不得夹外观/服饰形容
            m2 = _SUBJ_DEF_LOOSE.match(line) if (ctx or {}).get("ref2va") else None
            if m2 and not any(w in m2.group(3) for w in _LOOK_WORDS):
                n, pic, kind = m2.group(1), m2.group(2), "场景"
            else:
                bad.append("镜%d 「主体定义」不符合白名单句式（%s）——只允许 "
                           "`<Subject N> 是 <Picture M> 中的<身份词>。`，人物身份词只能取 %s，"
                           "不加任何修饰、句末带句号；场景可写场所名但不许夹外观/材质/服饰形容"
                           % (i, line[:36], " / ".join(_IDENT_WHITELIST)))
                continue
        else:
            n, pic, kind = m.group(1), m.group(2), m.group(3)
        if n in seen:
            bad.append("镜%d 「主体定义」重复定义 <Subject %s>——每个主体只能有一行" % (i, n))
        seen[n] = pic
        kinds[n] = kind
    nums = sorted(int(k) for k in seen)
    if nums and nums != list(range(1, len(nums) + 1)):
        bad.append("镜%d 「主体定义」主体编号不连续（当前 %s）——须从 <Subject 1> 起顺序编号"
                   % (i, "、".join("Subject %d" % x for x in nums)))
    ctx["subjects"] = {int(k): int(v) for k, v in seen.items()}
    ctx["subject_kind"] = {int(k): v for k, v in kinds.items()}
    ctx["audio_spk"] = audio_spk
    return bad


def _check_anthropomorphic(i, text, where, ctx):
    """场景/道具主体后紧跟人用动词 = 拟人句式，拦。

    处理只有一条路：按规范格式重新书写（`<Subject N> 是…那块<物件名>` 的是+名词句）。
    """
    bad = []
    kinds = (ctx or {}).get("subject_kind") or {}
    for m in _SUBJ_THEN_HUMAN_VERB.finditer(text or ""):
        n = int(m.group(1))
        kind = kinds.get(n)
        if kind not in _NON_VOCAL_KINDS:
            continue
        frag = " ".join(text[m.start():m.start() + 30].split())
        bad.append("镜%d 「%s」把%s主体 `<Subject %s>` 写成了拟人句（%s…）——「%s」不会自己做这个动作，"
                   "模型会照着文字凭空多生成一个人形。这类异常是**格式书写错误**，只按规范格式重新书写即可："
                   "写成 `<Subject %s> 是…那块<物件名>` 的是+名词句，动词落在物件上；"
                   "**禁用**补「画面里只有两个角色」这类元限定堵漏"
                   % (i, where, kind, n, frag, kind, n))
    return bad


# ================================================================ 段② 摘要


def _audit_summary(i, body, ctx):
    """段② 白名单：任务类型标签 + 站位句 + 目标视频句；不带 Picture、不带 (Sx)。"""
    if not body:
        return ["镜%d 「摘要」段为空——白名单结构（官方 §3）：`[reference generation] <站位句>。"
                "目标视频是 <本镜唯一动作>，收在 <终点态>。`"
                "图与人的对应关系写在主体定义里，摘要不重复挂 Picture" % i]
    bad = []
    flat = " ".join(_nonempty_lines(body))
    for name, rgx in _SUMMARY_REQUIRED:
        if rgx.search(flat):
            continue
        bad.append("镜%d 「摘要」缺白名单构件「%s」——官方 §3 只要求：方括号任务类型前缀 + "
                   "一段简短总结（站位 + 目标视频是…收在…），用已定义的标签描述主体与镜头流"
                   % (i, name))
    if "<Picture" in flat:
        bad.append("镜%d 「摘要」出现 <Picture N> —— 这是**首帧污染源**：H3 会把它当具体帧锚点，"
                   "把参考图画成画面第 0 帧（白底四视图 / 影棚人像）。摘要里一个 Picture 都不许有，"
                   "图源已由主体定义的 `<Subject N> 是 <Picture M> 中的…` 绑定" % i)
    m = re.match("^\\[reference generation[^\\]]*\\]\\s*(.*?)目标视频", flat)
    if m is not None and len(re.sub("\\s", "", m.group(1)).rstrip("。")) < _STATION_MIN_CHARS:
        bad.append("镜%d 「摘要」站位句不成立（标签与「目标视频是」之间只有 %d 字）——"
                   "白名单要求先写清谁在画面哪一侧、和谁什么关系，再写目标视频"
                   % (i, len(re.sub("\\s", "", m.group(1)))))
    al = _ALIAS_REF.search(flat)
    if al:
        bad.append("镜%d 「摘要」出现 (S%s)——`(Sx)` 是说话人编号，只在详细描述里主体开口处出现，摘要里一律不写"
                   % (i, al.group(1)))
    bad.extend(_check_anthropomorphic(i, flat, "摘要", ctx))
    return bad


# ================================================================ 段③ 延续分析


def _audit_retention(i, body, ctx):
    """段③ 白名单：人物 / 场景 / 道具三套整行模板逐字照抄。"""
    if not body:
        return ["镜%d 「延续分析」段为空——白名单句式按类目枚举：\n"
                "        人物：`<Subject N> (appears in [Shot 1]): fully_preserved - 人物身份、五官发型、"
                "以及参考图中的全部穿着与配件完全沿用 <Picture N>；不新增、不替换图里没有的任何服饰细节。`\n"
                "        场景：`<Subject N> (appears in [Shot 1]): fully_preserved - 场景环境、"
                "背景与画面中已有元素完全沿用 <Picture N>；不新增参考图里没有的任何内容。`" % i]
    bad = []
    kinds_hint = " / ".join(k for k, _ in _PRESERVED_KINDS)
    for line in _nonempty_lines(body):
        if _PRESERVED_AUDIO.match(line):
            continue
        hit = [k for k, rgx in _PRESERVED_KINDS if rgx.match(line)]
        if hit:
            continue
        bad.append("镜%d 「延续分析」不符合白名单句式（%s…）——只能照抄两类模板之一（%s），"
                   "面部 / 衣着 / 颜色等具体外观一个字都不写，也不带 (Sx) 说话人编号"
                   % (i, line[:34], kinds_hint))
    al = _ALIAS_REF.search(body)
    if al:
        bad.append("镜%d 「延续分析」出现 (S%s)——官方明确要求延续分析里不写 (Sx)，该段只写 <Subject N>"
                   % (i, al.group(1)))
    return bad


# ================================================================ 段④ 详细描述


def _audit_detail(i, body, ctx):
    """段④ 白名单：触发词 + 风格句 + `[Shot 1]` 正文；编号 / 别名 / 台词 / 首帧 / 表情。"""
    ref2va = bool((ctx or {}).get("ref2va"))
    if not body:
        return ["镜%d 「详细描述」段为空——白名单结构：触发词行（`camera motion`）+ 风格 / 影调句 + "
                "`[Shot 1]` 起头的正文" % i]
    bad = []
    lines = _nonempty_lines(body)

    if ref2va:
        if not (lines and _TRIGGER_CAM_RE.match(lines[0])):
            bad.append("镜%d 「详细描述」首行不是触发词 `camera motion`——漏挂 = 运镜 LoRA 完全不生效，"
                       "提示词文本上看不出任何异常，必须补" % i)
        shot_idx = next((k for k, ln in enumerate(lines) if ln.startswith("[Shot")), None)
        if shot_idx is None:
            bad.append("镜%d 「详细描述」没有以 `[Shot 1]` 起头的正文行——白名单要求正文以 `[Shot 1]` 开头" % i)
        if not _FIRST_FRAME_RE.search(body):
            bad.append("镜%d 「详细描述」没钉死首帧状态——必须写明「画面第一帧就是 <Subject N> 的实景…」，"
                       "否则模型会拿参考图的构图当第一帧（白底四视图 / 影棚人像），前 1~1.5 秒穿帮" % i)
        body_text = "\n".join(lines)
        style_line = lines[0] if lines else ""
        if style_line and _AT_RE.search(style_line):
            bad.append("镜%d 「详细描述」触发词行夹了时间轴——时间轴只写在 `[Shot 1]` 正文里" % i)
    else:
        if len(lines) != 2:
            bad.append("镜%d 「详细描述」是 %d 行——白名单只允许两行：首行风格 / 影调句，"
                       "次行 `[Shot 1]` 起头的正文一整段（正文内部不折行）" % (i, len(lines)))
        body_text = "\n".join(lines) if lines else body

    if not ref2va and lines:
        style_line = lines[0]
        body_text = "\n".join(lines)
        if style_line.startswith("[Shot"):
            bad.append("镜%d 「详细描述」首行以 `[Shot` 开头——首行必须是风格 / 影调句，"
                       "`[Shot 1]` 正文段放第二行" % i)
        else:
            if _D_TAG_RE.search(style_line):
                bad.append("镜%d 「详细描述」首行（风格句）夹了台词——台词只能出现在 `[Shot 1]` 正文段" % i)
            _at = _AT_RE.search(style_line)
            if _at:
                bad.append("镜%d 「详细描述」首行（风格句）夹了时间轴（%s）——时间轴只写在正文段"
                           % (i, _at.group(0)))
            if not (len(lines) >= 2 and lines[1].startswith("[Shot")):
                bad.append("镜%d 「详细描述」第二行没以 `[Shot 1]` 起头——白名单要求正文段紧跟风格句" % i)

    kinds = (ctx or {}).get("subject_kind") or {}
    # 裸 (Sx) / 给不会发声的主体标说话人编号
    for m in re.finditer("(<Subject (\\d+)>\\s*)?\\(S(\\d+)\\)", body_text):
        if not m.group(1):
            bad.append("镜%d 「详细描述」出现裸别名 (S%s)——官方写法每次都写 `<Subject N>` 全称，"
                       "只有会开口说话的主体才带说话人编号，写成 `<Subject N> (S%s)`"
                       "（可在出场句里提前声明）；`(Sx)` 是说话人编号，不是主体简写"
                       % (i, m.group(3), m.group(3)))
            continue
        kind = kinds.get(int(m.group(2)))
        if kind not in _NON_VOCAL_KINDS:
            continue
        bad.append("镜%d 「详细描述」给%s主体 `<Subject %s>` 标了 (S%s)——%s不会发声，"
                   "不带说话人编号，只写 `<Subject %s>`"
                   % (i, kind, m.group(2), m.group(3), kind, m.group(2)))

    a_spk = (ctx or {}).get("audio_spk") or {}
    for m in re.finditer("<Subject (\\d+)>\\s*\\(S(\\d+)\\)", body_text):
        n, sx = int(m.group(1)), int(m.group(2))
        if n in a_spk and a_spk[n] != sx:
            bad.append("镜%d 「详细描述」<Subject %d> 用了 (S%d)，与主体定义里音频绑定的 (S%d) 不一致——"
                       "同一个说话人在两处必须是同一个号（官方 §2.4 音频定义不重新编号）"
                       % (i, n, sx, a_spk[n]))

    pairs = [(int(m.group(1)), int(m.group(2)))
             for m in re.finditer("<Subject (\\d+)>\\s*\\(S(\\d+)\\)", body_text)]
    sub2spk = {}
    spk2sub = {}
    for n, sx in pairs:
        sub2spk.setdefault(n, set()).add(sx)
        spk2sub.setdefault(sx, set()).add(n)

    for n, sxs in sorted(sub2spk.items()):
        if len(sxs) > 1:
            bad.append("镜%d 「详细描述」<Subject %d> 挂了两个说话人编号 %s——编号一旦定下就固定，"
                       "同一主体每次开口都用同一个号"
                       "（官方 §5.4 “Assign (Sx) once … Reuse the corresponding ID at every actual vocal event”）"
                       % (i, n, "、".join("(S%d)" % x for x in sorted(sxs))))
    for sx, ns in sorted(spk2sub.items()):
        if len(ns) > 1:
            bad.append("镜%d 「详细描述」说话人编号 (S%d) 同时给了 %s——一号只归一个说话人，"
                       "第二个开口的人要按顺序拿自己的号"
                       % (i, sx, "、".join("<Subject %d>" % x for x in sorted(ns))))

    used = sorted(spk2sub)
    if used and used != list(range(1, len(used) + 1)):
        miss = [x for x in range(1, max(used) + 1) if x not in used]
        bad.append("镜%d 「详细描述」说话人编号是 %s，跳了 %s——编号从 S1 起按开口先后连续排，"
                   "中间不许空号（官方 §5.1 “stable (S1), (S2), and subsequent IDs”）"
                   % (i, "、".join("S%d" % x for x in used), "、".join("S%d" % x for x in miss)))

    a = _AT_RE.search(body_text)
    d = _D_TAG_RE.search(body_text)
    if a and d and a.start() < d.start():
        bad.append("镜%d 「详细描述」第一句台词前就写了时刻（%s）——[Shot 1] 的第一个事件不写时刻，"
                   "从第二个事件起才用 `At MM:SS.mmm,`" % (i, a.group(0)))

    n_d = len(_D_TAG_RE.findall(body_text))
    n_lang = len(_D_LANG_RE.findall(body_text))
    if n_d and n_lang < n_d:
        bad.append("镜%d 「详细描述」有 %d 处 `<d>` 但只有 %d 处带语种标签——"
                   "台词白名单写法是 `<d>[Chinese]…</d>`" % (i, n_d, n_lang))

    pm = _PICTURE_REF.search(body_text)
    if pm:
        bad.append("镜%d 「详细描述」直接引用了 <Picture %s>——正文白名单只认 `<Subject N>`，"
                   "参考图只在主体定义与摘要里挂接" % (i, pm.group(1)))

    bad.extend(_check_anthropomorphic(i, body_text, "详细描述", ctx))

    if ref2va:
        if not _EXPR_RE.search(body_text):
            bad.append("镜%d 「详细描述」整段没有一处面部表情（笑 / 眼神 / 神情 / 眉眼 / 嘴角…）——"
                       "人物会一脸空白地站着。要按本段歌词情绪写清脸上在发生什么，并给出双人之间的眼神或笑意互动" % i)
        _kinds = (ctx or {}).get("subject_kind") or {}
        n_char = sum(1 for k in _kinds.values() if k not in _NON_VOCAL_KINDS)
        if n_char >= 2 and not _GAZE_RE.search(body_text):
            bad.append("镜%d 「详细描述」%d 个人物同框，却没有任何眼神 / 表情互动——"
                       "要么各看各的像两个陌生人，要么全程严肃。双人段必须写出「对视 / 相视一笑 / 她抬眼看他」"
                       "这类互动（用户定：不同歌词、不同段落要有不同的表情与互动方式）" % (i, n_char))
    return bad


# ================================================================ 段⑤ 整体音效


def _audit_soundscape(i, body, ctx):
    """段⑤ 白名单：非空、落到具体声音、不夹台词、不带 (Sx)。"""
    if not body:
        return ["镜%d 「整体音效」段为空——白名单：必须写全片的环境声与物理声"
                "（如「室内极轻的机器低鸣，指尖在屏幕上轻点两下」）；没有就明确写「无」" % i]
    bad = []
    flat = " ".join(_nonempty_lines(body))
    if not any(w in flat for w in _SOUND_WHITELIST):
        bad.append("镜%d 「整体音效」没写出任何声音（%s…）——白名单要求这一段落到具体声音上"
                   "（声 / 响 / 鸣 / 嗡 / 滴答 / 摩擦…），不得只写动作" % (i, flat[:24]))
    if _D_TAG_RE.search(body):
        bad.append("镜%d 「整体音效」夹了台词 `<d>`——对白只写在详细描述，这一段只写环境声与物理声" % i)
    al = _ALIAS_REF.search(body)
    if al:
        bad.append("镜%d 「整体音效」出现 (S%s)——`(Sx)` 只在详细描述里主体开口处写，"
                   "这一段要指认人物就写 <Subject N>" % (i, al.group(1)))
    return bad


# ================================================================ 段⑥ 非叙事音乐


def _audit_music(i, body, ctx):
    """段⑥ 白名单：非空；写「无」或命中音乐构件词。"""
    if not body:
        return ["镜%d 「非叙事音乐」段为空——白名单：有配乐就写乐器 / 节奏 / 力度，没有就明确写「无」" % i]
    bad = []
    flat = " ".join(_nonempty_lines(body))
    if not (_MUSIC_NONE.match(flat) or any(w in flat for w in _MUSIC_WHITELIST)):
        bad.append("镜%d 「非叙事音乐」没落到具体音乐特征（%s…）——白名单要求写乐器 / 节奏 / 力度"
                   "（旋律 / 电子 / 单音 / 铺底 / 鼓 / 渐弱…），或者直接写「无」" % (i, flat[:24]))
    if _D_TAG_RE.search(body):
        bad.append("镜%d 「非叙事音乐」夹了台词 `<d>`——唱词 / 对白只写在详细描述" % i)
    al = _ALIAS_REF.search(body)
    if al:
        bad.append("镜%d 「非叙事音乐」出现 (S%s)——`(Sx)` 只在详细描述里主体开口处写，"
                   "要指认就写 <Subject N>" % (i, al.group(1)))
    return bad


# ================================================================ 跨段


def _audit_cross_section(i, secs, ctx):
    """延续分析须覆盖主体定义里每个主体；摘要 / 详细描述不得出现未定义主体。"""
    subs = (ctx or {}).get("subjects") or {}
    if not subs:
        return []
    bad = []
    defined = set(subs)
    ret = secs.get("延续分析") or ""

    ret_nums = {int(m.group(1)) for m in _DETAIL_SUBJ.finditer(ret)}
    extra = sorted(ret_nums - defined)
    if extra:
        bad.append("镜%d 「延续分析」引用了未定义的主体 %s——主体必须在「主体定义」里先出现"
                   % (i, "、".join("<Subject %d>" % x for x in extra)))
    missing = sorted(defined - ret_nums)
    if missing:
        bad.append("镜%d 「延续分析」漏了主体 %s——主体定义里的每一个主体都要有一行延续分析"
                   % (i, "、".join("<Subject %d>" % x for x in missing)))

    for name in ("摘要", "详细描述"):
        used = {int(m.group(1)) for m in _DETAIL_SUBJ.finditer(secs.get(name) or "")}
        und = sorted(used - defined)
        if und:
            bad.append("镜%d 「%s」引用了未定义的主体 %s——先在「主体定义」里定义"
                       % (i, name, "、".join("<Subject %d>" % x for x in und)))
    return bad


_AUDITORS = (
    ("主体定义", _audit_subject_defs),
    ("摘要", _audit_summary),
    ("延续分析", _audit_retention),
    ("详细描述", _audit_detail),
    ("整体音效", _audit_soundscape),
    ("非叙事音乐", _audit_music),
)


# ================================================================ 入口


def audit_short_drama_six_segment(shots, allow_chinese=True):
    """逐镜检查六段式提示词是否符合白名单模板，返回违规条目列表（空列表 = 通过）。

    shots: list[dict]，每项含 "prompt"（六段式中文段名提示词）。

    allow_chinese=True（默认）：四段正文允许中文；
    False：仍按旧的「四段须全英文、中文只进 <d>」拦截。
    """
    bad = []
    for i, s in enumerate(shots, 1):
        prompt = (s.get("prompt") or "") if isinstance(s, dict) else ""
        if not prompt.strip():
            continue
        secs, idx = _split_sections(prompt)
        miss = [h for h in SEG_ORDER if h not in idx]
        if miss:
            bad.append("镜%d 六段不全，缺：%s（顺序应为 %s）"
                       % (i, "、".join(miss), " → ".join(SEG_ORDER)))
        else:
            if [idx[h] for h in SEG_ORDER] != sorted(idx[h] for h in SEG_ORDER):
                got = sorted(SEG_ORDER, key=lambda h: idx[h])
                bad.append("镜%d 六段顺序不对：当前 %s，应为 %s"
                           % (i, " → ".join(got), " → ".join(SEG_ORDER)))
        ctx = {}
        for name, fn in _AUDITORS:
            if name not in secs:
                continue
            bad.extend(fn(i, secs[name], ctx))
        bad.extend(_audit_cross_section(i, secs, ctx))
        if allow_chinese:
            continue
        for name in _ENGLISH_SECTIONS:
            body = _strip_d_tags(secs.get(name) or "")
            if not body.strip():
                continue
            hit = _CJK_RE.search(body)
            if not hit:
                continue
            spans = "".join(m.group(0) for m in _CJK_RE.finditer(body))[:24]
            bad.append("镜%d 「%s」段出现中文短语（%s…），该段须全英文，中文只进 <d>[Chinese]…</d>"
                       % (i, name, spans))
    return bad


def audit_ref2va_six_segment(prompts, allow_chinese=True, label="段"):
    """Ref2VA 分支（MV / 数字人口播）的六段式门禁 —— **与短剧共用同一套校验器**。

    短剧走中文段名、MV / 口播走英文段名，本模块用 `SEG_ALIAS` 归一化后跑同一套 `_AUDITORS`；
    差异只在两点（Ref2VA 的真实结构）：详细描述允许正文跨多行、场景主体允许场所名词。

    prompts: list[str] 或 list[dict{prompt}]。返回违规列表（空 = 通过）。
    """
    bad = []
    for i, p in enumerate(prompts, 1):
        if isinstance(p, dict):
            p = p.get("prompt") or ""
        if not (p or "").strip():
            bad.append("%s%d 提示词为空" % (label, i))
            continue
        secs, idx = _split_sections(p)
        miss = [h for h in SEG_ORDER if h not in idx]
        if miss:
            bad.append("%s%d 六段不全，缺：%s（顺序应为 %s）"
                       % (label, i, "、".join(miss), " → ".join(SEG_ORDER)))
        else:
            if [idx[h] for h in SEG_ORDER] != sorted(idx[h] for h in SEG_ORDER):
                got = sorted(SEG_ORDER, key=lambda h: idx[h])
                bad.append("%s%d 六段顺序不对：当前 %s，应为 %s"
                           % (label, i, " → ".join(got), " → ".join(SEG_ORDER)))
        ctx = {"ref2va": True}
        for name, fn in _AUDITORS:
            if name not in secs:
                continue
            bad.extend(fn(i, secs[name], ctx))
        bad.extend(_audit_cross_section(i, secs, ctx))
    return bad
