# fxai-workflow —— 共享工作流目录（目录名保留 `fxai-` 前缀作隔离边界）

本目录集中存放「凤希 AI 全家桶」所有 ComfyUI 工作流 JSON，**跨技能共享**：各技能一律读这一份，重复、版本漂移、命名冲突都不会发生。

> **命名约定**：**只有技能文件夹**加 `fxai-` 前缀（与他人技能命名区分开），**工作流文件本身不加 `fxai-` 前缀**。目录名 `fxai-workflow` 保留前缀，作为这套工作流的整体隔离边界；工作流文件名用 `<类别>-<模型>-<用途>[_api].json` 命名（如 `mv-minimax-single.json` / `img-qwen21-process.json` / `music-yue2.json`），**显式带模型 token**（kling / sdxl / yue3…），换模型不撞名、可区分。工作流**内部占位符仍统一用 `fxai_xxx`**（如 `fxai_prompt`/`fxai_seed`/`fxai_outputdir`），这是节点机制，保持原样。

## 命名规范（硬性）

文件名结构：

```
<类别>-<模型>-<用途>[_api].json
```

- **类别（category）**：用连字符的业务前缀，标明业务域
 - `mv` —— 音乐 MV（单/多/多参考）
 - `img` —— 千问2.1 生图（图片处理/三视图/四视图）
 - `music` —— 音乐生成（Yue2）
 - `shortdrama` —— 短剧（影视剧场双采版）
 - `narrate` —— 数字人口播（**待接入**）
 - `dance` —— 舞蹈（**待接入**）
- **模型（model）**：紧跟类别的模型 token，标明该工作流跑哪个模型（换模型不撞名、可区分）
 - `minimax` —— MiniMax Hailuo H3（视频生成，MV/短剧共用）
 - `qwen21` —— 千问2.1（图片生成，视图/场景图）
 - `yue2` —— Yue2（音乐生成）
 - （未来可扩展：`kling` / `sdxl` / `yue3` …）
- **用途（purpose）**：`single` / `multi` / `multiref` / `process` / `threeview` / `fourview` / `yue2` / `filmdrama` …
- **`_api` 后缀（可选）**：表示「自包含 API 模板」——内部已写死参数、运行时由代码直接改节点，不依赖 `fxai_xxx` 占位符替换。
 - **不带 `_api`** 的是「占位符版」：提交前由 `comfy_client.submit_workflow` 做 `fxai_xxx` 占位符注入（见 fxai-mv-pipeline SKILL.md「占位符工作流」）。

> ⚠️ 工作流文件名**不加 `fxai-` 前缀**，且**必须带模型 token**（`<类别>-<模型>-<用途>`）。目录名 `fxai-workflow` 本身保留前缀。新增工作流务必用连字符命名，勿使用纯数字或无前缀中文名。

## 文件清单

