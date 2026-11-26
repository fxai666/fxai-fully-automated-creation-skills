---
name: fxai-xiangongyun-deploy
description: 仙宫云 GPU 实例一键部署凤希技能包（ComfyUI + fxai-toolkit 节点 + 全套模型），并管实例生命周期。当用户给 SSH 或访问令牌并要求「装环境/下模型/部署到仙宫云/云端装节点/跑工作流」时用；也用于「开关机/释放GPU/存镜像/查余额/查账户/充值/新建实例/升级同步云端 ComfyUI 和节点」。下载走「先摸地址表、再按表单源下载」：魔搭为主、必要时 HF，支持分组装、并发、幂等续传、体积对账、文件头校验、磁盘预检、失败清单；另有本机直传。无 SSH 可生成脚本贴 Jupyter 终端。触发词：仙宫云、仙宫云部署、云端装模型、传到云上、hf.x-gpu、仙宫云GPU、部署技能包到云端、仙宫云开关机/释放GPU、仙宫云API/余额/充值/镜像、仙宫云升级/同步云端。V1.9 起换模型与工作流，升级后须重建实例（镜像 ID 不变、覆盖更新）；报「模型不存在」即提示重建、确认后才真建（建实例实时扣费）。不做：本机 ComfyUI 运维（→fxai-workflow）、生成任务等待（→fxai-comfyui-watcher）。
---

# 仙宫云 · 凤希技能包一键部署

一条命令把「ComfyUI 本体 → 凤希自研节点 → 模型文件」装到仙宫云 GPU 实例，可中断可续跑。另管实例生命周期：开机/关机/释放GPU/存镜像/查余额/查账户/充值/新建实例。

## 本技能文件清单（先读这里，再决定读哪册）
| 文件 | 什么时候读 |
|---|---|
| `references/01-实例管理API.md` | 开机/关机/释放GPU/建实例/存镜像/查余额/充值。认证、命令表、API 判据、接口规范；**SSH 维护免手输密码（`xgy_ssh_api.py`）** |
| `references/02-部署清单与下载管线.md` | 装节点/下模型/探源/对账/本机直传。装什么、怎么下、下坏了怎么办 |
| `references/03-装完与排障.md` | 装完后的配置、**镜像内置默认工作流（扩展 TARGETS + bundle 兜底）**、重启、节点报IMPORT FAILED、**升级 ComfyUI/节点（命令+脚本口径）**、网络现状 |
| `references/04-实例现状与云端运维.md` | 实例现状、本机复用工具、推节点、外网连通性 |
| `references/仙宫云速查.md` | 官方教程摘录：HF 加速、传文件 5 法、系统盘/数据盘、排障 |

**确定本次要做什么之后，只读对应的那一两册，其余一律不读。**

## 铁律速查（常驻）
1. 🚨 **`release-gpu` 之后先等冷却**：etc 状态真正回到 `shutdown` 再 `boot`。冷却期内 boot 返 `code=1000 该实例暂时无法进行操作`。
2. 🚨 **建实例实时扣费**：`--yes` 缺省时只打印请求体；用户明确说「建/开一个实例/按这个镜像建」并看过请求体后再加 `--yes` 真发。
3. 🔴 **凭据只有全局配置这一处**：`~/.workbuddy/fxai_global.json`（`xgy_api_token` / `xgy_ssh`）。**SSH 地址与密码不写进技能**（要打包分发）。
4. **成功码是 `code=200`**，不是 0。以 `success` 字段为准。
5. **「不存在」和「认证失败」都返 `code=1000`** —— 提示**按 `msg` 文字分类**；按 code分类会把「镜像不存在」误报成「令牌有问题」。
6. **`web_url` 不是 ComfyUI 地址**。ComfyUI 默认 **8188**，用 `xgy_api.py comfy-url <id>` 拿。
7. **关机释放 GPU 才省钱**；「关机保留 GPU」卡还占着，照全价计费。
8. **每个模型锁死单一已验证源**，中途换源会让不支持续传的源把进度清零。新增源**先跑 `probe_sources.py` 确认返 206**再写 `models.json`。
9. **失败时保留半截文件**（`curl -C -` 天然续传）。**并发 4 路封顶**，**一轮全跑完之后**才对账重下。
10. **长任务后台跑 + 等完成通知**，用 `xgy_progress.py` 或 `progress.html` 看进度（纪律见 `fxai-common`/SKILL.md）。
11. **资源存在性走接口确认**，云端盘留给下载落盘。
12. 🔴 **SSH 是唯一能执行命令的通道**（Jupyter kernel 405/terminal 404）。维护免手输密码：`python xgy_ssh_api.py run <id> "<cmd>"`（info/run/put/get/shell），凭证 `GET /instance/{id}` 实时取、只靠 `xgy_api_token`，省略 ID 走 `xgy_instance_id`；库函数 `xgy_cfg.ssh_cred_from_api(iid)`。见 `references/01`。
13. 🔴 **V1.9 及更早须重建实例**（V2.0 常规更新即可）：模型在实例系统盘，旧镜像缺本版模型，重下/改名/重启都补不齐。用镜像 ID `9463d4ca-2f86-45cf-b0a3-a81ba6f3b27c` 新建。报「模型不存在」即判缺底座→提示新建并给 `deploy-image <ID> --yes`，**只提示不自动建**。见 `references/01`。
14. 🔴 **工作流唯一来源 = ComfyUI 工作区 `user/default/workflows/发布版`**（旧名「仙宫云」已改）。单向同步、线上只收不编：`sync_workflows.py --profile <实例ID>`（先 `--dry-run`，md5 对账）；来源读全局配置 `workflows.release_dir`（配 `comfy_root` 时拼接，`--src`/`FXAI_XGY_WF_SRC` 覆盖），禁硬编码。勿混 `workflows/凤希AI/`（本机 dev）。品牌条=发布版标记（开发版占位图/发布版真实图），默认 `--release` 推时临时换图。见 `references/04`。
15. 🔴 **缺卡不是错，默认抢到为止**：`deploy-image` / `boot` 缺卡（部署 `code=1000`、开机 `code=1003`，不扣费）自动每 5 秒重抢一次、**不限次数**，`Ctrl+C` 停；`--grab N` 限次、`--no-grab` 关闭。缺卡必须走软调用 `call(..., soft=True)`，抛异常会把重试变成死代码（v2.x 真实翻车点）。
16. 🔴 **体检、升级都只认咱们镜像建的机器**：`check` 三态、`upgrade <id>` 在线升级，判定一律只看 image_id == `9463d4ca`，其他镜像实例不算、不提示、不碰。

