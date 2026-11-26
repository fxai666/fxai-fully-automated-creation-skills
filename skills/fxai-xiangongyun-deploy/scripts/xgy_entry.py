#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安装入口：判定本地 / 仙宫云，分流两条配置路径，支持随时切换，只读探测不建机。

用法：
    python xgy_entry.py                 文本报告
    python xgy_entry.py --json          机器读
    python xgy_entry.py use local       切回本机 ComfyUI（写 comfy_url）
    python xgy_entry.py use cloud <id>  切到云端实例（写 comfy_url + 绑定 id）
    python xgy_entry.py set-token       从标准输入读访问令牌写入全局配置（不回显）

判据全部复用现成实现：令牌与实例读 xgy_cfg，令牌验身与「咱们镜像机器」读 xgy_api，
comfy_url 是否云端按 xgy_guard 的容器域名判据。本脚本不另造令牌机制、不建机、不扣费。
"""
from __future__ import annotations

import json
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xgy_api  # noqa: E402
import xgy_cfg  # noqa: E402

CLOUD_HOST = ".container.x-gpu.com"  # 与 xgy_guard.is_xgpu 同一判据
LOCAL_HOSTS = ("127.0.0.1", "localhost", "host.docker.internal")
LOCAL_COMFY = "http://127.0.0.1:8188"


def cloud_comfy(iid):
    """实例 ID → 云端 ComfyUI 地址（容器域名规则与 xgy_guard.instance_id_of 互逆）。"""
    return "https://%s-8188%s" % (iid, CLOUD_HOST)


def classify(url):
    """按 comfy_url 判当前模式：local / cloud / undecided。"""
    text = str(url or "").strip()
    if not text:
        return "undecided"
    low = text.lower()
    if CLOUD_HOST in low:
        return "cloud"
    if any(h in low for h in LOCAL_HOSTS):
        return "local"
    return "undecided"


def probe_cloud(token):
    """有令牌才现查账号与咱们镜像的机器；只读，失败给原因不中断。"""
    out = {"checked": False, "account": "", "own_instances": [], "error": ""}
    if not token:
        return out
    try:
        who = xgy_api.get_whoami(token)
        me = who.get("data") or {}
        out["account"] = str(me.get("nickname") or me.get("uuid") or "")
        own = xgy_api.own_image_instances(token)
        out["own_instances"] = [
            {"id": str(i.get("id") or ""),
             "status": str(i.get("status") or ""),
             "name": str(i.get("name") or "")}
            for i in own
        ]
        out["checked"] = True
    except Exception as exc:
        out["error"] = str(exc)[:200]
    return out


def detect():
    cfg = xgy_cfg.cfg() or {}
    token = xgy_cfg.api_token()
    url = str(cfg.get("comfy_url") or "")
    iid = xgy_cfg.current_instance_id()
    mode = classify(url)
    cloud = probe_cloud(token)

    paths = []
    if not token:
        paths.append({
            "key": "token",
            "title": "路径一 · 复制访问令牌（自己配）",
            "steps": [
                "注册 / 登录仙宫云：" + xgy_api.XGY_REG_URL,
                "控制台「访问令牌」页生成令牌（形如 eyJ...，不是 SSH 密码、不是 api_key）",
                "对话里说「用仙宫云」，把令牌贴进来交给助手写入全局配置，或跑 python xgy_entry.py set-token",
                "再跑 python xgy_api.py check 做接入体检",
            ],
        })
        paths.append({
            "key": "auto",
            "title": "路径二 · 自动安装与部署（助手带你走完）",
            "steps": [
                "对话里说「用仙宫云，帮我自动配置」，助手按注册 / 登录链接引导拿令牌",
                "令牌到位后由助手跑 python xgy_api.py check：验令牌 → 列咱们镜像的机器 → 检测是否已有可用机器",
                "没有机器时助手给建机请求体，你确认后才加 --yes 真建（实时扣费）；建完绑定实例并验四服务",
                "全程可一句话切换：说「改用本机」或「用仙宫云」即改安装模式",
            ],
        })
    elif not cloud.get("own_instances"):
        paths.append({
            "key": "machine",
            "title": "令牌已就绪 · 还缺咱们镜像的机器",
            "steps": [
                "跑 python xgy_api.py check 看完整建机演练请求体",
                "确认后 python xgy_api.py deploy-image " + xgy_api.OWN_IMAGE_ID + " --yes",
                "建完 python xgy_guard.py use <新实例ID> 绑定，再 python xgy_api.py verify-services <新实例ID>",
            ],
        })
    else:
        paths.append({
            "key": "ready",
            "title": "令牌与咱们镜像的机器都就绪",
            "steps": [
                "跑 python xgy_api.py check 列出可用机器与绑定命令",
                "绑定一台：python xgy_entry.py use cloud <实例ID>",
                "验四服务：python xgy_api.py verify-services <实例ID>",
            ],
        })

    if mode == "local":
        verdict = "当前是本机部署（comfy_url 指向本机）。本地就绪检测跑 python check_client.py。"
    elif mode == "cloud":
        verdict = "当前是仙宫云部署（comfy_url 指向云端容器）。云端运维跑 python xgy_api.py check。"
    else:
        verdict = "还没定安装模式。选本机就说「用本机」，选仙宫云就说「用仙宫云」，助手会写入 comfy_url。"

    return {
        "mode": mode,
        "comfy_url": url,
        "token_status": "present" if token else "missing",
        "instance_id": iid,
        "image_id": xgy_api.OWN_IMAGE_ID,
        "cloud": cloud,
        "verdict": verdict,
        "paths": paths,
        "switch": {
            "to_local": "python xgy_entry.py use local",
            "to_cloud": "python xgy_entry.py use cloud <实例ID>",
            "by_chat": "对话里说「改用本机」或「用仙宫云」，助手调用上面两条命令之一",
        },
        "config_path": xgy_cfg.cfg_path(),
    }


def write_cfg(patch):
    """只改传入的键，保留其余配置；凭据不回显。"""
    path = xgy_cfg.cfg_path()
    data = xgy_cfg.cfg() or {}
    data.update(patch)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return path


def command_use(args):
    if len(args) < 2:
        print("用法：python xgy_entry.py use local | use cloud <实例ID>")
        return 2
    target = args[1].lower()
    if target == "local":
        p = write_cfg({"comfy_url": LOCAL_COMFY})
        print("已切到本机部署：comfy_url=%s（%s）" % (LOCAL_COMFY, p))
        return 0
    if target == "cloud":
        if len(args) < 3 or not args[2].strip():
            print("use cloud 需要实例 ID：python xgy_entry.py use cloud <实例ID>")
            return 2
        iid = args[2].strip()
        url = cloud_comfy(iid)
        p = write_cfg({"comfy_url": url, "xgy_instance_id": iid})
        print("已切到云端实例 %s：comfy_url=%s（%s）" % (iid, url, p))
        print("接着验四服务：python xgy_api.py verify-services %s" % iid)
        return 0
    print("未知模式：%s（只认 local / cloud）" % target)
    return 2


def command_set_token():
    token = sys.stdin.read().strip()
    if not token:
        print("没有读到令牌。把令牌通过标准输入传入，命令行明文会留痕。")
        return 2
    p = write_cfg({"xgy_api_token": token})
    print("访问令牌已写入全局配置（%s），不回显内容。" % p)
    print("接着跑 python xgy_api.py check 验令牌并看咱们镜像的机器。")
    return 0


def print_report(r):
    print("=" * 64)
    print("  安装入口检测 · 本地 / 仙宫云")
    print("=" * 64)
    print("  当前模式：%s" % r["mode"])
    print("  comfy_url：%s" % (r["comfy_url"] or "（未配）"))
    print("  访问令牌：%s" % ("已配置" if r["token_status"] == "present" else "未配置"))
    print("  绑定实例：%s" % (r["instance_id"] or "（未绑定）"))
    c = r["cloud"]
    if c["checked"]:
        print("  令牌验身：%s" % (c["account"] or "（未取到昵称）"))
        print("  咱们镜像机器：%d 台" % len(c["own_instances"]))
        for i in c["own_instances"]:
            print("    %s  %-10s  %s" % (i["id"], i["status"], i["name"]))
    elif c["error"]:
        print("  云端探测失败：%s" % c["error"])
    print("\n[判定] %s" % r["verdict"])
    for group in r["paths"]:
        print("\n[%s]" % group["title"])
        for idx, step in enumerate(group["steps"], 1):
            print("  %d. %s" % (idx, step))
    print("\n[随时切换] %s" % r["switch"]["by_chat"])
    print("  本机：%s" % r["switch"]["to_local"])
    print("  云端：%s" % r["switch"]["to_cloud"])


def main():
    args = sys.argv[1:]
    if args and args[0] == "use":
        return command_use(args)
    if args and args[0] == "set-token":
        return command_set_token()
    r = detect()
    if "--json" in args:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print_report(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
