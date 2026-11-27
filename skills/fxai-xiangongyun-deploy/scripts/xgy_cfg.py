"""全局配置读取：~/.workbuddy/fxai_global.json。

仙宫云的凭据只认这份全局配置，各技能共用同一份（这也是它存在的意义）。
访问令牌读 xgy_api_token；SSH 连接信息读 xgy_ssh（可选，没有就回落到技能包里的
secrets/xiangongyun.json）。只写一份凭据文件 —— 两份只会互相盖。

🔴 SSH 连接信息有两条来源（2026-11-16 加）：
  - ssh_cred()            读本地配置/文件（旧路径，静态）
  - ssh_cred_from_api()   按实例 ID 从开放平台 API 现取（新路径，免手输密码）
维护实例优先用后者：GET /instance/{id} 的 data 里就带 password/ssh_domain/ssh_port/ssh_user，
不需要人再把 SSH 密码手工塞进任何配置文件。
"""
from __future__ import annotations

import json
import os
import sys


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
    """SSH 连接信息（host/port/user/password/key）—— 本地静态来源。

    全局配置的 xgy_ssh 优先；没配就回落到脚本位置往上找的 secrets/xiangongyun.json。
    🔴 维护实例请优先用 ssh_cred_from_api(iid)：那里的密码是平台实时返回的，
    换实例/改密码都不用动本地文件。本函数只作为「不提供实例 ID 时」的兜底。
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


def current_instance_id():
    """当前绑定的实例 ID（全局配置 xgy_instance_id），没有就空串。

    和 fxai-workflow/xgy_guard.py 的 CFG_KEY 保持同一个键，两处通用。
    """
    return str((cfg() or {}).get("xgy_instance_id") or "").strip()


def ssh_cred_from_api(iid, token=None):
    """按实例 ID 从开放平台 API 现取 SSH 凭证 —— 免手输密码的核心函数。

    数据源：GET /open/instance/{id} 的 data 里直接带：
        password / ssh_domain / ssh_port / ssh_user
    （令牌用 xgy_api_token，不需要任何本地 SSH 配置文件。）

    返回 dict：host / port(int) / user / password / id / status / label。
    实例不在令牌名下、字段缺失、令牌失效都会抛带指引的 RuntimeError。

    用法：
        from xgy_cfg import ssh_cred_from_api
        cred = ssh_cred_from_api("<实例ID>")
    """
    if not iid:
        raise RuntimeError("缺少实例 ID。传 --id <实例ID>，或先用配置绑定：改 ~/.workbuddy/fxai_global.json 的 xgy_instance_id")
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import xgy_api  # 延迟导入，规避 xgy_cfg <-> xgy_api 循环

    tok = token or api_token()
    if not tok:
        raise RuntimeError("缺少访问令牌。在 ~/.workbuddy/fxai_global.json 设 xgy_api_token，或设环境变量 XGY_API_TOKEN")
    try:
        j = xgy_api.get_instance(tok, iid)
    except xgy_api.ApiError as e:
        msg = str(e)
        if ("不存在" in msg or "not found" in msg.lower() or "404" in msg):
            raise RuntimeError(f"实例 {iid} 不在当前令牌名下（可能已销毁或换令牌）。当前令牌下的实例：\n"
                               f"  python xgy_api.py list") from None
        raise RuntimeError(f"查实例 {iid} 失败：{msg}") from None
    inst = xgy_api.pick_instance(j, iid) or {}
    host = inst.get("ssh_domain") or ""
    port = inst.get("ssh_port") or 22
    user = inst.get("ssh_user") or "root"
    pw = inst.get("password") or ""
    if not host:
        raise RuntimeError(f"实例 {iid} 的 API 详情里没有 ssh_domain，无法 SSH。原始状态：{inst.get('status')}")
    if not pw:
        raise RuntimeError(f"实例 {iid} 的 API 详情里没有 password（SSH key 实例？），本函数只支持密码登录")
    return {
        "id": iid,
        "host": host,
        "port": int(port),
        "user": user,
        "password": pw,
        "status": inst.get("status") or "",
        "label": f"{inst.get('name') or iid}（{inst.get('status') or '?'}）",
    }