| 现文件名 | 原文件名/编号 | 类别 | 用途 | 类型 | 被谁使用 |
|---|---|---|---|---|---|
| `mv-minimax-single.json` | `9.json` | mv | 单场景 | 占位符版 | fxai-mv-pipeline（`WF_MV_SINGLE`） |
| `mv-minimax-multi.json` | `11.json` | mv | 多场景 | 占位符版 | fxai-mv-pipeline（`WF_MV_MULTI`） |
| `mv-minimax-multiref.json` | `12.json` | mv | 多参考(Ref2VA) | 占位符版 | fxai-mv-pipeline（`WF_MV_MULTIREF`） |
| `mv-minimax-multiref.json.bak` | `12.json.bak` | mv | 多参考(旧版备份) | 占位符版 | 历史备份，不参与运行 |
| `img-qwen21-process.json` | `16.json` | img | 图片处理(场景图) | 占位符版 | fxai-scene-gen（`WF_IMG`） |
| `img-qwen21-threeview.json` | `17.json` | img | 三视图 | 占位符版 | fxai-fourview-gen（`WF_THREEVIEW`） |
| `img-qwen21-fourview.json` | `18.json` | img | 四视图 | 占位符版 | fxai-fourview-gen（`WF_FOURVIEW`） |
| `music-yue2.json` | `19.json` | music | Yue2 音乐生成 | 占位符版 | fxai-mv-pipeline（`WF_MUSIC`） |
| `mv-minimax-single-api.json` | `MiniMax无限时长数字人MV单场景双采版V1API.json` | mv | 单场景 | 自包含 API 版 | 备份/对照 |
| `mv-minimax-multi-api.json` | `MiniMax无限时长数字人MV多场景双采版V1API.json` | mv | 多场景 | 自包含 API 版 | 备份/对照 |
| `mv-minimax-multiref-api.json` | `MiniMax无限时长数字人MV多参考V2(双采版)API.json` | mv | 多参考 | 自包含 API 版 | 备份/对照 |
| `music-yue2-api.json` | `Yue2音乐生成V1API.json` | music | 音乐 | 自包含 API 版 | 备份/对照 |
| `img-qwen21-process-api.json` | `千问2.1图片处理API.json` | img | 图片处理 | 自包含 API 版 | 备份/对照 |
| `img-qwen21-threeview-api.json` | `千问2.1三视图V1API.json` | img | 三视图 | 自包含 API 版 | 备份/对照 |
| `img-qwen21-fourview-api.json` | `千问2.1四视图V1API.json` | img | 四视图 | 自包含 API 版 | 备份/对照 |
| `shortdrama-minimax-filmdrama-api.json` | `MiniMax影视剧场双采版V1API.json`（原 fxai-short-drama/assets） | shortdrama | 影视剧场(批量+合并) | 自包含 API 版 | fxai-short-drama（`drama_pipeline.DEFAULT_WORKFLOW`） |
| `vc-melband-vocalsep-api.json` | 服务端「人声分离」工作流导出（fxai-toolkit 节点） | vc | 人声分离(Mel-Band RoFormer) | 自包含 API 版 | fxai-vocal-separate（`WF_VOCALSEP`，默认模型 `melband`） |
| `vc-uvr-vocalsep-api.json` | （占位模板） | vc | 人声分离(换模型备用) | 占位模板 | 备用，不参与运行 |
| `face-restore-lite.json` | `人脸精修轻量链V1API.json` | face | 人脸精修(全脸·分段) | 占位符版 | fxai-face-edit（`resolve_wf("facerestore")`，默认模型 `lite`，入口 `scripts/face_restore.py`） |
| `h3-char-swap-api.json` | `凤希AI-H3角色替换循环分段V1.json` | face | 角色替换(整片换人换装·分段) | 占位符版 | fxai-face-edit（`resolve_wf("charswap")`，默认模型 `h3`，入口 `scripts/char_swap.py`） |
| `upscale-fvsr.json` | `视频超分精修V1API.json` | upscale | 视频超分+修脸(分段) | 占位符版 | fxai-video-upscale（`resolve_wf("upscale","fvsr")`，入口 `scripts/video_upscale.py`） |
| `img-moss-process-api.json` | `MOSS-Image指令编辑API.json` | img | 图片指令编辑(在原图上改) | 自包含 API 版 | `resolve_wf("imgedit","moss")`，无独立技能入口，调用方直接提交 |

## 代码如何加载

- **fxai-mv-pipeline**：`mv_pipeline.py` 顶部的客户端逻辑已抽到 `comfy_client.py`，`DL = _cfg("workflow_dir", fxai-workflow 目录)`，各 `WF_*` 别名经 `resolve_wf(任务, 模型)` 解析（默认模型见 `WORKFLOWS` 注册表，如 `img-qwen21-fourview.json`）。包外自定义工作流可用 `fxai_global.json` 的 `workflow_dir` / `workflow_models` / `wf_mv_single` 覆盖。
- **fxai-short-drama**：`drama_pipeline.py` 的 `DEFAULT_WORKFLOW` 直接指向上表共享路径 `../fxai-workflow/shortdrama-minimax-filmdrama-api.json`（与 fxai-mv-pipeline 同级的公共目录，依赖 fxai-workflow 同装）。

## 查已生成分镜（断点续跑 · 生成视频通用）

多段循环生成（短剧 / MV / 数字人 / 幻灯片视频）中途断了，先查「已生成到第几个分镜」再续跑，不整批重投：

```bash
python <技能目录>/../fxai-workflow/shot_query.py <prompt_id>   # 渲染中随时查
python shot_query.py now                 # 不记 pid：查「此刻正在跑的那个」
python shot_query.py <pid> --json        # 机器读（remaining / eta_seconds / next_index）
python shot_query.py <pid> --resume      # 只出 start_index
```

