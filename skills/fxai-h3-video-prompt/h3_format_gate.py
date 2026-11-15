# -*- coding: utf-8 -*-
"""
H3 六段式提示词格式门禁（短剧 / 漫剧）。

《仙宫云新版镜像：短剧漫剧 H3 提示词六段式规范》一文只写「该怎么做」。
本模块收「不允许出现」的写法，在提交渲染前统一拦截，命中即不让过。

硬规则（规范 + 用户口径 2026-11-14）：
  1. 主体定义只指认：`<Subject N> 是 <Picture M> 中的<身份词>。`
     身份词只到「男子 / 女子 / 老人 / 小孩 / 场景 / 道具」这一层，
     不得加国籍 / 年龄 / 民族 / 身材 / 相貌 / 衣着修饰
     （如「中国年轻男子」「古装年轻女子」+ 黑发 / 眼镜 / 衬衫 等外观串）。
  2. 四段正文（摘要 / 详细描述 / 整体音效 / 非叙事音乐）一律英文，
     中文只出现在 `<d>[Chinese]…</d>` 对白标签内；
     在这四段正文里出现中文短语（标签外）即违规。

唯一入口：audit_short_drama_six_segment(shots) -> list[str]（空列表 = 通过）。
短剧提交口 drama_pipeline.build_workflow 调用它，命中即抛错停在提交前。
"""


import re

# 中文六段段名（短剧 / 漫剧分支）
SEG_HEADERS = ["主体定义", "延续分析", "摘要", "详细描述", "整体音效", "非叙事音乐"]

# 主体定义里禁止出现的修饰词：国籍 / 年龄 / 民族 / 相貌 / 衣着 / 身材 / 颜色类外观
# 结构：① 一批独立修饰词（直接命中）；②「颜色 + 后缀」组合（如 黑发 / 白衬衫 / 蓝瞳）
_FORBIDDEN_MODIFIER = re.compile(
    r"(?:"
    r"中国|古装|现代|当代|老年|中年|青年|少年|幼年|儿童|小孩|年轻|"
    r"黑发|白发|金发|银发|短发|长发|卷发|直发|秃|"
    r"眼镜|墨镜|胡子|胡须|"
    r"衬衫|裙子|裤子|西装|外套|大衣|运动鞋|高跟鞋|凉鞋|靴子|连衣裙|"
    r"身材|高大|魁梧|苗条|丰满|胖|瘦|"
    r"民族|汉族|少数民族|黄种|白种|黑种|黑人|白人|黄人|混血|亚裔|欧裔|非裔|"
    r"蓝眼|黑眼|双眼皮|单眼皮"
    r")"
    r"|"
    r"(?:红|橙|黄|绿|青|蓝|紫|白|黑|灰|棕)\s*(?:色|发|衣|衫|裙|裤|鞋|瞳|肤|框)"
)

# 主体定义行：捕捉「中的 <身份词> 。」
_SUBJECT_LINE_RE = re.compile(r"是\s*<Picture[^>]*>\s*中的(.*?)。")

# 主体定义段：从「主体定义」到「延续分析」
_SUBJECT_SEC_RE = re.compile(r"主体定义\s*([\s\S]*?)\n\s*延续分析", re.M)

# 四段英文正文段（正文在段名之后、下一个段名之前）
_ENGLISH_SECTIONS = {
    "摘要": re.compile(r"摘要\s*([\s\S]*?)\n\s*详细描述", re.M),
    "详细描述": re.compile(r"详细描述\s*([\s\S]*?)\n\s*整体音效", re.M),
    "整体音效": re.compile(r"整体音效\s*([\s\S]*?)\n\s*非叙事音乐", re.M),
    "非叙事音乐": re.compile(
        r"非叙事音乐\s*([\s\S]*?)(?=\n\s*(?:subject|主体|延续|摘要|详细|整体|"
        r"非叙事|negative|world|audio|\Z))",
        re.M | re.I),
}

# 主体定义里出现这些，说明写了身份词之外的外观描写（逗号 / 顿号 / 着 / 穿 / 戴 / 系 / 披 / 裹）
_APPEARANCE_HINT = re.compile(r"[，、,]|着|穿|戴|留着|系着|披着|裹着|戴着")

_CJK_RE = re.compile(r"[一-鿿]")


def _strip_d_tags(text):
    """去掉 `<d>…</d>` 对白标签（含其中文），门禁只看标签外的正文。"""
    return re.sub(r"<d>.*?</d>", "", text, flags=re.S)


def _clean_anchor(ident):
    """去掉允许的（角色名）锚括号，只留身份词用于判定。"""
    return re.sub(r"[（(][^）)]*[）)]", "", ident)


def audit_short_drama_six_segment(shots):
    """逐镜检查六段式提示词格式，返回违规条目列表（空列表 = 通过）。

    shots: list[dict]，每项含 "prompt"（六段式中文段名提示词）。
    ## 仅拦截短剧 / 漫剧的中文六段格式；其他格式（Ref2VA 英文段名）不匹配、不误拦。
    """
    bad = []
    for i, s in enumerate(shots, 1):
        prompt = (s.get("prompt") or "") if isinstance(s, dict) else ""
        if not prompt.strip():
            continue

        # ① 主体定义：只指认，不得加修饰 / 外观
        msec = _SUBJECT_SEC_RE.search(prompt)
        if msec:
            for line in msec.group(1).splitlines():
                line = line.strip()
                if "中的" not in line:
                    continue
                m = _SUBJECT_LINE_RE.search(line)
                ident = m.group(1) if m else line
                ident_clean = _clean_anchor(ident)
                if _FORBIDDEN_MODIFIER.search(ident_clean):
                    bad.append("镜%d 主体定义含国籍/年龄/民族/相貌/衣着修饰（\"%s\"），"
                               "应只写到身份词（男子/女子/场景/道具）"
                               % (i, ident.strip()))
                    continue
                if _APPEARANCE_HINT.search(ident_clean):
                    bad.append("镜%d 主体定义写了身份词之外的外观描述（\"%s\"），"
                               "应只写「<Picture M> 中的<身份词>。」"
                               % (i, ident.strip()))

        # ② 四段正文须全英文，中文只进 <d> 标签
        for name, rgx in _ENGLISH_SECTIONS.items():
            m = rgx.search(prompt)
            if not m:
                continue
            body = _strip_d_tags(m.group(1))
            hit = _CJK_RE.search(body)
            if hit:
                spans = "".join(x.group(0) for x in _CJK_RE.finditer(body))[:24]
                bad.append("镜%d 「%s」段出现中文短语（%s…），该段须全英文，"
                           "中文只进 <d>[Chinese]…</d>"
                           % (i, name, spans))

    return bad
