#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目目录整洁自检（只读，不删任何文件）。

用法：
    python check_project_clean.py <项目目录> [--json] [--strict]

判据见 references/01-产出铁律.md，与客户端 tool/project/js/projectkit.js 同源：
  - 垃圾：__pycache__ / *.pyc / node_modules / .git / .ipynb_checkpoints /
    Thumbs.db / desktop.ini / ~$* / *.tmp / *.part —— 出现在项目根与交付目录即不合规。
  - 过程目录：`_` 前缀目录（白名单 _base 除外）属中间产物，交付时删除。
  - 归档目录：`_归档*` / `_backup*` / `备份` / `历史版本` / `旧版` 一律不合规（不得留档）。
  - 过程文件：代码 / 日志 / 进程号 / 中间态 / 自检帧，交付时删除。
  - 过程区残留：`_tmp` 等过程区内仍有文件即不合规（交付前清空到 0）。
  - 历史版本：交付目录里同一交付物的旧版 / 变体（去品牌条 / v2 / 备份 / draft…）
    与最终版并存即不合规，判据见 01 册「产物落点」。
  - 分层目录：video / music / doc / audio / output 属旧布局，交付物一律放项目根
    （图片多才允许 image/）。
  - 项目清单：项目根应写 _fxai_project.json（category + title）。
  - 项目记忆：项目根应写 _fxai_memory.md（跨会话续做靠它接口径）。

