#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""客户端软件与登录检测（08 册唯一执行点）。

按「配置 → 路径 → 登录 → 账号库」四步判定，第一个不通过即停，给出该步的结论与用户动作。
只读全局配置里写明的两个目录字段，不枚举目录内容、不按固定目录名扫盘。

用法：
    python check_client.py [--json]

退出码：0 就绪 / 2 未安装或配置不全 / 3 未登录 / 4 账号库缺失
"""
import json
import os
import sys

CONFIG_PATH = os.environ.get("FXAI_GLOBAL_CONFIG") or os.path.join(
    os.path.expanduser("~"), ".workbuddy", "fxai_global.json")

NEXT = {
    "install": [
        "安装凤希AI伴侣客户端（渠道见安装说明第一节：GitHub 仓库 fxai666/fxai-toolkit 与夸克 / 迅雷 / 百度网盘）",
        "按站点「软件配置（全面使用凤AI必看）」与「视频教程」两个区块的步骤装好并登录",
        "客户端 → 本机配置 → 技能运行环境 → 复制智能体安装口令，粘贴回对话",
    ],
    "config": [
        "打开客户端 → 本机配置 → 技能运行环境",
        "确认「软件安装目录」「软件数据目录」两行都读到了值，再点「复制智能体安装口令」",
        "把口令粘贴回对话，由智能体写入 ~/.workbuddy/fxai_global.json",
    ],
    "login": [
        "打开凤希AI伴侣客户端，登录一次（登录成功会重写安装目录下的 userinfo.json）",
        "回到对话说一声，重跑本检测",
    ],
    "db": [
        "在客户端打开一次用到该账号数据的页面（如 AI伙伴 / 项目创作），让客户端建库建表",
        "回到对话说一声，重跑本检测",
    ],
}

STAGE_TITLE = {
    "install": "未检测到客户端",
    "config": "客户端配置不全",
    "login": "未检测到登录",
    "db": "账号数据未初始化",
    "ok": "就绪",
}

STAGE_CODE = {"install": 2, "config": 2, "login": 3, "db": 4, "ok": 0}


def norm_path(value):
    text = str(value or "").strip().replace("\\", "/").rstrip("/")
    return text


def read_config():
    """返回 (数据, 错误说明)。数据为 dict；读不到时返回 (None, 说明)。"""
    if not os.path.isfile(CONFIG_PATH):
        return None, "全局配置文件不存在：%s" % CONFIG_PATH
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "全局配置文件读不了：%s（%s）" % (CONFIG_PATH, exc)
    if not isinstance(data, dict):
        return None, "全局配置不是 JSON 对象：%s" % CONFIG_PATH
    return data, ""


def read_identity(path):
    """只取身份字段，token 不读进返回值。"""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    uid = str(data.get("myid") or "").strip()
    if not uid:
        return None
    return {"uid": uid, "nickname": str(data.get("nickname") or "").strip()}


def detect():
    result = {
        "ok": False, "stage": "install", "code": 2, "title": STAGE_TITLE["install"],
        "detail": "", "config_path": CONFIG_PATH, "install_dir": "", "data_dir": "",
        "identity_path": "", "uid": "", "nickname": "", "db_path": "",
        "next": list(NEXT["install"]),
    }

    cfg, err = read_config()
    if cfg is None:
        result["detail"] = err
        return result

    install = norm_path(cfg.get("software_install_dir") or os.environ.get("FXAI_INSTALL_DIR"))
    data = norm_path(cfg.get("software_data_dir") or os.environ.get("FXAI_DATA_DIR"))
    result["install_dir"] = install
    result["data_dir"] = data

    missing = []
    if not install:
        missing.append("software_install_dir 没有值")
    elif not os.path.isdir(install):
        missing.append("软件安装目录不存在：%s" % install)
    if not data:
        missing.append("software_data_dir 没有值")
    elif not os.path.isdir(data):
        missing.append("软件数据目录不存在：%s" % data)
    if missing:
        result["stage"] = "config"
        result["code"] = STAGE_CODE["config"]
        result["title"] = STAGE_TITLE["config"]
        result["detail"] = "；".join(missing)
        result["next"] = list(NEXT["config"])
        return result

    identity_path = os.path.join(install, "userinfo.json").replace("\\", "/")
    result["identity_path"] = identity_path
    identity = read_identity(identity_path)
    if identity is None:
        result["stage"] = "login"
        result["code"] = STAGE_CODE["login"]
        result["title"] = STAGE_TITLE["login"]
        result["detail"] = "身份文件缺失或没有 myid：%s" % identity_path
        result["next"] = list(NEXT["login"])
        return result

    result["uid"] = identity["uid"]
    result["nickname"] = identity["nickname"]

    db_path = os.path.join(data, "sqlite", "user_%s.db" % identity["uid"]).replace("\\", "/")
    result["db_path"] = db_path
    if not os.path.isfile(db_path):
        result["stage"] = "db"
        result["code"] = STAGE_CODE["db"]
        result["title"] = STAGE_TITLE["db"]
        result["detail"] = "账号库不存在：%s" % db_path
        result["next"] = list(NEXT["db"])
        return result

    result.update({"ok": True, "stage": "ok", "code": 0,
                   "title": STAGE_TITLE["ok"], "detail": "", "next": []})
    return result


def print_text(result):
    who = result["nickname"] or "（未取到昵称）"
    print("[%s] %s" % (result["title"], result["detail"] or "安装目录、数据目录、登录身份、账号库齐全"))
    print("  配置文件：%s" % result["config_path"])
    print("  安装目录：%s" % (result["install_dir"] or "（无）"))
    print("  数据目录：%s" % (result["data_dir"] or "（无）"))
    if result["identity_path"]:
        print("  身份文件：%s" % result["identity_path"])
    if result["uid"]:
        print("  当前登录：%s（用户 %s）" % (who, result["uid"]))
    if result["db_path"]:
        print("  账号库：%s" % result["db_path"])
    if result["next"]:
        print("  用户动作：")
        for idx, step in enumerate(result["next"], 1):
            print("    %d. %s" % (idx, step))
    print("  退出码：%d" % result["code"])


def main():
    as_json = "--json" in sys.argv[1:]
    result = detect()
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text(result)
    return result["code"]


if __name__ == "__main__":
    sys.exit(main())
