#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""类别表读取：`fxai-standard/categories.json` 是项目类别的唯一事实源。

fxai_project.py / check_project_clean.py / check_standard.py / sync_categories.py 共用本模块；
加类别只改那份 JSON，其余位置由它推导。
"""
import io
import json
import os
import sys

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
CATEGORY_FILE = os.path.normpath(os.path.join(HERE, os.pardir, 'categories.json'))
STAGE_KEYS = ('script', 'image', 'audio', 'video', 'publish')

# 类别表缺失时的兜底取值（只保脚本能跑，判据以 JSON 为准）
FALLBACK_ORDER = ('drama', 'anime', 'mv', 'music', 'image', 'voiceover', 'ad', 'novel', 'wechat', 'remake', 'other')


def load():
    """返回 (order, items)。表缺失或损坏时退到兜底顺序。"""
    try:
        with io.open(CATEGORY_FILE, 'r', encoding='utf-8') as fp:
            data = json.load(fp)
    except (OSError, ValueError):
        return list(FALLBACK_ORDER), {}
    order = [str(k) for k in (data.get('order') or []) if str(k)]
    items = data.get('items') or {}
    if not order or not isinstance(items, dict):
        return list(FALLBACK_ORDER), {}
    return order, items


def categories():
    """合法类别取值，按展示顺序。"""
    return tuple(load()[0])


def item(key):
    return load()[1].get(key) or {}


def name_of(key):
    return item(key).get('name') or key


def stages_of(key):
    """该类别默认参与哪几段。"""
    return tuple(item(key).get('stages') or STAGE_KEYS)


def skill_of(key):
    """该类别默认对接的技能名。"""
    return item(key).get('skill') or ''


def skill_to_category():
    """技能名 → 类别，用于校验 PRODUCERS 登记。"""
    out = {}
    for key in load()[0]:
        sk = skill_of(key)
        if sk:
            out.setdefault(sk, key)
    return out


# 兼容别名：fxai_project.py / check_project_clean.py 是 `from catkit import CATEGORIES`。
# 没有这个别名时 import 失败，两个脚本会一直吃自己那份硬编码兜底，categories.json 改了也不生效。
CATEGORIES = categories()


if __name__ == '__main__':
    order, items = load()
    print('类别表：%s' % CATEGORY_FILE)
    print('共 %d 类：%s' % (len(order), ' / '.join('%s(%s)' % (k, name_of(k)) for k in order)))
    for k in order:
        print('  %-10s %-4s %-24s 阶段 %s  技能 %s'
              % (k, name_of(k), item(k).get('icon') or '-',
                 '/'.join(stages_of(k)), skill_of(k) or '-'))
