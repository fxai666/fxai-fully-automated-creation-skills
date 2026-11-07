#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""标点与标记规范化：投稿前统一正文体例，只动标点、标记与空格，不动文字。

用法：
    python normalize_punct.py <书名目录> [--out 目录] [--apply]
                              [--only scene|quote|md|heading|space]
                              [--scene mark|none|keep] [--md strip|keep]
                              [--heading strip|keep] [--space strip|keep]

处理项：
  1. 场景分隔符：行首独立成行的 `---` 或 `＊　＊　＊`，按 --scene 处理：
     `mark`（默认）统一为 `＊　＊　＊`；`none` 整行删除；`keep` 不动。
     番茄等平台的编辑器不解析 Markdown，`---` 会原样显示为三条横线。
  2. 引号：直引号 `"..."` → 中文弯引号 `“...”`；直角引号 `「」` → `“”`；`『』` 按层级处理
     （一级 `“”`，嵌在 `「」` 内时降为二级 `‘’`）。
  3. Markdown 强调标记：按 --md 处理，`strip`（默认）去掉 `**粗**` / `*斜*` / `` `代码` ``
     的标记符、保留其中文字；`keep` 不动。平台不解析 Markdown 时这些标记会原样显示。
  4. 标题标记：按 --heading 处理，`strip`（默认）去掉行首 `#{1,6}` 及其后空白，
     保留标题文字（`# 第001章 章名` → `第001章 章名`）；`keep` 不动。
  5. 正文空格：按 --space 处理，`strip`（默认）去掉中英混排自动间距——空格一侧是汉字、
     另一侧不是（`鸡 3 只` → `鸡3只`、`VR 记忆` → `VR记忆`）；英文词间空格
     （`GAME OVER`、`print “hello”`）与汉字之间的空格一律保留，标题行整体不动。

约束：
  - 直引号须在同一行内成对，按非贪婪配对替换；存在落单直引号时报错退出，不写文件。
  - 默认输出到 <书名目录>/标点预览/，原文不动；加 --apply 才覆盖 正文/。