## 接入体检 + 部署后在线升级（用户选仙宫云先跑）
`python xgy_api.py check`：验令牌 → 列**咱们全自动镜像（`9463d4ca`）建的机器** → 给绑定（`xgy_guard.py use`）+ 验服务（`verify-services`）命令；没有就给建机请求体，**确认后才 `--yes`**（扣费）。无令牌/无账号时打印注册+镜像链接、提示没填邀请码填 `9LWQ36` 得 3 元赠金，写全局配置 `xgy_api_token` 再重跑。详见 `references/01「首次接入体检」`。

`python xgy_api.py upgrade <id>`：技能包更新后同步云端——只对咱们镜像实例、走令牌免密 SSH 上传执行 `xgy_upgrade.sh`：检测落后 → ComfyUI pull（含 shallow/脏工作区坑）→ fxai-toolkit pull/zip 重装 → 不无脑升 torch → **自动重启 + 四服务验收**（升级前后节点数不减少）。`--check` 只检测、`--no-restart` 不重启、`--no-comfy` 只升节点。报告在实例 `/tmp/xgy_upgrade.report`。🔴 判定只看 image_id，其他镜像实例一律不算、不碰。详见 `references/01`、`references/03`「更新 ComfyUI/节点」。

## 三种跑法，按现场条件选

### A. 有 SSH（首选）
```bash
cd <技能目录>/scripts
python deploy.py plan  --group all                              # 先看装什么、多大
python deploy.py probe -H <ip> -p <端口> -u root -P <密码>       # 体检 GPU/磁盘/已有 ComfyUI
python deploy.py run   -H <ip> -p <端口> -u root -P <密码> --group all
```
`run` 顺序：体检 → 计划 → 上传 → 后台跑 → 实时拉回日志 → 验收/失败清单；`Ctrl+C` 只断开查看，云端继续。密码登录要 `paramiko`，公钥传 `-k` 走系统 ssh 免装；密码可用 `XGY_SSH_PASSWORD` 免留历史。

### B. 无 SSH / 手动执行
```bash
python deploy.py emit --group all --out xgy_deploy.sh
```
自包含 bash 粘到 **Jupyter → Terminal** 跑，下载 `wget -nc` 跳过已存在。

### C. 只取其一
```bash
python deploy.py verify -H ...     # 只看远端现状
python deploy.py run --no-models   # 只装节点
python deploy.py run --no-nodes    # 只下模型
```

## 常用开关
各分组体积与全部开关见 `references/02-部署清单与下载管线.md`；`--variant pruned` 已废弃（现役清单只认 full，PRISM 底座已下线）。

## 全装体积
**≈ 89.8 GB**（13 条模型）。系统盘 = 镜像 + 100 GB × GPU 数。下载前做磁盘预检，不够会直接告诉你该 `--group img` 还是该关机扩容。
另有 6 类依赖（自研 LoRA / 文本编码器 / insightface / vosr2 / FlashVSR）不走公共下载源，见 `assets/models.json` 的 `final_version`。

## 镜像分发：默认工作流
两处配合（**禁建 `workflows/默认/` 这类目录**，用户明确勿增多余文件夹和标签）：
① 打开哪几条 → `custom_nodes/fxai-toolkit/js/fxai_default_workflow.js` 的 `TARGETS`（可列多条，第一条前台、其余标签；标签名=文件名）；
② 兜底不出官方默认图 → `assets/default-workflow/` 的 `fxai_default_workflow.py`（放 `/root/xgyscripts/`）+ `99-fxai-default-wf.sh`（放 `/scripts/start.d/`，开机自动跑）随镜像带上。

规范、`?workflows=` 参数不适用的缘由、验收命令见 `references/03-装完与排障.md`「打开就是指定工作流」。
