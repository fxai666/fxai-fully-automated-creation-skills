---
name: fxai-xiangongyun-deploy
description: 仙宫云 GPU 实例一键部署凤希技能包（ComfyUI + fxai-toolkit 自定义节点 + 全套工作流所需模型），并管理实例生命周期。当用户给出仙宫云实例的 SSH 地址/端口/密码或访问令牌，并要求「装环境」「下模型」「部署到仙宫云」「把模型传到云上」「云端装 ComfyUI 节点」「仙宫云上跑我们的工作流」时使用；也用于「仙宫云开机/关机/释放GPU/销毁实例/保存镜像/查余额/查账户/充值/新建实例」等 API 操作。下载走「先摸地址表、再按表单源下载」：魔搭为主、必要时 HF，支持分组装、并发、幂等续传、体积对账、文件头校验、磁盘预检、失败清单；另有本机直传通道。也支持无 SSH 时生成脚本贴到 Jupyter 终端执行。触发词：仙宫云、仙宫云部署、云端装模型、传到云上、hf.x-gpu、仙宫云GPU、部署技能包到云端、仙宫云关机、仙宫云开机、释放GPU、仙宫云API、仙宫云余额、仙宫云充值、仙宫云镜像。V1.9 起换了模型与工作流，升级后必须重新创建实例（镜像 ID 不变，是覆盖更新；仙宫云同样必须重建）；运行中报「模型不存在」即提示用户重建，确认后才真建（建实例实时扣费）。不做：本机 ComfyUI 运维（→fxai-workflow）、生成任务等待（→fxai-comfyui-watcher）。
---

# 仙宫云 · 凤希技能包一键部署

一条命令把「ComfyUI 本体 → 凤希自研自定义节点 → 模型文件」装到仙宫云 GPU 实例上，可中断可续跑。另管实例生命周期：开机/关机/释放GPU/存镜像/查余额/查账户/充值/新建实例。

## 本技能文件清单（先读这里，再决定读哪册）
| 文件 | 什么时候读 |
|---|---|
| `references/01-实例管理API.md` | 开机/关机/释放GPU/建实例/存镜像/查余额/充值。认证、命令表、API 判据、接口规范 |
| `references/02-部署清单与下载管线.md` | 装节点/下模型/探源/对账/本机直传。装什么、怎么下、下坏了怎么办 |
| `references/03-装完与排障.md` | 装完后的配置、**镜像内置默认工作流（扩展 TARGETS + bundle 兜底）**、重启、节点报IMPORT FAILED、升级 ComfyUI/节点、网络现状 |
| `references/04-实例现状与云端运维.md` | 已建实例 `9xtjztkxzajn9vp5` 的现状、本机复用工具、推节点、ComfyUI-Manager、外网连通性 |
| `references/仙宫云速查.md` | 官方教程摘录：HF 镜像加速、传文件 5 法、系统盘/数据盘、基础排障 |

**确定本次要做什么之后，只读对应的那一两册，其余一律不读。**

## 铁律速查（常驻）
1. 🚨 **`release-gpu` 之后先等冷却**：etc 状态真正回到 `shutdown` 再 `boot`。冷却期内 boot 返 `code=1000 该实例暂时无法进行操作`。
2. 🚨 **建实例实时扣费**：`--yes` 缺省时只打印请求体；用户明确说「建/开一个实例/按这个镜像建」并看过请求体后再加 `--yes` 真发。
3. 🔴 **凭据只有全局配置这一处**：`~/.workbuddy/fxai_global.json`（`xgy_api_token` / `xgy_ssh`）。**SSH 地址与密码不写进技能**（要打包分发）。
4. **成功码是 `code=200`**，不是 0。以 `success` 字段为准。
5. **「不存在」和「认证失败」都返 `code=1000`** —— 提示**按 `msg` 文字分类**；按 code分类会把「镜像不存在」误报成「令牌有问题」。
6. **`web_url` 不是 ComfyUI 地址**。ComfyUI 默认 **8188**，用 `xgy_api.py comfy-url <id>` 拿。
7. **关机释放 GPU 才省钱**；「关机保留 GPU」卡还占着，照全价计费。
8. **每个模型锁死单一已验证源**，中途换源会让不支持续传的源把进度清零。新增源**先跑 `probe_sources.py` 确认返 206**再写进 `models.json`。
9. **失败时保留半截文件**（`curl -C -` 天然续传）。**并发 4 路封顶**，**一轮全跑完之后**才对账重下。
10. **长任务后台跑 + 等完成通知**，主线程用 `xgy_progress.py` 或 `progress.html` 看进度（纪律见 `fxai-common`/SKILL.md「等任务纪律」）。
11. **资源存在性走接口确认**，云端盘留给下载落盘。
12. **Jupyter 通道走不通**（kernel 405、terminal 404），**SSH 是唯一能执行命令的通道**。
13. 🔴 **V1.9 起必须重新创建实例**：本版换了模型与工作流，旧镜像实例系统盘里没有新模型，升级技能包、重下模型、改模型名、重启都补不齐。**用仙宫云时必须重新创建一台新实例**：镜像 ID 固定 `9463d4ca-2f86-45cf-b0a3-a81ba6f3b27c`，凤希用新版覆盖同一 ID，旧实例是创建时的镜像快照、不会跟着变，只能新建；不得沿用或只重启旧实例。运行中提交返回「模型不存在 / 找不到模型」即判定旧实例 → **提示用户用该镜像 ID 新建实例**并给出 `deploy-image <镜像ID> --yes` 入口；建实例实时扣费，**只提示、不自动建**，确认后才真建。细则见 `references/01-实例管理API.md「V1.9 必须重新创建实例」`。
14. 🔴 **工作流唯一来源 = ComfyUI 工作区的 `user/default/workflows/仙宫云`**（用户 2026-11-13 定）。
   仙宫云所有工作流一律从这个目录**单向同步**上去，线上只做接收端、**不在线上编辑**。
   同步用 `scripts/sync_workflows.py --profile <实例ID>`（先 `--dry-run` 看差异，md5 对账、幂等）；
   来源目录由全局配置 `xgy_workflow_src` 提供，不用每次传。
   别和 `workflows/凤希AI/` 混（那是本机 dev 用）。细则见 `references/04-实例现状与云端运维.md「工作流唯一来源」`。

