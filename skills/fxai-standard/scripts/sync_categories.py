#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 `categories.json` 生成进客户端项目页，并核对两侧是否一致。

用法：
    python sync_categories.py --site <站点根>              # 只核对（默认）
    python sync_categories.py --site <站点根> --write      # 写入客户端页面

站点根 = 含 `tool/project/` 的那一层（如 `<盘符>:/xxx/site`，按实际填）。
改动点：`tool/project/js/projectkit.js` 的 CATEGORIES / CATEGORY_ORDER / TYPE_STAGES、
`tool/project/detail.js` 的 SKILL_OF，以及 `index.html` / `detail.html` 里的 `?v=` 版本号。
"""
import argparse
import io
import os
import re
import sys

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import catkit  # noqa: E402

PROJ = os.path.join('tool', 'project')


def read(path):
    with io.open(path, 'r', encoding='utf-8-sig') as fp:
        return fp.read()


def write(path, text):
    with io.open(path, 'w', encoding='utf-8-sig', newline='') as fp:
        fp.write(text)


def js_str(s):
    return "'%s'" % str(s).replace("\\", "\\\\").replace("'", "\\'")


def js_list(values):
    return '[%s]' % ', '.join(js_str(v) for v in values)


def body_categories(order, items):
    lines = []
    for k in order:
        it = items.get(k) or {}
        if it.get('comment'):
            lines.append('    // %s' % it['comment'])
        lines.append('    %s: { name: %s, icon: %s, color: %s, words: %s },'
                     % (k, js_str(it.get('name') or k), js_str(it.get('icon') or ''),
                        js_str(it.get('color') or ''), js_list(it.get('words') or [])))
    return '\n' + '\n'.join(lines)


def body_order(order, items=None):
    return ', '.join(js_str(v) for v in order)


def body_stages(order, items):
    lines = []
    for k in order:
        it = items.get(k) or {}
        if it.get('comment'):
            lines.append('    // %s' % it['comment'])
        lines.append('    %s: %s,' % (k, js_list(it.get('stages') or catkit.STAGE_KEYS)))
    return '\n' + '\n'.join(lines)


def body_skills(order, items):
    lines = []
    for k in order:
        it = items.get(k) or {}
        lines.append('            %s: %s,' % (k, js_str(it.get('skill') or '')))
    return '\n' + '\n'.join(lines)


BLOCKS = {
    os.path.join('js', 'projectkit.js'): [
        (r'(ProjectKit\.CATEGORIES = \{)([\s\S]*?)(\n\};)', body_categories),
        (r'(ProjectKit\.CATEGORY_ORDER = \[)([\s\S]*?)(\];)', body_order),
        (r'(ProjectKit\.TYPE_STAGES = \{)([\s\S]*?)(\n\};)', body_stages),
    ],
    'detail.js': [
        (r'(SKILL_OF: \{)([\s\S]*?)(\n        \},)', body_skills),
    ],
}


def patch(path, rules, order, items):
    """返回 (改动块数, 新文本)。"""
    text = read(path)
    hits = 0
    for pat_str, make in rules:
        pat = re.compile(pat_str)
        m = pat.search(text)
        if not m:
            raise SystemExit('%s 里找不到待替换的块：%s' % (path, pat_str))
        want = make(order, items)
        if m.group(2) != want:
            text = text[:m.start(2)] + want + text[m.end(2):]
            hits += 1
    return hits, text


VER_RE = r'(\b%s\?v=)([a-z])(\d+)'


def bump_text(text, script):
    """把 `script?v=xNN` 的序号加一，返回 (改动数, 新文本)。"""
    pat = re.compile(VER_RE % re.escape(script))

    def repl(m):
        return '%s%s%d' % (m.group(1), m.group(2), int(m.group(3)) + 1)

    return pat.subn(repl, text)


def main():
    ap = argparse.ArgumentParser(description='类别表 → 客户端项目页')
    ap.add_argument('--site', required=True, help='站点根目录（含 tool/project）')
    ap.add_argument('--write', action='store_true', help='写入；不带则只核对')
    args = ap.parse_args()

    root = os.path.join(os.path.abspath(args.site), PROJ)
    if not os.path.isdir(root):
        raise SystemExit('找不到 %s（--site 指向含 tool/project 的那一层）' % root)

    order, items = catkit.load()
    if not order or not items:
        raise SystemExit('categories.json 读不出内容：%s' % catkit.CATEGORY_FILE)

    dirty = []
    for fname, rules in BLOCKS.items():
        path = os.path.join(root, fname)
        if not os.path.isfile(path):
            raise SystemExit('缺文件：%s' % path)
        hits, new = patch(path, rules, order, items)
        if hits:
            dirty.append(fname)
            if args.write:
                write(path, new)
                print('已写入 %s（%d 处块）' % (fname, hits))
            else:
                print('不一致 %s（%d 处块）' % (fname, hits))
        else:
            print('一致   %s' % fname)

    if dirty and args.write:
        for html, scripts in (('index.html', ['js/projectkit.js']),
                              ('detail.html', ['js/projectkit.js', 'detail.js'])):
            path = os.path.join(root, html)
            text = read(path)
            bumped = []
            for s in scripts:
                text, n = bump_text(text, s)
                if n:
                    bumped.append(s)
            if bumped:
                write(path, text)
                print('已升版本 %s：%s' % (html, ', '.join(bumped)))
        print('—— 完成，刷新客户端项目页生效')
        return 0

    if dirty:
        print('—— 有差异：改完 categories.json 后加 --write 生成到页面')
        return 1
    print('—— 两侧一致（%d 个类别）' % len(order))
    return 0



if __name__ == "__main__":
    sys.exit(main())
