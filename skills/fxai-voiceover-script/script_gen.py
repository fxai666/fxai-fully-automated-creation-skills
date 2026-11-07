# -*- coding: utf-8 -*-
# =====================================================================
# fxai-voiceover-script —— 口播文案生成（把规范/原文变成能直接念的口播稿）
#
# 两种模式，看给了哪个参数：
#   A 贴标签（默认）：--text-file <现成文案>  → 只加语气/情感标签，不动文章一个字
#   B 提炼生成      ：--source-file <原文>    → 忠实原文抽要点成篇（开头提问 / 结尾固定句）
#
# 输出格式（引擎白名单：只有 CosyVoice 用这套标签）：
#   cosyvoice → 用<语言><情感>、<情绪>、<语气>的语气说<|endofprompt|>。<说话内容>
#   qwen3tts  → 直接输出正常写稿文案，不加任何标签
#   每个空行段一条，一行一段音频，可直接喂 voice_gen.py --from-script
# =====================================================================
import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))      # 技能目录
_SKILLS = os.path.dirname(_HERE)                        # 所有技能共同的父目录
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_SKILLS, "fxai-common"))
import fxai_common as fxc

MAX_CHARS = 80          # 单条说话内容上限：官方 split_paragraph 的 token_max_n=80（中文按字符数算）
                        # 只算要念的正文，「用…的语气说<|endofprompt|>。」这段标签不计入

ENDING = "对此您有什么看法请在评论区告诉我，关注我，后面我接着更，咱们下次见。"
OPENER = "为啥"          # 开头口语提问固定是「为啥…？」

KEYWORDS = ("因为", "所以", "方法", "技巧", "注意", "建议", "关键", "其实", "原来",  # std:allow
            "根本", "只要", "才能", "学会", "搞定", "效率", "第一步", "其次",
            "最后", "就是", "比如", "举个例子", "重点", "核心", "真正", "一旦")


def _cfg():
    path = os.environ.get(
        "FXAI_GLOBAL_CONFIG", os.path.expanduser("~/.workbuddy/fxai_global.json"))
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("tts") or {}
    except Exception:
        return {}


