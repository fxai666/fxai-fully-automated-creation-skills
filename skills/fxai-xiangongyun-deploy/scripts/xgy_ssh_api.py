#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""仙宫云 SSH 维护入口 —— 从开放平台 API 现取凭证，免手输密码。

核心能力：给一个实例 ID（或从全局配置自动解析），自动 GET /instance/{id} 拿到
ssh_domain / ssh_port / ssh_user / password，直连上去做维护。以后维护任何仙宫云
实例都不用再手工给 SSH 账号密码 —— 只要访问令牌在全局配置里就行。

🔴 凭据来源：API 实时返回（xgy_cfg.ssh_cred_from_api），不读任何本地 SSH 配置文件。
   访问令牌读 ~/.workbuddy/fxai_global.json 的 xgy_api_token（XGY_API_TOKEN 优先）。

paramiko 依赖：
    - run/put/get 需要 paramiko（密码登录 + sftp）；info/shell 不需要。
    - 当前解释器缺 paramiko 时，脚本自动 pip 装到当前解释器，装完复核；
      解释器路径一律动态取自当前解释器，装失败才给通用手动指引。

用法：
    python xgy_ssh_api.py info [ID]                     # 看凭证（密码打码）+ 实例状态，不连 SSH
    python xgy_ssh_api.py run  [ID] "<远程命令>"          # 执行命令，打印输出
    python xgy_ssh_api.py put  [ID] <本地文件> <远端路径>  # 上传
    python xgy_ssh_api.py get  [ID] <远端路径> <本地文件>  # 下载
    python xgy_ssh_api.py shell [ID]                     # 交互式 shell

    ID 省略时取全局配置 xgy_instance_id；再没有则用令牌下唯一那台实例（多台要显式给）。

例子：
    python xgy_ssh_api.py run  <实例ID> "hostname; uptime"
    python xgy_ssh_api.py put  <实例ID> ./patch.py /root/xgyscripts/patch.py
    python xgy_ssh_api.py run  <实例ID> "bash /scripts/start.d/comfyui.sh"
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import xgy_cfg
from xgy_ssh import SSH, SSHFail


def _mask(s):
    s = str(s or "")
    if len(s) <= 4:
        return "*" * len(s)
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


def resolve_cred(iid, token=None):
    """定实例 → 从 API 取凭证。iid 空时走配置/唯一实例。"""
    tok = token or xgy_cfg.api_token()
    iid = (iid or "").strip() or xgy_cfg.current_instance_id()
    if not iid:
        # 配置没绑：查令牌下的实例，唯一一台就自动用，多台让人显式给（不猜）
        import xgy_api
        if not tok:
            raise RuntimeError("缺少访问令牌：在 ~/.workbuddy/fxai_global.json 设 xgy_api_token")
        lst = (xgy_api.list_instances(tok).get("data") or {}).get("list") or []
        ids = [str(x.get("id") or "") for x in lst if x.get("id")]
        if len(ids) == 1:
            iid = ids[0]
            print(f"[ssh] 未指定实例，自动用令牌下唯一那台：{iid}", file=sys.stderr)
        elif len(ids) > 1:
            raise RuntimeError("令牌下有多台实例，请显式给实例 ID：\n  " + "\n  ".join(ids))
        else:
            raise RuntimeError("令牌下没有任何实例，无法连接")
    return xgy_cfg.ssh_cred_from_api(iid, token=tok)


def _need_paramiko(action):
    """确保当前解释器可用 paramiko；缺则自动 pip 装到当前解释器，装完复核。

    返回 True 可用；False 表示自动安装失败（已打印明确指引）。
    pip 源可由环境变量 FXAI_PIP_INDEX / FXAI_PIP_EXTRA 覆盖，默认清华源 + 官方兜底。
    """
    try:
        import paramiko  # noqa: F401
        return True
    except Exception:
        pass

    import subprocess
    idx = os.environ.get("FXAI_PIP_INDEX", "https://pypi.tuna.tsinghua.edu.cn/simple")
    extra = os.environ.get("FXAI_PIP_EXTRA", "https://pypi.org/simple")
    print(f"[ssh] 当前解释器缺少 paramiko，自动安装到 {sys.executable} …", file=sys.stderr)
    cmd = [sys.executable, "-m", "pip", "install", "-q",
           "-i", idx, "--extra-index-url", extra, "paramiko"]
    try:
        subprocess.check_call(cmd, timeout=300)
        import importlib
        importlib.invalidate_caches()
        import paramiko  # noqa: F401
        print("[ssh] paramiko 已就绪。", file=sys.stderr)
        return True
    except Exception as e:
        print(
            f"[ssh] 自动安装 paramiko 失败：{e}\n"
            f"[ssh] 请手动执行：\"{sys.executable}\" -m pip install paramiko\n"
            "（或传 --key 走系统 ssh 密钥认证，那条路不用 paramiko）",
            file=sys.stderr,
        )
        return False


