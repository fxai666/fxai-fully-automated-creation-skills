#!/usr/bin/env bash
# ============================================================
#  仙宫云 · 凤希技能包一键部署
#  由 WorkBuddy 技能 fxai-xiangongyun-deploy 生成
#  分组: img   变体: full   预计新增: 21.0G
#  幂等：已存在且体积达标的文件会跳过；中断后重跑即可续上。
# ============================================================
set -uo pipefail

MIRROR="${XGY_MIRROR:-http://hf.x-gpu.com}"
FALLBACK="${XGY_FALLBACK:-https://huggingface.co}"
MIRROR2="${XGY_MIRROR2:-https://www.modelscope.cn/models}"
RESUME_MS="1"
RESUME_HF="0"
RESUME_MIRROR="0"
COMFY_DIR="${XGY_COMFY_DIR:-}"
UPGRADE_COMFY="0"
PARALLEL="1"
RC_FILE=/tmp/otai_deploy.rc
BAD_FILE=/tmp/otai_deploy.bad
OK_FILE=/tmp/otai_deploy.ok
SKIP_FILE=/tmp/otai_deploy.skip
rm -f "$BAD_FILE" "$OK_FILE" "$SKIP_FILE"
echo 0 > "$RC_FILE"
finish() { rc=$?; echo $rc > "$RC_FILE"; }
trap finish EXIT

ts() { date '+%H:%M:%S'; }
log() { echo "[$(ts)] $*"; }
err() { echo "[$(ts)] [ERR] $*" >&2; }
step() { echo; echo "======== $* ========"; }
mark() { echo "$2" >> "$1"; }

