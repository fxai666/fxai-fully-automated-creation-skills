# -*- coding: utf-8 -*-
"""文案合规预检：写完 segments.json 就查，不等成片。

三档结果：
  ERROR   联系方式露出 / 私信引导话术 / 公网版开了引导条 / 主推标题还没产出 —— 必须补或改
  WARN    违禁词命中、缺真人经验、缺互动锚、引导跳转主页、钩子不合规、标题超 22 字 —— 人工确认后可放行
  INFO    六类必选发布标签待选定、话题标签待补

用法：
    python sv_compliance.py <segments.json> [--mode public|private]
    python sv_compliance.py <segments.json> --titles titles.json

`--mode public` 为默认，按抖音 / 视频号 / 小红书 / 快手的尺度判联系方式与私信引导；
出私域版时传 `--mode private` 放行引导条、联系方式与私信引导。

退出码：0 = 无 ERROR（WARN 仍可放行）；1 = 存在 ERROR。
"""
import argparse
import json
import re
import sys

LEVEL_ORDER = {"ERROR": 0, "WARN": 1, "INFO": 2}
TITLE_MAX = 22          # 主推标题字数上限（去空白后的字符数）
# 🔴 首段（口播第 1 段）不限字数：22 字只管发布标题，正文开篇讲完为止

CONTACT_PAT = [
    (r"QQ\s*群?\s*[：:]?\s*\d+", "QQ 群号"),
    (r"\b1[3-9]\d{9}\b", "手机号"),
    (r"微信|加微|微信号|vx|v信|绿泡泡", "微信"),
    (r"二维码|扫码|扫一扫", "二维码"),
    (r"https?://|www\.", "网址"),
    (r"[\w-]+\.(com|cn|net|site|org)\b", "域名"),
    (r"网盘|夸克|迅雷云盘|领取安装包", "网盘/安装包"),
    (r"私信我|私聊我|后台留言|评论区扣\s*1|扣\s*1\s*领", "领取类暗语"),
]

# 🔴 私信引导：平台按「把用户从站内引出去」判违规，模糊话术（私信我获取 / 如有需要可私信获取 /
# 如您需要私信获取 / 主页有惊喜）同样命中，谐音与委婉前缀拦不住。
# 只命中「带获取意图或委婉前缀」的组合，纯讨论场景里的「私信」不判。
DM_KEY_PAT = re.compile(r"私[信聊]|后台留言|扣\s*1")
# 无需邻近词即可判的纯暗语
DM_CODE_PAT = re.compile(r"主页有惊喜|主页有彩蛋|私信我拿|滴滴我|扣我")
DM_ACQ_PAT = re.compile(r"获取|领取|拿|发[给]|给我|留[资]|联系|咨询|详聊|打包|发你|甩你|安排")
DM_HEDGE_PAT = re.compile(r"如有需要|如需|如您需要|如果需要|需要的话|有需要|想要|感兴趣|想要的|需要的")


def dm_guide_hits(s):
    """返回文中命中「私信引导」的片段，只判带获取意图或委婉前缀的组合。"""
    out = []
    for m in DM_CODE_PAT.finditer(s):
        out.append(m.group(0))
    for m in DM_KEY_PAT.finditer(s):
        seg = s[max(0, m.start() - 14): m.end() + 14]
        if DM_ACQ_PAT.search(seg) or DM_HEDGE_PAT.search(seg):
            out.append(seg.strip())
    return out

WORD_PAT = [
    (r"最[佳好强便宜]|最强|全网最低|最低价|性价比最高", "极限词"),
    (r"全网第一|销量第一|行业第一|NO\.?1|TOP\s*1|独一无二|仅此一家", "第一/独一"),
    (r"唯一|独家|首发|首款|顶级|顶尖|天花板|巅峰|yyds|封神|绝绝子", "夸大类"),
    (r"100%|百分百|万能|永久|永不|根治|彻底|完美|无敌|零缺陷", "绝对断言"),
    (r"国家级|世界级|权威认证|驰名商标|三甲医院|官方唯一授权|质量免检", "权威背书"),
    (r"治疗|治愈|消炎|杀菌|防癌|排毒|增强免疫力|降三高", "医疗暗示"),
    (r"保本|稳赚|必赚|零风险|内幕消息|包过|必过|包上岸", "承诺诱导"),
    (r"跳楼价|亏本清仓|再不抢就没|最后一波|随时涨价|手慢无", "逼单话术"),
]