**任务是排队串行的**，所以「现在跑到第几镜」＝「队列里 running 的那个 pid 跑到第几镜」，
`now` 就是干这个的：从 `GET /queue` 取 running 的 pid，再按下面的口径算，顺带报队列里还排着几个。
多个任务连排（比如逐镜重渲）时不用记 pid，一条 `now` 就是当前那一个。

**只给 prompt_id 就够**：保存目录、总镜数、任务起点都从提交快照取（台账 / `/history` 取不到也算得出），
输出「已生成 N/M 镜 · 还剩 K 镜 · 约 X 分钟 · 下一镜第 N 镜」，单镜均耗取相邻落盘间隔的中位数。
总镜数取快照 `lines_data` 行数（一镜一行）；结束索引写 `-1` 表示整集，不按索引推。

**单镜重渲任务（开始索引 == 结束索引）单独口径**：它只跑那一镜，结束索引不是「整集最后一镜」，
只作「单镜重渲」标记用；总镜数与「还剩几镜 / 续跑参数」一律跳过这一项；
输出改成「任务类型 : 单镜重渲（只跑第 N 镜）」＋「已生成 / 还没落盘」。同一目录连续跑多个单镜任务时，
时间窗会把上一个任务刚写的文件也框进来，因此单镜任务只认自己那一镜的镜号。

**`/fxai/tasks/result` 只在任务结束后写一条结果**（实测 2026-10-18，探针任务全程每 20s 采一次）：

```
23:02:55 url=None  23:03:25 url=None  23:03:55 url=None
23:04:25 url=None  23:04:55 url=video/merged|fxai_20261018_230435.mp4
```

排队中、生成中一律返回 `None`，任务跑完（合并保存）才写一条记录，且只有**最后那一个产物**。
所以「按 pid 查生成到第几镜」靠的不是这个接口，而是下面两条：

- **有监听进程**（`fxai-comfyui-watcher`）：`fxai:progress` 广播带 `current/total`，直接就是第 N/M 镜；
  `fxai:task_saved` 每次存文件都带 `prompt_id + url`，能精确到「这个 pid 刚存了哪个分镜」。
- **没监听进程**：HTTP 侧只剩保存目录的文件清单，只能按「该 pid 的提交时刻」划时间窗来界定哪些文件是它的。

**保存目录由该任务自己的工作流决定**（短剧默认 `sucai`，MV / 数字人 / 幻灯片各用己方目录），
查询侧不假定固定目录；目录被多个任务共用时只数任务起点之后落盘的文件。`--dir` / `--total` 仅在快照丢失时兜底。
目录被覆盖不影响计数：同名文件被重写时 `mtime` 会刷新进本任务窗口。

**断了 / 服务器挂了之后一条命令接着跑**（不用重新准备分镜数据）：

```bash
python shot_query.py resume <prompt_id>            # 查断点 → 自动重投，返回新 prompt_id
python shot_query.py resume <pid> --dry-run        # 只算不提交
python shot_query.py resume <pid> --wait           # 提交后就地等到终态
# 也可：comfy_watch.py resume <pid> …（同一套参数）
```

⚠️ **`/history` 是内存态，服务重启就没了**（实测：本机只留最近 10 条、全在 0.4 天内，而输出目录里存着 23 天前的分镜）。
所以 `run_workflow` 与短剧 `submit_workflow` 在**提交成功那一刻**就落一份工作流快照到
`~/.workbuddy/runtime/comfy_ws/snapshots/<pid>.json`；`resume` 取工作流的顺序是
`/history` → `--wf <文件>` → 本地快照，三处都没有才报 blocked（那时只能按原脚本重投并显式给 `start_index`）。
库调用：`comfy_client.resume_task(pid)` / `comfy_client.shot_progress(pid)`。

