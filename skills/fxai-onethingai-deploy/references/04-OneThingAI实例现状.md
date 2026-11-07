# OneThingAI 实例现状与踩过的坑

实例 `cz80fhheabvmlvio-ev1hdtjm`（区域 8，4090，按量 0.033/min）
SSH：`ssh -p 21706 root@proxy-ai.onethingai.com`

## 硬件与系统
- GPU：NVIDIA GeForce RTX 4090，24 GB
- **系统盘只有 70 GB**（仙宫云是 100 GB × 卡数）→ 全装 79.5 GB **装不下**，必须分组
- Python：`/root/miniconda3/bin/python`（3.10.13），系统 python3 是 3.10.12
- torch 2.7.1+cu126，CUDA 可用
- pip 源已配阿里云镜像 + modelscope/hf-mirror 均可用

## 🚨 GitHub 直连不通（本机通、远端不通）
容器网络受限，直连 `github.com` 会在 TLS 握手阶段断：
```
fatal: unable to access '...': GnuTLS recv error (-110): The TLS connection was non-properly terminated.
```
**所有 git clone 必须走镜像**：
```bash
git clone https://gh-proxy.com/https://github.com/<owner>/<repo>.git
# ComfyUI 主仓已改 remote：
git remote set-url origin https://gh-proxy.com/https://github.com/comfyanonymous/ComfyUI.git
```
pip / 模型下载不受影响（阿里云、hf-mirror、modelscope 都通）。

## ComfyUI 现状（2026-10-15 清理重建 → 16:40 改端口）
- 路径 `/root/ComfyUI`，**版本已升到 v0.40.0**（原镜像 v0.35.0）
- ✅ **端口已从 7860 改成 8188，且改为 `--listen 0.0.0.0`**（公网映射指向 8188，
  只改端口不改成外部照样进不来 —— 这两个必须一起改）
- 启动命令（**唯一正确的写法**，改端口时照抄）：
  ```bash
  cd /root/ComfyUI
  setsid /root/miniconda3/bin/python -u main.py --port 8188 --listen 0.0.0.0 \
    --disable-auto-launch --enable-cors-header </dev/null >/tmp/otai_comfy.log 2>&1 &
  ```
- ✅ **公网已实测通**：`/fxai/health` 与 `/system_stats` 都返回 200，
  `system_stats` 报 ComfyUI `0.40.0` + `NVIDIA GeForce RTX 4090`（vram 25.2G）
- 后台跑必须 `setsid` + `</dev/null` + 全部流重定向，否则子进程攥着 SSH channel 的 pipe，
  `exec_command` 永远等不到 EOF，直接 PipeTimeout / SIGTERM。

### 🚨 清理 ComfyUI 重复进程的正确做法
`deploy.py run/probe` **每次执行都会自己拉一个 ComfyUI 起来**（跑在 7860），
和手动起的 8188 进程并存，排查时容易误判。

- 杀旧进程**勿按 pid 排序取最小去保留** —— 最小那个常是
  `bash -c cd ... && setsid python` 的 **bash 包装进程**，杀真正的 python 反而把服务弄没了。
  这个坑踩过一次：结果 8188 和 7860 全停，ComfyUI 彻底下线。
- 正确：先按 `pgrep -f 'main[.]py --port 7860'` 杀干净的旧端口进程，
  再 `setsid` 启一个 8188，最后 `curl 127.0.0.1:8188/fxai/health` 确认。
- `ps | grep main.py` 会连 bash 壳一起列出来，`pgrep -cf` 也会把壳数进去，
  **判断"是否重复"要看 `ss -ltnp | grep 8188` 里的 pid 数量，不是 grep 出来的行数**。

## 端口映射与访问地址（用户 16:30 在控制台手工加好）
`customPort` 已在控制台加好四个，API 读得到，地址全部可用：

| 端口 | 服务 | 公网地址 | 状态 |
|---|---|---|---|
| 8188 | ComfyUI | `n84-dkefmz999ukjgya3f-3dtzfdsd-custom.service.onethingrobot.com` | ✅ **已通** |
| 8086 | 千问 TTS | `…-3dtefdso-custom.service.onethingrobot.com` | ❌ 服务未起 |
| 8182 | CosyVoice3 | `…-3dtzfdsq-custom.service.onethingrobot.com` | ❌ 服务未起 |
| 8185 | Whisper 转写 | `…-3dtzfdsv-custom.service.onethingrobot.com` | ❌ 服务未起 |