## 三种跑法，按现场条件选

### A. 有 SSH（首选）
```bash
cd <技能目录>/scripts
python deploy.py plan  --group all                 # 先看要装什么、多大
python deploy.py probe -H <ip> -p <端口> -u root -P <密码>   # 体检：GPU/磁盘/已有 ComfyUI
python deploy.py run   -H <ip> -p <端口> -u root -P <密码> --group all
```
`run` 顺序：体检 → 打印计划 → 上传脚本 → 后台开跑 → 实时把远端日志拉回本地 → 跑完给验收清单和失败清单。`Ctrl+C` 只断开查看，云端任务继续跑。
密码登录需要 `paramiko`；已有公钥传 `-k ~/.ssh/id_ed25519` 走系统 ssh 免装依赖。密码也可用环境变量 `XGY_SSH_PASSWORD`，shell 历史不留明文。

### B. 没有 SSH / 只想手动执行
```bash
python deploy.py emit --group all --out xgy_deploy.sh
```
生成自包含 bash，整段粘到 **Jupyter → Terminal** 执行。下载一律 `wget -nc`，已存在则跳过。

### C. 只想要别的东西
```bash
python deploy.py verify -H ...        # 只看远端模型/节点现状，不动任何东西
python deploy.py run --no-models      # 只装节点
python deploy.py run --no-nodes       # 只下模型
```

## 常用开关

`--group img`(24.3 GB) / `--group music`(7.8 GB) / `--group mv`(约 42.5 GB) / `--group vsr`(5.3 GB) / `--group vocalsep`(0.9 GB)；
`--variant pruned` **已废弃**（PRISM 新底座无剪枝变体）。其余开关见 `references/02-部署清单与下载管线.md`。

## 全装体积

**≈ 80.7 GB**（12 条模型）。系统盘 = 镜像 + 100 GB × GPU 数。下载前做磁盘预检，不够会直接告诉你该 `--group img` 还是该关机扩容。
另有 6 类依赖（自研 LoRA / 文本编码器 / insightface / vosr2 / FlashVSR）不走公共下载源，见 `assets/models.json` 的 `final_version` 段。

## 镜像分发：默认工作流

两处配合，**不要建 `workflows/默认/` 这类目录**（用户明确不要多出来的文件夹和标签）：

1. **打开哪几条** → `custom_nodes/fxai-toolkit/js/fxai_default_workflow.js` 的 `TARGETS` 数组
   （可以列多条，第一条前台、其余后台标签；打开的是真实持久化工作流，标签名=文件名）。
2. **不出官方默认图**（兜底）→ `assets/default-workflow/` 两个文件随镜像带上：
   `fxai_default_workflow.py`（放 `/root/xgyscripts/`）+ `99-fxai-default-wf.sh`
   （放 `/scripts/start.d/`，开机自动跑）。

任何浏览器、零 localStorage，裸地址打开就是指定工作流。规范、为何不能用 `?workflows=` 参数、
验收命令见 `references/03-装完与排障.md`「打开就是指定工作流」。
