#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目清单 _fxai_project.json 的读写（技能侧唯一入口）。

页面（tool/project）读同一份文件，据此展示项目类型、标题、逐集进度。
本脚本只读写清单，不碰任何交付物。

用法：
    # 定位项目（任意文件/子目录 → 项目根 + 命中集；找不到给建议落点）
    python fxai_project.py context <任意路径> [--workspace <目录>] [--json] [--require]

    # 开局建档（可重复调用，只更新本次给到的字段）
    python fxai_project.py init <项目目录> --title 云上清楼 --category drama --skill fxai-short-drama

    # 登记一集 + 该集各阶段状态
    python fxai_project.py episode <项目目录> --no 1 --name 风尘花魁 --dir "1、风尘花魁" --script 2 --image 2

    # 项目级阶段（无集结构的项目用，如单篇生图 / 小说）
    python fxai_project.py stage <项目目录> --script 2 --image 1

    # 登记交付文件（相对项目根的路径）
    python fxai_project.py deliver <项目目录> --no 1 --video "成片_ep1.mp4" --script "六段式提示词.md"

    # 项目记忆（_fxai_memory.md）：--add 追加一条，不给 --add 即打印全文
    python fxai_project.py memory <项目目录> --add "女主音色定温柔青年音" --tag 约定

    # 查看 / 校验
    python fxai_project.py show <项目目录> [--json]
    python fxai_project.py check <项目目录> [--json]

阶段词表：script 剧本/文案/分镜 · image 画面 · audio 音频 · video 成片 · publish 发布物料
阶段取值：0 未开始 / 1 部分 / 2 完成（省略即不改）

时间戳由脚本自己写，调用方不用传。
有 episodes 时，项目级 stage 按集汇总（done / total），手工值只作无集时的兜底。

退出码：0 成功；1 清单不合规；2 目录不存在或参数错误；3 context 未定位到项目（--require）。
"""
import sys

sys.dont_write_bytecode = True

import argparse
import io
import json
import os
import time

MANIFEST = '_fxai_project.json'
MEMORY = '_fxai_memory.md'
MEMORY_MAX = 4000
MEMORY_TAGS = ('约定', '决策', '待办', '风险')
TAG_SECTION = {'约定': '稳定约定', '待办': '待办与未决', '决策': '决策与流水', '风险': '决策与流水'}
MEMORY_TPL = u"""# 项目记忆 · {title}

- 项目：{title}（{category} / {skill}）
- 建档：{date}

## 稳定约定

（长期生效的口径：人设 / 风格 / 参数 / 素材来源 / 角色库指针）

## 决策与流水

（按时间追加：`日期 [标签] 一条结论`）

