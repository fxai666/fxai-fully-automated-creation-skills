# -*- coding: utf-8 -*-
"""把本地「仙宫云」专用工作流目录单向同步到线上 ComfyUI。

🔴 唯一来源（用户 2026-11-13 定）：本机 ComfyUI 工作区的
   user/default/workflows/仙宫云 目录。
   线上 user/default/workflows/ 只做接收端，不在线上直接编辑。

用法：
    python sync_workflows.py                  # 同步（只推有差异的，幂等）
    python sync_workflows.py --dry-run        # 只比对，不写（--check 同义）
    python sync_workflows.py --profile <实例ID>   # 指定凭据 profile
    python sync_workflows.py --src <目录> --remote <远端目录>
    python sync_workflows.py --prune          # 额外报告远端独有的文件（默认只提示不删）

来源目录解析顺序：--src → 环境变量 FXAI_XGY_WF_SRC → 全局配置 xgy_workflow_src
→ 当前目录下的 user/default/workflows/仙宫云。
凭据读取顺序：环境变量 XGY_HOST/XGY_PORT/XGY_PASS → --profile → xgy_cfg.ssh_cred()
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SECRET_NAME = "xianggongyun.json"
DEFAULT_REMOTE = "/root/ComfyUI/user/default/workflows"


def default_src():
    """工作流唯一来源目录的解析顺序（不写死本机路径）。

    1) 环境变量 FXAI_XGY_WF_SRC
    2) 全局配置 ~/.workbuddy/fxai_global.json 的 xgy_workflow_src
    3) 当前目录下 user/default/workflows/仙宫云（在 ComfyUI 根目录跑时自动命中）
    4) 当前目录本身就是那个目录
    """
    env = os.environ.get("FXAI_XGY_WF_SRC", "")
    if env and os.path.isdir(env):
        return env
    try:
        import xgy_cfg
        v = (xgy_cfg.cfg() or {}).get("xgy_workflow_src") or ""
        if v and os.path.isdir(v):
            return v
    except Exception:
        pass
    cand = os.path.join(os.getcwd(), "user", "default", "workflows", "仙宫云")
    if os.path.isdir(cand):
        return cand
    if os.path.basename(os.getcwd()) == "仙宫云":
        return os.getcwd()
    return ""


def find_secret_file():
    """secrets/xianggongyun.json 的解析：环境变量 → 全局配置 → 从脚本目录逐级向上找。"""
    env = os.environ.get("FXAI_XGY_SECRETS", "")
    if env and os.path.isfile(env):
        return env
    try:
        import xgy_cfg
        v = (xgy_cfg.cfg() or {}).get("xgy_secrets") or ""
        if v and os.path.isfile(v):
            return v
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    for up in range(0, 7):
        p = os.path.join(*([here] + [".."] * up), "secrets", SECRET_NAME)
        if os.path.isfile(p):
            return p
    return ""


def cred_from_profile(profile=""):
    """从 secrets 文件读某台实例的 SSH 信息；profile 空则用 default。"""
    p = find_secret_file()
    if not p:
        return {}
    try:
        d = json.load(open(p, encoding="utf-8")) or {}
    except Exception:
        return {}
    if profile:
        prof = d.get(profile)
        return prof if isinstance(prof, dict) and prof.get("host") else {}
    prof = d.get("default")
    if isinstance(prof, dict) and prof.get("host"):
        return prof
    return {}


def resolve_cred(profile=""):
    host = os.environ.get("XGY_HOST", "")
    if host:
        return {
            "host": host,
            "port": int(os.environ.get("XGY_PORT", "22")),
            "user": os.environ.get("XGY_USER", "root"),
            "password": os.environ.get("XGY_PASS", ""),
        }
    if profile:
        c = cred_from_profile(profile)
        if c:
            return c
        raise SystemExit("secrets 里找不到 profile: %s" % profile)
    try:
        import xgy_cfg
        c = xgy_cfg.ssh_cred()
        if c and c.get("host"):
            return c
    except Exception:
        pass
    c = cred_from_profile("")
    if c:
        return c
    raise SystemExit("拿不到 SSH 凭据：设 XGY_HOST/XGY_PASS，或用 --profile <实例ID>")


def md5_bytes(b):
    return hashlib.md5(b).hexdigest()


def main():
    ap = argparse.ArgumentParser(description="仙宫云工作流单向同步（本地 仙宫云/ → 线上）")
    ap.add_argument("--src", default="", help="本地来源目录；不给则按 环境变量/全局配置/当前目录 自动解析")
    ap.add_argument("--remote", default=DEFAULT_REMOTE)
    ap.add_argument("--profile", default="")
    ap.add_argument("--dry-run", "--check", dest="dry", action="store_true")
    ap.add_argument("--prune", action="store_true", help="报告远端独有的 json（不删，只列）")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    src = a.src or default_src()
    if not src:
        raise SystemExit(
            "找不到工作流来源目录。三种指定方式（任选其一）：\n"
            "  1) --src <目录>\n"
            "  2) 环境变量 FXAI_XGY_WF_SRC=<目录>\n"
            "  3) 全局配置 ~/.workbuddy/fxai_global.json 写 \"xgy_workflow_src\": \"<目录>\"")
    if not os.path.isdir(src):
        raise SystemExit("源目录不存在: %s" % src)

    files = sorted(glob.glob(os.path.join(src, "*.json")))
    if not files:
        raise SystemExit("源目录里没有 json: %s" % src)

    local = {}
    print("源目录: %s" % src)
    print("本地 %d 个 JSON，逐个做合法性预检：" % len(files))
    for p in files:
        b = open(p, "rb").read()
        try:
            json.loads(b.decode("utf-8"))
        except Exception as e:
            raise SystemExit("JSON 不合法，先修好再推：%s (%s)" % (os.path.basename(p), e))
        name = os.path.basename(p)
        local[name] = (md5_bytes(b), len(b), p)
        print("  %-40s %8d B  %s" % (name, len(b), local[name][0][:12]))

    c = resolve_cred(a.profile)
    host, port = c["host"], int(c.get("port") or 22)
    user = c.get("user") or "root"
    print("\n目标: %s@%s:%d  %s" % (user, host, port, a.remote))

    try:
        import paramiko
    except ImportError:
        raise SystemExit("缺 paramiko：用部署 venv 的 python 跑，或 pip install paramiko")

    t = paramiko.Transport((host, port))
    kw = {"username": user}
    if c.get("password"):
        kw["password"] = c["password"]
    if c.get("key") and os.path.isfile(os.path.expanduser(c["key"])):
        kw["pkey"] = paramiko.Ed25519Key.from_private_key_file(os.path.expanduser(c["key"]))
    try:
        t.connect(**kw)
    except Exception as e:
        raise SystemExit(
            "连不上 %s@%s:%d —— %s: %s\n"
            "常见原因：实例已关机/已销毁（控制台确认），或端口变了（用 --profile <新实例ID>），"
            "或凭据过期（改 secrets/xianggongyun.json）。"
            % (user, host, port, type(e).__name__, e))
    sftp = paramiko.SFTPClient.from_transport(t)

    new, changed, same = [], [], []
    try:
        existing = set(sftp.listdir(a.remote))

        for name, (lmd5, size, lp) in local.items():
            if name not in existing:
                new.append(name)
                continue
            with sftp.open(a.remote + "/" + name, "rb") as f:
                rmd5 = md5_bytes(f.read())
            (changed if rmd5 != lmd5 else same).append(name)

        print("\n差异：新增 %d / 变更 %d / 一致 %d" % (len(new), len(changed), len(same)))
        for n in new:
            print("  + %s" % n)
        for n in changed:
            print("  ~ %s" % n)
        if a.prune:
            extra = sorted(x for x in existing if x.endswith(".json") and x not in local)
            if extra:
                print("\n远端独有（未删除，需要删请手动确认）：")
                for n in extra:
                    print("  ? %s" % n)

        if a.dry:
            print("\n[DRY-RUN] 未写入任何文件。")
            return 0

        todo = new + changed
        if not todo:
            print("\n无需同步：远端已与本地一致。")
            return 0

        print("\n开始上传 %d 个：" % len(todo))
        for name in todo:
            lmd5, size, lp = local[name]
            rp = a.remote + "/" + name
            sftp.put(lp, rp)
            sftp.chmod(rp, 0o644)
            with sftp.open(rp, "rb") as f:
                rmd5 = md5_bytes(f.read())
            if rmd5 != lmd5:
                raise SystemExit("MD5 校验失败：%s local=%s remote=%s" % (name, lmd5, rmd5))
            print("  OK %-40s %s" % (name, "覆盖" if name in existing else "新增"))

        # 收尾：远端总数对账
        after = [x for x in sftp.listdir(a.remote) if x.endswith(".json")]
        print("\n完成：推送 %d 个，md5 全部一致；远端 json 总数 %d" % (len(todo), len(after)))
    finally:
        sftp.close()
        t.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