退出码：0 成功；1 缺正文或存在落单直引号。
"""

import argparse
import os
import re
import sys

SEP_LINE = re.compile(r"^[ \t]*(?:-{3,}|(?:\uFF0A[ \t\u3000]*){2,})[ \t]*$")
SCENE_MARK = "＊　＊　＊"
HEAD_MARK = re.compile(r"^#{1,6}[ \t\u3000]*")
TITLE_LINE = re.compile(r"^第[零一二三四五六七八九十百千0-9]+[章回节][ \t\u3000]")
HAN = re.compile(r"[\u4e00-\u9fff]")
SPACE_RUN = re.compile(r"[ \t\u3000]+")
STRAIGHT_PAIR = re.compile(r'"([^"\n]{1,200})"')
MD_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
MD_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
MD_CODE = re.compile(r"`([^`\n]+)`")
OPEN_CORNER = "\u300c"
CLOSE_CORNER = "\u300d"
OPEN_DOUBLE_CORNER = "\u300e"
CLOSE_DOUBLE_CORNER = "\u300f"
OPEN_CURLY = "\u201c"
CLOSE_CURLY = "\u201d"
OPEN_SINGLE = "\u2018"
CLOSE_SINGLE = "\u2019"


def convert_scene(text, mode):
    """场景分隔行处理：mark 统一为 SCENE_MARK，none 整行删除，keep 不动。

    返回 (新文本, 处理行数)。
    """
    if mode == "keep":
        return text, 0
    out = []
    n = 0
    for line in text.splitlines():
        if SEP_LINE.match(line):
            n += 1
            if mode == "mark":
                out.append(SCENE_MARK)
            continue
        out.append(line)
    new = "\n".join(out)
    if mode == "none":
        new = re.sub(r"\n{3,}", "\n\n", new)
    if text.endswith("\n"):
        new += "\n"
    return new, n


def convert_corner(text):
    """直角引号转中文弯引号，按层级：一级 “”，嵌在「」内的降为二级 ‘’。

    返回 (新文本, 处理字符数)。
    """
    out = []
    stack = []          # 每个元素是当前层的开引号字符
    n = 0
    for ch in text:
        if ch == OPEN_CORNER:
            out.append(OPEN_CURLY)
            stack.append(OPEN_CURLY)
            n += 1
        elif ch == CLOSE_CORNER:
            if stack and stack[-1] == OPEN_CURLY:
                stack.pop()
            out.append(CLOSE_CURLY)
            n += 1
        elif ch == OPEN_DOUBLE_CORNER:
            if stack and stack[-1] == OPEN_CURLY:
                out.append(OPEN_SINGLE)
                stack.append(OPEN_SINGLE)
            else:
                out.append(OPEN_CURLY)
                stack.append(OPEN_CURLY)
            n += 1
        elif ch == CLOSE_DOUBLE_CORNER:
            if stack and stack[-1] == OPEN_SINGLE:
                stack.pop()
                out.append(CLOSE_SINGLE)
            else:
                if stack and stack[-1] == OPEN_CURLY:
                    stack.pop()
                out.append(CLOSE_CURLY)
            n += 1
        else:
            out.append(ch)
    return "".join(out), n


def strip_md(text):
    """去掉 Markdown 强调标记，保留其中文字。返回 (新文本, 计数 dict)。"""
    stat = {"bold": 0, "italic": 0, "code": 0}
    text, n = MD_BOLD.subn(lambda m: m.group(1), text)
    stat["bold"] = n
    text, n = MD_ITALIC.subn(lambda m: m.group(1), text)
    stat["italic"] = n
    text, n = MD_CODE.subn(lambda m: m.group(1), text)
    stat["code"] = n
    return text, stat


def strip_heading(text):
    """去掉行首 Markdown 标题标记 `#{1,6}` 及其后空白，保留标题文字。

    返回 (新文本, 处理行数)。
    """
    out = []
    n = 0
    for line in text.split("\n"):
        m = HEAD_MARK.match(line)
        if m:
            line = line[m.end():]
            n += 1
        out.append(line)
    return "\n".join(out), n


def strip_space_line(line):
    """去掉中英混排自动间距（空格一侧是汉字、另一侧不是）。返回 (新行, 处理处数)。"""
    n = 0

    def repl(m):
        nonlocal n
        before = line[m.start() - 1] if m.start() > 0 else ""
        after = line[m.end()] if m.end() < len(line) else ""
        if bool(HAN.match(before)) != bool(HAN.match(after)):
            n += 1
            return ""
        return m.group(0)

    return SPACE_RUN.sub(repl, line), n


def strip_space(text):
    """逐行去中英混排间距；标题行 `第NNN章 章名` 整体不动。返回 (新文本, 处理处数)。"""
    out = []
    n = 0
    for line in text.split("\n"):
        if TITLE_LINE.match(line):
            out.append(line)
            continue
        line, k = strip_space_line(line)
        n += k
        out.append(line)
    return "\n".join(out), n


def collect(root):
    body_dir = os.path.join(root, "正文")
    if os.path.isdir(body_dir):
        return [(n, os.path.join(body_dir, n)) for n in sorted(os.listdir(body_dir))
                if n.endswith(".md")]
    for name in ("正文.md", "正文.html"):
        path = os.path.join(root, name)
        if os.path.exists(path):
            return [(name, path)]
    return []


def normalize(text, scene_mode="mark", md_mode="strip", do_scene=True, do_quote=True,
              do_md=True, do_heading=True, do_space=True):
    """返回 (新文本, 改动计数 dict)。落单直引号抛 ValueError。"""
    stat = {"scene": 0, "straight": 0, "corner": 0, "bold": 0, "italic": 0,
            "code": 0, "heading": 0, "space": 0}
    if do_scene:
        text, stat["scene"] = convert_scene(text, scene_mode)
    if do_quote:
        left = text.count('"') - len(STRAIGHT_PAIR.findall(text)) * 2
        if left:
            raise ValueError("落单直引号 %d 个，无法配对" % left)
        text, n = STRAIGHT_PAIR.subn(lambda m: "\u201c" + m.group(1) + "\u201d", text)
        stat["straight"] = n * 2
        text, stat["corner"] = convert_corner(text)
    if do_md:
        text, md_stat = strip_md(text)
        stat.update(md_stat)
    if do_heading:
        text, stat["heading"] = strip_heading(text)
    if do_space:
        text, stat["space"] = strip_space(text)
    return text, stat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--out", default="")
    ap.add_argument("--only", choices=("scene", "quote", "md", "heading", "space"), default="",
                    help="只处理其中一项；缺省五项都处理")
    ap.add_argument("--scene", choices=("mark", "none", "keep"), default="mark",
                    help="场景分隔行：mark=统一为 %s；none=整行删除；keep=不动" % SCENE_MARK)
    ap.add_argument("--md", choices=("strip", "keep"), default="strip",
                    help="Markdown 强调标记：strip=去标记留文字；keep=不动")
    ap.add_argument("--heading", choices=("strip", "keep"), default="strip",
                    help="行首标题标记 `#`：strip=去掉、留标题文字；keep=不动")
    ap.add_argument("--space", choices=("strip", "keep"), default="strip",
                    help="正文空格：strip=去与汉字相邻的空格（英文词间空格保留）；keep=不动")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    root = os.path.abspath(args.path)
    files = collect(root)
    if not files:
        print("未找到正文：缺 正文/ 目录或 正文.md")
        return 1

    do_scene = args.only in ("", "scene")
    do_quote = args.only in ("", "quote")
    do_md = args.only in ("", "md")
    do_heading = args.only in ("", "heading")
    do_space = args.only in ("", "space")

    # 先整体校验，任一文件有问题就不写任何文件
    plans = []
    for name, path in files:
        text = open(path, encoding="utf-8", errors="replace").read()
        try:
            new_text, stat = normalize(text, args.scene, args.md, do_scene, do_quote,
                                       do_md, do_heading, do_space)
        except ValueError as exc:
            print("[错误] %s：%s" % (name, exc))
            return 1
        plans.append((name, text, new_text, stat))

    dest = os.path.abspath(args.out) if args.out else os.path.join(root, "标点预览")
    if args.apply:
        dest = os.path.join(root, "正文") if os.path.isdir(os.path.join(root, "正文")) else root
    os.makedirs(dest, exist_ok=True)

    total = {"scene": 0, "straight": 0, "corner": 0, "bold": 0, "italic": 0,
             "code": 0, "heading": 0, "space": 0}
    touched = 0
    for name, text, new_text, stat in plans:
        for k in total:
            total[k] += stat[k]
        if new_text != text:
            touched += 1
        with open(os.path.join(dest, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new_text)

    print("处理 %d 章，改动 %d 章" % (len(plans), touched))
    scene_desc = {"mark": "→ `%s`" % SCENE_MARK, "none": "整行删除", "keep": "不动"}[args.scene]
    print("  场景分隔符 %s：%d 行" % (scene_desc, total["scene"]))
    print("  直引号 → 弯引号：%d 处" % total["straight"])
    print("  直角引号 → 弯引号：%d 处" % total["corner"])
    print("  Markdown 标记 %s：粗体 %d 处、斜体 %d 处、代码 %d 处"
          % ("去标记" if args.md == "strip" else "不动",
             total["bold"], total["italic"], total["code"]))
    print("  标题标记 %s：%d 行"
          % ("去 `#`" if args.heading == "strip" else "不动", total["heading"]))
    print("  正文空格 %s：%d 处"
          % ("去中英间距" if args.space == "strip" else "不动", total["space"]))
    print("输出目录：%s" % dest)
    if not args.apply:
        print("原文未改动；确认效果后加 --apply 覆盖。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
