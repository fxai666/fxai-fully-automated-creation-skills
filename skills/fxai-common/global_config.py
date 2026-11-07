#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用户级全局配置：所有技能共用的一份本机数据。

落点（唯一）：~/.workbuddy/fxai_global.json
可用环境变量 FXAI_GLOBAL_CONFIG 改落点（多宿主 / 测试用）。

规则：
- 本机路径（python 解释器、logo / 图标 / 头像、字体目录）一律写在这里，技能源码与文档不得写死。
- 全局配置只存本机数据，不进分发包；包里只带 fxai_global.example.json 空模板。
- 读取一律走本模块，取不到时给通用兜底（当前解释器 / 系统字体目录 / 空串），不猜盘符。
- --init 用于安装环节：探测本机数据、合并写入全局配置，已存在的键不覆盖。

用法：
    from global_config import get, python_exe, brand_asset, font_dir
    python global_config.py --init       # 安装时采集并写入
    python global_config.py --show       # 打印当前采集口径
"""
import argparse
import json
import os
import sys

CONFIG_NAME = "fxai_global.json"
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")

# 安装时需要采集的本机数据键（点路径）。logo / 图标 / 头像由安装者提供，不自动猜路径。
COLLECT_KEYS = (
    "python_exe",
    "brand.logo_ico",
    "brand.logo_svg",
    "brand.avatar_512",
    "brand.font_dir",
)


def config_path():
    p = os.environ.get("FXAI_GLOBAL_CONFIG", "").strip()
    if p:
        return os.path.abspath(os.path.expanduser(p))
    return os.path.join(os.path.expanduser("~"), ".workbuddy", CONFIG_NAME)


def load():
    """读整份配置；文件缺失或解码失败一律返回空字典，不抛异常。"""
    p = config_path()
    if not os.path.isfile(p):
        return {}
    for enc in ENCODINGS:
        try:
            with open(p, encoding=enc) as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (UnicodeDecodeError, ValueError):
            continue
        except OSError:
            return {}
    return {}


def get(key, default=None, cfg=None):
    """按键取值，支持点路径（如 brand.logo_ico）。缺键返回 default。"""
    c = cfg if cfg is not None else load()
    cur = c
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    if cur in ("", None) and default is not None:
        return default
    return cur


def python_exe(cfg=None):
    """本机 Python 解释器：python_exe > whisper_py > 当前解释器。"""
    c = cfg if cfg is not None else load()
    return get("python_exe", "", c) or get("whisper_py", "", c) or sys.executable


def brand_asset(key, default="", cfg=None):
    """品牌资源路径：brand.<key>（logo_ico / logo_svg / avatar_512 / font_dir ...）。"""
    c = cfg if cfg is not None else load()
    return get("brand.%s" % key, default, c)


def font_dir(cfg=None):
    """字体目录：brand.font_dir > 系统字体目录。"""
    c = cfg if cfg is not None else load()
    d = brand_asset("font_dir", "", c)
    if d:
        return d
    windir = (os.environ.get("WINDIR") or os.environ.get("SystemRoot")
              or os.environ.get("SYSTEMROOT") or "")
    return os.path.join(windir, "Fonts") if windir else ""


def _put(cfg, key, value):
    """按点路径写值；键已存在且非空时不覆盖。返回是否写入。"""
    parts = key.split(".")
    cur = cfg
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    old = cur.get(parts[-1])
    if old not in ("", None):
        return False
    cur[parts[-1]] = value
    return True


def collect():
    """探测可自动判定的本机数据；logo / 图标 / 头像留给安装者填。"""
    found = {"python_exe": sys.executable}
    d = font_dir(load())
    if d:
        found["brand.font_dir"] = d
    return found


def init_config():
    """安装环节调用：合并采集结果，打印待补键，返回 (路径, 写入键列表)。"""
    p = config_path()
    cfg = load()
    wrote = []
    for key, val in collect().items():
        if _put(cfg, key, val):
            wrote.append(key)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return p, wrote


def main():
    ap = argparse.ArgumentParser(description="凤希AI 全局配置采集与查看")
    ap.add_argument("--init", action="store_true", help="采集本机数据并合并写入全局配置")
    ap.add_argument("--show", action="store_true", help="打印当前全局配置采集口径")
    a = ap.parse_args()
    if a.init:
        p, wrote = init_config()
        print("全局配置：%s" % p)
        print("已写入：%s" % ("、".join(wrote) or "（无新增，键均已存在）"))
        cfg = load()
        missing = [k for k in COLLECT_KEYS if not get(k, "", cfg)]
        if missing:
            print("待安装者补填：%s" % "、".join(missing))
            print("补填方式：把文件实际路径写进全局配置对应键；logo / 图标 / 头像不得用生成图替代。")
        else:
            print("采集键齐全。")
        return 0
    if a.show:
        cfg = load()
        print("全局配置：%s" % config_path())
        for k in COLLECT_KEYS:
            print("  %-20s %s" % (k, get(k, "", cfg) or "（未填）"))
        print("  %-20s %s" % ("python_exe(生效)", python_exe(cfg)))
        print("  %-20s %s" % ("font_dir(生效)", font_dir(cfg)))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
