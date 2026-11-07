"""全局配置读取：~/.workbuddy/fxai_global.json。

仙宫云的凭据只认这份全局配置，各技能共用同一份（这也是它存在的意义）。
访问令牌读 xgy_api_token；SSH 连接信息读 xgy_ssh（可选，没有就回落到技能包里的
secrets/xiangongyun.json）。只写一份凭据文件 —— 两份只会互相盖。
"""
from __future__ import annotations

import json
import os


def cfg_path():
    """全局配置自身的路径；FXAI_GLOBAL_CONFIG 指向别处时以它为准。"""
    return os.environ.get(
        "FXAI_GLOBAL_CONFIG",
        os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json"))


def cfg():
    p = cfg_path()
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def api_token():
    """开放平台访问令牌：环境变量 XGY_API_TOKEN 优先，其次全局配置的 xgy_api_token。"""
    env = os.environ.get("XGY_API_TOKEN", "")
    if env:
        return env
    return (cfg() or {}).get("xgy_api_token") or ""


def ssh_cred():
    """SSH 连接信息（host/port/user/password/key）。

    全局配置的 xgy_ssh 优先；没配就回落到脚本位置往上找的 secrets/xiangongyun.json。
    """
    g = cfg() or {}
    if isinstance(g.get("xgy_ssh"), dict) and g["xgy_ssh"].get("host"):
        return g["xgy_ssh"]
    here = os.path.dirname(os.path.abspath(__file__))
    for up in range(0, 7):
        p = os.path.join(*([here] + [".."] * up), "secrets", "xiangongyun.json")
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f) or {}
            except Exception:
                continue
            prof = d.get("default") if isinstance(d.get("default"), dict) else d
            if prof.get("host"):
                return prof
    return {}