**判定口径**：视频文件只要不在合并目录（`merged`）里，文件名上的文件号 = 分镜序号（`000.mp4` = 第 1 镜）；成片不计。
**数据源**（全部走接口，不扫本机磁盘——本机磁盘 ≠ 服务端磁盘）：台账 → `POST /fxai/tasks/result` → `GET /history/<pid>` 的 `prompt[2]`（反查「保存目录」）→ `GET /fxai/files/list?dir=fxai&subdir=video/<目录>`。
加 `--debug` 会把每次真实 HTTP 请求打到 stderr：`GET /queue` → `GET /history` → `POST /fxai/tasks/result` → `GET /history/<pid>` → `GET /fxai/files/list?…`，便于逐条核对结论来源。
⚠️ fxai-toolkit 节点**不写 `/history` 的 outputs**（实测为空），分镜清单以文件列表为准。

## 等任务结果

`run_workflow` / `submit_workflow` 提交后等 `fxai:task_saved`（＝合并/保存完毕）才返回，返回即已过终态门。

机制：提交时带上监听自己的 `clientId`（定向的 `execution_error` 第一时间可见）→ 收 fxai 全局广播
（`fxai:progress` 进度 / `fxai:task_saved` 完成）→ 产物 url 从 `POST /fxai/tasks/result` 取（完成后最多等 90s）。
队列等 GPU 期间无事件，查 `GET /queue` 确认「还在队列里」就继续等。监听器起不来时自动回落 `/history` 轮询。

**调用方按三态行事**：

| 阶段 | 规则 |
|---|---|
| 等待期·**正常** | ✅ 只查健康（服务器/进程还活着吗）<br>❌ 不做数据交互：不查 `/history`、不拉文件列表、不取结果、不下载、不反复 curl `/queue` |
| 等待期·**报错** | ✅ **这时才做数据交互**：取错误详情与日志，分析原因并解决。异常里带 `prompt_id` 与查详情指引 |
| **终态门** | 日志出现 `✅ 合并成功，已保存：<url>` 之后，才做后续：取产物 → 烧字幕 → 归档 → 交付 |

- 要在对话里只在完成时播报一句 → `comfy_watch.py next <pid>`（只出终态，不刷过程）。
- 要全程看进度 → 双击 `fxai-comfyui-watcher/progress.html`（直连 WS，绕过对话，不耗 token）。
- 手动查看：`comfy_watch.py status <prompt_id前缀>` / `top`（全服务器在跑什么）/ `wait <pid>`（自己守着等）。

## 仙宫云（xgy_guard.py 细节）

`comfy_url` 指向仙宫云容器（`*.container.x-gpu.com`）时实例大概率关机。判定与调用方契约见 SKILL.md「仙宫云」节；这里是开机流程与收工文案的完整版。

### 开机流程

探 `/fxai/health`，通了直接往下；探不通 + 是仙宫云地址 → 解析实例 ID → `/instance/boot` → 等 `running` → 再回探一次端口（远端报 running ≠ 容器端口已通）。
进度示意（booting → running 约 1~2 分钟）：

```
[xgy] 服务正在启动，预计 1~2 分钟，正在检测中…
  [10:00:01] 状态 = booting（正在开机）
  [10:01:42] 状态 = running（正在运行）
[xgy] ✓ 服务已启动，开始继续后面的任务
[xgy] 服务已就绪，ComfyUI 端口还在加载，正在检测…   ← 只有远端 running 但端口没通时才出现
```

`ensure_online()` 在开机成功那一步会顺带打印「收工契约」：本次任务全部做完、准备交付之前必须 `finish()`（或 `xgy_guard.py hint`）问用户是否关实例两档。

### 开机前的令牌准备

**没令牌就停下来提示**，不硬闯：去「凤希 AI → 仙宫云管理台」点设置令牌粘上（存本机 `sysconfig/xgy_*.json`），或临时 `XGY_API_TOKEN=xxx`（控制台「访问令牌」页生成，不是 SSH 密码）。配上以后每次都是自动开机，不用人盯。

### 实例选择

**实例 ID 依次取**：配置 `xgy_instance_id` > 地址域名反解（`abc-8188.container.x-gpu.com` → `abc`）> 令牌下唯一那台。
令牌下已有机器的唯一那台自动用；多台或查不到就报错列出来，由人选定后 `use <实例ID>` 写死。用哪个实例由人定，初次配好之后固定用它。
配置里的 ID 若已不属于当前令牌（销毁了/换令牌），开机时会报出来并重新列一遍实例让你重选。

### 收工关机两档