EXPERIENCE_PAT = re.compile(r"(我们|自己|我这边|我自己|我的|我当时|我有一回|我记得|我怎样|咱)")
# 第一人称在场：平台会判「缺少第一人称感受」，带第一人称的段要够多、中段保持连贯
FIRST_PERSON_PAT = re.compile(r"我|咱们|自己")
MAX_NO_FP_RUN = 3          # 连续无第一人称的段数上限
QUESTION_PAT = re.compile(r"[？?]")
DIGIT_PAT = re.compile(r"\d")
# 中文数字（两点 / 四小时 / 百分之三十）：口播文案按中文读法写，同样算"带数字"
# 模糊数词（几 / 近 / 余 / 多）：近几周、近几年、十余、多次 这类"数字感表达"也算带数字
# （用户定调：开篇不一定要硬数字 123，模糊数字表达同样能留住人）
CN_DIGIT_PAT = re.compile(r"[〇零一二三四五六七八九十百千万亿两几近余多]")
_MODEL_EDGE = re.compile(r"[A-Za-z_]")     # 型号/版本号里的数字（GPT-4、Qwen3）放行


def arabic_hits(plain):
    """口播文案里还写成阿拉伯数字的地方，返回命中串（型号与版本号放行）。"""
    out = []
    for m in re.finditer(r"\d+(?:\.\d+)?%?", plain):
        s, e = m.span()
        prev = plain[s - 1] if s else ""
        nxt = plain[e] if e < len(plain) else ""
        if _MODEL_EDGE.match(prev or " ") or _MODEL_EDGE.match(nxt or " "):
            continue
        out.append(m.group(0))
    return out
# 第一人称亲历段里的主观感受：只讲事实不讲感受，读者读不出「真人用过」
FEEL_PAT = re.compile(r"抓狂|崩溃|劝退|头大|头疼|踩过坑|填坑|惊喜|意外|没想到|真香|后悔|"
                      r"折腾|无奈|弃坑|上头|窒息|卡死|翻车|白干|白折腾|浪费|心疼|"
                      r"踏实|轻松|解脱|痛快|爽感|安心|救命|懵|受不了|忍不了")
# 互动锚 = 面向观众的提问或邀请，陈述句里的问号不计
ASK_PAT = re.compile(r"[？?]\s*(你|大家|各位|咱们)|(你|大家|各位)\s*[^。！]{0,12}[？?]|评论区|说说你|聊聊你")
GROUP_PAT = re.compile(r"大家|各位|你们|朋友们")     # 面向群体，不是只问某一个人
# 关注动作白名单：当页就能完成的关注 / 收藏 / 点赞
CTA_PAT = re.compile(r"评论区|评论|留言|收藏|关注|点赞")
# 关注动作白名单之外的主页类引导跳转，见 fxai-voiceover-script/references/05-结尾与引导.md
JUMP_HOME_PAT = re.compile(r"点头像|进我?主页|看主页|主页合集|主页找")

# 🔴 套路化开篇：模板句式每条稿子都套一遍，观众一听就出戏（见 fxai-voiceover-script 03 册「开篇三不」）
CLICHE_OPEN_PAT = [
    (r"凌晨|半夜|深夜|大半夜|三点|两点", "夜半熬夜式"),
    (r"你(现在)?还在[^。！]{0,12}吗|是不是又|有没有也被", "反问逼问式"),
    (r"一晚上全没了|白折腾一天|彻底崩了|全崩了|一整天就这么没了", "灾难夸张式"),
]


def walk_strings(node, path=""):
    """递归取出 text / fields 里所有可见字符串，返回 [(位置, 文本), ...]。"""
    out = []
    if isinstance(node, str):
        out.append((path, node))
    elif isinstance(node, dict):
        for k, v in node.items():
            out += walk_strings(v, "%s.%s" % (path, k) if path else str(k))
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            out += walk_strings(v, "%s[%d]" % (path, i))
    return out


def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s)


def clen(t):
    """标题 / 钩子字数：去空白后的字符数。"""
    return len(re.sub(r"\s+", "", t))


