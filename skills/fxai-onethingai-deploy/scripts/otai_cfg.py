"""全局配置读取：~/.workbuddy/fxai_global.json。

OneThingAI 的凭据只认这份全局配置，各技能共用同一份。
凭据统一收在全局配置的 `otai` 节点里（api_token / ssh），
兼容旧的扁平写法 otai_api_token / otai_ssh（读到节点即只取嵌套写法）。
只写一份凭据文件 —— 两份只会互相盖。
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


def save_cfg(patch: dict):
    """把 patch 合并写回全局配置（浅合并，顶层键覆盖）。"""
    p = cfg_path()
    cur = cfg()
    if not isinstance(cur, dict):
        cur = {}
    cur.update(patch or {})
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return cur


def _otai_node():
    """全局配置里的 otai 节点（OneThingAI 统一登记处）。"""
    g = cfg() or {}
    node = g.get("otai")
    return node if isinstance(node, dict) else {}


def api_token():
    """开发者密钥：环境变量 OTAI_API_TOKEN 优先，其次 otai.api_token。"""
    env = os.environ.get("OTAI_API_TOKEN", "")
    if env:
        return env
    g = cfg() or {}
    return _otai_node().get("api_token") or g.get("otai_api_token") or ""


def ssh_cred():
    """SSH 连接信息（host/port/user/password/key）。otai.ssh 优先，兼容扁平 otai_ssh。"""
    g = cfg() or {}
    s = _otai_node().get("ssh")
    if isinstance(s, dict) and s.get("host"):
        return s
    if isinstance(g.get("otai_ssh"), dict) and g["otai_ssh"].get("host"):
        return g["otai_ssh"]
    return {}
