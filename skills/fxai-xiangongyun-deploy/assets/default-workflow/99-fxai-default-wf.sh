#!/bin/bash
# 凤希AI 开机幂等补丁（内置镜像用）：保证「打开 ComfyUI 就是自定义默认工作流」。
# 放在 /scripts/start.d/ 下，每次开机执行；重复执行安全，前端包升级后自动重打。
#
# 它做两件事（具体实现在同目录 fxai_default_workflow.py）：
#   ① 把前端 bundle 里硬编码的官方基础文生图换成我们的工作流 → 永不出现官方默认图
#   ② 给 index.html 补首访重定向 → 打开的是持久化工作流标签，而不是临时草稿
# 伴生脚本 /scripts/start.d/fxai_default_workflow.py 必须随镜像一起保存。
set -u

PY=""
for c in /root/miniconda3/envs/comfyui/bin/python python3; do
  [ -x "$c" ] && { PY="$c"; break; }
  command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
done
[ -z "$PY" ] && { echo "[fxai] 无 python，跳过默认工作流补丁"; exit 0; }

# 优先用独立脚本（完整实现：两份前端包都打、哈希幂等、可 --revert）
for f in /root/xgyscripts/fxai_default_workflow.py \
         /scripts/start.d/fxai_default_workflow.py; do
  if [ -f "$f" ]; then
    echo "[fxai] 应用默认工作流补丁: $f"
    "$PY" "$f" && exit 0
    echo "[fxai] 主脚本失败，走内联兜底"
    break
  fi
done

# 兜底：伴生脚本丢了也能保证「目录 + 首页重定向」不丢（不会改 bundle）
WFROOT=/root/ComfyUI/user/default/workflows
SRC="$WFROOT/千问2.1图片处理.json"
DST="$WFROOT/默认/千问2.1图片处理.json"
mkdir -p "$WFROOT/默认"
if [ -f "$SRC" ] && [ "$(readlink "$DST" 2>/dev/null)" != "../千问2.1图片处理.json" ]; then
  rm -f "$DST" && ln -sfn "../千问2.1图片处理.json" "$DST"
  echo "[fxai] 已补默认工作流软链接"
fi
"$PY" - <<'PY'
import glob, os
MARK_S, MARK_E = "<!--FXAI_DF_START-->", "<!--FXAI_DF_END-->"
snip = (MARK_S + "<script>try{(function(){"
        "if(location.search)return;"
        "if(sessionStorage.getItem('fxai_df_wf'))return;"
        "if(localStorage.getItem('Comfy.PreviousWorkflow')||"
        "localStorage.getItem('Comfy.OpenWorkflowsPaths'))return;"
        "sessionStorage.setItem('fxai_df_wf','1');"
        "location.replace('/?workflows='+encodeURIComponent('%s'));"
        "})()}catch(e){}</script>" % "默认" + MARK_E)
for p in sorted(glob.glob("/root/miniconda3/envs/*/lib/python*/site-packages/"
                          "comfyui_frontend_package/static/index.html")):
    s = open(p, encoding="utf-8").read()
    if MARK_S in s:
        print("[fxai] 已有注入:", p); continue
    s2 = s.replace("<head>", "<head>" + snip, 1) if "<head>" in s else snip + s
    open(p, "w", encoding="utf-8").write(s2)
    print("[fxai] 已补 index.html 重定向:", p)
PY
