#!/usr/bin/env python
"""仙宫云 · 凤希技能包一键部署 CLI。

用法速查（都在 scripts/ 目录下跑）：
  python deploy.py plan  --group all
  python deploy.py emit  --group mv --out xgy_mv.sh          # 生成脚本，贴到 Jupyter 终端
  python deploy.py probe -H <ip> -p <port> -u root -P <密码>
  python deploy.py run   -H <ip> -p <port> -u root -P <密码> --group all
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gen_script  # noqa: E402
from xgy_ssh import SSH, SSHFail, have_paramiko  # noqa: E402
from xgy_cfg import ssh_cred  # noqa: E402

PROBE = r"""
echo "### OS"; (cat /etc/os-release 2>/dev/null | head -n2) || true
echo "### GPU"; (nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null) || echo "无 nvidia-smi"
echo "### CPU/MEM"; nproc; free -h | head -n2
echo "### 磁盘"; df -h | grep -vE "^(tmpfs|devtmpfs|overlay .*/var/lib)" | head -n20
echo "### ComfyUI 候选"; ls -d /root/ComfyUI* /workspace/ComfyUI* /home/*/ComfyUI /opt/ComfyUI /data/*/ComfyUI 2>/dev/null | head
echo "### ComfyUI 版本"; for c in /root/ComfyUI /workspace/ComfyUI; do [ -d "$c" ] && echo "$c -> $(git -C "$c" rev-parse --short HEAD 2>/dev/null) $(git -C "$c" log -1 --format=%cd --date=short 2>/dev/null)"; done
echo "### 已装自定义节点"; for c in /root/ComfyUI /workspace/ComfyUI; do [ -d "$c/custom_nodes" ] && { echo "[$c/custom_nodes]"; ls -1 "$c/custom_nodes" | head -n40; }; done
echo "### 现有模型"; for c in /root/ComfyUI /workspace/ComfyUI; do [ -d "$c/models" ] && { echo "[$c/models]"; du -sh "$c/models"/* 2>/dev/null | head -n20; }; done
echo "### 端口占用"; (ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null) | grep -E ":(8188|22|7860)" | head
echo "### 工具"; for b in git wget curl aria2c python3 pip node; do printf "%-8s %s\n" "$b" "$(command -v $b || echo 缺)"; done
echo "### 内存/CPU 负载"; uptime
echo "### /root/cloud 云储存"; ls -la /root/cloud 2>/dev/null | head -n10 || echo "无 /root/cloud"
echo "### 容器?"; (docker ps --format '{{.Names}} {{.Image}} {{.Ports}}' 2>/dev/null | head) || echo "无 docker 或无权限"
echo "### PROBE_DONE"
"""


def parse_args():
    p = argparse.ArgumentParser(description="仙宫云 · 凤希技能包一键部署")
    p.add_argument("cmd", choices=["plan", "emit", "probe", "run", "verify"])
    # 选什么
    p.add_argument("--group", default="all",
                   help="要装的分组，逗号分隔：img,music,vocalsep,mv 或 all（默认 all）")
    p.add_argument("--variant", default="full", help="H3 主模型变体：full / pruned / w4a8（默认 full）")
    p.add_argument("--only", default="", help="只装这些模型 id（逗号分隔），与 --group 互斥")
    p.add_argument("--skip", default="", help="跳过这些模型 id（逗号分隔）")
    p.add_argument("--include-optional", action="store_true", help="连可选模型一起装（如 SheetSage2）")
    p.add_argument("--set-dir", action="append", default=[],
                   help="覆盖某个模型的落盘子目录，例：--set-dir h3_singularity=diffusion_models")
    p.add_argument("--comfy-dir", default="", help="手动指定 ComfyUI 目录（默认自动探测）")
    p.add_argument("--upgrade-comfy", action="store_true",
                   help="顺带 git pull 升级 ComfyUI 到最新版（要重启 ComfyUI 才生效）")
    p.add_argument("--no-nodes", action="store_true", help="不装自定义节点")
    p.add_argument("--no-models", action="store_true", help="不下载模型")
    p.add_argument("--all-nodes", action="store_true", help="连可选节点一起装（含 ComfyUI-Manager）")
    p.add_argument("--parallel", type=int, default=1,
                   help="模型并发下载数（默认 1。镜像单连接约 8MB/s，填 3~4 能明显提速；"
                        "已下好的会 wget -c 续传，重复执行很安全）")
    p.add_argument("--out", default="xgy_deploy.sh", help="emit 子命令的输出文件")
    # 连接
    p.add_argument("-H", "--host", default="", help="实例 IP / 主机名（不传则读凭据文件）")
    p.add_argument("-p", "--port", default="", help="SSH 端口（仙宫云实例详情页给的那个）")
    p.add_argument("-u", "--user", default="", help="SSH 用户名")
    p.add_argument("-P", "--password", default=os.environ.get("XGY_SSH_PASSWORD", ""),
                   help="SSH 密码（也可用环境变量 XGY_SSH_PASSWORD；建议走密钥就别传）")
    p.add_argument("-k", "--key", default=os.environ.get("XGY_SSH_KEY", ""), help="私钥路径")
    p.add_argument("--foreground", action="store_true",
                   help="前台跑（直接看输出）；默认后台跑+实时跟踪日志")
    p.add_argument("--dry-run", action="store_true", help="只出计划，不连服务器")
    return p.parse_args()




def load_secrets(path, profile):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"读凭据文件失败：{path} → {e}", file=sys.stderr)
        return {}
    sec = data.get(profile)
    if not isinstance(sec, dict):
        print(f"凭据文件里没有 profile '{profile}'：{path}", file=sys.stderr)
        return {}
    return sec


def collect(a):
    groups = ["img", "music", "vocalsep", "mv"] if a.group == "all" else \
        [g.strip() for g in a.group.split(",") if g.strip()]
    only = [x.strip() for x in a.only.split(",") if x.strip()]
    skip = [x.strip() for x in a.skip.split(",") if x.strip()]
    set_dir = {}
    for kv in a.set_dir:
        if "=" in kv:
            k, v = kv.split("=", 1)
            set_dir[k.strip()] = v.strip()
    return groups, only, skip, set_dir


def print_plan(models, nodes, total):
    print("=" * 74)
    print("仙宫云 · 凤希技能包部署计划")
    print("=" * 74)
    if nodes:
        print("\n[自定义节点]")
        for n in nodes:
            print(f"  - {n['name']:<28} {n['repo']}")
            if n.get("note"):
                print(f"      {n['note']}")
    if models:
        print("\n[模型]")
        w = max([len(m["target_name"] or os.path.basename(m["path"])) for m in models] + [12])
        print(f"  {'id':<22}{'分组':<10}{'落盘位置':<{w + 2}}{'下限':>7}")
        for m in models:
            name = m["target_name"] or os.path.basename(m["path"])
            tag = " (可选)" if m.get("optional") else ""
            print(f"  {m['id']:<22}{m['group']:<10}{name:<{w}}{m['min_bytes']/2**30:>6.1f}G{tag}")
            print(f"  {'':<22}{'':<10}← {m['repo']}/{m['path']}")
        print(f"\n  合计（不含可选）：{total/2**30:.1f} GB")
    print()


def main():
    a = parse_args()
    groups, only, skip, set_dir = collect(a)
    script, models, nodes, total = gen_script.render(
        groups, a.include_optional, a.variant, only, skip, set_dir,
        required_only=not a.all_nodes,
        do_nodes=not a.no_nodes, do_models=not a.no_models, comfy_dir=a.comfy_dir,
        upgrade_comfy=a.upgrade_comfy, parallel=max(1, int(a.parallel)),
    )

    if a.cmd == "plan":
        print_plan(models, nodes, total)
        print("要真跑：")
        print("  python deploy.py run -H <ip> -p <port> -u root -P <密码> --group all")
        return 0

    if a.cmd == "emit":
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(script)
        print_plan(models, nodes, total)
        print(f"已生成：{os.path.abspath(a.out)}")
        print("用法：Jupyter → Terminal，整段粘贴执行；或 scp 到实例后 bash 执行。")
        return 0

    # ---- 以下都需要 SSH ----
    sec = ssh_cred()
    if sec:
        print("凭据来源：全局配置 xgy_ssh", file=sys.stderr)

    host = a.host or sec.get("host", "")
    port = a.port or sec.get("port", 22)
    user = a.user or sec.get("user", "root")
    password = a.password or sec.get("password", "") or None
    key = a.key or sec.get("key", "") or None

    if not host:
        print("缺少实例地址。任选其一：\n"
              "  · 全局配置 ~/.workbuddy/fxai_global.json 里补上 xgy_ssh（host/port/user/password/key）\n"
              "  · 命令行给 -H <ip>", file=sys.stderr)
        return 2
    if a.dry_run:
        print_plan(models, nodes, total)
        return 0
    if not have_paramiko() and password:
        print("提示：本机没有 paramiko，无法用密码登录。", file=sys.stderr)
        print("  pip install paramiko   或   改用密钥：-k ~/.ssh/id_ed25519", file=sys.stderr)
        return 2

    print(f"→ 目标 {user}@{host}:{port}  （认证：{'密码' if password else '密钥/agent'}）", file=sys.stderr)
    ssh = SSH(host, port, user, password=password, key=key)
    try:
        if a.cmd == "probe":
            print(ssh.run("bash -lc " + shlex.quote(PROBE), timeout=180).text())
            return 0

        if a.cmd == "verify":
            r = ssh.run("bash -lc " + shlex.quote(
                'C=$(ls -d /root/ComfyUI /workspace/ComfyUI 2>/dev/null | head -n1);'
                'echo "ComfyUI=$C";'
                'for d in diffusion_models text_encoders vae loras checkpoints latent_upscale_models audio_encoders; do'
                '  [ -d "$C/models/$d" ] && { echo "--- $d"; ls -la "$C/models/$d" | head -n40; };'
                'done; echo "--- custom_nodes"; ls -1 "$C/custom_nodes" 2>/dev/null'), timeout=120)
            print(r.text())
            return 0

        # run
        print_plan(models, nodes, total)
        print("→ 连接实例做体检 ...\n")
        probe = ssh.run("bash -lc " + shlex.quote(PROBE), timeout=240)
        print(probe.text())
        if "PROBE_DONE" not in probe.text():
            print("体检没跑完（可能是超时），仍继续部署；上面这段可回头自己再跑一次 probe。", file=sys.stderr)

        print("→ 上传部署脚本 ...", flush=True)
        ssh.put_text(script, "/tmp/xgy_deploy.sh")
        ssh.run("chmod +x /tmp/xgy_deploy.sh")

        if a.foreground:
            r = ssh.run("bash /tmp/xgy_deploy.sh", timeout=60 * 60 * 12)
            print(r.text())
            return r.rc

        print("→ 后台启动，边跑边跟日志（Ctrl+C 只会断开查看，不会停远端任务）\n", flush=True)
        ssh.run_bg("/tmp/xgy_deploy.sh", "/tmp/xgy_deploy.log")
        time.sleep(2)
        ssh.stream("/tmp/xgy_deploy.log", poll=4.0, tail_from=0)
        rc = ssh.run("cat /tmp/xgy_deploy.rc 2>/dev/null || echo 0").out.strip()
        bad = ssh.run("cat /tmp/xgy_deploy.bad 2>/dev/null || true").out.strip()
        print("\n" + "=" * 74)
        if bad:
            print("失败清单：")
            for line in bad.splitlines():
                print("  -", line)
            print("\n下一步：① 401 = 镜像没收录，找仙宫云客服收录；② 磁盘满 = 少装分组或扩盘；")
            print("        ③ 只重跑失败项：--only <模型id>")
        else:
            print("全部就位 ✅")
        print("别忘了：重启 ComfyUI 服务（容器里跑就重启容器），新节点才会加载。")
        print("=" * 74)
        return int(rc or 0)
    except SSHFail as e:
        print(f"[SSH 失败] {e}", file=sys.stderr)
        return 1
    finally:
        ssh.close()


if __name__ == "__main__":
    sys.exit(main())