# ---------- 加工 ----------
def num2cn(n):
    """整数 → 中文数字（口播念读规范）。"""
    if n == 0:
        return "零"
    digits, units = "零一二三四五六七八九", ("", "十", "百", "千")
    if n < 10000:
        out = ""
        for i in range(3, -1, -1):          # 高位往低位拼；「零」只补在两个有数的位中间
            d = (n // 10 ** i) % 10
            if d:
                out += digits[d] + units[i]
            elif out and not out.endswith("零") and (n % 10 ** i):
                out += "零"
        return out[1:] if out.startswith("一十") else out   # 一十五 → 十五
    if n < 100000000:
        wan, rest = n // 10000, n % 10000
        return num2cn(wan) + "万" + ("零" + num2cn(rest) if rest else "")
    return str(n)


def to_chinese(text):
    """阿拉伯数字 → 中文数字（0~99，再大的原文里少见）。"""
    return re.sub(r"\d+", lambda m: num2cn(int(m.group(0))), text)


def polite(text):
    """第二人称 → 您。"""
    for a, b in (("你们", "您们"), ("你", "您")):
        text = text.replace(a, b)
    return text


def polish(text, polite_second=True):
    """数字转中文 + 可选第二人称敬语转换。

    A 模式（--text-file，只加标签不改写）必须传 polite_second=False。
    """
    t = to_chinese(text)
    return polite(t) if polite_second else t


def split_sentences(text):
    return [s.strip() for s in re.split(r"(?<=[。！？；!?;])", text) if s.strip()]


def cut(s, n=MAX_CHARS):
    """按句末标点切到 ≤n 字；单句本身就超 n 字的按 n 硬切。"""
    out = []
    for sent in split_sentences(s):
        while len(sent) > n:
            out.append(sent[:n])
            sent = sent[n:]
        if not sent:
            continue
        if out and len(out[-1]) + len(sent) <= n:
            out[-1] += sent
        else:
            out.append(sent)
    return out


def lines_of(text, n=MAX_CHARS):
    """空行段 → 文案行（每段再按 n 字切）。"""
    out = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        out.extend(cut(para, n))
    return out


# ---------- 标签 ----------
def tag_text(lang, emotion, mood, tone):
    """`用<语言>、<情感>、<情绪>的语气<说>`：语气动词默认「说」。

    情感/情绪没给就取默认「自然、口语」；要情绪就显式给，顶掉默认值。
    lang 不给就不出现语言词（默认普通话），明确要方言才把「四川话」这类词填进来。
    """
    s = "".join([x for x in (lang, emotion or "自然", mood or "口语") if x])
    return ("用" + s + "的语气" + (tone or "说")) if s else ""


NO_TAG = False           # True = 本次不贴标签（引擎白名单判定后置位）


def norm_engine(e):
    return re.sub(r"[^a-z0-9]", "", str(e or "").lower())


def need_tag(engine=""):
    """引擎白名单：只有 CosyVoice 走「用…的语气说<|endofprompt|>。」格式。

    千问 Qwen3-TTS 直接吃正常写稿文案——给它套标签会多念出指令字、出现吞字漏读。
    """
    e = norm_engine(engine) or norm_engine(_cfg().get("default_engine"))
    return e == "cosyvoice"


def tag_of(a, emotion="", mood="", tone=""):
    """整段标签：--tag > 语言/情感/情绪/语气拼出来的 > 全局 tts.default_instruct_text。
    结尾段单独取时用 tag_of(a, a.end_emotion, a.end_mood, a.end_tone)，没给就回落本段的。
    """
    if NO_TAG:
        return ""
    if a.tag:
        return a.tag
    slot = tag_text(a.lang, emotion or a.emotion, mood or a.mood, tone or a.tone)
    return slot or _cfg().get("default_instruct_text") or ""


def render(tag, content):
    return "%s<|endofprompt|>。%s" % (tag, content) if tag else content




# ---------- 模式 A：贴标签 ----------
def paras_of(text):
    """空行分段：空行是硬边界，段与段之间各自独立保留。"""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def assign_tags(sents, tag_lines, start=0):
    """标签按句子顺序逐句套用：第 i 句用第 i 条，标签不够时沿用最后一条。"""
    return [(tag_lines[min(start + i, len(tag_lines) - 1)], s) for i, s in enumerate(sents)]


def merge_same(pairs, n=MAX_CHARS):
    """相邻同标签的句子归成一块；一块装不下 n 字就接着开新块（标签不变）。"""
    out = []
    for tag, content in pairs:
        if out and out[-1][0] == tag and len(out[-1][1]) + len(content) <= n:
            out[-1] = (tag, out[-1][1] + content)
            continue
        out.append((tag, content))
    return out


def cut_pairs(pairs, n=MAX_CHARS):
    """块还超 n 字（多半是单句就超）就按句子继续切，标签原样带着。"""
    out = []
    for tag, content in pairs:
        out.extend((tag, ln) for ln in (cut(content, n) if len(content) > n else [content]))
    return out


def mode_label(text, a, tag_lines=None):
    """现成文案 → 只加标签，不动内容一个字。返回 [(标签, 内容)]。

    给了 tag_lines：按句逐句套标签 → 相邻同标签并成一块 → 块再按 80 字切。
    标签文件因此可以按「语气区块」写，一句话一个语气也认。
    """
    limit = a.max_chars or MAX_CHARS
    paras = paras_of(polish(text, polite_second=False))   # A 模式不改写人称
    if not tag_lines:
        return [(tag_of(a), ln) for para in paras for ln in cut(para, limit)]

    out, cursor = [], 0
    for para in paras:
        sents = split_sentences(para)
        out.extend(cut_pairs(merge_same(assign_tags(sents, tag_lines, cursor), limit), limit))
        cursor += len(sents)
    return out


# ---------- 模式 B：提炼生成 ----------
def pick_core(sents, need):
    """从原文按分数挑核心句，返回顺序严格照原文（口播稿按原文顺序念）；首句让给开头提问，不重抽。"""
    scored = []
    for i, s in enumerate(sents):
        score = sum(2 for k in KEYWORDS if k in s)
        if i > 0:
            score += 1
        if i == 0:
            score -= 5
        scored.append((score, i, s))
    picked = sorted(scored, key=lambda x: (-x[0], x[1]))[:need]
    return [s for _, _, s in sorted(picked, key=lambda x: x[1])]


def topic_of(sents, maxlen=28):
    """开头提问用：取最靠前的、最长的那句（=原文里的场景/功能/方法名）。"""
    for s in sents:
        s = re.sub(r"^[^\u4e00-\u9fa5A-Za-z0-9]*", "", s)
        s = s.strip("。！？，、；：,.!?")
        if len(s) >= 6:
            return s[:maxlen]
    return ""


def mode_generate(text, a):
    """忠实原文抽要点成篇：开头口语提问 → 中间核心句 → 结尾固定句。返回 [(标签, 内容)]。"""
    sents = split_sentences(polish(text, polite_second=a.polite_second))
    if not sents:
        return []
    need = a.lines or 6
    topic = topic_of(sents)
    opener = "%s%s？" % (OPENER, topic) if topic else "今天这条内容，值得一听。"

    limit = a.max_chars or MAX_CHARS
    body = []
    for s in pick_core(sents, max(need - 2, 2)):
        body.extend(cut(s, limit))

    tag = tag_of(a)
    end_tag = tag_of(a, a.end_emotion, a.end_mood, a.end_tone)

    pairs = [(tag, opener)]
    pairs += [(tag, ln) for ln in body]
    pairs.append((end_tag, ENDING))
    return pairs


def repair(pairs, bad, limit, default_tag):
    """按质检问题单自动修一轮，返回修好的 pairs：

    正文超 limit 字 → 再按句子切；内容为空 → 丢段；缺标签 → 补默认标签。
    """
    hit = {i - 1 for i, _, _ in bad}          # 质检段号从 1 起
    out = []
    for i, (tag, content) in enumerate(pairs):
        if i not in hit:
            out.append((tag, content))
            continue
        if not content:
            continue
        tag = tag or default_tag
        if len(content) <= limit:
            out.append((tag, content))
            continue
        for ln in cut(content, limit):
            out.append((tag, ln))
    return out


# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser(
        description="口播文案生成：A 贴标签（默认） / B 按原文提炼",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="用法：\n"
               "  # A：现成文案加标签，不动一个字\n"
               "  python script_gen.py --project 一个人的队伍 --text-file 文案.txt "
               "--lang 普通话 --emotion 亲切 --mood 自然 --tone 说\n"
               "  # A：逐句换语气（标签文件一行一个，按句顺序套用，相邻同标签自动并成一块）\n"
               "  python script_gen.py --project 一个人的队伍 --text-file 文案.txt --tag-file 标签.txt\n"
               "  # B：忠实原文提炼成篇\n"
               "  python script_gen.py --project 一个人的队伍 --source-file 原文.txt --lines 6\n")
    ap.add_argument("--project", help="项目名：文案落 <项目根>/<项目名>/doc/")
    ap.add_argument("--text-file", help="A 模式：现成口播文案（只加标签，不改写）")
    ap.add_argument("--source-file", help="B 模式：原文素材（忠实提炼）")
    ap.add_argument("--tag-file", help="A 模式：标签文件（一行一个标签，按句子顺序套用；"
                                       "相邻同标签的句子自动并成一块，所以按「语气区块」写就行）")
    ap.add_argument("--polite-second", action="store_true",
                    help="把第二人称「你」转成敬语「您」（A 模式默认不转——那属于改写）")
    ap.add_argument("--tag", help="整篇用一个标签（如 '用亲切、温暖、笑眯眯的语气说'）")
    ap.add_argument("--lang", default="", help="语言，如 普通话 / 四川话")
    ap.add_argument("--emotion", default="", help="情感，如 亲切 / 无奈")
    ap.add_argument("--mood", default="", help="情绪，如 笑眯眯 / 拖着长音")
    ap.add_argument("--tone", default="", help="语气动作：说 / 喊 / 笑着说")
    ap.add_argument("--end-emotion", dest="end_emotion", default="", help="结尾段情感（默认同 emotion）")
    ap.add_argument("--end-mood", dest="end_mood", default="", help="结尾段情绪")
    ap.add_argument("--end-tone", dest="end_tone", default="", help="结尾段语气动作")
    ap.add_argument("--end-tag", dest="end_tag", default="", help="结尾段整条标签")
    ap.add_argument("--lines", type=int, default=0, help="B 模式：口播总条数（默认 6）")
    ap.add_argument("--max-chars", type=int, default=0, help="每段字数上限（默认 80）")
    ap.add_argument("--engine", default="", help="TTS 引擎（默认取全局 tts.default_engine）："
                                                 "cosyvoice 走标签格式，qwen3tts 输出纯正文")
    ap.add_argument("--out", help="不建项目目录时的输出 txt 路径")
    args = ap.parse_args()

    global NO_TAG
    eng = norm_engine(args.engine) or norm_engine(_cfg().get("default_engine")) or "cosyvoice"
    if not need_tag(args.engine):
        NO_TAG = True
        print("[script] 引擎 %s：不加语气标签，直接输出正常文案" % eng, flush=True)
    else:
        print("[script] 引擎 %s：按 CosyVoice 格式加语气标签" % eng, flush=True)

    if not args.text_file and not args.source_file:
        raise SystemExit("! 给素材：--text-file（现成文案，只加标签）或 --source-file（原文，提炼成篇）")
    src = args.text_file or args.source_file
    with open(src, encoding="utf-8") as f:
        raw = f.read()
    if not raw.strip():
        raise SystemExit("! %s 是空的" % src)

    limit = args.max_chars or MAX_CHARS
    tags = None
    if args.tag_file:
        with open(args.tag_file, encoding="utf-8") as f:
            tags = [fxc.parse_line(l)[0] or l.strip() for l in f if l.strip()]

    pairs = mode_label(raw, args, tags) if args.text_file else mode_generate(raw, args)
    if not pairs:
        raise SystemExit("! 素材里有能念的内容都没有")

    # 质检 → 不过就自动修一轮再复检（最多 3 轮）；3 轮后仍不过才报错，带着问题往下走之前先拦住
    # 分隔符是 CosyVoice 标签格式的部件：千问模式不查分隔符，只查正文长度
    req_tag = not NO_TAG
    for _ in range(3):
        out_lines = [render(t, c) for t, c in pairs]
        bad = fxc.check_script(out_lines, limit, require_tag=req_tag)
        if not bad:
            break
        print("[script] 质检发现问题，自动修一轮：\n" + fxc.format_bad(bad), flush=True)
        pairs = repair(pairs, bad, limit, tag_of(args))
        if not pairs:
            raise SystemExit("! 素材里没有能念的内容")
    else:
        out_lines = [render(t, c) for t, c in pairs]
        bad = fxc.check_script(out_lines, limit, require_tag=req_tag)
        raise SystemExit("! 自动修了 3 轮仍不过，请手动改文案：\n" + fxc.format_bad(bad))

    script = "\n\n".join(out_lines)
    items = [{"index": i + 1, "instruct_text": t, "text": c} for i, (t, c) in enumerate(pairs)]

    if args.out:
        path = os.path.abspath(args.out)
    else:
        sys.path.insert(0, os.path.join(_SKILLS, "fxai-workflow"))
        import output_layout as ol
        path = os.path.join(ol.layout(args.project)["doc"], "口播文案_标签.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(script + "\n")

    jp = os.path.join(os.path.dirname(path), "口播文案_标签_段落.json")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump({"project": args.project or "", "count": len(items), "items": items},
                  f, ensure_ascii=False, indent=2)

    print("[script] 质检通过：%d 段，格式合规、正文均 ≤%d 字" % (len(out_lines), limit))
    print("[script] 文案：%s" % path)
    print("[script] 段落：%s" % jp)
    for l in out_lines[:3]:
        print("  " + l[:66])
    return 0


if __name__ == "__main__":
    sys.exit(main())