域名形态 `n84-<hash>-<hash>-custom.service.onethingrobot.com`，
与仙宫云 `<id>-<port>.container.x-gpu.com` **完全不同**，别按仙宫云规则拼域名。
地址已写入 `~/.workbuddy/fxai_global.json` 的 `otai` 节点（统一登记处），
各技能仍从 `comfy_cloud_url` / `tts.engines.{cosyvoice,qwen3tts}.cloud_url` /
`whisper.cloud_url` 消费，与 `otai` 节点同值，改地址两边一起改。

**加端口走控制台，API 改不了**（穷举 5 接口 × 9 参数确认，见 references/01）。

### 8188 曾 502：镜像默认把 ComfyUI 起在 7860（2026-10-15 修复）
公网域名映射的是 8188，但镜像的启动脚本 `/usr/bin/comfyui` 从
`/usr/bin/comfyui-startup.env` 读 `ARGS`，默认写死 `--port 7860`，且只监听 127.0.0.1
→ 网关打到 8188 无人应答，返 502。
修复：把 env 文件改成 `ARGS="--port 8188 --listen 0.0.0.0 --disable-auto-launch --enable-cors-header"`，
`kill` 掉旧 main.py，`/usr/bin/launcher` 会自动按新 env 重启（进程树 launcher→comfyui→main.py）。
改完公网 `/system_stats` 返 200。**重建实例/换镜像后要重新改这个 env**。


## 节点现状：只有我们自己的
清空前有 **153 个第三方节点、4.2 GB**（efficiency-nodes 461M、AlekPet 318M、DragNUWA 300M…），
已按用户要求全删，现在只有：
- `fxai-toolkit`（凤希自研，核心，`e0da25a`）→ 109 个 FxAi 节点

验收：`curl 127.0.0.1:7860/object_info` 共 1304 节点、109 个 FxAi、**0 个 IMPORT FAILED**；
`curl 127.0.0.1:7860/fxai/health` → `{"status":"ok","service":"fxai-toolkit","version":"1.0"}`

## 🚨🚨 这些改动随时会丢，必须存镜像
远端是容器（`/.dockerenv` 存在）。`/root/ComfyUI` 下的所有改动
（清节点、升 v0.40.0、装 fxai-toolkit、装 comfy_aimdo 0.5.6）
**只存在于当前实例的可写层**，以下情况一律回到初始镜像、全部归零：
- 卸载实例后重建
- 换镜像 / 重置实例
- 迁移区域

**结论：现在必须去控制台把当前状态保存成新镜像**，否则这次清理与升级白做。
平台 API 文档里没有"保存镜像"接口，只能在控制台点。

## 镜像 ID 的坑
- 我们的镜像：`PRIVATE_cz80fhheabvmlvio-ev1hdtjm-ne1i54io`（区域 8，151.8 MB，状态 4=保存成功）
- 控制台**只显示后半段** `cz80fhheabvmlvio-ev1hdtjm-ne1i54io`（用户常这么给，补 `PRIVATE_` 前缀即可）
- 拿裸 ID 查 `/api/v2/resources` 会返回一堆 `maxGpuNum=0`，看着像"没资源"，其实是 ID 不完整
- `otai_api.py` 的 `normalize_image_id()` 会自动补 `PRIVATE_` 前缀
- 名字「凤希AI全自动创作全能镜像包」；`resolve-image` 可按名定位
- 该镜像 4090 可用 `maxGpuNum=2`（区域 8 共 7 种组合，只有 4090 有量）
- ⚠ **当前正在跑的实例用的不是它**，是官方 `PUBLIC_306`（见文末「尚未完成」）


## 升级 ComfyUI 的坑
1. **v0.40.0 需要 `comfy_aimdo`**，镜像自带 0.5.3 但**没有 `comfy_aimdo.storage` 子模块**，
   启动直接 `ModuleNotFoundError: No module named 'comfy_aimdo.storage'`。
   `pip install comfy_aimdo` 会说「已满足」—— **必须 `--upgrade`**（升到 0.5.6 才有 storage）。
2. 装完还要按新版本要求补齐前端包：
   ```bash
   /root/miniconda3/bin/python -m pip install -i https://mirrors.aliyun.com/pypi/simple/ \
     -r /root/ComfyUI/requirements.txt
   ```