def check_titles(d, required=True):
    """发布标题校验：接收 `sv_title.build()` 产出的 dict。"""
    issues = []
    if not d or not d.get("main"):
        if required:
            issues.append(("ERROR", "标题", "还没产出发布标题：跑 python sv_title.py <segments.json>"))
        return issues

    main = d["main"]
    n = clen(main)
    if n > TITLE_MAX:
        issues.append(("WARN", "标题", "主推 %d 字，超过 %d 字" % (n, TITLE_MAX)))
    for pat, label in CONTACT_PAT:
        m = re.search(pat, main, re.I)
        if m:
            issues.append(("ERROR", "标题", "主推含%s「%s」" % (label, m.group(0))))
    for pat, label in WORD_PAT:
        m = re.search(pat, main, re.I)
        if m:
            issues.append(("WARN", "标题", "%s 命中「%s」" % (label, m.group(0))))
    n_alt = len(d.get("alts") or [])
    if n_alt < 3:
        issues.append(("WARN", "标题", "备选 %d 条，要求 ≥3 条（数字对比 / 极短痛点 / 提问钩子 / 清单式各一路）"
                       % n_alt))
    if not d.get("tags"):
        issues.append(("INFO", "标题", "话题标签待补：2 个垂直赛道 + 1 个泛赛道"))
    return issues


def check_specs(specs, mode="public"):
    issues = []
    exp_n = ask_n = feel_n = group_n = 0
    fp_n = q_n = 0               # 带第一人称的段数 / 带真实问句的段数
    fp_run = fp_max_run = 0      # 当前连续无第一人称段数 / 最长值
    exp_at = []          # 第一人称亲历段落在第几段

    for i, seg in enumerate(specs, 1):
        name = seg.get("slug") or "第%d段" % i
        fields = seg.get("fields") or {}
        text = seg.get("text") or ""

        if mode == "public" and fields.get("site_bar"):
            issues.append(("ERROR", name, "公网版开了 site_bar 引导条；删掉该项或改用 --mode private"))

        for path, raw in walk_strings({"text": text, "fields": fields}):
            s = strip_tags(raw)
            for pat, label in CONTACT_PAT:
                for m in re.finditer(pat, s, re.I):
                    issues.append(("ERROR", name, "%s 命中「%s」（%s）" % (label, m.group(0), path)))
            if mode == "public":
                for frag in dm_guide_hits(s):
                    issues.append(("ERROR", name,
                                    "私信引导命中「%s」（%s）：公域按「引流出站内」判违规，"
                                    "改成关注 / 收藏这类当页动作" % (frag, path)))
                for m in JUMP_HOME_PAT.finditer(s):
                    issues.append(("WARN", name,
                                    "「%s」（%s）不在关注动作白名单；关注动作取当页完成的"
                                    "关注 / 收藏 / 点赞" % (m.group(0), path)))
            for pat, label in WORD_PAT:
                for m in re.finditer(pat, s, re.I):
                    issues.append(("WARN", name, "%s 命中「%s」（%s）" % (label, m.group(0), path)))

        plain = strip_tags(text)
        for hit in arabic_hits(plain):
            issues.append(("WARN", name, "口播文案里的数字要写中文读法：「%s」" % hit))
        if EXPERIENCE_PAT.search(plain) and (DIGIT_PAT.search(plain)
                                             or CN_DIGIT_PAT.search(plain)
                                             or FEEL_PAT.search(plain)):
            exp_n += 1
            exp_at.append(i)
            if FEEL_PAT.search(plain):
                feel_n += 1
        if ASK_PAT.search(plain):
            ask_n += 1
        if GROUP_PAT.search(plain):
            group_n += 1
        if FIRST_PERSON_PAT.search(plain):
            fp_n += 1
            fp_run = 0
        else:
            fp_run += 1
            fp_max_run = max(fp_max_run, fp_run)
        if QUESTION_PAT.search(plain):
            q_n += 1

    head = strip_tags((specs[0].get("text") or "")) if specs else ""
    if head:
        hook_vals = specs[0].get("fields") or {}
        hook_txt = strip_tags(str(hook_vals.get("hook", "") or ""))
        if "？" in head or "?" in head or "？" in hook_txt or "?" in hook_txt:
            issues.append(("WARN", "首段", "开篇用提问句；改成共同经历式叙事（我们前不久还在…）"))
        elif not (DIGIT_PAT.search(head) or CN_DIGIT_PAT.search(head)
                  or DIGIT_PAT.search(hook_txt) or CN_DIGIT_PAT.search(hook_txt)):
            issues.append(("WARN", "首段", "钩子缺数字，开篇留不住人"))
        for pat, label in CLICHE_OPEN_PAT:
            if re.search(pat, head):
                issues.append(("WARN", "首段", "套路化开篇（%s）：从真实接触经历起手，"
                               "不写夜半熬夜 / 反问逼问 / 灾难夸张" % label))

    if exp_n < 2:
        issues.append(("WARN", "全篇", "第一人称真人经验 %d 段，要求 ≥2 段（我们+具体动作+结果数字或故障）" % exp_n))
    if feel_n < 1:
        issues.append(("WARN", "全篇", "亲历段里没有主观感受（抓狂/踩过坑/没想到/解脱…）；只讲事实读者读不出真人用过"))
    if exp_at and max(exp_at) <= len(specs) // 2:
        issues.append(("WARN", "全篇", "亲历段全在前半段，中后段要再落一次自己的体验"))
    if specs and fp_n * 2 < len(specs):
        issues.append(("WARN", "全篇", "带第一人称的段只有 %d/%d，不足半数；平台会判「缺少第一人称感受」"
                       % (fp_n, len(specs))))
    if fp_max_run > MAX_NO_FP_RUN:
        issues.append(("WARN", "全篇", "有连续 %d 段没有第一人称（上限 %d 段），中段要保持第一人称在场"
                       % (fp_max_run, MAX_NO_FP_RUN)))
    if q_n < 2:
        issues.append(("WARN", "全篇", "全篇真实问句只有 %d 段，要求 ≥2 段（中后段一处 + 结尾一处）；"
                       "平台会判「缺少互动引导」" % q_n))
    if ask_n < 2:
        issues.append(("WARN", "全篇", "互动锚 %d 处，要求 ≥2 处（结尾关注引导 + 中后段开放式提问）" % ask_n))
    if group_n < 1:
        issues.append(("WARN", "全篇", "没有面向群体的邀请（大家/各位）；互动锚写成「大家踩过什么坑」这类群体提问"))
    tail = strip_tags(str((specs[-1].get("text") or ""))) if specs else ""
    if specs and not (ASK_PAT.search(tail) and CTA_PAT.search(tail)):
        issues.append(("WARN", "结尾段", "结尾要同时含提问与关注动作（关注/收藏），提问在前、引导在后"))
    # 🔴 空心引导：评论区打XX / 打1 / 扣1（无自动回复承接，观众打完无下文）
    HOLLOW_CTA = re.compile(r"评论区\s*打|评论区\s*扣|打\s*1|扣\s*1|打\s*[「『“]")
    if specs and HOLLOW_CTA.search(tail):
        issues.append(("WARN", "结尾段",
                       "结尾用「评论区打XX / 打1 / 扣1」式空心引导：本技能无自动回复承接，"
                       "观众打完无下文，既掉信任也不诚实。改成真实讨论邀请（聊经历 / 说是否需要自动化 / 写体会）+ 关注动作（关注/收藏）"))
    if specs and not QUESTION_PAT.search(tail):
        issues.append(("WARN", "结尾段", "结尾没有真实问句（问号）；只写「评论区打…」不算互动引导"))
    issues.append(("INFO", "全篇", "发布标签选一项写进交付清单：含 AI 生成 / 含营销信息 / 个人观点 / 虚构演绎 / 转载 / 无需标注"))

    issues.sort(key=lambda x: LEVEL_ORDER[x[0]])
    return issues