############################################################
# 1) 定位 ComfyUI
############################################################
detect_comfy() {
  if [ -n "$COMFY_DIR" ] && [ -d "$COMFY_DIR" ]; then echo "$COMFY_DIR"; return 0; fi
  local c
  for c in /root/ComfyUI /root/ComfyUI-* /workspace/ComfyUI /workspace/ComfyUI-* \
           /home/*/ComfyUI /root/*/ComfyUI /opt/ComfyUI /data/ComfyUI /data/*/ComfyUI; do
    [ -d "$c" ] && { echo "$c"; return 0; }
  done
  c=$(find / -maxdepth 5 -type d -name custom_nodes -path '*ComfyUI*' 2>/dev/null | head -n1)
  [ -n "$c" ] && { echo "$(dirname "$c")"; return 0; }
  return 1
}

COMFY_DIR=$(detect_comfy || true)
if [ -z "$COMFY_DIR" ]; then
  log "未找到 ComfyUI，克隆官方仓库到 /root/ComfyUI（需 >= 0.30.0，H3 支持起点）"
  command -v git >/dev/null 2>&1 || { err "没有 git：apt-get update && apt-get install -y git"; }
  [ -d /root/ComfyUI ] || git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git /root/ComfyUI
  COMFY_DIR=/root/ComfyUI
fi
log "ComfyUI = $COMFY_DIR"
MODELS_DIR="$COMFY_DIR/models"
CN_DIR="$COMFY_DIR/custom_nodes"
mkdir -p "$MODELS_DIR" "$CN_DIR"

############################################################
# 1b) ComfyUI 版本
############################################################
comfy_version() {
  tr -d " \t" < "$COMFY_DIR/comfyui_version.py" 2>/dev/null \
    | sed -n 's/^__version__=//p' | head -n1 | tr -d '"'
}
CV=$(comfy_version)
log "ComfyUI 版本：${CV:-未知}  （本技能包要求 >= 0.30.0）"
CV_MAJ=${CV%%.*}
CV_MIN=$(echo "${CV:-0.0.0}" | cut -d. -f2)
REQ_MAJ=0
REQ_MIN=30
WANT_MAJ=${REQ_MAJ}
WANT_MIN=${REQ_MIN}
if [ -z "${CV_MAJ:-}" ] || [ "${CV_MAJ:-0}" -lt "$WANT_MAJ" ] 2>/dev/null \
   || { [ "${CV_MAJ:-0}" -eq "$WANT_MAJ" ] && [ "${CV_MIN:-0}" -lt "$WANT_MIN" ]; }; then
  err "  ⚠ ComfyUI 版本过低（当前 $CV / 需要 0.30.0）。H3 / 千问2.1 / YuE2 相关节点会缺失。加 --upgrade-comfy 可升级。"
fi

if [ "$UPGRADE_COMFY" = "1" ]; then
  log "升级 ComfyUI（git pull --ff-only）..."
  if git -C "$COMFY_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    # 坑 1：工作区脏（有人删过 blueprints/*.json）会让 pull 直接拒绝，先恢复被删的文件。
    if [ -n "$(git -C "$COMFY_DIR" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
      log "  工作区有改动，先 checkout 恢复（只动已跟踪文件的删除/修改）"
      git -C "$COMFY_DIR" status --porcelain --untracked-files=no 2>/dev/null | head -5 | sed "s/^/    /"
      git -C "$COMFY_DIR" checkout -- . 2>&1 | tail -n 2 | sed "s/^/    /"
    fi
    # 坑 2（真正的原因）：--depth N 的 shallow clone 算不出 merge-base，
    #     git 会误判成历史分叉，pull --ff-only 必然报 "Not possible to fast-forward"。
    #     报错文本带「领先 N 提交」（深度 67 的 ComfyUI 报「领先 5988 提交」）时，
    #     按缺历史处理，不是真分叉。
    #     解法：先 fetch --unshallow 把历史补全，pull 就正常了。
    if [ -f "$COMFY_DIR/.git/shallow" ]; then
      log "  是 shallow clone（深度 $(wc -l < "$COMFY_DIR/.git/shallow" | tr -d " ")），先补全历史 ..."
      git -C "$COMFY_DIR" fetch --unshallow origin >/dev/null 2>&1 || git -C "$COMFY_DIR" fetch --deepen 3000 origin >/dev/null 2>&1 || true
      [ -f "$COMFY_DIR/.git/shallow" ] && log "  仍是 shallow（不致命，继续尝试 pull）" || log "  历史已补全 ✓"
    fi
    git -C "$COMFY_DIR" pull --ff-only 2>&1 | tail -n 6 | sed "s/^/    /"
  else
    log "  不是 git 仓库，跳过升级（不致命）"
  fi
  log "升级后版本：$(comfy_version)（需要重启 ComfyUI 才生效）"
fi

# 找可用的 python 解释器。**必须用跑 ComfyUI 的那个**，系统 python3 往往没 pip 也没 torch。
# 坑：screen/tmux 的 cmdline 里也含 'python main.py'，pgrep -f 会一起命中，
#     /proc/<screen_pid>/exe 指向 screen 而不是 python —— 所以必须按 exe 名过滤。
detect_py() {
  local c p b
  # 1) 从正在运行的 ComfyUI 进程反查解释器（最准）
  for p in $(pgrep -f "main.py" 2>/dev/null); do
    c=$(readlink -f /proc/$p/exe 2>/dev/null) || continue
    b=$(basename "$c" 2>/dev/null)
    case "$b" in python*) ;; *) continue ;; esac
    [ -x "$c" ] && { echo "$c"; return 0; }
  done
  # 2) conda / venv 里带 torch 的解释器
  for c in /root/miniconda3/envs/*/bin/python /opt/conda/envs/*/bin/python \
           /root/anaconda3/envs/*/bin/python "$COMFY_DIR/venv/bin/python" "$COMFY_DIR/.venv/bin/python"; do
    [ -x "$c" ] || continue
    [ -d "$(dirname "$(dirname "$c")")/lib/python"*/site-packages/torch ] || continue
    echo "$c"; return 0
  done
  # 3) 兜底：PATH 里的 python3
  for c in "$(command -v python3)" "$(command -v python)"; do
    [ -x "$c" ] && { echo "$c"; return 0; }
  done
  return 1
}

PY=$(detect_py || true)
if [ -z "$PY" ]; then err "找不到 ComfyUI 用的 python 解释器，节点依赖装不上（不致命，可重启 ComfyUI 后手动补）";
else log "python = $PY ($($PY -V 2>&1))"; fi
if [ -n "$PY" ] && ! "$PY" -m pip -V >/dev/null 2>&1; then
  log "  该解释器没有 pip，尝试 ensurepip ..."
  "$PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi

HAVE_CURL=""; command -v curl >/dev/null 2>&1 && HAVE_CURL=1
if [ -z "$HAVE_CURL" ]; then
  err "curl 没有，先装：apt-get update && apt-get install -y curl"
fi
log "下载器：curl=${HAVE_CURL:-无}（不用 wget：镜像源不支持 Range 续传）"

