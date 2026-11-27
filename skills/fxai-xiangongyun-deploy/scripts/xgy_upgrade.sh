#!/usr/bin/env bash
# ============================================================
#  仙宫云 · 凤希技能包 —— 云端 ComfyUI / 自定义节点在线升级
#  由 fxai-xiangongyun-deploy 通过 SSH 上传执行（xgy_api.py upgrade）
#  权威依据：references/03-装完与排障.md「更新 ComfyUI / 自定义节点」节
#  幂等：已是最新会报告「无需升级」，中断后重跑安全。
#  模式（环境变量）：
#    XGY_UPGRADE_CHECK=1     只检测，不改任何东西
#    XGY_UPGRADE_NO_RESTART=1 升级代码但不重启（默认升级后自动重启）
#    XGY_UPGRADE_NO_COMFY=1  跳过 ComfyUI 本体，只升自定义节点
# ============================================================
set -uo pipefail

CHECK_ONLY="${XGY_UPGRADE_CHECK:-0}"
NO_RESTART="${XGY_UPGRADE_NO_RESTART:-0}"
SKIP_COMFY="${XGY_UPGRADE_NO_COMFY:-0}"

REPORT=/tmp/xgy_upgrade.report
BASE=/tmp/xgy_upgrade.before
TOOLKIT_REPO="https://github.com/fxai666/fxai-toolkit.git"
: > "$REPORT"
say() { echo "$*" | tee -a "$REPORT"; }
step() { echo; echo "======== $* ========" | tee -a "$REPORT"; }

ts() { date '+%H:%M:%S'; }
log() { echo "[$(ts)] $*" | tee -a "$REPORT"; }
err() { echo "[$(ts)] [ERR] $*" | tee -a "$REPORT" >&2; }

# 本机 curl 可能带代理环境变量，强制绕过本地回环
# 用函数 + --noproxy='*'（单引号保护 *）：若写成变量 CURL="... --noproxy * ..."，
# 在 $CURL 未加引号展开时 * 会被 shell 展开成当前目录文件名，污染 -w '%{http_code}' 断言。
curl_local() { curl -s --noproxy='*' --max-time 20 "$@"; }

