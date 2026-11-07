#!/bin/bash
# 凤希AI 云端全套语音服务 · 统一启动脚本
#
# 设计原则（照仙宫云那一套来，别画蛇添足）：
#   - 端口固定：8188 ComfyUI / 8086 千问TTS / 8182 CosyVoice3 / 8185 Whisper
#   - 全部 0.0.0.0 监听（公网端口映射进来必须）
#   - 模型按需加载 + 空闲自动卸载，规避跟 ComfyUI 抢 4090 的 24G 显存
#   - setsid + </dev/null + 全流重定向：否则后台进程会攥着 SSH channel 的 pipe 不放
#
# 用法：
#   bash /root/fxai_services.sh start                     启动全部
#   bash /root/fxai_services.sh stop                      停全部
#   bash /root/fxai_services.sh status                    看状态
#   bash /root/fxai_services.sh restart comfy            只重启某个（comfy|tts|whisper|cosy）
set -u

QWEN_DIR=/root/qwen3tts
WHISPER_DIR=/root/whisper
CV_DIR=/root/cosyvoice
COMFY_DIR=/root/ComfyUI
QWEN_PY=/root/miniconda3/bin/python
CV_PY=/root/miniconda3/envs/cosyvoice/bin/python

log() { echo "[fxai] $*"; }

# 按 pid 精确杀，不用 pkill -f（pkill -f 会匹配到 bash 命令行自己，把自己杀了）
kill_by_pattern() {
  local pat="$1"
  for p in $(pgrep -f "$pat" 2>/dev/null); do
    [ "$p" != "$$" ] && kill "$p" 2>/dev/null
  done
  sleep 2
}

# start_one <日志名> <端口> <命令...>
start_one() {
  local name="$1" port="$2"; shift 2
  if ss -ltnp 2>/dev/null | grep -q ":$port "; then
    log "$name 已在跑（$port），跳过"
    return 0
  fi
  log "启动 $name（端口 $port）…"
  setsid "$@" </dev/null >"/tmp/${name}.log" 2>&1 &
  sleep 8
  if ss -ltnp 2>/dev/null | grep -q ":$port "; then
    log "$name 启动成功"
  else
    log "$name 启动失败，日志：/tmp/${name}.log"
    tail -8 "/tmp/${name}.log" 2>/dev/null
  fi
}

start_comfy() {
  # 必须 --listen 0.0.0.0，否则公网端口映射进来连不上（8086 那次改过一次）
  start_one comfy 8188 "$QWEN_PY" "$COMFY_DIR/main.py" \
    --listen 0.0.0.0 --port 8188
}

start_tts() {
  start_one tts 8086 "$QWEN_PY" -u "$QWEN_DIR/qwen3tts_server.py" --host 0.0.0.0 --port 8086
}

start_whisper() {
  start_one whisper 8185 "$QWEN_PY" -u "$WHISPER_DIR/whisper_server.py" \
    --host 0.0.0.0 --port 8185 --idle-timeout 1800
}

start_cosy() {
  if [ ! -x "$CV_PY" ]; then
    log "CosyVoice 环境不存在：$CV_PY（先跑 _install_cv_env.sh）"
    return 1
  fi
  if [ ! -d "$CV_DIR/pretrained_models/Fun-CosyVoice3-0.5B" ]; then
    log "CosyVoice3 模型未下载，跳过（先下模型）"
    return 1
  fi
  start_one cosy 8182 "$CV_PY" "$CV_DIR/apiv3.py" --host 0.0.0.0 --port 8182 \
    --model_dir pretrained_models/Fun-CosyVoice3-0.5B
}

status_one() {
  local name="$1" port="$2" path="$3"
  if ss -ltnp 2>/dev/null | grep -q ":$port "; then
    printf '  %-10s %-6s ✅ 监听中  ' "$name" "$port"
    curl -s -m 8 "http://127.0.0.1:$port$path" 2>/dev/null | head -c 120
    echo
  else
    printf '  %-10s %-6s ❌ 未监听\n' "$name" "$port"
  fi
}

case "${1:-status}" in
  start)
    log "启动全部服务…"
    start_comfy
    start_tts
    start_whisper
    start_cosy
    ;;
  stop)
    log "停止全部服务…"
    kill_by_pattern "[q]wen3tts_server.py"
    kill_by_pattern "[w]hisper_server.py"
    kill_by_pattern "[a]piv3.py"
    kill_by_pattern "[C]omfyUI/main.py"
    log "已停"
    ;;
  restart)
    case "${2:-}" in
      comfy)   kill_by_pattern "[C]omfyUI/main.py"; start_comfy ;;
      tts)     kill_by_pattern "[q]wen3tts_server.py"; start_tts ;;
      whisper) kill_by_pattern "[w]hisper_server.py";   start_whisper ;;
      cosy)    kill_by_pattern "[a]piv3.py";             start_cosy ;;
      *)       echo "用法: $0 restart {comfy|tts|whisper|cosy}"; exit 1 ;;
    esac
    ;;
  status)
    echo "=== 凤希AI 云端服务状态 ==="
    status_one ComfyUI   8188 /fxai/health
    status_one 千问TTS    8086 /health
    status_one Whisper   8185 /health
    status_one CosyVoice 8182 /health
    echo
    nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null | \
      sed 's/^/  显存 /'
    df -h / | tail -1 | awk '{print "  磁盘 "$3" 已用 "$4" 可用 "$5}'
    ;;
  *)
    echo "用法: $0 {start|stop|restart <tts|whisper|cosy>|status}"; exit 1 ;;
esac