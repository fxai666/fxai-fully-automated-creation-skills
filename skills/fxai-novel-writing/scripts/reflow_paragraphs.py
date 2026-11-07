#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""段落重排：把单句成段的正文按目标段长合并为自然段，对话行与强调块保持独立成段。

用法：
    python reflow_paragraphs.py <书名目录> [--out 目录] [--target 60] [--scene keep|soft] [--merge-dialog N] [--apply]
默认输出到 <书名目录>/重排预览/，原文不动；加 --apply 才覆盖 正文/ 与 正文.md。

--scene keep（默认）：`---` 视为场景分隔，作为硬边界保留。
--scene soft：`---` 视为普通换行，句子跨 `---` 合并，输出无 `---`。
   用于「一句一个 `---`」的章（章内 `---` 数 >50），此时 `---` 是分段符而非场景符。
--scene auto：逐章判定——章内分隔符平均间隔（汉字数）< --scene-gap（默认 100）走 soft，否则走 keep。
   间隔小 = 每个小段落之间都插一条，属分段符；间隔大 = 换场标记。用于两种写法并存的场合。

输出：逐章 [重排] 段数 原X → 新Y，平均段字数 原A → 新B
退出码：0。
"""

import argparse
import os
import re
import shutil
import sys

HAN = re.compile(r"[\u4e00-\u9fff]")
SPEECH = re.compile(r"^[^\n]{0,10}?(?:说|道|问|答|喊|骂|叫|嘟囔|嘀咕)[：:]\s*([^\n]+)$")
QUOTED = re.compile(r"^[\u201c\u300c][^\n]*[\u201d\u300d]?\s*[。！？…]?\s*$")
INLINE = re.compile(r"[\"\u201c\u300c][^\"\u201d\u300d]{2,}[\"\u201d\u300d]")
EMPHASIS = re.compile(r"^\*\*.*\*\*$|^\s*#|^第[零一二三四五六七八九十百千0-9]+[章回节][ \t\u3000]")
CH_MD = re.compile(r"^(?:#{1,6}\s*)?第([零一二三四五六七八九十百千]+|[0-9]+)[章回节]")
CH_FILE = re.compile(r"^第(\d{3})章\s+(.+)\.md$")
SEP = r"(?:-{3,}|(?:\uFF0A[ \t\u3000]*){2,})"
SPLIT = re.compile(r"\n[ \t]*(" + SEP + r")[ \t]*\n")
SEP_LINE = re.compile(r"^[ \t]*" + SEP + r"[ \t]*$")
SCENE_MARK = "\uFF0A\u3000\uFF0A\u3000\uFF0A"

TARGET = 60
SCENE_GAP = 100


def count_scene(text):
    return len(SPLIT.findall(text))


def scene_gap(text):
    """章内分隔符的平均间隔（汉字数）。无分隔符时返回 inf。"""
    n = count_scene(text)
    if not n:
        return float("inf")
    return hanzi(text) / n


def hanzi(text):
    return len(HAN.findall(text))


def is_solo(line):
    """对话行、强调块、标题保持独立成段。"""
    return bool(SPEECH.match(line) or QUOTED.match(line)
                or EMPHASIS.match(line) or INLINE.search(line))


def reflow_block(block, target, merge_dialog=0):
    lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
    out = []
    buf = []
    talk = 0
    for line in lines:
        solo = is_solo(line)
        if solo and not merge_dialog:
            if buf:
                out.append("".join(buf))
                buf = []
            out.append(line)
            continue
        buf.append(line)
        talk = talk + 1 if solo else 0
        if hanzi("".join(buf)) >= target or (merge_dialog and talk >= merge_dialog):
            out.append("".join(buf))
            buf = []
            talk = 0
    if buf:
        out.append("".join(buf))
    return "\n\n".join(out)


def sep_of(raw):
    """分隔符原文 → 输出用分隔符：全角星号样式保留星号，其余用 `---`。"""
    return SCENE_MARK if "\uFF0A" in raw else "---"


def reflow(text, target, merge_dialog=0, scene="keep"):
    head = ""
    body = text
    first = text.lstrip().splitlines()[0] if text.strip() else ""
    if CH_MD.match(first):
        head = first.strip()
        body = text.lstrip()[len(first):]
    if scene == "soft":
        body = SPLIT.sub("\n", body)
        joined = reflow_block(body, target, merge_dialog)
    else:
        pieces = SPLIT.split(body)          # [文本, 分隔符, 文本, 分隔符, …]
        texts = pieces[0::2]
        marks = pieces[1::2]
        segments = []
        for i, t in enumerate(texts):
            mark_before = marks[i - 1] if i > 0 and i - 1 < len(marks) else None
            p = reflow_block(t, target, merge_dialog)
            if p.strip():
                segments.append((mark_before, p))
        if not segments:
            joined = ""
        else:
            joined = segments[0][1]
            for mark_before, p in segments[1:]:
                joined += "\n\n" + sep_of(mark_before or "---") + "\n\n" + p
    return (head + "\n\n" + joined).strip() + "\n" if head else joined.strip() + "\n"


def stat(text):
    paras = [l.strip() for l in text.splitlines()
             if l.strip() and not SEP_LINE.match(l.strip())]
    if not paras:
        return 0, 0.0
    return len(paras), hanzi("\n".join(paras)) / len(paras)


def collect(root):
    body_dir = os.path.join(root, "正文")
    if os.path.isdir(body_dir):
        return [(n, os.path.join(body_dir, n)) for n in sorted(os.listdir(body_dir))
                if n.endswith(".md")]
    for name in ("正文.md", "正文.html"):
        if os.path.exists(os.path.join(root, name)):
            return [(name, os.path.join(root, name))]
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--out", default="")
    ap.add_argument("--target", type=int, default=TARGET)
    ap.add_argument("--merge-dialog", type=int, default=0,
                    help="连续对话合并句数，0 表示对话一律独立成段")
    ap.add_argument("--scene", choices=("keep", "soft", "auto"), default="keep",
                    help="keep=--- 作场景硬边界保留；soft=--- 当普通换行、跨段合并；auto=逐章判定")
    ap.add_argument("--scene-gap", type=int, default=SCENE_GAP,
                    help="auto 判定阈值：章内分隔符平均间隔（汉字）< 此值走 soft（默认 %d）" % SCENE_GAP)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    root = os.path.abspath(args.path)
    files = collect(root)
    if not files:
        print("未找到正文：缺 正文/ 目录或 正文.md")
        return 1

    dest = os.path.abspath(args.out) if args.out else os.path.join(root, "重排预览")
    if args.apply:
        dest = os.path.join(root, "正文") if os.path.isdir(os.path.join(root, "正文")) else root
    os.makedirs(dest, exist_ok=True)

    for name, path in files:
        text = open(path, encoding="utf-8", errors="replace").read()
        before_n, before_avg = stat(text)
        scene = args.scene
        if scene == "auto":
            scene = "soft" if scene_gap(text) < args.scene_gap else "keep"
        new_text = reflow(text, args.target, args.merge_dialog, scene)
        after_n, after_avg = stat(new_text)
        out_path = os.path.join(dest, name)
        with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new_text)
        print("[重排] %s 段数 %d → %d，平均段字数 %.1f → %.1f%s"
              % (name, before_n, after_n, before_avg, after_avg,
                 "（soft）" if scene == "soft" and args.scene == "auto" else ""))
    print("输出目录：%s" % dest)
    if not args.apply:
        print("原文未改动；确认效果后加 --apply 覆盖。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
