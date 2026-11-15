# -*- coding: utf-8 -*-
"""工作流发布 / 品牌条 的全局配置读取（自包含，不依赖仙宫云技能）。

唯一配置源：~/.workbuddy/fxai_global.json（FXAI_GLOBAL_CONFIG 可改位置）。
顶层块 `workflows`（通用，仙宫云 / 其它发布渠道共用），盘符路径按本机实际填，
代码侧一律经本模块解析，不在脚本里写死：

    "workflows": {
        "comfy_root": "<本机 ComfyUI 安装目录>",
        "dev_dir":    "user/default/workflows",
        "release_dir":"user/default/workflows/发布版",
        "banner_real":      "fxai_workflow_banner.png",
        "banner_placeholder":"fxai_workflow_banner_placeholder.png",
        "secrets":  "<实例密钥 json 的绝对路径>"
    }

解析规则：comfy_root + 相对子目录 → 绝对路径；缺 comfy_root 时子目录按相对当前目录。
旧扁平键 `xgy_workflow_src` / `xgy_secrets` 仍作向后兼容回退（仅 sync 用）。
环境变量 FXAI_WORKFLOW_RELEASE_DIR / FXAI_COMFY_ROOT 优先于配置文件。
"""
from __future__ import annotations

import json
import os


def cfg_path() -> str:
    return os.environ.get(
        "FXAI_GLOBAL_CONFIG",
        os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json"))


def cfg() -> dict:
    try:
        with open(cfg_path(), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _join_base(base: str, sub: str) -> str:
    if not sub:
        return base
    if os.path.isabs(sub):
        return sub
    return os.path.join(base, sub)


def workflows_cfg() -> dict:
    """返回规整后的工作流配置字典（键缺省给默认值）。"""
    g = cfg() or {}
    wf = g.get("workflows") if isinstance(g.get("workflows"), dict) else {}

    base = os.environ.get("FXAI_COMFY_ROOT", "") or wf.get("comfy_root") or ""

    def resolve(sub: str, fallback: str = "") -> str:
        if sub and os.path.isabs(sub):
            return sub
        v = _join_base(base, sub or fallback) if base else (sub or "")
        # 没配 base 又给了相对子目录时，落到当前目录
        if not base and (sub or fallback):
            return _join_base(os.getcwd(), sub or fallback)
        return v

    # release_dir：优先 workflows 块；缺省时回退旧的绝对路径键 xgy_workflow_src
    release_dir = wf.get("release_dir", "")
    if not release_dir:
        legacy = g.get("xgy_workflow_src", "")
        if legacy and os.path.isabs(legacy):
            release_dir = legacy
    elif os.path.isabs(release_dir):
        pass
    else:
        release_dir = _join_base(base, release_dir) if base else _join_base(os.getcwd(), release_dir)

    secrets = wf.get("secrets") or g.get("xgy_secrets") or ""

    return {
        "comfy_root": base,
        "dev_dir": resolve(wf.get("dev_dir", ""), "user/default/workflows"),
        "release_dir": release_dir,
        "banner_real": wf.get("banner_real", "fxai_workflow_banner.png"),
        "banner_placeholder": wf.get("banner_placeholder", "fxai_workflow_banner_placeholder.png"),
        "secrets": secrets,
        # 向后兼容：旧扁平键
        "xgy_workflow_src": g.get("xgy_workflow_src", ""),
        "xgy_secrets": g.get("xgy_secrets", ""),
    }


def release_dir() -> str:
    """发布版目录绝对路径（环境变量优先，其次配置，最后报错留空）。"""
    env = os.environ.get("FXAI_WORKFLOW_RELEASE_DIR", "")
    if env and os.path.isdir(env):
        return env
    w = workflows_cfg()
    rd = w.get("release_dir", "")
    if rd and os.path.isdir(rd):
        return rd
    return ""