def build_ssh(cred, key=""):
    return SSH(host=cred["host"], port=cred["port"], user=cred["user"],
               password=cred["password"], key=key or None, timeout=30)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="仙宫云 SSH 维护：从 API 现取凭证，免手输密码")
    ap.add_argument("action", choices=["info", "run", "put", "get", "shell"],
                    help="info=看凭证(打码)/run=执行命令/put=上传/get=下载/shell=交互")
    ap.add_argument("id", nargs="?", default="", help="实例 ID（省略则取配置/唯一实例）")
    ap.add_argument("extra", nargs="*", default=[], help="位置参数：run=远程命令 / put=本地+远端 / get=远端+本地")
    ap.add_argument("--cmd", default="", help="run 的远程命令（等价位置参数）")
    ap.add_argument("--local", default="", help="put/get 的本地路径")
    ap.add_argument("--remote", default="", help="put/get 的远端路径")
    ap.add_argument("--key", default="", help="可选：私钥路径（走密钥认证，不需要 paramiko）")
    ap.add_argument("--token", default="", help="可选：访问令牌（默认读全局配置）")
    ap.add_argument("--timeout", type=int, default=600, help="run 的超时秒数")
    a = ap.parse_args()

    # 位置参数拆分：仙宫云实例 ID 是 16 位小写字母数字，用它区分「ID」和「远程命令」，
    # 这样 run "cmd"（省略 ID，走配置）与 run <id> "cmd" 都能正确解析。
    import re
    pos = [a.id] + list(a.extra or [])
    pos = [p for p in pos if p != ""]
    is_iid = lambda s: bool(re.fullmatch(r"[a-z0-9]{12,28}", str(s)))
    iid = pos[0] if pos and is_iid(pos[0]) else ""
    rest = pos[1:] if iid else pos

    # info 只查 API、不连 SSH，也不需要 paramiko
    cred = resolve_cred(iid, a.token or None)
    if a.action == "info":
        print(f"实例 ID : {cred['id']}")
        print(f"名称状态: {cred['label']}")
        print(f"SSH     : {cred['user']}@{cred['host']} -p {cred['port']}")
        print(f"密码     : {_mask(cred['password'])}  (len={len(cred['password'])})")
        print("来源     : 开放平台 API GET /instance/{id}（实时）")
        return

    # shell 需要交互式 pty，paramiko 直连用 stdin 更麻烦，明确告知用系统 ssh + 从 info 取串
    if a.action == "shell":
        if not _need_paramiko(a.action):
            return
        if not a.key:
            # 无私钥：打印 API 取到的密码让人粘贴（sshpass 非标配，不强依赖）
            print(f"[ssh] 交互 shell：{cred['user']}@{cred['host']} -p {cred['port']}")
            print(f"[ssh] 密码（API 实时取得，粘贴即可）：{cred['password']}")
            os.execvp("ssh", ["ssh", "-p", str(cred["port"]), "-o", "StrictHostKeyChecking=no",
                              f"{cred['user']}@{cred['host']}"])
        else:
            os.execvp("ssh", ["ssh", "-p", str(cred["port"]), "-o", "StrictHostKeyChecking=no",
                              "-i", a.key, f"{cred['user']}@{cred['host']}"])

    if not _need_paramiko(a.action):
        return
    ssh = build_ssh(cred, a.key)

    try:
        if a.action == "run":
            cmd = a.cmd or " ".join(rest)
            if not cmd:
                raise SystemExit("run 需要远程命令：python xgy_ssh_api.py run <id> \"<命令>\"")
            r = ssh.run(cmd, timeout=a.timeout)
            sys.stdout.write(r.out or "")
            if r.err:
                sys.stderr.write("[stderr]\n" + r.err)
            print(f"[exit={r.rc}]", file=sys.stderr)
            sys.exit(r.rc)
        elif a.action == "put":
            local = a.local or (rest[0] if rest else "")
            remote = a.remote or (rest[1] if len(rest) > 1 else "")
            if not local or not remote or not os.path.isfile(local):
                raise SystemExit("put 用法：python xgy_ssh_api.py put <id> <本地文件> <远端路径>")
            c = ssh.connect()._client
            size = os.path.getsize(local)
            print(f"put {local} -> {cred['user']}@{cred['host']}:{remote} ({size/1e6:.1f} MB)")
            sftp = c.open_sftp()
            sftp.put(local, remote)
            st = sftp.stat(remote)
            sftp.close(); ssh.close()
            print(f"[done] remote size={st.st_size} expect={size} match={st.st_size == size}")
            sys.exit(0 if st.st_size == size else 1)
        elif a.action == "get":
            remote = a.remote or (rest[0] if rest else "")
            local = a.local or (rest[1] if len(rest) > 1 else "")
            if not (remote and local):
                raise SystemExit("get 用法：python xgy_ssh_api.py get <id> <远端路径> <本地文件>")
            c = ssh.connect()._client
            sftp = c.open_sftp()
            sftp.get(remote, local)
            sftp.close(); ssh.close()
            print(f"[done] {cred['user']}@{cred['host']}:{remote} -> {local}")
    except (SSHFail, RuntimeError) as e:
        print(f"[ssh 错误] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        sys.exit(130)
