---
name: fxai-onethingai-deploy
description: OneThingAI（api-lab.onethingai.com）GPU 实例管理与凤希技能包部署。当用户给出 OneThingAI 实例的 SSH 地址/端口/密码或开发者密钥，并要求「装环境」「下模型」「部署到 OneThingAI」「传到云上」「云端装 ComfyUI 节点」时使用；也用于「OneThingAI 开机/关机/建实例/查余额/查消费/查资源/监控 GPU/清理节点/升级 ComfyUI」等操作。下载走「先摸地址表、再按表单源下载」：魔搭为主、必要时 HF 镜像，支持分组装、并发、幂等续传、体积对账、文件头校验、磁盘预检、失败清单。触发词：OneThingAI、onethingai、api-lab、GPU 实例、部署到云端、云端装模型、云端装 ComfyUI、升级 ComfyUI、清理节点。不做：本机 ComfyUI 运维（→fxai-workflow）、任务生成等待（→fxai-comfyui-watcher）。
---

# OneThingAI · 凤希技能包一键部署

一条命令把「ComfyUI 本体 → 凤希自研节点（fxai-toolkit）→ 模型文件 → 语音服务」装到 OneThingAI GPU 实例，可中断可续跑。另管实例生命周期：建实例/开机/关机/查余额/查资源/监控。

## 本技能文件清单（先读这里，再决定读哪册）
| 文件 | 什么时候读 |
|---|---|
| `references/01-实例管理API.md` | 建实例/开关机/查余额/查消费/查资源/监控。认证、命令表、接口规范、**与仙宫云的差异** |
| `references/02-部署清单与下载管线.md` | 装节点/下模型/探源/对账/本机直传。装什么、怎么下、下坏了怎么办 |
| `references/03-装完与排障.md` | 装完后的配置、重启、节点报 IMPORT FAILED、升级 ComfyUI、网络现状 |
| `references/04-OneThingAI实例现状.md` | 实例 `cz80fhheabvmlvio-ev1hdtjm` 的现状与踩过的坑 |

**确定本次要做什么之后，只读对应的那一两册，其余一律不读。**

## 🚨 与仙宫云不同的六件事（最容易踩错的地方）
1. **成功码是 `code=0`**，不是 200，也没有 `success` 字段。参考 `fxai-xiangongyun-deploy` 时别照抄判据。
2. **开关机是 `PUT`，卸载是 `DELETE`** —— 不是 POST。客户端代理只有 GET/POST，但 **OneThingAI 支持 CORS**，所以网页端能直连。
3. **GitHub 直连不通**（容器网络受限，本机通远端不通）。所有 `git clone` 必须走 `https://gh-proxy.com/https://github.com/...`。**hf-mirror / modelscope / 阿里云 pip 源都通**。
4. **系统盘只有 70 GB**（仙宫云是 100 GB × 卡数）。全装 79.5 GB **装不下**，必须按 `--group` 分组装。
5. **ComfyUI 默认跑 7860 且只监听 127.0.0.1**（不是 8188）。对外地址看 `customPort` 里 `localPort` 对应的 `subDomain`。
6. **`webUIAddress` 常常是空串**，别拿它当 ComfyUI 地址。
7. 🚨 **远端是容器，实例重建会丢所有手工改动**（清节点/升级/装节点全在可写层）。
   改完必须在控制台**保存成新镜像**，否则卸载重建就归零。平台 API 没有保存镜像接口。
8. **镜像 ID 必须带 `PRIVATE_` 前缀**。控制台只显示后半段，拿裸 ID 查资源会返回
   一堆 `maxGpuNum=0`，看着像"没资源"其实是 ID 不完整。脚本已自动补全。

## 铁律速查（常驻）
1. 🔴 **建实例实时扣费**：`--yes` 缺省时只打印请求体；用户明确说「建/开一个实例」并看过请求体后再加 `--yes`。
2. 🔴 **凭据只有全局配置这一处**：`~/.workbuddy/fxai_global.json`（`otai` 节点：`api_token` / `ssh`）。**SSH 密码不写进技能**（要打包分发）。
3. **不封装「卸载实例」**：不可恢复，技能侧不该有能力一键调它，需要销毁走控制台。
4. **可用资源看 `maxGpuNum>0`**，不是看 GPU 型号在不在列表里。列表里 maxGpuNum=0 的照样建不出来。
5. **每个模型锁死单一已验证源**，中途换源会让不支持续传的源把进度清零。新增源先跑 `probe_sources.py` 确认返 206。
6. **失败时保留半截文件**（`curl -C -` 天然续传）。**并发 4 路封顶**，**一轮全跑完之后**才对账重下。
7. **长任务后台跑 + 等完成通知**，用 `otai_progress.py` 看进度。
8. **磁盘只有 70G**，下载前必做磁盘预检。

## 三种跑法，按现场条件选

### A. 有 SSH（首选）
```bash
cd <技能目录>/scripts
python deploy.py plan  --group all                 # 先看要装什么、多大
python deploy.py probe -H <host> -p <端口> -u root -P <密码>   # 体检：GPU/磁盘/已有 ComfyUI
python deploy.py run   -H <host> -p <端口> -u root -P <密码> --group all
```
密码登录需要 `paramiko`；已有公钥传 `-k ~/.ssh/id_ed25519` 走系统 ssh。密码也可用环境变量 `OTAI_SSH_PASSWORD`。

### B. 没有 SSH / 只想手动执行
```bash
python deploy.py emit --group all --out otai_deploy.sh
```
生成自包含 bash，整段粘到实例终端执行。

### C. 只想要别的东西
```bash
python deploy.py verify -H ...        # 只看远端现状
python deploy.py run --no-models      # 只装节点
python deploy.py run --no-nodes       # 只下模型
```

## 常用开关

`--group img`(21 GB) / `--group music`(7.3 GB) / `--group mv`(约 55 GB) / `--group vocalsep`(0.9 GB)；
`--variant pruned` 让 H3 主模型 34 GB → 19.6 GB。**70G 盘建议 `--group img` + `--group vocalsep` 分两轮。**

## 常用命令速查
```bash
python otai_api.py balance                     # 余额与代金券
python otai_api.py list                        # 实例列表
python otai_api.py available                   # 当前真能建的资源（maxGpuNum>0）
python otai_api.py private-images              # 我的镜像
python otai_api.py create --port 8188          # 演练；加 --yes 才真建
python otai_api.py boot <id>                   # 开机并等运行中
python otai_api.py shutdown <id>               # 关机并等已关机
python otai_api.py monitor <id> --kind gpuutil # 近 30 分钟 GPU
```
