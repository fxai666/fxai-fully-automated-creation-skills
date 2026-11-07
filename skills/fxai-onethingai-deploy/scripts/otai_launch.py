"""远端快检 + 一键全并发拉起下载。

用法：
  python otai_launch.py check              # 只看现状（磁盘/已下字节/残留进程）
  python otai_launch.py launch [N]         # 全分组+可选模型，N 路并发后台跑（默认 8）
  python otai_launch.py log [N]            # 追最近 N 行日志（默认 40）
  python otai_launch.py kill               # 精确停掉远端下载进程（按 PID，不用 pkill 免误杀自己）
"""
from __future__ import annotations

import base64
import io
import json
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otai_cfg import ssh_cred  # noqa: E402
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gen_script  # noqa: E402
from otai_ssh import SSH  # noqa: E402

LOG = "/tmp/otai_deploy.log"
PIDF = "/tmp/otai_deploy.pid"

LIST = r'''
for f in diffusion_models/qwen_image_2.1_bf16.safetensors text_encoders/qwen3vl_8b_int8_convrot.safetensors vae/qwen_image_2.1_vae_bf16.safetensors checkpoints/yue2_3b_bf16.safetensors audio_encoders/sheetsage2_bf16.safetensors diffusion_models/MelBandRoformer_fp32.safetensors diffusion_models/minimax_h3_singularity_ref2va_int8.safetensors text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors vae/minimax_h3_video_vae_fp16.safetensors vae/minimax_h3_audio_vae_fp32.safetensors loras/minimax_h3_ref2v_turbo_8step_768p_bf16.safetensors latent_upscale_models/minimax_h3_bf16_conservative.safetensors; do
  p="$C/models/$f"
  if [ -f "$p" ]; then printf "%12.2fG  %s\n" "$(echo "scale=2;$(stat -c%s "$p")/1073741824"|bc)" "$f"
  else printf "%12s  %s\n" 缺失 "$f"; fi
done
echo "--- 后台下载进程数 ---"; ps -eo args | grep -c "[c]url -L"
echo "--- 日志尾 ---"; tail -n 12 "$LOG"
'''

CHECK = r'''
C=$(ls -d /root/ComfyUI /workspace/ComfyUI 2>/dev/null | head -n1); echo "COMFY=$C"
df -h "$C" | tail -n1
for f in diffusion_models/qwen_image_2.1_bf16.safetensors text_encoders/qwen3vl_8b_int8_convrot.safetensors vae/qwen_image_2.1_vae_bf16.safetensors checkpoints/yue2_3b_bf16.safetensors audio_encoders/sheetsage2_bf16.safetensors diffusion_models/MelBandRoformer_fp32.safetensors diffusion_models/minimax_h3_singularity_ref2va_int8.safetensors text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors vae/minimax_h3_video_vae_fp16.safetensors vae/minimax_h3_audio_vae_fp32.safetensors loras/minimax_h3_ref2v_turbo_8step_768p_bf16.safetensors latent_upscale_models/minimax_h3_bf16_conservative.safetensors; do
  if [ -f "$C/models/$f" ]; then printf "%14d  %s\n" $(stat -c%s "$C/models/$f") "$f"; else printf "%14s  %s\n" MISS "$f"; fi
done
echo '--- .otai 暂存 ---'
find "$C/models" -path '*/.otai/*' -type f -printf '%s  %p\n' 2>/dev/null | sort -rn
echo '--- curl 进程 ---'
ps -eo pid,etime,args | grep -E '[c]url' | head -20
'''


def ssh_of():
    d = otai_cfg.ssh_cred()
    s = SSH(d["host"], d["port"], d["user"], password=d["password"])
    s.connect()
    return s


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    sh = ssh_of()
    try:
        if cmd == "check":
            print(sh.run("bash -lc " + _q(CHECK), timeout=120).text())
        elif cmd == "sizes":
            r = sh.run("bash -lc " + _q(f"C=$(ls -d /root/ComfyUI /workspace/ComfyUI 2>/dev/null | head -n1);" + LIST), timeout=60)
            print(r.text())
        elif cmd == "launch":
            par = int(sys.argv[2]) if len(sys.argv) > 2 else 8
            # 先把上一轮还在跑的下载掐掉：它们和新任务可能写同一个文件，
            # 两路 curl -C - 抢同一个目标会互相覆盖出坏文件。
            # ⚠️ 只杀「外壳脚本」进程（bash /tmp/otai_deploy.sh），**绝不得杀 curl**。
            # 只匹配 otai_deploy 的 bash 进程：把 curl 一起匹配会掐掉下到一半的进度。
            # 外壳杀了、setsid 起来的 curl 子进程照跑，du 完的还能接着续。
            r = sh.run("ps -eo pid,args | grep '[x]gy_deploy.sh' | awk '{print $1}'", timeout=60)
            killed = [x for x in r.out.split() if x.isdigit()]
            for p in killed:
                sh.run(f"kill -9 {p} 2>/dev/null || true")
            alive = sh.run("ps -eo args | grep -c '[c]url -L'", timeout=30).out.strip()
            print(f"  已停外壳脚本 {len(killed)} 个；现有下载进程保留 {alive} 路")
            print("已停旧进程；磁盘：", sh.run("df -h / | tail -n1").out.strip())
            script, models, nodes, total = gen_script.render(
                ["img", "music", "vocalsep", "mv"], True, "full",
                None, None, {}, required_only=True,
                do_nodes=False, do_models=True, comfy_dir="",
                upgrade_comfy=False, parallel=par,
            )
            sh.put_text(script, "/tmp/otai_deploy.sh")
            sh.run("chmod +x /tmp/otai_deploy.sh")
            sh.run_bg("/tmp/otai_deploy.sh", LOG)
            print(f"已拉起：{len(models)} 个模型 / {par} 路并发")
            for m in models:
                print("  -", m["id"], m.get("source_url") or "")
        elif cmd == "log":
            n = sys.argv[2] if len(sys.argv) > 2 else "40"
            print(sh.run(f"tail -n {n} {LOG} 2>/dev/null || echo '(无日志)'", timeout=60).text())
        elif cmd == "kill":
            r = sh.run("ps -eo pid,args | grep -E '[c]url|[x]gy_deploy' | awk '{print $1}'", timeout=60)
            pids = [p for p in r.out.split() if p.isdigit()]
            self_pid = sh.run(f"cat {PIDF} 2>/dev/null").out.strip()
            print("待停 PID:", pids)
            for p in pids:
                sh.run(f"kill -9 {p} 2>/dev/null || true")
            if self_pid.isdigit():
                sh.run(f"kill -9 {self_pid} 2>/dev/null || true")
            print("已停。")
    finally:
        sh.close()


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    main()