⚠️ 技能侧只管开关机，没有销毁能力：「关机并销毁 / 销毁实例 / 储存镜像并销毁 / 销毁镜像」这些接口不可调用，也别自己拼 HTTP 去调——真要销毁，人去仙宫云控制台点。

`comfy_client.finish()` / `xgy_guard.py hint` 只在「所有任务全部做完、准备交付」那一步调；单个子任务跑完时跳过。关机前必须用提问工具问用户，就两档给（跟仙宫云管理台的「取消 / 普通关机 / 释放 GPU」确认框同一套逻辑）；用户没说停就别停，也别替他默认选。

| 选项（默认走第 1 档） | 命令 | 后果 |
|---|---|---|
| 释放 GPU（默认、推荐，最省钱） | `python xgy_guard.py stop <comfy_url>` | 停计费，磁盘数据保留，下次要重分配 GPU + 加载镜像，明显变慢。长时间不用就选它 |
| 普通关机（保留 GPU） | `python xgy_guard.py stop <comfy_url> --keep-gpu` | GPU 留着、照常计费，下次基本秒起。只有用户明确说「马上还要接着用」才走这档 |

问的时候把上面两档的后果照原样说清楚（尤其「释放 GPU 下次启动会慢」这句要讲明）。本机地址（127.0.0.1）与其它非仙宫云地址不触发这一整套（不自动开机、也不提关机）。

### 子命令

```bash
python xgy_guard.py ensure <comfy_url>      # 只保证在线，离线就开机（默认等 300s）
python xgy_guard.py hint                    # 整套跑完时打印询问文案（只云地址才打）
python xgy_guard.py id <comfy_url>          # 反解实例 ID
python xgy_guard.py stop <comfy_url>        # 关机并释放 GPU
python xgy_guard.py stop <comfy_url> --keep-gpu   # 普通关机
```

## 接口速查

| 动作 | 接口 |
|---|---|
| 提交工作流 | `POST /prompt` |
| 查队列 | `GET /queue` |
| 查任务结果 | `POST /fxai/tasks/result` |
| 列服务端目录 | `GET /fxai/files/list?dir=fxai&subdir=video/<子目录>`（查已生成分镜用） |
| 下载产物 | `GET /fxai/down/file?url=<category/subdir\|file>` |
| 中断当前任务 | `POST /interrupt`（空 body，只断 running，不杀进程、不清队列） |
| 释放显存 | `POST /free` |
| 查系统状态 | `GET /system_stats` |

## 服务端模式铁律细则

- **ComfyUI 是服务器端模式运行，服务器很可能在另一台机器上。本机磁盘 ≠ 服务端磁盘。**
- **资源存在性只认服务端，一律走接口确认**。`find` / `ls` / 资源管理器查的是本机，本机看到的「有 / 无」与服务端真实状态无关——本机没这份文件，服务端可能有；本机有，服务端也可能是另一份。
  - **最权威**：直接提交工作流。服务端返回 `prompt_id` 并 `queued`，即证明服务端已成功读到该参考图——任务能提交成功 = 资源可达，无需任何额外确认。
  - **主动探测**：`GET /fxai/down/file?url=<category/subdir|file>`（整段与 file 名用 `|` 分隔），或读 `POST /fxai/tasks/result`。服务端路径的有无由服务端自己回答，本机文件不参与比对。