############################################################
# 2) 模型清单（由本地 assets/models.json 编译而来）
############################################################
# 格式: id|repo|ms_repo|仓库内路径|目标子目录|落盘文件名|体积下限|可选|分组|说明|直链|真实字节|源顺序
#
# 第 11、12 列和第 13 列都是 probe_sources.py **提前探测**的结果：
#   哪个源真有这个文件、真实 URL、真实大小、**支不支持 Range 续传**。
# 源顺序、真实 URL、真实大小、Range 能力都由 probe_sources.py 探测后写死
# （2026-10-03 探测结果），运行时照表下单。
#
# 第 13 列形如 ms:1,hf:0 —— 冒号后是探测时拿到的 Range 能力：
#   1 = 该源返回 206，用 curl -C - 续传；0 = 返回 200 全量，续传位记 0。
# Range 位 0 的源上跑 curl -C - 会把目标文件从 0 静默重写且日志无报错
# （HF 官方源会把已下到 8.1 GB 的 CLIP 打回 27 MB），所以续传前先按本列校验。
# 源顺序固定为「Range 位 1 的排前面，Range 位 0 的排在最后、已有进度时直接跳过」。
MODELS=(
  "qwen21_unet|Comfy-Org/Qwen-Image-2.1|Comfy-Org/Qwen-Image-2.1|diffusion_models/qwen_image_2.1_bf16.safetensors|diffusion_models|qwen_image_2.1_bf16.safetensors|13250000000|0|img|千问2.1 扩散模型 UNet/UNETLoader 槽位|https://www.modelscope.cn/models/Comfy-Org/Qwen-Image-2.1/resolve/master/diffusion_models/qwen_image_2.1_bf16.safetensors|14230280616|ms"
  "qwen21_clip|Comfy-Org/Qwen-Image-2.1|Comfy-Org/Qwen-Image-2.1|text_encoders/qwen3vl_8b_int8_convrot.safetensors|text_encoders|qwen3vl_8b_int8_convrot.safetensors|8710000000|0|img|Qwen3-VL 8B int8 文本编码器 / CLIPLoader 槽位|https://www.modelscope.cn/models/Comfy-Org/Qwen-Image-2.1/resolve/master/text_encoders/qwen3vl_8b_int8_convrot.safetensors|9350798360|ms"
  "qwen21_vae|Comfy-Org/Qwen-Image-2.1|Comfy-Org/Qwen-Image-2.1|vae/qwen_image_2.1_vae_bf16.safetensors|vae|qwen_image_2.1_vae_bf16.safetensors|630000000|0|img|千问2.1 VAE / VAELoader 槽位|https://www.modelscope.cn/models/Comfy-Org/Qwen-Image-2.1/resolve/master/vae/qwen_image_2.1_vae_bf16.safetensors|675509688|ms"
)

NEED=0
for row in "${MODELS[@]}"; do
  IFS="|" read -r id repo msrepo path tdir tname minb opt grp note <<<"$row"
  [ "$opt" = "1" ] && continue
  NEED=$((NEED + minb))
done
NEED=$((NEED * 12 / 10))
AVAIL=$(df -PB1 "$MODELS_DIR" 2>/dev/null | tail -n1 | awk "{print \$4}")
AVAIL=${AVAIL:-0}
step "磁盘/显存预检"
if command -v nvidia-smi >/dev/null 2>&1; then
  VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d " ")
  [ -n "$VRAM" ] && log "GPU 显存 ${VRAM} MiB（单个模型超过这个数就要靠节点的分块/卸载，24G 卡建议用 --variant pruned 之类的小档）"
fi
log "本次预计新增 $((NEED/1073741824)) GB（含 20% 余量），目标盘可用 $((AVAIL/1073741824)) GB"
df -h "$MODELS_DIR" | tail -n1 | sed "s/^/  /"
if [ "$AVAIL" -lt "$NEED" ]; then
  err "磁盘不够，这批模型撑不下。"
  err "  省事：只装当前要用的分组，例如 --group img（可省掉 30+GB 的 H3 主模型）。"
  err "  或：仙宫云「关机（释放GPU）→ 开机」会自动免费扩容系统盘。"
  err "  或：挂数据盘后用 --set-dir <模型id>=<子目录> 把大模型放到数据盘。"
fi