def report(issues):
    counts = {"ERROR": 0, "WARN": 0, "INFO": 0}
    for lv, where, msg in issues:
        counts[lv] += 1
        print("  [%s] %-8s %s" % (lv, where, msg))
    print("  合计 ERROR %d / WARN %d / INFO %d" % (counts["ERROR"], counts["WARN"], counts["INFO"]))
    return counts


def main():
    ap = argparse.ArgumentParser(description="文案合规预检（写完 segments.json 立即跑）")
    ap.add_argument("spec", help="分段规格 JSON")
    ap.add_argument("--mode", choices=["public", "private"], default="public",
                    help="public=按公域平台尺度（默认）；private=私域版放行联系方式")
    ap.add_argument("--titles", help="同查发布标题（sv_title.py 产出的 titles.json）")
    a = ap.parse_args()

    with open(a.spec, encoding="utf-8") as f:
        data = json.load(f)
    specs = data if isinstance(data, list) else data.get("segments", [])

    print("[合规] %s  mode=%s  共 %d 段" % (a.spec, a.mode, len(specs)))
    issues = check_specs(specs, mode=a.mode)
    if a.titles:
        try:
            with open(a.titles, encoding="utf-8") as f:
                tdata = json.load(f)
        except Exception:
            tdata = {}
        issues += check_titles(tdata)
    counts = report(issues)
    if counts["ERROR"]:
        print("[合规] 存在 ERROR，改完再进管线")
        sys.exit(1)
    print("[合规] 可进管线")
    sys.exit(0)


if __name__ == "__main__":
    main()
