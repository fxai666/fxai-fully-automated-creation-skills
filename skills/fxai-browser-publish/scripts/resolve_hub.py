#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析发布实例目录指针（跨账号 / 多客户端共享的唯一入口）。

归属原则：
  - 技能包只写「怎么找」，不写「在哪」；不含站点名、域名、账号、凭据与本机绝对路径。
  - 实例目录指针写在**全局配置** ~/.workbuddy/fxai_global.json 的 publish_hub.hub_dir。
    全局配置在技能目录之外，换账号 / 换客户端登录后依然能读到。
  - 环境变量 FXAI_PUBLISH_HUB 只用于本次进程覆盖，不落盘、不改全局配置。
  - 站点条目、账号信息、凭据全在实例目录内，不在全局配置、不在技能包。
  - 本脚本只读取全局配置的 publish_hub 一段，绝不枚举或打印其他字段
    （全局配置里另有 token 等敏感项）。

用法：
    python -B resolve_hub.py                 # 人类可读自检
    python -B resolve_hub.py --json          # 机器可读，输出路径、来源与站点清单
    python -B resolve_hub.py --check         # 定位失败或关键文件缺失时退出码 2
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ENV = "FXAI_PUBLISH_HUB"
GLOBAL_CONFIG_REL = Path(".workbuddy") / "fxai_global.json"
GLOBAL_SECTION = "publish_hub"
SKILL_NAME = "fxai-browser-publish"
SKILL_DIR = Path(__file__).resolve().parent.parent
# 新实例用 sites；既有实例沿用 platforms 时同样识别，接入新站点按该实例既有键落记录。
SITE_SECTIONS = ("sites", "platforms")


def global_config_path() -> Path:
    return Path.home() / GLOBAL_CONFIG_REL


def read_global_hub() -> tuple[str | None, str]:
    """只读 publish_hub.hub_dir，不碰同文件里其他字段。"""
    p = global_config_path()
    if not p.is_file():
        return None, "全局配置不存在"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return None, f"全局配置读不了（{type(e).__name__}）"
    if not isinstance(data, dict):
        return None, "全局配置不是 JSON 对象"
    sec = data.get(GLOBAL_SECTION)
    if not isinstance(sec, dict):
        return None, f"全局配置里没有 {GLOBAL_SECTION} 段"
    val = sec.get("hub_dir")
    if not val or not isinstance(val, str):
        return None, f"{GLOBAL_SECTION}.hub_dir 未配置"
    return val.strip(), "OK"


def shared_config() -> dict:
    """技能包内的共享规则：只放默认值与字段名，不含路径、站点或凭据。"""
    for name in ("config.json", "config.example.json"):
        p = SKILL_DIR / name
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
    return {}


def resolve() -> dict:
    raw = os.environ.get(ENV, "").strip().strip('"').strip("'")
    if raw:
        hub_raw, source, hint = raw, "env", "临时覆盖（未落盘）"
    else:
        val, why = read_global_hub()
        if not val:
            raise SystemExit(
                f"无法定位实例目录：{why}。\n"
                f"先跑一次初始化：python -B scripts/init_hub.py --hub \"<发布目录>\"\n"
                f"或在全局配置 {GLOBAL_CONFIG_REL} 里配置：\n"
                f'  "publish_hub": {{ "hub_dir": "<发布目录绝对路径>", '
                f'"skill": "{SKILL_NAME}" }}\n'
                f"该文件在技能目录之外，换账号登录后仍然有效；也可临时用 {ENV} 覆盖。"
            )
        hub_raw, source, hint = val, "global_config", "全局配置（跨账号共享）"

    hub = Path(hub_raw).expanduser()
    hub = hub.resolve() if hub.is_absolute() else (Path.home() / hub).resolve()

    cfg = shared_config()
    sites: list[str] = []
    sites_section = ""
    platforms_file = hub / "config" / "platforms.json"
    sites_dir = hub / "config" / "sites"
    if platforms_file.is_file():
        try:
            data = json.loads(platforms_file.read_text(encoding="utf-8"))
            for key in SITE_SECTIONS:
                block = data.get(key)
                if isinstance(block, dict) and block:
                    sites = [k for k, v in block.items()
                             if isinstance(v, dict) and v.get("enabled", True)]
                    sites_section = key
                    break
        except (json.JSONDecodeError, OSError, AttributeError):
            pass
    # 站点各自一个文件：目录里有几个可用文件就是几个站点，
    # 全局文件只放默认规则，不再承载站点集合。
    if sites_dir.is_dir():
        file_sites = []
        for fp in sorted(sites_dir.glob("*.json")):
            if fp.name.startswith("_"):
                continue
            try:
                entry = json.loads(fp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(entry, dict) and entry.get("enabled", True):
                file_sites.append(fp.stem)
        if file_sites:
            sites = file_sites
            sites_section = "sites"
    accounts_file = hub / "config" / "accounts.json"
    return {
        "source": source,
        "source_hint": hint,
        "skill": SKILL_NAME,
        "global_config": f"~/{GLOBAL_CONFIG_REL.as_posix()}",
        "global_section": GLOBAL_SECTION,
        "hub": str(hub),
        "platforms_config": str(platforms_file),
        "sites_dir": str(sites_dir),
        "accounts_registry": str(accounts_file),
        "tasks_db": str(hub / "tasks.db"),
        "hub_exists": hub.is_dir(),
        "platforms_config_exists": platforms_file.is_file(),
        "sites_dir_exists": sites_dir.is_dir(),
        "accounts_registry_exists": accounts_file.is_file(),
        "sites": sites,
        "sites_section": sites_section,
        "site_count": len(sites),
        "require_release_sheet": bool(cfg.get("defaults", {}).get("require_release_sheet", True)),
        "dry_run_by_default": bool(cfg.get("defaults", {}).get("dry_run_by_default", True)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="解析发布实例目录指针")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--check", action="store_true", help="定位失败或关键文件缺失时返回退出码 2")
    a = ap.parse_args()
    info = resolve()
    ok = (
        info["hub_exists"]
        and info["platforms_config_exists"]
        and info["accounts_registry_exists"]
    )
    if a.json:
        info["ok"] = ok
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(f"技能            : {info['skill']}")
        print(f"来源            : {info['source']}（{info['source_hint']}）")
        print(f"全局配置        : {info['global_config']} 的 {info['global_section']} 段")
        if os.environ.get(ENV):
            print(f"{ENV} = 已设置（临时覆盖）")
        for k in ("hub", "platforms_config", "accounts_registry", "tasks_db"):
            print(f"  {k:<20}: {info[k]}")
        print(f"  已启用站点数      : {info['site_count']}（台账键 {info['sites_section'] or '未识别'}）")
        print(f"  发布单闸门        : {'开启（必须先出单待确认）' if info['require_release_sheet'] else '关闭'}")
        print(f"  默认 dry-run      : {'是' if info['dry_run_by_default'] else '否'}")
        print(f"  自检              : {'OK' if ok else '不完整，先跑 init_hub.py'}")
    if a.check and not ok:
        return 2
    return 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.exit(main())