3. 前端包版本低了会刷 WARNING（frontend-package / workflow-templates / embedded-docs / comfy-kitchen）。

## 镜像自带的杂物（不用管，已确认无害）
- `custom_nodes/._ComfyUI_Onething_Encrypted` —— 空目录
- `custom_nodes/.disabled/ComfyUI-nunchaku@nightly` —— 标记目录
- `custom_nodes/websocket_image_save.py` —— ComfyUI 自带
- 删掉的节点会留孤儿进程占用 8080，删完记得 `pkill`

## 🟢 平台自带 1PB 公共模型库 —— 但「做映射」这条路走不通（2026-10-15 深挖）
路径 `/.ot_u_public/onething-ai/models`，`df` 显示 `JuiceFS:yxyz-oss 1.0P`。
看着像"1PB 免下载模型库、软链过去就行"，**实际不是**，别再抱这个期望。

### 它到底是什么
- 根目录 `.config` 写得很清楚：**MinIO 对象存储**（`Format.Storage=minio`，
  bucket `onethingai-fs`），JuiceFS 只是把它挂成 POSIX 视图。
- 目录是**内容寻址（CAS）**：`models/<hash前2位>/<hash后2位>/<完整sha256>`，
  共 **3191 个叶子节点**。
- **叶子节点是「文件」不是目录**（`ls` 报 `Not a directory`），文件名就是 SHA256。
- 内容确实是真模型：抽到的两个分别是
  torch pickle（`PK…archive/data.pkl` + `collections.OrderedDict`，290MB）
  和 JSON 片段（`{"__metadata__":{"ss_bucket…`，37MB）。
- **没有任何索引/清单文件**，全库 `find -name '*.json' -o -name index*` 查不到东西。

### 为什么不得软链映射
没有名字 → **没法按 `Qwen3-TTS` / `whisper` 这样的模型名定位**。
`find -iname '*qwen3*tts*'` / `'-iname '*whisper*'` 都是空。
要拿某个模型只能**遍历 3191 个文件逐个读 `__metadata__` 反查来源**（每个几百 MB，不现实），
或者干脆走正常下载。**结论：装模型一律走 hf-mirror 拉，别惦记这个库。**

### 唯一还有点用的地方
`Chunk.CacheDir: /cache/models/9a4f9ade-...`、`CacheSize: 2TB`
说明平台侧有本地 chunk 缓存 —— 同平台用户拉过的同一模型，第二次可能命中缓存、明显更快。
但这只影响速度，不改变「没有索引、按名字找不到」这个事实。

## 语音服务现状（2026-10-15 16:55 实测）
| 服务 | 端口 | 状态 |
|---|---|---|
| ComfyUI | 8188 | ✅ 运行中，公网通 |
| 千问 TTS | 8086 | 🔄 服务端代码已传 `/root/qwen3tts/`，模型下载中 |
| CosyVoice3 | 8182 | ❌ 未装 |
| Whisper 转写 | 8185 | ❌ 未装（服务端代码已写好待传） |

**已有的底子**（不用重装）：`faster-whisper 1.2.0`、`librosa 0.10.2`、
`modelscope 1.27.1`、`soundfile 0.12.1`、torch 2.7.1+cu126（CUDA True）、
`huggingface_hub 0.34.3`。

**`deploy.py` 不覆盖语音服务**（它只管 ComfyUI 自定义节点 + 模型），语音要单独装。

## 千问 TTS 部署要点（本机 → 云端）
本机源码在 `<盘>:\fxaitool\aiproject\qwen3tts\`，**8.5G，其中 models 占 8.5G**：
- `qwen3tts_server.py` 38.9KB —— 服务端，**纯标准库起服务**（`http.server`，
  不依赖 fastapi/uvicorn），默认端口就是 8086，与统一端口表对得上
- 依赖只有 `numpy` + `soundfile`，远端**都已装**，几乎零额外依赖
- 模型是标准 HF 格式两个（各 4.3G）：
  `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`（默认引擎用这个）
  `Qwen/Qwen3-TTS-12Hz-1.7B-Base`（声音克隆用）

**分两步传，别整个目录上传**：
1. 代码 4 个文件直接 `put_text` 传（合计 42KB，1 秒）
2. models/ 走**远端自己 `snapshot_download` 拉 hf-mirror**（远端实测 hf-mirror 200 通），
   约 10MB/s，比本机上传 8.5G 快一个量级

