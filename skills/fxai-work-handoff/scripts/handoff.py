#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工作交接清单：建骨架、体检、打印。

用法:
    python handoff.py init  <项目目录> [--to "<接手人>"] [--force]
    python handoff.py check <项目目录>
    python handoff.py show  <项目目录>

    <项目目录>   实际工作的项目文件夹（可给项目内任意层级路径，自动上溯到带清单的根）
    --to         交接给谁，缺省「下一个接手的 AI 同事」
    --force      已存在清单时重建；旧清单改名进 <项目根>/_tmp/

退出码:
    0  通过 / 已生成
    3  待补（已存在不覆盖、缺节、有 <待填 残留、路径失效）
    1  出错

口径见 fxai-standard `references/01-产出铁律.md` 与本技能 `references/01-交接清单写法.md`。
"""

import argparse
import io
import json
import os
import re
import shutil
import sys
import time

sys.dont_write_bytecode = True

DOC = '工作交接清单.md'
MANIFEST = '_fxai_project.json'
MEMORY = '_fxai_memory.md'
TODO = '<待填'
UNBUILT = '尚未生成'
MAX_UP = 6

SECTIONS = [
    ('一、一句话现状', '项目在做什么，停在哪一步，还差什么'),
    ('二、交接卡', '项目名 / 项目根 / 分类 / 当前阶段 / 交接时间 / 交接给谁 / 交付物'),
    ('三、接手第一件事', '编号动作：在哪做 → 怎么做 → 做完判据'),
    ('四、已完成', '产物绝对路径 + 判据，标明勿重做的部分'),
    ('五、待办与下一步', '表格：编号 / 任务 / 依赖 / 产物 / 验收判据 / 卡点'),
    ('六、关键文件地图', '表格：路径 / 是什么 / 改动方式'),
    ('七、跑法与环境', '所需服务、端口、启动命令、账号取值位置'),
    ('八、硬约束', '本项目取值：时长 / 尺寸 / 档位 / 模型 / 文案 / 编码 / 命名'),
    ('九、坑与绕法', '现象 → 处理，标已绕开 / 仍卡住'),
    ('十、素材与授权', '素材来源、可商用范围、授权台账位置'),
    ('十一、记忆与标准指针', '_fxai_memory.md / _fxai_project.json / 工作区记忆 / 该读的技能册'),
    ('十二、验收标准', '什么算这条活干完，逐条可判'),
    ('十三、交接自查', '勾选：各项已填、路径已验、产物未动、记忆已更新'),
]

PATHISH = re.compile(r'\.(md|json|py|mp4|mov|wav|mp3|png|jpg|jpeg|txt|srt|log|ps1|bat|sh|csv|xlsx)$',
                     re.I)
HEAD = re.compile(r'^##\s+(.+?)\s*$')
ABS_PATH = re.compile(r'^(?:[A-Za-z]:[\\/]|\\\\|/)')

def now():
    return time.strftime('%Y-%m-%d %H:%M')


def find_root(given):
    """从给定路径上溯，取最近一个带项目清单的目录；都没有则取给定目录本身。"""
    path = os.path.abspath(given)
    if os.path.isfile(path):
        path = os.path.dirname(path)
    if not os.path.isdir(path):
        return None
    cur = path
    for _ in range(MAX_UP):
        if os.path.isfile(os.path.join(cur, MANIFEST)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return path


def load_manifest(root):
    p = os.path.join(root, MANIFEST)
    if not os.path.isfile(p):
        return {}
    try:
        with io.open(p, 'r', encoding='utf-8') as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def workspace_memory(root):
    """项目根上溯找 <工作区>/.workbuddy/memory。"""
    cur = root
    for _ in range(MAX_UP):
        mem = os.path.join(cur, '.workbuddy', 'memory')
        if os.path.isdir(mem):
            return mem
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def human_size(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%d %s' % (n, unit)) if unit == 'B' else ('%.1f %s' % (n, unit))
        n /= 1024.0


def tree_rows(root):
    rows = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return rows
    for name in names:
        if name in (DOC, MANIFEST, MEMORY, '.git', '__pycache__'):
            continue
        full = os.path.join(root, name)
        if os.path.isdir(full):
            try:
                count = len(os.listdir(full))
            except OSError:
                count = 0
            what = '目录（%d 项）' % count
            editable = '待确认'
        else:
            try:
                what = '文件（%s）' % human_size(os.path.getsize(full))
            except OSError:
                what = '文件'
            editable = '待确认'
        rows.append('| `%s` | %s | %s |' % (name, what, editable))
    return rows


def build(root, to):
    man = load_manifest(root)
    title = man.get('title') or os.path.basename(root.rstrip('\\/'))
    category = man.get('category') or '待确认'
    st = man.get('stages') or {}
    cn = {'script': '剧本', 'image': '画面', 'audio': '音频', 'video': '成片', 'publish': '发布'}
    stage = ' / '.join('%s=%s' % (cn.get(k, k), v) for k, v in st.items()) if isinstance(st, dict) and st else '待确认'
    mem = os.path.join(root, MEMORY)
    mani = os.path.join(root, MANIFEST)
    ws = workspace_memory(root)

    def need(p):
        return '`%s`' % p if os.path.exists(p) else UNBUILT

    lines = []
    add = lines.append
    add('# 工作交接清单 · %s' % title)
    add('')
    add('> 交接时间：%s　交接给：%s' % (now(), to))
    add('> 项目根：`%s`　分类：%s' % (root, category))
    add('> 接手方先读第一、三、十二节，读完即开工；逐节要求见 `fxai-work-handoff/references/01-交接清单写法.md`。')
    add('')

    add('## 一、一句话现状')
    add('')
    add('%s：项目做到哪一步、还差什么，一句话。' % TODO)
    add('')

    add('## 二、交接卡')
    add('')
    add('| 项 | 内容 |')
    add('|---|---|')
    add('| 项目名 | %s |' % title)
    add('| 项目根 | `%s` |' % root)
    add('| 分类 | %s |' % category)
    add('| 当前阶段 | %s |' % stage)
    add('| 交接时间 | %s |' % now())
    add('| 交接给 | %s |' % to)
    add('| 当前交付物 | %s：最终产物绝对路径，没有就写「尚无」 |' % TODO)
    add('')

    add('## 三、接手第一件事')
    add('')
    add('1. %s：在哪做（文件 / 目录）→ 怎么做（命令或界面操作）→ 跑完看什么算过。' % TODO)
    add('')

    add('## 四、已完成')
    add('')
    add('- %s：产物绝对路径 + 判据 + 哪部分勿重做。' % TODO)
    add('')

    add('## 五、待办与下一步')
    add('')
    add('| 编号 | 任务 | 依赖 | 产物 | 验收判据 | 卡点 |')
    add('|---|---|---|---|---|---|')
    add('| 1 | %s | — | — | — | — |' % TODO)
    add('')

    add('## 六、关键文件地图')
    add('')
    add('| 路径 | 是什么 | 改动方式 |')
    add('|---|---|---|')
    for row in tree_rows(root):
        add(row)
    if os.path.isfile(mani):
        add('| `%s` | 项目清单：阶段与交付登记 | 用 `fxai_project.py` 改 |' % MANIFEST)
    if os.path.isfile(mem):
        add('| `%s` | 项目记忆：约定 / 决策 / 待办 | 追加，不覆盖 |' % MEMORY)
    add('')

    add('## 七、跑法与环境')
    add('')
    add('- 所需服务与端口：%s' % ('%s：端口 → 用途 → 启动命令' % TODO))
    add('- 账号 / 密钥取值位置：%s（只写位置）' % TODO)
    add('- 本机端口口径见用户级 `本机端口表.md`，不在此复写。')
    add('')

    add('## 八、硬约束')
    add('')
    add('- %s：本项目取值，写数字：时长 / 尺寸 / 档位 / 模型 / 文案口径 / 编码 / 命名。' % TODO)
    add('- 标准正文只给指针：%s（技能名 + 册名）。' % TODO)
    add('')

    add('## 九、坑与绕法')
    add('')
    add('- %s：现象 → 处理（已绕开 / 仍卡住）。' % TODO)
    add('')

    add('## 十、素材与授权')
    add('')
    add('- 素材来源：%s；可商用范围与授权台账位置同列。' % TODO)
    add('')

    add('## 十一、记忆与标准指针')
    add('')
    add('| 层 | 指向 |')
    add('|---|---|')
    add('| 项目记忆 | %s |' % need(mem))
    add('| 项目清单 | %s |' % need(mani))
    add('| 工作区记忆 | %s |' % ('`%s`' % ws if ws else '工作区记忆 %s' % UNBUILT))
    add('| 用户级 | 跨项目铁律在用户级 `MEMORY.md`，本清单不复制 |')
    add('| 技能册 | %s：本项目用到哪几个技能，逐个列名 + 该读的册 |' % TODO)
    add('')

    add('## 十二、验收标准')
    add('')
    add('1. %s：逐条可判，写「看到什么 / 量到什么」算过。' % TODO)
    add('')

    add('## 十三、交接自查')
    add('')
    add('- [ ] 一~十二节逐节填完，全文无未填占位')
    add('- [ ] 清单内路径逐个验过，不存在的已标「%s」' % UNBUILT)
    add('- [ ] 产物未因本次交接改动')
    add('- [ ] 项目记忆已追加交接记录')
    add('- [ ] `python -B handoff.py check <项目目录>` 退出码 0')
    add('')
    return '\n'.join(lines)


def section_bodies(text):
    """返回 {节标题: [行]}。"""
    out, cur = {}, None
    for line in text.splitlines():
        m = HEAD.match(line)
        if m:
            cur = m.group(1).strip()
            out[cur] = []
            continue
        if cur is not None:
            out[cur].append(line)
    return out


def looks_path(tok):
    if not tok or tok.startswith(('http://', 'https://', 'www.')):
        return False
    if '<' in tok or '>' in tok or '*' in tok or '...' in tok:
        return False
    if '/' in tok or '\\' in tok:
        return True
    return bool(PATHISH.search(tok))


def check(root):
    doc = os.path.join(root, DOC)
    msgs, code = [], 0
    if not os.path.isfile(doc):
        return 1, ['缺 %s：先跑 handoff.py init %s' % (DOC, root)]
    with io.open(doc, 'r', encoding='utf-8') as f:
        text = f.read()
    bodies = section_bodies(text)

    missing = [s for s, _ in SECTIONS if s not in bodies]
    for s in missing:
        msgs.append('缺节：## %s' % s)
    if missing:
        code = 3

    todo = [(i + 1, l.strip()) for i, l in enumerate(text.splitlines()) if TODO in l]
    for ln, l in todo:
        msgs.append('第 %d 行未填：%s' % (ln, l[:60]))
    if todo:
        code = 3

    body3 = '\n'.join(bodies.get('三、接手第一件事', []))
    if not re.search(r'^\s*\d+\.\s+\S', body3, re.M):
        msgs.append('「三、接手第一件事」缺编号动作')
        code = 3

    body5 = '\n'.join(bodies.get('五、待办与下一步', []))
    if not re.search(r'^\s*\|\s*\d+\s*\|', body5, re.M) and not re.search(r'^\s*[-*]\s+\S', body5, re.M):
        msgs.append('「五、待办与下一步」没有可执行的条目')
        code = 3

    body6 = '\n'.join(bodies.get('六、关键文件地图', []))
    if not re.search(r'^\s*\|', body6, re.M):
        msgs.append('「六、关键文件地图」缺表格')
        code = 3

    bad = []
    for line in bodies.get('六、关键文件地图', []):
        if UNBUILT in line or not line.lstrip().startswith('|'):
            continue
        cells = [c.strip().strip('`') for c in line.strip().strip('|').split('|')]
        if not cells or cells[0] in ('路径', '项') or '<' in cells[0] or set(cells[0]) <= set('-: '):
            continue
        tok = cells[0]
        full = tok if os.path.isabs(tok) else os.path.join(root, tok)
        if not os.path.exists(full):
            bad.append('六：%s' % tok)
    for s in ('二、交接卡',):
        for line in bodies.get(s, []):
            if UNBUILT in line:
                continue
            for tok in re.findall(r'`([^`]+)`', line):
                tok = tok.strip()
                if '/' not in tok and '\\' not in tok:
                    continue
                if not looks_path(tok):
                    continue
                full = tok if os.path.isabs(tok) else os.path.join(root, tok)
                if not os.path.exists(full):
                    bad.append('%s：%s' % (s.split('、')[0], tok))
    # 三/四/五/十一：产物与落点一律写绝对路径，只验绝对路径，
    # 十一节的技能册指针是相对技能根的，不按项目根校验
    for s in ('三、接手第一件事', '四、已完成', '五、待办与下一步', '十一、记忆与标准指针'):
        for line in bodies.get(s, []):
            if UNBUILT in line:
                continue
            toks = re.findall(r'`([^`]+)`', line) + re.findall(r'[A-Za-z]:[\\/][^\s`|、，。：]+', line)
            for tok in toks:
                tok = tok.strip()
                if not ABS_PATH.match(tok) or not looks_path(tok):
                    continue
                if not os.path.exists(tok):
                    bad.append('%s：%s' % (s.split('、')[0], tok))
    for b in sorted(set(bad)):
        msgs.append('路径不存在：%s' % b)
    if bad:
        code = 3

    if code == 0:
        msgs.append('通过：%d 节齐全，无待填，路径有效（%s）' % (len(SECTIONS), doc))
    return code, msgs


def cmd_init(root, to, force):
    doc = os.path.join(root, DOC)
    if os.path.exists(doc):
        if not force:
            print('已存在 %s，未覆盖（加 --force 重建）' % doc)
            return 3
        tmp = os.path.join(root, '_tmp')
        os.makedirs(tmp, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        shutil.move(doc, os.path.join(tmp, '工作交接清单.%s.md' % stamp))
        print('旧清单已移入 %s' % os.path.join(tmp, '工作交接清单.%s.md' % stamp))
    text = build(root, to)
    with io.open(doc, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print('已生成：%s' % doc)
    print('下一步：逐节补 %s 处占位，再跑 check' % text.count(TODO))
    return 0


def cmd_show(root):
    doc = os.path.join(root, DOC)
    if not os.path.isfile(doc):
        print('无清单：%s' % doc)
        return 3
    with io.open(doc, 'r', encoding='utf-8') as f:
        text = f.read()
    print('项目根：%s' % root)
    for s, _ in SECTIONS:
        body = section_bodies(text).get(s)
        mark = '缺' if body is None else ('待填' if any(TODO in l for l in body) else 'ok')
        print('[%s] ## %s' % (mark, s))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='工作交接清单：建骨架 / 体检 / 打印')
    sub = ap.add_subparsers(dest='cmd')

    p = sub.add_parser('init', help='建骨架')
    p.add_argument('path')
    p.add_argument('--to', default='下一个接手的 AI 同事')
    p.add_argument('--force', action='store_true')

    p = sub.add_parser('check', help='体检')
    p.add_argument('path')

    p = sub.add_parser('show', help='打印节标题与待填状态')
    p.add_argument('path')

    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    root = find_root(a.path)
    if not root:
        print('目录不存在：%s' % a.path)
        return 1

    if a.cmd == 'init':
        return cmd_init(root, a.to, a.force)
    if a.cmd == 'show':
        return cmd_show(root)
    code, msgs = check(root)
    for m in msgs:
        print(m)
    return code


if __name__ == '__main__':
    sys.exit(main())
