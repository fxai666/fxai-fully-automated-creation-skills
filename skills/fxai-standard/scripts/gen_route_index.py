#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成并校验「意图路由索引」（references/13-意图路由索引.md）。

数据源
  1. 操作手册 HTML 的技能卡片：中文名、一句话产出、触发词、边界指向
  2. 无卡片技能取其 SKILL.md 的 description 作为产出说明
  3. 包根 SKILL.md 第二节清单：分组归属与技能全集

用法
  python -B gen_route_index.py                 # 重新生成索引
  python -B gen_route_index.py --check         # 只校验，索引与数据源一致则退出码 0
  python -B gen_route_index.py --manual <html> --skills-root <dir> --out <md>
"""
from __future__ import print_function

import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SKILLS_ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
DEFAULT_MANUAL = os.path.join(
    DEFAULT_SKILLS_ROOT, 'fxai-share-skill-package', 'assets', '凤希AI技能操作手册.html')
DEFAULT_OUT = os.path.abspath(
    os.path.join(HERE, '..', 'references', '13-意图路由索引.md'))

TITLE_TMPL = '# 意图路由索引（%d 技能）'
INTRO = [
    '> 用户说要做某件事时，先在本表按「意图 / 触发」找到技能，再读该技能的 `SKILL.md` 拿命令与参数。',
    '> 本表只回答「用哪个技能、产出什么、边界去哪」，命令细节不在此重复。',
    '> 本册由 `scripts/gen_route_index.py` 从操作手册卡片与技能 description 生成；改过手册后重跑，`--check` 校验一致性。',
]


def read_text(path):
    with io.open(path, 'r', encoding='utf-8') as f:
        return f.read()


def strip_tags(s):
    s = re.sub(r'(?is)<script\b.*?</script>|<style\b.*?</style>', '', s)
    s = re.sub(r'<[^>]+>', '', s)
    s = s.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
    s = s.replace('\u8981\u4e0d\u8981', '\u662f\u5426')  # 疑问式改陈述式
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def parse_cards(html):
    """按出现顺序解析技能卡片。"""
    cards = []
    pattern = re.compile(r'<div class="card">(.*?)(?=<div class="card">|</div>\s*</div>\s*<h2|$)', re.S)
    for m in re.finditer(r'<div class="card">', html):
        seg = html[m.start():m.start() + 3000]
        nm = re.search(r'class="nm">([^<]+)<', seg)
        sid = re.search(r'class="id">([^<]+)<', seg)
        one = re.search(r'class="one">(.*?)</p>', seg, re.S)
        if not (nm and sid):
            continue
        head = seg[:seg.find('class="one"') + 1200] if 'class="one"' in seg else seg[:1200]
        says = re.findall(r'class="say">([^<]+)<', head)
        bound = re.search(r'class="v bound">(.*?)</div>', seg, re.S)
        cards.append({
            'name': strip_tags(nm.group(1)),
            'id': sid.group(1).strip(),
            'one': strip_tags(one.group(1)) if one else '',
            'says': [strip_tags(x) for x in says],
            'bound': strip_tags(bound.group(1)) if bound else '',
        })
    del pattern
    return cards


def parse_groups(pack_md):
    """解析包根清单的分组与技能顺序。"""
    groups = []
    cur = None
    for line in pack_md.splitlines():
        m = re.match(r'^###\s+(.+?)\s*$', line)
        if m:
            cur = {'title': m.group(1).strip(), 'skills': []}
            groups.append(cur)
            continue
        m = re.match(r'^-\s+`(fxai-[A-Za-z0-9_-]+)`', line)
        if m and cur is not None:
            cur['skills'].append(m.group(1))
    return [g for g in groups if g['skills']]


def first_sentence(text, limit=60):
    text = text.strip()
    for sep in ['。当用户', '；当用户', '。用于', '；用于']:
        if sep in text:
            text = text.split(sep)[0]
            break
    text = text.strip('。 ')
    if len(text) > limit:
        text = text[:limit].rstrip('，、 ') + '…'
    return text


def clip(text, limit):
    text = text.strip().strip('。 ')
    if len(text) > limit:
        text = text[:limit].rstrip('，、； ') + '…'
    return text


def bound_targets(bound, limit=34):
    """边界列只留两件事：指向哪个技能、一句极短的限制。"""
    bound = bound.strip()
    if not bound:
        return '—'
    targets = []
    for tid in re.findall(r'fxai-[A-Za-z0-9_-]+', bound):
        if tid not in targets:
            targets.append(tid)
    if targets:
        return '→ ' + ' / '.join(targets[:3])
    return clip(bound, limit)


def skill_description(skills_root, skill_id):
    path = os.path.join(skills_root, skill_id, 'SKILL.md')
    if not os.path.exists(path):
        return ''
    m = re.search(r'^description:[ \t]*(.+)$', read_text(path), re.M)
    return m.group(1).strip() if m else ''


def triggers_from_description(desc):
    m = re.search(r'当(?:用户|需要|要)[^「；]*「([^」]+)」', desc)
    if not m:
        return []
    return [t.strip() for t in re.split(r'[/、]', m.group(1)) if t.strip()][:3]


def build_index(skills_root, manual):
    pack_md_path = os.path.join(skills_root, 'SKILL.md')
    groups = parse_groups(read_text(pack_md_path))
    cards = {c['id']: c for c in parse_cards(read_text(manual))}

    lines = [TITLE_TMPL % len(set(sum([g['skills'] for g in groups], []))), '']
    lines.extend(INTRO)
    lines.append('')

    seen = set()
    missing = []
    for g in groups:
        rows = []
        for sid in g['skills']:
            if sid in seen:
                continue
            seen.add(sid)
            card = cards.get(sid)
            desc = skill_description(skills_root, sid)
            if card:
                says = card['says'][:3]
                one = clip(card['one'], 42)
                bound = bound_targets(card['bound'])
                name = card['name']
            else:
                says = triggers_from_description(desc)
                one = first_sentence(desc, 42) if desc else ''
                bound = '—'
                name = sid.replace('fxai-', '')
                missing.append(sid)
            intent = '、'.join(says) if says else '—'
            one = one or '（见该技能 SKILL.md）'
            bound = bound or '—'
            rows.append('| %s | `%s`（%s） | %s | %s |' % (intent, sid, name, one, bound))
        if not rows:
            continue
        lines.append('## %s' % g['title'])
        lines.append('')
        lines.append('| 意图 / 触发 | 技能 | 产出 | 边界 / 下一步 |')
        lines.append('|---|---|---|---|')
        lines.extend(rows)
        lines.append('')

    extra = [sid for sid in cards if sid not in seen]

    lines.append('## 使用口径')
    lines.append('')
    lines.append('- 命中技能后只读该技能的 `SKILL.md`，按其命令与参数执行；本表不提供命令。')
    lines.append('- 表内没有的意图：读 `凤希AI技能操作手册.html` 对应章节，或问用户要哪一类产出。')
    lines.append('- 手册卡片改动后重跑本脚本，`--check` 退出码 0 才算索引与数据源一致。')
    lines.append('')
    return '\n'.join(lines), missing, extra


def main():
    ap = argparse.ArgumentParser(description='生成或校验意图路由索引')
    ap.add_argument('--manual', default=DEFAULT_MANUAL)
    ap.add_argument('--skills-root', default=DEFAULT_SKILLS_ROOT)
    ap.add_argument('--out', default=DEFAULT_OUT)
    ap.add_argument('--check', action='store_true', help='只校验，不写文件')
    args = ap.parse_args()

    if not os.path.exists(args.manual):
        print('缺少操作手册：%s' % args.manual)
        return 2

    content, missing, extra = build_index(args.skills_root, args.manual)

    if args.check:
        if not os.path.exists(args.out):
            print('缺少索引：%s' % args.out)
            return 1
        old = read_text(args.out)
        if old.strip() == content.strip():
            print('索引与数据源一致（%d 行）' % len(content.splitlines()))
            return 0
        print('索引与数据源不一致，重跑 gen_route_index.py 重新生成')
        return 1

    with io.open(args.out, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    print('已写入 %s（%d 行）' % (args.out, len(content.splitlines())))
    if missing:
        print('无手册卡片、用 description 兜底的技能：%s' % '、'.join(missing))
    if extra:
        print('手册有卡但不在包清单（未收录）：%s' % '、'.join(extra))
    return 0


if __name__ == '__main__':
    sys.exit(main())
