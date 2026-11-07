#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""专用解析器骨架。复制为 <解析器>.py 后按填位补齐。"""
import sys
sys.dont_write_bytecode = True

import core

NAME = "<解析器标识>"
LABEL = "<解析器名称>"
HOSTS = ("<链接特征>",)
BOOTSTRAP = ()
PRIORITY = 50


def match(url):
    """命中本解析器适用范围返回 True。"""
    return bool(url) and any(h in url for h in HOSTS)


def collect(url, raw, opts):
    """返回资源描述：site / id / title / urls / images / source_url。"""
    return {
        "site": NAME,
        "id": "<素材 ID>",
        "title": "<标题>",
        "urls": ["<候选媒体地址>"],
        "images": [],
        "source_url": url,
        "final_url": url,
    }


def selftest_adapter():
    """离线自检，构造本地数据校验解析函数。"""
    return True