############################################################
# 3) 自定义节点
############################################################
step "安装自定义节点"
install_node() {
  local repo="$1" dir="$2" i
  local target="$CN_DIR/$dir"
  if [ -d "$target/.git" ]; then
    log "更新 $dir ..."
    git -C "$target" pull --ff-only >/dev/null 2>&1 || log "  pull 失败，保留现有版本（不致命）"
  else
    rm -rf "$target"
    for i in 1 2 3; do
      log "克隆 $dir（第 $i 次）..."
      git clone --depth 1 "$repo" "$target" > /tmp/otai_git.log 2>&1
      [ -d "$target/.git" ] && break
      tail -n 2 /tmp/otai_git.log | sed "s/^/    /"
      log "    失败，3 秒后重试（GitHub TLS 握手被掐是常见抖动）"
      rm -rf "$target"; sleep 3
    done
    if [ ! -d "$target/.git" ]; then err "三次都克隆失败：$repo"; return 1; fi
  fi
  if [ -f "$target/requirements.txt" ] && [ -n "$PY" ]; then
    log "  安装依赖 requirements.txt"
    if "$PY" -m pip install -r "$target/requirements.txt" > /tmp/otai_pip.log 2>&1; then
      log "  依赖装好"
    else
      err "  依赖安装失败，末尾输出："
      tail -n 8 /tmp/otai_pip.log 2>/dev/null | sed "s/^/    /"
      err "  不致命；重启 ComfyUI 后看日志，或手动：$PY -m pip install -r $target/requirements.txt"
    fi
  fi
  return 0
}

install_node "https://github.com/fxai666/fxai-toolkit.git" "fxai-toolkit" || true

NODE_MISSING=0
for d in fxai-toolkit; do
  [ -d "$CN_DIR/$d" ] || { err "节点未装成功：$d"; NODE_MISSING=$((NODE_MISSING+1)); }
done
log "custom_nodes 现有内容："
ls -1 "$CN_DIR" 2>/dev/null | sed "s/^/  - /"

############################################################
# 5) 验收
############################################################
step "验收清单"
printf "%-48s %14s  %s\n" "文件" "大小(字节)" "状态"
for row in "${MODELS[@]}"; do
  IFS="|" read -r id repo msrepo path tdir tname minb opt grp note <<<"$row"
  [ -z "${id:-}" ] && continue
  f="$MODELS_DIR/$tdir/$tname"
  if [ -f "$f" ]; then
    sz=$(stat -c%s "$f"); st="OK"
    [ "$sz" -lt "$minb" ] && st="偏小!"
  else
    sz="-"; st="缺失"
  fi
  printf "%-48s %14s  %s\n" "$tname" "$sz" "$st"
done

echo "各模型目录占用："
du -sh "$MODELS_DIR"/* 2>/dev/null | sort -h | tail -n 20 | sed "s/^/  /"

echo "凤希自研节点实际读模型的目录（模型报找不到时看这里确认真实目录名）："
for f in "$CN_DIR"/fxai-toolkit/fxai_minimax_upscaler*.py "$CN_DIR"/fxai-toolkit/fxai_mel_roformer*.py; do
  [ -f "$f" ] || continue
  echo "  [$(basename "$f")]"
  grep -nE "latent_upscale|diffusion_models|audio_encoders|get_folder_paths|folder_paths" "$f" 2>/dev/null | head -n 12 | sed "s/^/    /"
done

############################################################
# 6) 收尾
############################################################
count_lines() { if [ -f "$1" ]; then wc -l < "$1" | tr -d " "; else echo 0; fi; }
NBAD=$(count_lines "$BAD_FILE")
NOK=$(count_lines "$OK_FILE")
NSKIP=$(count_lines "$SKIP_FILE")
log "本次新装 $NOK 个，已存在跳过 $NSKIP 个，失败 $NBAD 个"
if [ "${NODE_MISSING:-0}" -gt 0 ]; then
  err "有 $NODE_MISSING 个自定义节点没装上。工作流会报节点不存在，先解决网络或重跑 --no-models。"
fi
if [ "$NBAD" -gt 0 ] || [ "${NODE_MISSING:-0}" -gt 0 ]; then
  err "失败清单："
  sed "s/^/  - /" "$BAD_FILE"
  err "常见原因：① 镜像未收录该模型（401）→ 找仙宫云客服收录；② 磁盘满；③ 实例网络不通 huggingface 官方源"
  exit 1
fi
log "全部就位。下一步：重启 ComfyUI 让新节点生效，再把本机技能配置的 comfy_url 指向本实例。"
exit 0