退出码：0 = 干净；1 = 有不合规项；2 = 目录不存在。
--strict 把「过程目录 / 归档目录 / 过程区残留 / 过程文件 / 分层目录 / 历史版本 / 清单缺失」
一并计入失败。
不删除、不移动、不修改任何文件——只出报告，整改由调用方执行。
"""
import sys

sys.dont_write_bytecode = True

import json
import os
import re

JUNK_DIRS = {
    '__pycache__', 'node_modules', '.git', '.svn', '.hg', '.idea', '.vscode',
    '.vs', '.ipynb_checkpoints', '.pytest_cache', '.mypy_cache', '.ruff_cache',
    '.cache', '.thumbnails', '$recycle.bin', 'system volume information',
    'lost+found', '__macosx',
}
JUNK_FILES = {'thumbs.db', 'ehthumbs.db', 'desktop.ini', '.ds_store'}
JUNK_EXT = {'.pyc', '.pyo', '.pyd', '.tmp', '.temp', '.part', '.crdownload', '.download', '.swp', '.swo'}

# 技能约定的中间产物目录：允许存在，内容不计为交付物；交付时须清空
WORK_DIRS = {'_tmp', '_work', '_cache', '_logs', '_debug'}

# 归档 / 备份类目录：项目内一律不得存在（源码在仓库、成片已交付，本地留副本只占空间）
ARCHIVE_DIR_RE = re.compile(
    r'^(?:_?归档|_?备份|_?backup|_?archive|_?历史版本|_?旧版|_?历史|_?存档)', re.I)

# 「_」开头但确实是交付物的目录（发布/_base 存各平台封面）
PROCESS_DIR_ALLOW = {'_base'}

# 过程文件判据（与 projectkit.js 的 PROCESS_FILE_RE 同源）
PROCESS_FILE_RE = re.compile(
    r'\.(py|pyc|pyo|pyd|log|pid|sh|bat|ps1|cmd|tmp|temp|part|crdownload|swp|swo)$'
    r'|\.whisper_wl\.|\.words$|\.(bak|old|broken)[^.]*$',
    re.I,
)
MEDIA_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.mp4', '.mov', '.mkv', '.avi', '.webm'}
PID_TXT_RE = re.compile(r'(^|_)(pid|log|debug|trace|dryrun)(_|\.|=|$)')

# 历史版本标记：命中即视为旧版候选，由调用方确认后清掉（判据见 01 册「产物落点」）
STALE_WORD_RE = re.compile(
    r'去品牌条|去水印|遮挡|遮罩|修复版|重做|旧版|副本|备份|历史版|draft|wip'
    r'|(?:^|_)old|(?:^|_)bak|(?:^|_)backup|(?:^|_)temp|(?:^|_)test', re.I)
STALE_VER_RE = re.compile(r'(?:^|[_\-])v\d+(?:[_\-]|$)|v\d+(?=\.)|(?:^|[_\-])final[_\-]?\d+', re.I)
DELIVER_EXT = MEDIA_EXT | {'.mp3', '.wav', '.flac', '.m4a', '.pdf', '.md', '.docx', '.pptx', '.xlsx'}

# 旧的分层布局目录：交付物一律放项目根，图片多才允许 image/
LAYER_DIRS = {'video', 'videos', 'music', 'doc', 'docs', 'audio', 'output', 'outputs', '成片', '视频', '音频'}

MANIFEST = '_fxai_project.json'
MEMORY = '_fxai_memory.md'
try:
    from catkit import CATEGORIES as _CATEGORIES  # 类别表唯一事实源
    CATEGORIES = set(_CATEGORIES)
except ImportError:  # 类别表缺失时兜底
    CATEGORIES = {'drama', 'anime', 'mv', 'music', 'image', 'voiceover', 'slide',
                  'ad', 'novel', 'wechat', 'remake', 'other'}

MAX_DEPTH = 8


def is_junk_file(name):
    low = name.lower()
    if low in JUNK_FILES:
        return True
    if low.startswith('~$') or low.endswith('~'):
        return True
    return os.path.splitext(low)[1] in JUNK_EXT


def is_process_file(name):
    low = name.lower()
    if PROCESS_FILE_RE.search(low):
        return True
    if low.startswith('_') and os.path.splitext(low)[1] in MEDIA_EXT:
        return True
    if low.endswith('.txt') and PID_TXT_RE.search(low):
        return True
    return False


def is_stale_version(name):
    """交付目录里疑似历史版本的文件名（只读判据，命中不等于必须删）。"""
    if os.path.splitext(name.lower())[1] not in DELIVER_EXT:
        return False
    return bool(STALE_WORD_RE.search(name) or STALE_VER_RE.search(name))


def scan(root):
    junk, work, stray, procs, layers, stale = [], [], [], [], [], []
    for cur, dirs, files in os.walk(root):
        rel = os.path.relpath(cur, root)
        rel = '' if rel == '.' else rel.replace('\\', '/')
        depth = 0 if not rel else rel.count('/') + 1
        keep = []
        for d in sorted(dirs):
            low = d.lower()
            path = (rel + '/' + d).lstrip('/')
            if ARCHIVE_DIR_RE.match(low):
                junk.append({'path': path, 'kind': 'dir', 'why': '归档/备份目录（交付即删，不得留档）'})
                continue
            if low in WORK_DIRS:
                work.append(path)
                continue
            if low in JUNK_DIRS:
                junk.append({'path': path, 'kind': 'dir', 'why': d})
                continue
            if d.startswith('_') and low not in PROCESS_DIR_ALLOW:
                # 整目录只报一次，内部文件逐条跳过
                stray.append(path)
                continue
            if low in LAYER_DIRS:
                layers.append(path)
            keep.append(d)
        dirs[:] = keep if depth < MAX_DEPTH else []
        for f in sorted(files):
            path = (rel + '/' + f).lstrip('/')
            if is_junk_file(f):
                junk.append({'path': path, 'kind': 'file', 'why': f})
            elif is_process_file(f):
                procs.append(path)
            elif is_stale_version(f):
                stale.append(path)
    # 过程区残留：_tmp 等交付时应清空到 0
    tmp_left = {'files': 0, 'bytes': 0}
    for w in work:
        for cur, _, fs in os.walk(os.path.join(root, w.replace('/', os.sep))):
            for f in fs:
                tmp_left['files'] += 1
                try:
                    tmp_left['bytes'] += os.path.getsize(os.path.join(cur, f))
                except OSError:
                    pass
    return junk, work, stray, procs, layers, stale, tmp_left


def check_manifest(root):
    p = os.path.join(root, MANIFEST)
    if not os.path.isfile(p):
        return {'ok': False, 'msg': '%s 缺失：页面无法识别项目类型' % MANIFEST}
    try:
        with open(p, 'r', encoding='utf-8') as fp:
            obj = json.load(fp)
    except Exception as e:
        return {'ok': False, 'msg': '%s 解析失败：%s' % (MANIFEST, e)}
    if not isinstance(obj, dict):
        return {'ok': False, 'msg': '%s 不是对象' % MANIFEST}
    cat = str(obj.get('category') or obj.get('type') or '').strip()
    if not cat:
        return {'ok': False, 'msg': '%s 缺 category' % MANIFEST}
    if cat not in CATEGORIES:
        return {'ok': False, 'msg': '%s 的 category「%s」不在约定取值内' % (MANIFEST, cat)}
    if not str(obj.get('title') or '').strip():
        return {'ok': False, 'msg': '%s 缺 title' % MANIFEST}
    return {'ok': True, 'msg': ''}


def check_memory(root):
    p = os.path.join(root, MEMORY)
    if not os.path.isfile(p):
        return {'ok': False, 'msg': '%s 缺失：跨会话续做无口径可接' % MEMORY}
    try:
        with open(p, 'r', encoding='utf-8') as fp:
            body = fp.read()
    except Exception as e:
        return {'ok': False, 'msg': '%s 读取失败：%s' % (MEMORY, e)}
    if not body.strip():
        return {'ok': False, 'msg': '%s 为空' % MEMORY}
    return {'ok': True, 'msg': '%d 字符' % len(body)}


def brief(items, limit=15):
    lines = ['  ' + str(x) for x in items[:limit]]
    if len(items) > limit:
        lines.append('  …另有 %d 项，加 --json 看全量' % (len(items) - limit))
    return lines


def main(argv):
    args = [a for a in argv[1:] if not a.startswith('-')]
    as_json = '--json' in argv
    strict = '--strict' in argv
    if not args:
        print(__doc__)
        return 2
    root = os.path.abspath(args[0])
    if not os.path.isdir(root):
        print('目录不存在：%s' % root)
        return 2

    junk, work, stray, procs, layers, stale, tmp_left = scan(root)
    man = check_manifest(root)
    mem = check_memory(root)
    soft = (len(stray) + len(procs) + len(layers) + len(stale)
            + (1 if tmp_left['files'] else 0)
            + (0 if man['ok'] else 1) + (0 if mem['ok'] else 1))
    failed = bool(junk) or (strict and soft > 0)

    if as_json:
        print(json.dumps({
            'root': root,
            'ok': not failed,
            'junk': junk,
            'work_dirs': work,
            'tmp_left': tmp_left,
            'stray_dirs': stray,
            'process_files': procs,
            'layer_dirs': layers,
            'stale_versions': stale,
            'manifest': man,
            'memory': mem,
        }, ensure_ascii=False, indent=2))
        return 1 if failed else 0

    print('项目目录：%s' % root)
    if junk:
        print('不合规项 %d 个（交付即删，不留档、不迁 _tmp）：' % len(junk))
        for j in junk:
            print('  [%s] %s    <- %s' % ('目录' if j['kind'] == 'dir' else '文件', j['path'], j['why']))
    else:
        print('垃圾文件：干净（无 __pycache__ / .git / *.pyc / Thumbs.db 等）')
    print('过程区：%s' % ('、'.join(work) if work else '无'))
    if tmp_left['files']:
        print('过程区残留 %d 个文件 / %.1f MB（交付前清空到 0，见 01 册「交付即清中间产物」）'
              % (tmp_left['files'], tmp_left['bytes'] / 1048576.0))
    if stray:
        print('过程/备份目录 %d 个（_ 前缀，交付时删除）：' % len(stray))
        print('\n'.join(brief(stray)))
    if procs:
        print('过程文件 %d 个（代码/日志/自检帧，交付时删除）：' % len(procs))
        print('\n'.join(brief(procs)))
    if layers:
        print('分层目录 %d 个（旧布局，交付物放项目根；图片多才允许 image/）：' % len(layers))
        print('\n'.join(brief(layers)))
    if stale:
        print('疑似历史版本 %d 个（交付结果只留最终版，见 01 册「产物落点」）：' % len(stale))
        print('\n'.join(brief(stale)))
    print('项目清单：%s' % ('%s OK' % MANIFEST if man['ok'] else 'WARN ' + man['msg']))
    print('项目记忆：%s' % ('%s OK（%s）' % (MEMORY, mem['msg']) if mem['ok'] else 'WARN ' + mem['msg']))
    tail = '不合规，按上面清单整改后重跑' if failed else '通过'
    if not failed and soft:
        tail = '通过；过程文件/清单有 %d 项提示，交付前用 --strict 收紧' % soft
    print('结论：%s' % tail)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