## 待办与未决
"""
SCHEMA = 2
STAGES = ('script', 'image', 'audio', 'video', 'publish')
STAGE_CN = {'script': '剧本', 'image': '画面', 'audio': '音频', 'video': '成片', 'publish': '发布'}
try:
    from catkit import CATEGORIES  # 类别表：fxai-standard/categories.json（唯一事实源）
except ImportError:  # 类别表缺失时兜底，判据仍以 categories.json 为准
    CATEGORIES = ('drama', 'anime', 'mv', 'music', 'image', 'voiceover',
                  'ad', 'novel', 'wechat', 'remake', 'other')


def now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def path_of(root):
    return os.path.join(root, MANIFEST)


def load(root):
    """读清单。不存在返回 None；存在但坏了直接抛错，绝不覆盖用户数据。"""
    p = path_of(root)
    if not os.path.isfile(p):
        return None
    with io.open(p, 'r', encoding='utf-8') as fp:
        raw = fp.read()
    if not raw.strip():
        return None
    try:
        obj = json.loads(raw)
    except Exception as e:
        raise ValueError('%s 解析失败（%s）。先手工修好再跑，脚本不会覆盖它。' % (MANIFEST, e))
    if not isinstance(obj, dict):
        raise ValueError('%s 顶层不是对象' % MANIFEST)
    return obj


def save(root, obj):
    obj['schema'] = SCHEMA
    obj['updated'] = now()
    if not obj.get('created'):
        obj['created'] = obj['updated']
    p = path_of(root)
    tmp = p + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8') as fp:
        fp.write(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
    os.replace(tmp, p)


def norm(obj):
    """补默认结构，未知字段原样保留。"""
    obj = dict(obj or {})
    obj.setdefault('title', '')
    obj.setdefault('category', 'other')
    obj.setdefault('skill', '')
    obj.setdefault('summary', '')
    st = obj.get('stages')
    obj['stages'] = dict(st) if isinstance(st, dict) else {}
    eps = obj.get('episodes')
    obj['episodes'] = eps if isinstance(eps, list) else []
    for ep in obj['episodes']:
        if isinstance(ep, dict):
            s = ep.get('stages')
            ep['stages'] = dict(s) if isinstance(s, dict) else {}
            d = ep.get('deliver')
            ep['deliver'] = dict(d) if isinstance(d, dict) else {}
    return obj


def find_ep(obj, no):
    for ep in obj['episodes']:
        try:
            if int(ep.get('no')) == int(no):
                return ep
        except Exception:
            continue
    return None


def apply_stages(target, args):
    """把 --script/--image/... 写进目标 stages。返回是否真的改了。"""
    changed = False
    for key in STAGES:
        v = getattr(args, key, None)
        if v is None:
            continue
        if v not in (0, 1, 2):
            raise ValueError('--%s 只能取 0/1/2' % key)
        target[key] = v
        changed = True
    return changed


def rollup(obj):
    """有集时按集汇总项目级阶段：state 0 全未开始 / 2 全完成 / 1 中间。"""
    eps = [ep for ep in obj['episodes'] if isinstance(ep, dict)]
    if not eps:
        return
    for key in STAGES:
        done = 0
        started = False
        for ep in eps:
            v = ep['stages'].get(key)
            if v == 2:
                done += 1
                started = True
            elif v == 1:
                started = True
        state = 0 if not started else (2 if done == len(eps) else 1)
        obj['stages'][key] = state


def resolve_dir(root, given):
    """登记某一集时尽量把目录名落准，方便页面直接对上。"""
    if given:
        return given.replace('/', '\\').strip('\\')
    return ''


def memory_path(root):
    return os.path.join(root, MEMORY)


def ensure_memory(root, obj):
    """记忆骨架由脚本建；已有文件原样保留。返回是否新建。"""
    p = memory_path(root)
    if os.path.isfile(p):
        return False
    with io.open(p, 'w', encoding='utf-8') as fp:
        fp.write(MEMORY_TPL.format(title=obj.get('title') or '未命名项目',
                                   category=obj.get('category') or 'other',
                                   skill=obj.get('skill') or '-',
                                   date=now()))
    return True


def memory_add(path, text, tag):
    """一条记忆追加到所属段末尾；段不存在时补段。返回写入后字符数。"""
    sec = '## ' + TAG_SECTION[tag]
    line = '- %s [%s] %s' % (now()[:10], tag, text.strip())
    with io.open(path, 'r', encoding='utf-8') as fp:
        lines = fp.read().splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == sec), None)
    if start is None:
        lines += ['', sec, '', line]
    else:
        end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith('## ')), len(lines))
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        head, tail = lines[:end], lines[end:]
        if tail and not tail[0].strip():
            tail = tail[1:]
        if head[-1].strip() == sec or head[-1].lstrip().startswith('（'):
            head.append('')
        head.append(line)
        lines = head + ([''] + tail if tail else [])
    body = '\n'.join(lines).rstrip() + '\n'
    with io.open(path, 'w', encoding='utf-8') as fp:
        fp.write(body)
    return len(body)


def cmd_memory(root, a):
    obj = norm(load(root) or {})
    created = ensure_memory(root, obj)
    p = memory_path(root)
    if a.add:
        tag = a.tag if a.tag in MEMORY_TAGS else '决策'
        size = memory_add(p, a.add, tag)
        tail = '' if size <= MEMORY_MAX else '；%d 字符超 %d，压已闭环条目为一行结论' % (size, MEMORY_MAX)
        return 0, '已记入项目记忆[%s] %s%s' % (tag, a.add.strip(), tail)
    with io.open(p, 'r', encoding='utf-8') as fp:
        body = fp.read().rstrip()
    print(body)
    return 0, '—— %s（%d 字符%s）' % (p, len(body), '，新建' if created else '')


def cmd_init(root, a):
    obj = norm(load(root))
    if a.title is not None:
        obj['title'] = a.title.strip()
    if a.category:
        if a.category not in CATEGORIES:
            raise ValueError('--category 只能取：%s' % ' / '.join(CATEGORIES))
        obj['category'] = a.category
    if a.skill is not None:
        obj['skill'] = a.skill.strip()
    if a.summary is not None:
        obj['summary'] = a.summary.strip()
    if not obj['title']:
        obj['title'] = os.path.basename(root.rstrip('\\/')) or '未命名项目'
    save(root, obj)
    made = ensure_memory(root, obj)
    tail = '；记忆骨架 %s 已建' % MEMORY if made else ''
    return 0, '清单已写入：%s（%s / %s）%s' % (obj['title'], obj['category'], MANIFEST, tail)


def cmd_episode(root, a):
    obj = norm(load(root))
    if not obj['title']:
        obj['title'] = os.path.basename(root.rstrip('\\/')) or '未命名项目'
    ep = find_ep(obj, a.no)
    if ep is None:
        ep = {'no': int(a.no), 'name': '', 'dir': '', 'stages': {}, 'deliver': {}}
        obj['episodes'].append(ep)
    if a.name is not None:
        ep['name'] = a.name.strip()
    if a.dir_name is not None:
        ep['dir'] = resolve_dir(root, a.dir_name)
    if not apply_stages(ep['stages'], a):
        pass
    obj['episodes'].sort(key=lambda e: int(e.get('no') or 0))
    rollup(obj)
    save(root, obj)
    s = ' '.join('%s=%s' % (STAGE_CN[k], ep['stages'].get(k, '-')) for k in STAGES)
    return 0, '第 %s 集已登记：%s' % (a.no, s)


def cmd_stage(root, a):
    obj = norm(load(root))
    if not obj['title']:
        obj['title'] = os.path.basename(root.rstrip('\\/')) or '未命名项目'
    if not apply_stages(obj['stages'], a):
        raise ValueError('没有给任何阶段（--script/--image/--audio/--video/--publish）')
    if obj['episodes']:
        # 有集时手工值只在没有集数据时生效，这里明确提示，免得技能以为设上了
        rollup(obj)
        save(root, obj)
        return 0, '已按 %d 集汇总项目级阶段（%s）。逐集状态请用 episode 子命令。' % (
            len(obj['episodes']), ' '.join('%s=%s' % (STAGE_CN[k], obj['stages'].get(k, '-')) for k in STAGES))
    save(root, obj)
    return 0, '项目阶段已更新：%s' % ' '.join('%s=%s' % (STAGE_CN[k], obj['stages'].get(k, '-')) for k in STAGES)


def cmd_deliver(root, a):
    obj = norm(load(root))
    if a.no is None:
        raise ValueError('deliver 必须给 --no <集号>')
    ep = find_ep(obj, a.no)
    if ep is None:
        ep = {'no': int(a.no), 'name': '', 'dir': '', 'stages': {}, 'deliver': {}}
        obj['episodes'].append(ep)
        obj['episodes'].sort(key=lambda e: int(e.get('no') or 0))
    n = 0
    for key in STAGES:
        v = getattr(a, key, None)
        if v:
            ep['deliver'][key] = v.replace('/', '\\').strip('\\')
            n += 1
    if not n:
        raise ValueError('没有给任何交付项（--video/--script/--audio/--image/--publish）')
    rollup(obj)
    save(root, obj)
    return 0, '第 %s 集登记交付 %d 项' % (a.no, n)


def report(obj):
    lines = []
    lines.append('项目：%s' % (obj.get('title') or '(未命名)'))
    lines.append('类型：%s    技能：%s' % (obj.get('category') or '-', obj.get('skill') or '-'))
    if obj.get('summary'):
        lines.append('简介：%s' % obj['summary'])
    stages = obj.get('stages') or {}
    lines.append('阶段：%s' % ('  '.join('%s%s' % (STAGE_CN[k], ('●' if stages.get(k) == 2 else ('◐' if stages.get(k) == 1 else '○'))) for k in STAGES)))
    eps = obj.get('episodes') or []
    lines.append('集数：%d' % len(eps))
    for ep in eps[:60]:
        st = ep.get('stages') or {}
        marks = ''.join({'2': '●', '1': '◐'}.get(str(st.get(k)), '○') for k in STAGES)
        lines.append('  第%s集 %-16s %s  %s' % (ep.get('no'), (ep.get('name') or '')[:16], marks,
                                                len(ep.get('deliver') or {})))
    if len(eps) > 60:
        lines.append('  …（还有 %d 集）' % (len(eps) - 60))
    lines.append('更新时间：%s' % obj.get('updated', '-'))
    return '\n'.join(lines)


def validate(obj):
    errs = []
    if not str(obj.get('title') or '').strip():
        errs.append('缺 title')
    cat = str(obj.get('category') or '').strip()
    if cat not in CATEGORIES:
        errs.append('category「%s」不在约定取值内（%s）' % (cat, '/'.join(CATEGORIES)))
    seen = set()
    for ep in obj.get('episodes') or []:
        try:
            no = int(ep.get('no'))
        except Exception:
            errs.append('有一集缺 no')
            continue
        if no in seen:
            errs.append('第 %d 集重复登记' % no)
        seen.add(no)
        for k, v in (ep.get('stages') or {}).items():
            if k not in STAGES:
                errs.append('第 %d 集有未知阶段「%s」' % (no, k))
            elif v not in (0, 1, 2):
                errs.append('第 %d 集 %s 取值 %r 不合法' % (no, k, v))
    for k, v in (obj.get('stages') or {}).items():
        if k not in STAGES:
            errs.append('项目级有未知阶段「%s」' % k)
        elif v not in (0, 1, 2):
            errs.append('项目级 %s 取值 %r 不合法' % (k, v))
    return errs


MAX_UP = 5


def match_episode(obj, root, here):
    """路径落在哪一集的目录里（取最深匹配）；不在任何集目录下返回 None。"""
    best = None
    target = os.path.normcase(os.path.abspath(here))
    for ep in obj.get('episodes') or []:
        d = str(ep.get('dir') or '').strip().replace('/', os.sep)
        if not d:
            continue
        base = os.path.normcase(os.path.abspath(os.path.join(root, d)))
        if target == base or target.startswith(base + os.sep):
            if best is None or len(base) > best[0]:
                best = (len(base), {
                    'no': ep.get('no'), 'name': ep.get('name', ''), 'dir': d,
                    'stages': dict(ep.get('stages') or {}),
                })
    return best[1] if best else None


def find_project(start, workspace=None):
    """任意文件 / 子目录 → 项目根 + 命中集；找不到给建议落点（工作区下同名目录）。"""
    raw = os.path.abspath(start)
    # 路径按文件处理的判据：已存在的是文件；不存在但带扩展名的也按文件算
    is_file = os.path.isfile(raw) or (not os.path.isdir(raw) and bool(os.path.splitext(raw)[1]))
    here = os.path.dirname(raw) if is_file else raw
    cur, hops = here, 0
    while hops <= MAX_UP:
        m = os.path.join(cur, MANIFEST)
        if os.path.isfile(m):
            out = {'found': True, 'root': cur, 'manifest': m, 'input': raw, 'ups': hops,
                   'manifest_ok': True, 'title': '', 'category': '', 'episode': None}
            try:
                obj = load(cur)
            except ValueError as e:
                out['manifest_ok'] = False
                out['error'] = str(e)
                return out
            if obj:
                obj = norm(obj)
                out['title'] = obj.get('title', '')
                out['category'] = obj.get('category', '')
                out['stages'] = obj.get('stages') or {}
                out['episode'] = match_episode(obj, cur, here)
            return out
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur, hops = parent, hops + 1
    base = os.path.abspath(workspace or os.getcwd())
    if is_file:
        # 散落素材：在工作区下按文件名建项目
        stem = os.path.splitext(os.path.basename(raw))[0] or '项目'
        return {'found': False, 'input': raw, 'workspace': base,
                'suggest_mode': 'new_project', 'suggest_root': os.path.join(base, stem), 'ups': hops}
    # 已组织好的目录：就地建档，不搬动用户的目录结构
    return {'found': False, 'input': raw, 'workspace': base,
            'suggest_mode': 'init_here', 'suggest_root': here, 'ups': hops}


def cmd_context(a):
    res = find_project(a.path, a.workspace)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif res['found']:
        print('项目：%s（%s）' % (res['title'] or os.path.basename(res['root']),
                              res['category'] or '未分类'))
        print('根目录：%s' % res['root'])
        mp = memory_path(res['root'])
        print('记忆：%s' % (mp if os.path.isfile(mp)
                          else '缺失 —— 先建再读：python fxai_project.py memory "%s"' % res['root']))
        if not res['manifest_ok']:
            print('清单：损坏 —— %s' % res.get('error', ''))
        ep = res.get('episode')
        if ep:
            print('命中集：第 %s 集 %s（%s）' % (ep.get('no'), ep.get('name') or '', ep.get('dir')))
        print('登记：python fxai_project.py stage "%s" --<段> 2' % res['root'])
    else:
        print('未定位到项目清单：%s' % res['input'])
        if res.get('suggest_mode') == 'init_here':
            print('就地建档：%s（目录结构已在，补一份清单）' % res['suggest_root'])
        else:
            print('新建项目：%s（工作区下按素材名建目录）' % res['suggest_root'])
        print('建立项目：python fxai_project.py init "%s" --title <名称> --category <类型>' % res['suggest_root'])
    if a.require and not res['found']:
        return 3
    return 0


def main(argv):
    ap = argparse.ArgumentParser(
        prog='fxai_project.py',
        description='项目清单 %s 的读写（技能侧唯一入口）' % MANIFEST,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split('用法：', 1)[1] if '用法：' in __doc__ else '')
    sub = ap.add_subparsers(dest='cmd')

    def add_stage_args(p):
        for k in STAGES:
            p.add_argument('--' + k, type=int, default=None, metavar='0|1|2',
                           help='%s（0 未开始 / 1 部分 / 2 完成）' % STAGE_CN[k])

    p = sub.add_parser('init', help='建档 / 更新标题类型')
    p.add_argument('root')
    p.add_argument('--title', default=None)
    p.add_argument('--category', default=None, help='/'.join(CATEGORIES))
    p.add_argument('--skill', default=None)
    p.add_argument('--summary', default=None)

    p = sub.add_parser('episode', help='登记一集（可同时设该集阶段）')
    p.add_argument('root')
    p.add_argument('--no', type=int, required=True, help='第几集')
    p.add_argument('--name', default=None, help='集名（不含序号）')
    p.add_argument('--dir', dest='dir_name', default=None, help='该集目录名，含序号，如 "1、风尘花魁"')
    add_stage_args(p)

    p = sub.add_parser('stage', help='项目级阶段（无集结构时用）')
    p.add_argument('root')
    add_stage_args(p)

    p = sub.add_parser('deliver', help='登记某集的交付文件名')
    p.add_argument('root')
    p.add_argument('--no', type=int, required=True)
    for k in STAGES:
        p.add_argument('--' + k, default=None, help='%s文件（相对项目根）' % STAGE_CN[k])

    p = sub.add_parser('memory', help='项目记忆：--add 追加一条，不给即打印全文')
    p.add_argument('root')
    p.add_argument('--add', default=None, help='一条记忆，一条一件事')
    p.add_argument('--tag', default='决策', help='/'.join(MEMORY_TAGS))

    p = sub.add_parser('show', help='打印清单')
    p.add_argument('root')
    p.add_argument('--json', action='store_true')

    p = sub.add_parser('context', help='定位项目（任意路径 → 项目根 + 命中集）')
    p.add_argument('path', help='任意文件或子目录路径')
    p.add_argument('--workspace', default=None, help='未定位到项目时的建议落点父目录（默认当前工作目录）')
    p.add_argument('--json', action='store_true')
    p.add_argument('--require', action='store_true', help='未定位到项目时返回 3')

    p = sub.add_parser('check', help='校验清单结构')
    p.add_argument('root')
    p.add_argument('--json', action='store_true')

    a = ap.parse_args(argv[1:])
    if not a.cmd:
        ap.print_help()
        return 2
    if a.cmd == 'context':
        return cmd_context(a)
    root = os.path.abspath(a.root)
    if not os.path.isdir(root):
        print('目录不存在：%s' % root)
        return 2

    try:
        if a.cmd == 'show':
            obj = load(root)
            if obj is None:
                print('%s 不存在（用 init 建档）' % MANIFEST)
                return 1
            obj = norm(obj)
            if a.json:
                print(json.dumps(obj, ensure_ascii=False, indent=2))
            else:
                print(report(obj))
            return 0
        if a.cmd == 'check':
            obj = load(root)
            if obj is None:
                msg = '%s 不存在' % MANIFEST
                print(json.dumps({'ok': False, 'errors': [msg]}, ensure_ascii=False) if a.json else msg)
                return 1
            obj = norm(obj)
            errs = validate(obj)
            has_mem = os.path.isfile(memory_path(root))
            if a.json:
                print(json.dumps({'ok': not errs, 'errors': errs, 'memory': has_mem},
                                 ensure_ascii=False, indent=2))
            else:
                print('清单校验：%s' % ('通过' if not errs else '不合规'))
                for e in errs:
                    print('  - %s' % e)
                print('项目记忆：%s' % ('已建 %s' % MEMORY if has_mem else '缺失（memory 子命令补建）'))
            return 0 if not errs else 1

        fn = {'init': cmd_init, 'episode': cmd_episode, 'stage': cmd_stage,
              'deliver': cmd_deliver, 'memory': cmd_memory}[a.cmd]
        code, msg = fn(root, a)
        print(msg)
        return code
    except ValueError as e:
        print(str(e))
        return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
