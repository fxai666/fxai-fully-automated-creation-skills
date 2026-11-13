#!/bin/bash
# 凤希AI 开机幂等补丁（内置镜像用）：保证「打开 ComfyUI 不出现官方基础文生图」，
# 并且「打开就加载哪几条工作流」的扩展不会因为重装节点而丢失。
# 放在 /scripts/start.d/ 下，每次开机执行；重复执行安全，前端包升级后自动重打。
#
# 分工（2026-11-13 定）：
#   ① 前端扩展（主力）：custom_nodes/fxai-toolkit/js/fxai_default_workflow.js
#      里的 TARGETS 数组决定打开哪几条真实工作流。本脚本负责「丢了就补回来」。
#   ② 内置默认图（兜底）：调用 fxai_default_workflow.py 把前端 bundle 里硬编码的
#      官方基础文生图换成我们的工作流，保证任何情况都不露官方图。
#      不建任何文件夹、不注入首页跳转。
#
# 伴生文件（都必须随镜像保存）：
#   /root/xgyscripts/fxai_default_workflow.py     兜底图补丁
#   /root/xgyscripts/fxai_default_workflow.js     前端扩展的备份源
set -u

# ---------- ① 前端扩展自愈（节点重装 / 误删时补回来） ----------
EXT=/root/ComfyUI/custom_nodes/fxai-toolkit/js/fxai_default_workflow.js
BK=/root/xgyscripts/fxai_default_workflow.js
if [ -f "$EXT" ]; then
  echo "[fxai] 前端扩展在位：$EXT"
elif [ -f "$BK" ]; then
  mkdir -p "$(dirname "$EXT")"
  cp -f "$BK" "$EXT"
  echo "[fxai] 前端扩展缺失，已从备份恢复：$BK -> $EXT"
else
  echo "[fxai] 警告：前端扩展与备份都找不到，打开时不会自动加载指定工作流"
fi

# ---------- ② 内置默认图兜底补丁 ----------
PY=""
for c in /root/miniconda3/envs/comfyui/bin/python python3; do
  [ -x "$c" ] && { PY="$c"; break; }
  command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
done
[ -z "$PY" ] && { echo "[fxai] 无 python，跳过内置默认图补丁"; exit 0; }

for f in /root/xgyscripts/fxai_default_workflow.py \
         /scripts/start.d/fxai_default_workflow.py; do
  if [ -f "$f" ]; then
    echo "[fxai] 应用内置默认工作流补丁: $f"
    "$PY" "$f" && exit 0
    echo "[fxai] 主脚本执行失败：$f"
  fi
done

echo "[fxai] 警告：找不到 fxai_default_workflow.py，内置默认图未打补丁（扩展仍会打开指定工作流）"
exit 0
