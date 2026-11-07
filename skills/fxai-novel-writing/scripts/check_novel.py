#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""小说产出验收：字数、章节标签、文件命名、章号连续、章名一致。

用法：
    python check_novel.py <书名目录> [--type auto|short|long]
                          [--min-hanzi 8000] [--min-chapter-hanzi 2000] [--json]
输出：每项 [OK]/[WARN]/[ERROR] + 说明。
退出码：有 ERROR 为 1，其余为 0。

章内首行标题与短篇章节标题一律不带 `#` 等标记，形如 `第001章 章名`；
脚本兼容旧的 `# 第001章 章名` 写法，但产出应写成无标记体例。
"""

import argparse
import json
import os
import re
import sys

HAN = re.compile(r"[\u4e00-\u9fff]")
CN_NUM = "零一二三四五六七八九十百千"
CH_MD = re.compile(r"^(?:#{1,6}\s*)?第([" + CN_NUM + r"]+|[0-9]+)[章回节]\s*([^\n]*)", re.M)
CH_HTML = re.compile(r"<h[1-3][^>]*>\s*第([" + CN_NUM + r"]+|[0-9]+)[章回节]\s*([^<]*)", re.I)
CH_FILE = re.compile(r"^第(\d{3})章\s+(.+)\.md$")
CH_HEAD = re.compile(r"^第(\d{3})章\s+(.+?)\s*$", re.M)
TOC_ROW = re.compile(r"^\|\s*第(\d{3})章\s*\|\s*([^|]+?)\s*\|", re.M)
OUTLINE_ROW = re.compile(r"^#{2,4}\s*第(\d{3})章\s+(.+?)\s*$", re.M)

LONG_REQUIRED = ("00-目录.md", "01-创作思路.md", "02-人物画像.md", "03-章节梗概.md")
SHORT_BODY = ("正文.md", "正文.html")
SHORT_CHAPTER_RANGE = (6, 8)


def read(path):
    return open(path, encoding="utf-8", errors="replace").read()


def hanzi(path):
    return len(HAN.findall(read(path)))


def result(ok, ok_msg, err_msg):
    return ("OK", ok_msg) if ok else ("ERROR", err_msg)


def chapters_of(path):
    pattern = CH_HTML if path.lower().endswith(".html") else CH_MD
    return pattern.findall(read(path))


def is_cn_num(seq):
    return bool(re.match(r"^[" + CN_NUM + r"]+$", seq))


def detect(root):
    if os.path.isdir(os.path.join(root, "正文")):
        return "long"
    for name in SHORT_BODY:
        if os.path.exists(os.path.join(root, name)):
            return "short"
    return "unknown"


def check_short(root, min_hanzi):
    issues = []
    body = next((os.path.join(root, n) for n in SHORT_BODY
                 if os.path.exists(os.path.join(root, n))), None)
    if body is None:
        return [("ERROR", "缺正文文件（正文.md / 正文.html）")]

    n = hanzi(body)
    issues.append(result(n >= min_hanzi, "正文字数 %d" % n,
                         "正文字数 %d < %d" % (n, min_hanzi)))

    chs = chapters_of(body)
    issues.append(result(len(chs) >= 2, "章节数 %d" % len(chs),
                         "章节标签 %d 个（需要 ≥2）" % len(chs)))
    low, high = SHORT_CHAPTER_RANGE
    if not low <= len(chs) <= high:
        issues.append(("WARN", "章节数 %d，短篇建议 %d–%d 章" % (len(chs), low, high)))

    kinds = set("cn" if is_cn_num(seq) else "ar" for seq, _ in chs)
    if len(kinds) > 1:
        issues.append(("ERROR", "章序号混用中文数字与阿拉伯数字，全篇二选一"))

    bad_name = [t.strip() for _, t in chs if not 2 <= len(t.strip()) <= 8]
    if bad_name:
        issues.append(("WARN", "章名超出 2–8 字：%s" % "、".join(bad_name[:5])))

    for name in ("01-作品档案.md", "02-人物画像.md"):
        issues.append(result(os.path.exists(os.path.join(root, name)),
                             "%s 就位" % name, "缺 %s" % name))
    return issues


def scan_long_files(root):
    groups = {}
    body_dir = os.path.join(root, "正文")
    for base, _, names in os.walk(body_dir):
        groups[base] = sorted(n for n in names if n.endswith(".md"))
    return groups


def parse_chapter_files(groups, issues):
    parsed = {}
    bad_name = []
    for base, names in groups.items():
        for name in names:
            m = CH_FILE.match(name)
            if not m:
                bad_name.append(name)
                continue
            parsed.setdefault(base, []).append((int(m.group(1)), m.group(2),
                                                os.path.join(base, name)))
    if bad_name:
        issues.append(("ERROR", "章文件命名不符「第NNN章 章名.md」：%s"
                       % "、".join(bad_name[:5])))
    return parsed


def check_sequence(parsed, issues):
    for base, items in parsed.items():
        nums = sorted(num for num, _, _ in items)
        missing = [i for i in range(1, nums[-1] + 1) if i not in set(nums)]
        if missing:
            issues.append(("ERROR", "%s 章号不连续，缺：%s"
                           % (os.path.basename(base) or "正文",
                              "、".join("第%03d章" % i for i in missing[:5]))))
        dup = sorted(set(i for i in nums if nums.count(i) > 1))
        if dup:
            issues.append(("ERROR", "%s 章号重复：%s"
                           % (os.path.basename(base) or "正文",
                              "、".join("第%03d章" % i for i in dup[:5]))))


def check_each_chapter(parsed, min_chapter_hanzi, issues):
    short_chapters = []
    head_miss = []
    head_diff = []
    for base, items in parsed.items():
        for num, name, path in sorted(items):
            text = read(path)
            m = CH_HEAD.search(text)
            if not m:
                head_miss.append(os.path.basename(path))
            elif int(m.group(1)) != num or m.group(2) != name:
                head_diff.append(os.path.basename(path))
            h = len(HAN.findall(text))
            if h < min_chapter_hanzi:
                short_chapters.append("%s（%d 字）" % (os.path.basename(path), h))
    issues.append(result(not head_miss, "章内首行标题齐备",
                         "缺章内首行标题「第NNN章 章名」：%s" % "、".join(head_miss[:5])))
    issues.append(result(not head_diff, "章内标题与文件名一致",
                         "章内标题与文件名不一致：%s" % "、".join(head_diff[:5])))
    issues.append(result(not short_chapters, "单章字数均 ≥ %d" % min_chapter_hanzi,
                         "单章字数 < %d：%s" % (min_chapter_hanzi,
                                                "、".join(short_chapters[:5]))))


def parse_map(path, pattern):
    return {int(a): b for a, b in pattern.findall(read(path))} if os.path.exists(path) else None


def check_naming_against(parsed, root, issues, filename, pattern):
    table = parse_map(os.path.join(root, filename), pattern)
    if table is None:
        return
    names = {num: name for items in parsed.values() for num, name, _ in items}
    if len(parsed) > 1:
        issues.append(("WARN", "多卷结构，跳过与 %s 的章名一致性校验" % filename))
        return
    missing = [n for n in sorted(names) if n not in table]
    issues.append(result(not missing, "%s 章号齐备" % filename,
                         "%s 缺章号：%s" % (filename,
                                            "、".join("第%03d章" % i for i in missing[:5]))))
    diff = ["第%03d章 正文「%s」≠ %s「%s」" % (n, names[n], filename, table[n])
            for n in sorted(names) if n in table and table[n] != names[n]]
    issues.append(result(not diff, "章名与 %s 一致" % filename,
                         "章名与 %s 不一致：%s" % (filename, "；".join(diff[:3]))))


def check_long(root, min_chapter_hanzi):
    issues = []
    for name in LONG_REQUIRED:
        issues.append(result(os.path.exists(os.path.join(root, name)),
                             "%s 就位" % name, "缺 %s" % name))

    groups = scan_long_files(root)
    total = sum(len(v) for v in groups.values())
    if not total:
        issues.append(("ERROR", "正文/ 下无章节文件"))
        return issues
    issues.append(("OK", "章节文件 %d 个" % total))

    parsed = parse_chapter_files(groups, issues)
    if not parsed:
        return issues
    check_sequence(parsed, issues)
    check_each_chapter(parsed, min_chapter_hanzi, issues)
    check_naming_against(parsed, root, issues, "00-目录.md", TOC_ROW)
    check_naming_against(parsed, root, issues, "03-章节梗概.md", OUTLINE_ROW)
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--type", default="auto", choices=["auto", "short", "long"])
    ap.add_argument("--min-hanzi", type=int, default=8000)
    ap.add_argument("--min-chapter-hanzi", type=int, default=2000)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = os.path.abspath(args.path)
    kind = args.type if args.type != "auto" else detect(root)
    if kind == "short":
        issues = check_short(root, args.min_hanzi)
    elif kind == "long":
        issues = check_long(root, args.min_chapter_hanzi)
    else:
        issues = [("ERROR", "无法识别体裁：缺 正文.md / 正文.html 或 正文/ 目录")]

    if args.json:
        print(json.dumps({"root": root, "type": kind,
                          "issues": [{"level": l, "msg": m} for l, m in issues]},
                         ensure_ascii=False, indent=2))
    else:
        print("验收：%s（%s）" % (root, kind))
        for level, msg in issues:
            print("  [%s] %s" % (level, msg))
    return 1 if any(l == "ERROR" for l, _ in issues) else 0


if __name__ == "__main__":
    sys.exit(main())