############################################################
# 1) 定位 ComfyUI
############################################################
detect_comfy() {
  local c
  for c in /root/ComfyUI /root/ComfyUI-* /workspace/ComfyUI /workspace/ComfyUI-* \
           /home/*/ComfyUI /root/*/ComfyUI /opt/ComfyUI /data/ComfyUI /data/*/ComfyUI; do
    [ -d "$c/custom_nodes" ] && { echo "$c"; return 0; }
  done
  c=$(find / -maxdepth 5 -type d -name custom_nodes -path '*ComfyUI*' 2>/dev/null | head -n1)
  [ -n "$c" ] && { echo "$(dirname "$c")"; return 0; }
  return 1
}
COMFY_DIR=$(detect_comfy || true)
if [ -z "$COMFY_DIR" ]; then
  err "没找到 ComfyUI（找不到 custom_nodes 目录），无法升级。脚本未做任何改动。"
  exit 2
fi
CN_DIR="$COMFY_DIR/custom_nodes"
log "ComfyUI = $COMFY_DIR"

comfy_version() {
  tr -d " \t" < "$COMFY_DIR/comfyui_version.py" 2>/dev/null \
    | sed -n 's/^__version__=//p' | head -n1 | tr -d '"'
}
CV=$(comfy_version)
log "当前 ComfyUI 版本：${CV:-未知}"

############################################################
# 2) 升级前基线：object_info 节点数（升级后不该变少）
############################################################
node_counts() {
  curl_local http://127.0.0.1:8188/object_info 2>/dev/null \
    | python3 -c "import sys,json
try:
    d=json.load(sys.stdin)
    print('%d %d' % (len(d), sum(1 for k in d if k.startswith('FxAi'))))
except Exception:
    print('-1 -1')" 2>/dev/null || echo "-1 -1"
}
read -r BEFORE_ALL BEFORE_FXAI <<<"$(node_counts)"
log "升级前节点数：总 ${BEFORE_ALL} / FxAi* ${BEFORE_FXAI}（-1 表示 ComfyUI 未启动或探测失败）"
echo "${BEFORE_ALL} ${BEFORE_FXAI}" > "$BASE"

############################################################
# 3) 检测：各仓库落后多少
############################################################
step "检测落后情况（git fetch 后比对，不改代码）"
check_repo() {
  local dir="$1" label="$2"
  if [ ! -d "$dir/.git" ]; then
    say "  [$label] 无 .git（zip 装的目录）——升级时会 mv 成 .bak.<时间戳> 后重新 clone"
    return
  fi
  git -C "$dir" fetch origin --quiet 2>/dev/null || log "  [$label] fetch 失败（网络抖动？保留现状）"
  local cnt
  cnt=$(git -C "$dir" rev-list --left-right --count HEAD...@{u} 2>/dev/null)
  if [ -n "$cnt" ]; then
    say "  [$label] 落后/领先(本地...上游)：$cnt"
  else
    say "  [$label] 无法比对（可能是 shallow clone）——升级时会先补全历史"
  fi
}
check_repo "$COMFY_DIR" "ComfyUI 本体"
check_repo "$CN_DIR/fxai-toolkit" "fxai-toolkit"

# 依赖是否变化（只报告，绝不无脑 install -r）
step "依赖变化检测（只报告，不自动全量安装）"
if [ -d "$COMFY_DIR/.git" ]; then
  DEP_CHANGED=$(git -C "$COMFY_DIR" log --name-only --pretty=format: 'HEAD..@{u}' 2>/dev/null \
                | grep -Ei 'requirements|pyproject' | sort -u | head -10)
  if [ -n "$DEP_CHANGED" ]; then
    say "  ⚠ 上游改过依赖文件：$DEP_CHANGED"
    say "    脚本不会自动 pip install -r（会换掉 cu132 的 torch / 钉死的前端版本）。"
    say "    如确实需要，人工核对后只装变化项，并复核 system_stats 的 installed==required。"
  else
    say "  上游未改依赖文件，无需装任何包。"
  fi
else
  say "  ComfyUI 非 git 仓库，跳过依赖比对。"
fi

if [ "$CHECK_ONLY" = "1" ]; then
  step "检测完成（XGY_UPGRADE_CHECK=1，未改任何东西）"
  say "报告：$REPORT"
  exit 0
fi

############################################################
# 4) 升级 ComfyUI 本体（git pull --ff-only + 两个坑处理）
############################################################
if [ "$SKIP_COMFY" != "1" ] && [ -d "$COMFY_DIR/.git" ]; then
  step "升级 ComfyUI 本体"
  # 坑 1：工作区脏（有人删过 blueprints/*.json）pull 会拒绝 → 只恢复已跟踪文件
  if [ -n "$(git -C "$COMFY_DIR" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
    log "  工作区有改动，先 checkout 恢复已跟踪文件（不动用户未跟踪文件）"
    git -C "$COMFY_DIR" status --porcelain --untracked-files=no 2>/dev/null | head -5 | sed "s/^/    /" | tee -a "$REPORT"
    git -C "$COMFY_DIR" checkout -- . 2>&1 | tail -n 2 | sed "s/^/    /" | tee -a "$REPORT"
  fi
  # 坑 2：shallow clone 算不出 merge-base，pull --ff-only 必报「Not possible to fast-forward」
  if [ -f "$COMFY_DIR/.git/shallow" ]; then
    log "  shallow clone（深度 $(wc -l < "$COMFY_DIR/.git/shallow" | tr -d ' ')），先补全历史"
    git -C "$COMFY_DIR" fetch --unshallow origin >/dev/null 2>&1 \
      || git -C "$COMFY_DIR" fetch --deepen 3000 origin >/dev/null 2>&1 || true
    [ -f "$COMFY_DIR/.git/shallow" ] && log "  仍是 shallow（不致命，继续尝试 pull）" || log "  历史已补全"
  fi
  PULL_OUT=$(git -C "$COMFY_DIR" pull --ff-only origin master 2>&1 | tail -n 6)
  echo "$PULL_OUT" | sed "s/^/    /" | tee -a "$REPORT"
  if echo "$PULL_OUT" | grep -qiE 'error|fatal|not possible'; then
    err "  ComfyUI 本体 pull 失败，保留现有版本（不影响节点升级）"
  else
    log "  ComfyUI 本体已拉到最新（版本 $(comfy_version)）"
  fi
elif [ "$SKIP_COMFY" = "1" ]; then
  log "跳过 ComfyUI 本体（XGY_UPGRADE_NO_COMFY=1）"
else
  log "ComfyUI 非 git 仓库，跳过本体升级"
fi

############################################################
# 5) 升级自定义节点 fxai-toolkit
############################################################
step "升级自定义节点 fxai-toolkit"
TK="$CN_DIR/fxai-toolkit"
if [ -d "$TK/.git" ]; then
  log "  git 装 → pull --ff-only"
  TKO=$(git -C "$TK" pull --ff-only 2>&1 | tail -n 6)
  echo "$TKO" | sed "s/^/    /" | tee -a "$REPORT"
  if echo "$TKO" | grep -qiE 'error|fatal|not possible'; then
    # shallow 兜底
    if [ -f "$TK/.git/shallow" ]; then
      log "  疑似 shallow clone，补全历史后重试"
      git -C "$TK" fetch --unshallow origin >/dev/null 2>&1 || git -C "$TK" fetch --deepen 3000 origin >/dev/null 2>&1 || true
      git -C "$TK" pull --ff-only 2>&1 | tail -n 4 | sed "s/^/    /" | tee -a "$REPORT"
    else
      err "  fxai-toolkit pull 失败，保留现有版本"
    fi
  else
    log "  fxai-toolkit 已拉到最新"
  fi
else
  BAK="$TK.bak.$(date +%Y%m%d_%H%M%S)"
  log "  zip 装（无 .git）→ 旧目录 mv 到 $BAK 可回滚，再 clone"
  [ -d "$TK" ] && mv "$TK" "$BAK" 2>/dev/null
  OK=0
  for i in 1 2 3; do
    log "  clone 第 $i 次"
    git clone --depth 1 "$TOOLKIT_REPO" "$TK" > /tmp/xgy_up_git.log 2>&1 && { OK=1; break; }
    tail -n 2 /tmp/xgy_up_git.log | sed "s/^/    /" | tee -a "$REPORT"
    rm -rf "$TK"; sleep 3
  done
  if [ "$OK" != "1" ]; then
    err "  clone 三次都失败，回滚旧目录"
    [ -d "$BAK" ] && mv "$BAK" "$TK"
  else
    log "  clone 成功，旧目录保留在 $BAK（验收通过后清理）"
    echo "$BAK" > /tmp/xgy_upgrade.bakpath
  fi
fi

# 清残留 pycache：残留 pyc 会让节点看起来加载了其实没换代码
find "$CN_DIR" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
log "  已清理 custom_nodes 下的 __pycache__ 残留"

# toolkit 唯一已知依赖 soundfile（幂等，已装会跳过；绝不整包 install -r）
PY=$(readlink -f /proc/$(pgrep -f "main.py --enable-cors-header" | head -1)/exe 2>/dev/null || true)
if [ -n "$PY" ] && [ -x "$PY" ]; then
  "$PY" -m pip install -q soundfile >/dev/null 2>&1 && log "  soundfile 依赖已就绪" || log "  soundfile 已装或安装跳过（不致命）"
else
  log "  未找到 ComfyUI 的 python 进程，跳过依赖确认（重启后若 IMPORT FAILED 再手动装 soundfile）"
fi

############################################################
# 6) 重启（复用现成脚本，不手敲）
############################################################
if [ "$NO_RESTART" = "1" ]; then
  step "跳过重启（XGY_UPGRADE_NO_RESTART=1）——新代码需手动重启才生效"
else
  step "重启 ComfyUI（现成脚本 xgy_restart_comfy.sh）"
  if [ -f /root/xgyscripts/xgy_restart_comfy.sh ]; then
    bash /root/xgyscripts/xgy_restart_comfy.sh 2>&1 | tail -n 15 | sed "s/^/    /" | tee -a "$REPORT"
    log "  脚本 sleep 45 已过；再等 25 秒让启动任务跑完"
    sleep 25
  else
    err "  没找到 /root/xgyscripts/xgy_restart_comfy.sh，请手动重启：screen -S sd -X quit 后走 start.d/comfyui.sh"
  fi
fi

############################################################
# 7) 验收：四服务探活 + 节点数不减少
############################################################
step "验收"
H=$(curl_local -o /dev/null -w '%{http_code}' http://127.0.0.1:8188/fxai/health 2>/dev/null || echo 000)
S=$(curl_local -o /dev/null -w '%{http_code}' http://127.0.0.1:8188/system_stats 2>/dev/null || echo 000)
say "  /fxai/health = $H   /system_stats = $S"
read -r AFTER_ALL AFTER_FXAI <<<"$(node_counts)"
say "  升级后节点数：总 ${AFTER_ALL} / FxAi* ${AFTER_FXAI}（升级前 ${BEFORE_ALL}/${BEFORE_FXAI}）"
FAIL=0
[ "$H" = "200" ] || { err "  /fxai/health 非 200"; FAIL=1; }
[ "$S" = "200" ] || { err "  /system_stats 非 200"; FAIL=1; }
if [ "$BEFORE_ALL" -gt 0 ] 2>/dev/null && [ "$AFTER_ALL" -lt "$BEFORE_ALL" ] 2>/dev/null; then
  err "  节点总数比升级前少了（${BEFORE_ALL} → ${AFTER_ALL}），可能有 IMPORT FAILED"; FAIL=1
fi
if [ "$BEFORE_FXAI" -gt 0 ] 2>/dev/null && [ "$AFTER_FXAI" -lt "$BEFORE_FXAI" ] 2>/dev/null; then
  err "  FxAi* 节点数比升级前少了（${BEFORE_FXAI} → ${AFTER_FXAI}），检查 soundfile/日志"; FAIL=1
fi

# 验收通过才清 zip 装留下的 .bak（确认无脏改动，失败保留供回滚）
if [ "$FAIL" = "0" ] && [ -f /tmp/xgy_upgrade.bakpath ]; then
  BAK=$(cat /tmp/xgy_upgrade.bakpath)
  if [ -n "$BAK" ] && [ -d "$BAK" ]; then
    rm -rf "$BAK" && log "  验收通过，已清理旧目录备份 $BAK"
  fi
  rm -f /tmp/xgy_upgrade.bakpath
fi

if [ "$FAIL" = "0" ]; then
  step "✅ 升级完成且验收通过"
else
  step "⚠ 升级已应用但验收有 FAIL 项——见上面 [ERR]，必要时用 .bak 目录回滚"
fi
say "完整报告：$REPORT"
exit $FAIL
