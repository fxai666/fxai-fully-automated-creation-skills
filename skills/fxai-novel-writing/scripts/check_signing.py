#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""签约验收内容信号检查：段落长度、重复段落、相邻重复句、单章字数、短章占比、套话词频、对话占比、标点混用、标记与空格残留、章名重复。

用法：
    python check_signing.py <书名目录> [--json]
输出：每项 [OK]/[WARN]/[ERROR] + 说明。
退出码：有 ERROR 为 1，其余为 0。
"""

import argparse
import json
import os
import re
import sys
from collections import Counter

HAN = re.compile(r"[\u4e00-\u9fff]")
CH_FILE = re.compile(r"^第(\d{3})章\s+(.+)\.md$")
CH_MD = re.compile(r"^(?:#{1,6}\s*)?第([零一二三四五六七八九十百千]+|[0-9]+)[章回节]\s*([^\n]*)", re.M)
SENT_SPLIT = re.compile(r"[。！？!?…]+")
QUOTE = re.compile(r"[\u201c\u300c]([^\u201d\u300d]{1,200})[\u201d\u300d]")
SPEECH = re.compile(r"^[^\n]{0,10}?(?:说|道|问|答|喊|骂|叫|嘟囔|嘀咕)[：:]\s*([^\n]+)$", re.M)
INLINE = re.compile(r"[\"\u201c\u300c][^\"\u201d\u300d]{2,}[\"\u201d\u300d]")
HALF_PUNCT = re.compile(r"[\u4e00-\u9fff][,;?!]|[,;?!][\u4e00-\u9fff]")
MD_MARK = re.compile(r"^[#>\-\*\s`]+")
SEP_LINE = re.compile(r"^[ \t]*(?:-{3,}|(?:\uFF0A[ \t\u3000]*){2,})[ \t]*$")
HEAD_MARK = re.compile(r"(?m)^#{1,6}")
TITLE_LINE = re.compile(r"^第[零一二三四五六七八九十百千0-9]+[章回节][ \t\u3000]")
SPACE_RUN = re.compile(r"[ \t\u3000]+")

LONG_PARA = 300
LONG_PARA_WARN = 0.05
LONG_PARA_ERROR = 0.15
DENSE_WARN = 20
DENSE_ERROR = 12
DUP_PARA_MIN = 20
CROSS_DUP_MIN = 50
REPEAT_SENT_WARN = 0.08
REPEAT_SENT_ERROR = 0.15
SHORT_CHAPTER_RATIO = 0.30
CHAPTER_MIN = 1000      # 番茄《章节字数要求》：不足 1000 字无法发布
CHAPTER_MAX = 50000     # 番茄《章节字数要求》：单章上限 50000 字
CLICHE_PER_10K = 50
DIALOG_LOW = 0.10
DIALOG_HIGH = 0.70
PUNCT_PER_1K = 3

CLICHE = (
    "只见", "不由得", "心中一凛", "嘴角微扬", "眼中闪过一丝", "深吸一口气",
    "话音刚落", "下一秒", "与此同时", "微微一笑", "眉头微皱", "淡淡说道",
    "冷冷说道", "心中暗想", "忍不住", "说时迟那时快", "一如既往", "不由自主",
)


def read(path):
    return open(path, encoding="utf-8", errors="replace").read()


def hanzi(text):
    return len(HAN.findall(text))


def level_of(ratio, warn, error):
    if ratio > error:
        return "ERROR"
    return "WARN" if ratio > warn else "OK"


def collect(root):
    """返回 [(章名, 正文文本)]，长篇按文件，短篇按章节标题切。"""
    body_dir = os.path.join(root, "正文")
    if os.path.isdir(body_dir):
        items = []
        for name in sorted(os.listdir(body_dir)):
            if not name.endswith(".md"):
                continue
            m = CH_FILE.match(name)
            label = m.group(2) if m else os.path.splitext(name)[0]
            items.append((label, read(os.path.join(body_dir, name))))
        return items
    loose = sorted(n for n in os.listdir(root)
                   if n.endswith(".md") and CH_FILE.match(n))
    if loose:
        return [(CH_FILE.match(n).group(2), read(os.path.join(root, n))) for n in loose]
    for name in ("正文.md", "正文.html"):
        path = os.path.join(root, name)
        if os.path.exists(path):
            text = read(path)
            marks = list(CH_MD.finditer(text))
            if not marks:
                return [("全文", text)]
            items = []
            head = text[:marks[0].start()].strip()
            if head:
                items.append(("楔子", head))
            for i, m in enumerate(marks):
                end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
                items.append((m.group(2).strip() or "第%d章" % (i + 1),
                              text[m.end():end]))
            return items
    return []


def paragraphs(text):
    """正文段落。场景分隔行（`---` 与 `＊　＊　＊`）不计入段落。"""
    out = []
    for line in text.splitlines():
        line = MD_MARK.sub("", line).strip()
        if line and not SEP_LINE.match(line):
            out.append(line)
    return out


def narrative_paragraphs(text):
    return [p for p in paragraphs(text) if not is_dialog(p)]


def is_dialog(line):
    """对话行：网文排版中对话本就独立成短段，密度与重复统计时排除。"""
    return bool(SPEECH.match(line) or INLINE.search(line))


def grams(text, n=4):
    return {text[i:i + n] for i in range(max(0, len(text) - n + 1))}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def check_paragraphs(items, issues):
    paras = [p for _, text in items for p in paragraphs(text)]
    if not paras:
        issues.append(("ERROR", "正文无有效段落"))
        return
    long_ones = [p for p in paras if hanzi(p) > LONG_PARA]
    ratio = len(long_ones) / len(paras)
    level = level_of(ratio, LONG_PARA_WARN, LONG_PARA_ERROR)
    msg = "超 %d 字段落 %d/%d（%.1f%%）" % (LONG_PARA, len(long_ones), len(paras), ratio * 100)
    if level == "OK":
        issues.append(("OK", "段落长度：%s" % msg))
    else:
        sample = "、".join(p[:12] + "…" for p in long_ones[:2])
        issues.append((level, "段落长度：%s，前几处：%s" % (msg, sample)))

    inner = []
    for label, text in items:
        counter = Counter(re.sub(r"\s", "", p) for p in paragraphs(text))
        inner += [(label, t, c) for t, c in counter.items()
                  if c > 1 and hanzi(t) >= DUP_PARA_MIN]
    if inner:
        inner.sort(key=lambda x: -x[2])
        issues.append(("ERROR", "章内重复段落 %d 组，最重：%s「%s」重复 %d 次"
                       % (len(inner), inner[0][0], inner[0][1][:18] + "…", inner[0][2])))
    else:
        issues.append(("OK", "章内无重复段落"))

    cross = Counter()
    for _, text in items:
        seen = set(re.sub(r"\s", "", p) for p in narrative_paragraphs(text))
        for p in seen:
            if hanzi(p) >= CROSS_DUP_MIN:
                cross[p] += 1
    dups = [(t, c) for t, c in cross.items() if c > 1]
    if dups:
        dups.sort(key=lambda x: -x[1])
        issues.append(("ERROR", "跨章大段雷同 %d 组，最高重复 %d 次：%s"
                       % (len(dups), dups[0][1], dups[0][0][:20] + "…")))
    else:
        issues.append(("OK", "跨章无大段雷同"))


def check_density(items, issues):
    """单句成段密度：平均段落汉字数过低即碎尸式排版，对应「格式混乱」。
    先判全书均值，再逐章判——均值会被长章拉高，掩盖个别碎片章。"""
    rows = []
    for label, text in items:
        paras = narrative_paragraphs(text)
        if len(paras) < 5:
            continue
        rows.append((label, hanzi("\n".join(paras)) / len(paras), len(paras)))
    if not rows:
        issues.append(("OK", "段落密度：叙述段不足，跳过"))
        return
    total_han = sum(r[1] * r[2] for r in rows)
    total_par = sum(r[2] for r in rows)
    avg = total_han / total_par
    rows.sort(key=lambda r: r[1])
    thin = [r for r in rows if r[1] < DENSE_ERROR]
    if avg >= DENSE_WARN:
        if thin:
            sample = "、".join("%s(%.1f 字/段)" % (l, d) for l, d, _ in thin[:5])
            issues.append(("ERROR", "叙述段密度：全书均值 %.1f 字达标，但 %d 章低于 %d 字/段：%s"
                           % (avg, len(thin), DENSE_ERROR, sample)))
        else:
            issues.append(("OK", "叙述段密度：平均每段 %.1f 字，无碎片章（对话段不计）" % avg))
        return
    sample = "、".join("%s(%.1f 字/段)" % (l, d) for l, d, _ in rows[:3])
    level = "ERROR" if avg < DENSE_ERROR else "WARN"
    issues.append((level, "叙述段密度：平均每段 %.1f 字（阈值 %d），低于 %d 字/段的章 %d/%d，碎片章：%s"
                   % (avg, DENSE_WARN, DENSE_ERROR, len(thin), len(rows), sample)))


def check_repeat_sentence(items, issues):
    sentences = []
    for _, text in items:
        for seg in SENT_SPLIT.split(text):
            seg = MD_MARK.sub("", seg).strip()
            if hanzi(seg) >= 8:
                sentences.append(seg)
    if len(sentences) < 20:
        issues.append(("OK", "相邻重复句：句数不足，跳过"))
        return
    hit = sum(1 for i in range(len(sentences) - 1)
              if jaccard(grams(sentences[i]), grams(sentences[i + 1])) > 0.6)
    ratio = hit / (len(sentences) - 1)
    level = level_of(ratio, REPEAT_SENT_WARN, REPEAT_SENT_ERROR)
    msg = "相邻重复句 %d/%d（%.1f%%）" % (hit, len(sentences) - 1, ratio * 100)
    issues.append((level if level != "OK" else "OK",
                   "相邻重复句：%s" % msg))


def check_chapter_balance(items, issues):
    sizes = [(label, hanzi(text)) for label, text in items]
    if len(sizes) < 3:
        issues.append(("OK", "章节均衡：章数不足，跳过"))
        return
    avg = sum(s for _, s in sizes) / len(sizes)
    short = [(l, s) for l, s in sizes if s < avg * 0.5]
    ratio = len(short) / len(sizes)
    level = "WARN" if ratio > SHORT_CHAPTER_RATIO else "OK"
    msg = "低于均章 50%% 的章 %d/%d（%.1f%%），均章 %d 字" % (
        len(short), len(sizes), ratio * 100, avg)
    if level == "OK":
        issues.append(("OK", "章节均衡：%s" % msg))
    else:
        sample = "、".join("%s(%d)" % (l, s) for l, s in short[:3])
        issues.append((level, "章节失衡：%s，前几处：%s" % (msg, sample)))


def check_chapter_length(items, issues):
    """平台硬门槛：每章 1000–50000 汉字（番茄《章节字数要求》）。"""
    sizes = [(label, hanzi(text)) for label, text in items]
    too_short = [(l, s) for l, s in sizes if s < CHAPTER_MIN]
    too_long = [(l, s) for l, s in sizes if s > CHAPTER_MAX]
    if too_short:
        sample = "、".join("%s(%d)" % (l, s) for l, s in too_short[:5])
        issues.append(("ERROR", "单章字数低于 %d 字（番茄无法发布）%d 章：%s"
                       % (CHAPTER_MIN, len(too_short), sample)))
    if too_long:
        sample = "、".join("%s(%d)" % (l, s) for l, s in too_long[:5])
        issues.append(("ERROR", "单章字数高于 %d 字（超出发布上限）%d 章：%s"
                       % (CHAPTER_MAX, len(too_long), sample)))
    if not too_short and not too_long:
        issues.append(("OK", "单章字数：%d 章均在 %d–%d 字区间（最短 %d，最长 %d）"
                       % (len(sizes), CHAPTER_MIN, CHAPTER_MAX,
                          min(s for _, s in sizes), max(s for _, s in sizes))))


def check_cliche(items, issues):
    total = sum(hanzi(text) for _, text in items)
    if total < 1000:
        issues.append(("OK", "套话词频：字数不足，跳过"))
        return
    joined = "\n".join(text for _, text in items)
    hits = sum(joined.count(w) for w in CLICHE)
    per10k = hits / (total / 10000)
    level = "WARN" if per10k > CLICHE_PER_10K else "OK"
    msg = "套话 %d 次，每万字 %.1f 次（阈值 %d）" % (hits, per10k, CLICHE_PER_10K)
    issues.append((level, "套话词频：%s" % msg))


def check_dialog(items, issues):
    total = sum(hanzi(text) for _, text in items)
    if total < 1000:
        issues.append(("OK", "对话占比：字数不足，跳过"))
        return
    quoted = sum(hanzi(m.group(1)) for _, text in items for m in QUOTE.finditer(text))
    spoken = sum(hanzi(m.group(1)) for _, text in items for m in SPEECH.finditer(text))
    ratio = (quoted + spoken) / total
    detail = "引号 %.1f%% ＋ 冒号式 %.1f%%" % (quoted / total * 100, spoken / total * 100)
    if ratio < DIALOG_LOW:
        issues.append(("WARN", "对话占比 %.1f%%（低于 %.0f%%），叙述占比偏高｜%s"
                       % (ratio * 100, DIALOG_LOW * 100, detail)))
    elif ratio > DIALOG_HIGH:
        issues.append(("WARN", "对话占比 %.1f%%（高于 %.0f%%），叙述占比偏低｜%s"
                       % (ratio * 100, DIALOG_HIGH * 100, detail)))
    else:
        issues.append(("OK", "对话占比 %.1f%%｜%s" % (ratio * 100, detail)))


def check_punct(items, issues):
    total = sum(hanzi(text) for _, text in items)
    if total < 1000:
        issues.append(("OK", "标点：字数不足，跳过"))
        return
    hits = sum(len(HALF_PUNCT.findall(text)) for _, text in items)
    per1k = hits / (total / 1000)
    level = "WARN" if per1k > PUNCT_PER_1K else "OK"
    issues.append((level, "半角标点 %d 处，每千字 %.1f 处（阈值 %d）"
                   % (hits, per1k, PUNCT_PER_1K)))


def check_chapter_names(items, issues):
    names = [label for label, _ in items]
    dups = [n for n, c in Counter(names).items() if c > 1]
    if dups:
        issues.append(("ERROR", "章名重复：%s" % "、".join(dups[:5])))
    else:
        issues.append(("OK", "章名无重复（%d 章）" % len(names)))


def check_markdown(items, issues):
    """平台不解析 Markdown，标题标记与强调标记会原样显示成符号。"""
    hits = Counter()
    for _, text in items:
        for name, pat in (("#", HEAD_MARK), ("**", r"\*\*"), ("`", r"`")):
            n = len(re.findall(pat, text))
            if n:
                hits[name] += n
    if not hits:
        issues.append(("OK", "无 Markdown 标记残留"))
        return
    detail = "、".join("%s %d 处" % (k, v) for k, v in hits.most_common())
    issues.append(("ERROR", "Markdown 标记残留：%s，投稿前用 normalize_punct.py --heading strip --md strip 清掉"
                   % detail))


def space_hits(text):
    """中英混排间距：空格一侧是汉字、另一侧不是。标题行不计。返回 (处数, 前几处)。"""
    n = 0
    samples = []
    for line in text.splitlines():
        if TITLE_LINE.match(line):
            continue
        for m in SPACE_RUN.finditer(line):
            before = line[m.start() - 1] if m.start() > 0 else ""
            after = line[m.end()] if m.end() < len(line) else ""
            if bool(HAN.match(before)) != bool(HAN.match(after)):
                n += 1
                if len(samples) < 2:
                    samples.append(line.strip()[:24])
    return n, samples


def check_space(items, issues):
    """正文内中英混排间距会原样显示成多余空白。标题行不算。"""
    hits = 0
    samples = []
    for label, text in items:
        n, s = space_hits(text)
        hits += n
        samples += ["%s「%s」" % (label, x) for x in s]
    if not hits:
        issues.append(("OK", "正文无中英混排间距"))
        return
    issues.append(("ERROR", "正文中英混排间距 %d 处（前几处：%s），投稿前用 normalize_punct.py --space strip 清掉"
                   % (hits, "；".join(samples[:3]))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = os.path.abspath(args.path)
    items = collect(root)
    issues = []
    if not items:
        issues.append(("ERROR", "未找到正文：缺 正文/ 目录或 正文.md / 正文.html"))
    else:
        total = sum(hanzi(text) for _, text in items)
        issues.append(("OK", "正文 %d 章，汉字 %d" % (len(items), total)))
        check_paragraphs(items, issues)
        check_density(items, issues)
        check_repeat_sentence(items, issues)
        check_chapter_balance(items, issues)
        check_chapter_length(items, issues)
        check_cliche(items, issues)
        check_dialog(items, issues)
        check_punct(items, issues)
        check_markdown(items, issues)
        check_space(items, issues)
        check_chapter_names(items, issues)

    if args.json:
        print(json.dumps({"root": root, "chapters": len(items),
                          "issues": [{"level": l, "msg": m} for l, m in issues]},
                         ensure_ascii=False, indent=2))
    else:
        print("签约验收：%s" % root)
        for level, msg in issues:
            print("  [%s] %s" % (level, msg))
        bad = sum(1 for l, _ in issues if l == "ERROR")
        warn = sum(1 for l, _ in issues if l == "WARN")
        print("汇总：ERROR %d / WARN %d" % (bad, warn))
        print("打分区间由大模型按 references/08-签约验收.md 完成。")
    return 1 if any(l == "ERROR" for l, _ in issues) else 0


if __name__ == "__main__":
    sys.exit(main())