- **进程管理归「凤希本地AI控制台」**：状态全靠接口（`GET /queue`、任务结果接口）判定。
- **产物落地**：本机部署从 ComfyUI 本地成品目录（如 `<技能目录>\output\`）复制进项目；远端部署走下载接口按返回相对路径拉取。

## V1.9 · 提交返回「模型不存在」→ 必须重新创建实例

本版换了**模型与工作流**（PRISM 新底座 / RelayVSR 超分 / 去油 LoRA 重新上线等）。
模型与自定义节点装在云端实例的系统盘里，技能包升级改不到已创建的实例：
**旧实例永远补不齐这批新模型**，重下模型、改工作流里的模型名、重启 ComfyUI 都救不回来。

判据落在代码里，不靠人记：`comfy_client._fmt_model_fix()` 在 `unresolved`
（服务端连可替换的候选都没有）或 `/prompt` 被 `value_not_in_list` 拒且纠正不了时，
自动打印「必须新建实例（镜像 ID 不变）」的指引；`repair_models` /
`repair_from_node_errors` / `submit_workflow` 三处都走同一份提示。

| 现象 | 判定 | 动作 |
|---|---|---|
| 提交返回模型不存在，服务端**有**同族新名字 | 模型改名 | `repair_models` 自动换名重发，不用管 |
| 提交返回模型不存在，服务端**连候选都没有**，且地址是云端实例 | 旧实例缺 V1.9 新模型 | **提示用户用同一个镜像 ID 新建实例**（内容已覆盖为 V1.9；仙宫云同样必须重建），确认后才真建 |
| 同上，但地址是本机 | 本机整合包模型没补齐 | 按 `fxai-xiangongyun-deploy/assets/models.json` 补模型 |

🔴 **建实例会实时扣费**：技能只提示并给出 `deploy-image 9463d4ca-2f86-45cf-b0a3-a81ba6f3b27c --yes` 入口（镜像 ID 固定不变，覆盖更新），
**绝不自动创建**；使用者看过请求体并明确同意后才加 `--yes`（见 `fxai-xiangongyun-deploy` 铁律 2 / 13）。
新实例建好后 `xgy_guard.py use <新实例ID>` 写进全局配置，再把 `comfy_url` 指向新实例的 8188 地址。

## 本机优先细则

- 落地方式：`comfy_client.prefer_local()`。探 `127.0.0.1:8188`，探得通就把全局地址切成本机（本进程内之后的调用全走本机），探不通返回 `False`、调用方照旧走 `comfy_url` 里的云地址（`require_comfyui()` 会按云地址自动开机）。
- **生图技能已内置**：`image_gen.generate_image()` 在 `require_comfyui()` 之前自动调 `prefer_local()`，调用方照旧调 `require_comfyui()` 即可。
- 强制走云（本机缺模型/自定义节点、提交报 400）：显式传 `url=` 或调 `C.comfy_url("<云地址>")` 覆盖；`fxai_global.json` 的 `comfy_url` 是全局配置，会影响所有技能，保持原样。
- 走本机时不触发收工关机询问（只看守仙宫云地址）。

## 地址取值细则

三级取值的完整说明：`submit_workflow()` / `require_comfyui()` / `check_comfyui()` / `wait_for_comfyui()` / `finish()` / `_download()` 等一律收 `url=` 参数（传了就打那台）> `~/.workbuddy/fxai_global.json` 的 `comfy_url` > 回落 `127.0.0.1:8188`。取值统一走 `comfy_client.comfy_url(override="")`，下载基址走 `down_base(url)`，两者与提交任务那台保持一致。地址只从参数与配置取：`export` 环境变量只对当前终端有效、换终端就失效。

## 新增工作流步骤

1. 文件名遵循 `<类别>-<模型>-<用途>[_api].json`（**不带 `fxai-` 前缀**，但**必须带模型 token**；目录名 `fxai-workflow` 本身保留前缀作为隔离边界），占位符仍用 `fxai_xxx`。
2. 放本目录；占位符版需在 `fxai-workflow/nodeconfig.json` 登记占位符，并在 `comfy_client.WF_SPECS` 登记必填节点（multiref 还需 `submit_workflow` 自动注入「保存目录」）。
   - **本目录的 `nodeconfig.json` 是 API 侧唯一生效的占位符表**：`comfy_client` 只读它的 `placeholders.type/default` 与 `non_string_placeholders`（做提交前类型校验与缺参回退），`used_in` 字段代码不消费。
   - 插件/UI 侧另有一份同名文件（如 `<数据目录>/comfyui/nodeconfig.json`）属**参考补全**：更新工作流时可拿它对照补齐新占位符，但**不得整份覆盖**本目录这份——它缺 `fxai_filename_index`（四视图/场景图靠它 + 「启用文件名覆盖」精确落 `<server_index>.png`）与三个 boolean（`fxai_skip_initial`/`fxai_include_tail`/`fxai_average_segment`），覆盖后序号会被当字符串插进节点、布尔会被包成 `"True"`。合并时以本目录这份为基底。
3. 在 `comfy_client.py` 的 `WORKFLOWS` 注册表增加 `任务 → {模型slug: 文件名}` 一项（并视需在 `fxai_global.json` 的 `workflow_models` 设默认）；`WF_*` 别名会自动指向默认模型。
4. 在本表补充一行。
