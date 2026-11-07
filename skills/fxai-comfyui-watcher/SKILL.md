---
name: fxai-comfyui-watcher
description: ComfyUI 任务进度广播监听与「等任务完成」通知（全局共享，零依赖）。当需要「等渲染跑完再继续」「看任务跑到第几镜/百分之几」「一次看到服务器上所有在跑的任务」「拿到产物文件名」时使用；提交任何 ComfyUI 任务（短剧成片、生图、MV、四视图、超分）后都应改用它等待，取代反复 sleep 轮询 /history。主通道是 fxai-toolkit 自己广播的 fxai:progress / fxai:task_saved 事件，按 prompt_id 路由，任何项目共用同一份台账。
---

# ComfyUI 进度广播监听（fxai-comfyui-watcher）

## 本技能文件清单（先读这里，再决定读哪几册）

| 文件 | 什么时候读 |
|------|-----------|
| `SKILL.md` | 本文件：地址铁律/快捷命令/标准流程/台账/终态判据 |
| `scripts/comfy_watch.py` | 监听器实现（纯标准库，自己握手 WS）；按命令调 |
| `progress.html` | 浏览器进度面板，双击直连 WS |
| `scripts/start_watcher.cmd` | 常驻监听启动器（双击无窗口） |
| `references/01-排障与边界.md` | 排障表/僵尸占卡/台账并发/库用法/事件与终态全表/看进度与地址细节——**只在报错、怀疑卡住、要查字段或嵌等待逻辑时读** |
| `references/02-续跑与查镜.md` | 中断续跑、查已生成到第几镜、逐段产物与自动合并、三种看进度方式——**只在续跑、断点、核对分镜数时读** |

**确定本次要做什么之后，只读对应的那一两册，其余一律不读。**

常驻进程订阅 `<server>/ws`，按 `prompt_id` 路由落盘，任何项目读同一份台账。`scripts/comfy_watch.py` 纯标准库，自己实现 RFC6455 握手与帧解析，无需装包。`fxai-workflow/comfy_client.py` 的 `run_workflow` / `submit_workflow` 已内置本技能，跑 MV / 生图 / 短剧时日志自己滚进度行；手工命令用于「手动查 / 守着等 / 翻台账」。

## 服务器地址：每次按实际用的那台解析

`resolve_server()` 优先级（`comfy_watch.py` 与库用法共用）：显式 `--url` → 探活本机 `127.0.0.1:8188` → 全局配置 `comfy_url` → 本机默认兜底。

第 2 步每轮必做：全局配置里的地址通常指向仙宫云，漏掉本机探活会让本机任务连到云上空等。URL 一律先 `norm_url()` 规范化（反斜杠→正斜杠、压重复斜杠、去尾斜杠与空白，与公共层 `comfy_client.norm_url` 同规则）。每条任务在台账记 `server` 字段，`wait` / `status` 优先用该任务自己那台，`status` 打印 `服务器`。本机 HTTP 请求走模块内免代理 opener（`_OPENER = build_opener(ProxyHandler({}))`），理由见 references/01。

## 快捷命令

监听器路径（下文 `WATCH` 代指）：`WATCH="python <技能目录>/scripts/comfy_watch.py"`

| 需求 | 命令 |
|------|------|
| **等某个任务跑完（首选）** | `$WATCH wait <prompt_id> --timeout 5400` |
| 看进度/产物/最近日志 | `$WATCH status <prompt_id>`（前缀即可，如 `status 1c8d942e`） |
| 在跑的任务 | `$WATCH top` |
| **查已生成到第几镜** | `$WATCH shots <prompt_id> [--dir 保存目录] [--total 总镜数] [--files]` |
| **断了接着跑（一条命令）** | `$WATCH resume <prompt_id> [--wait] [--dry-run] [--dir 目录] [--total N]` |
| 常驻监听（广播台账） | `$WATCH watch`（地址自动解析；`--url <地址>` 指定） |
| 取监听自己的 clientId | `$WATCH sid` |
| 停掉常驻监听 | `$WATCH stop` |

`wait` 退出码：**0=success / 1=error·interrupted / 2=超时**，完成时直接打印成片文件名。`next <pid>` 等「全部保存完毕」就退出并触发会话通知；`progress <pid>` 读本地游标看中途进度；`--log` 让过程落盘供事后查。

## 标准流程：提交 → 提交 wait → 等通知

提交渲染拿到 `prompt_id`（写进 `epNN_pid.txt`）后立刻起一个 wait 放后台，它出终态时本会话会收到通知，收到后跑 `acceptance_burn` 烧字幕：`python ".../comfy_watch.py" wait <prompt_id> --timeout 5400`。

## 台账文件（运行时目录 `~/.workbuddy/runtime/comfy_ws`，可用 `COMFY_WATCH_HOME` 改）

`state.json` 全局任务快照、`events.jsonl` 全量事件流、`tasks/<prompt_id>.log` 单任务进度、`watcher.pid` / `watcher.log` 常驻进程 pid 与心跳日志；字段见 references/01。记录保留最近 200 条，旧的自动清掉。

## 通道：主通道是 fxai 事件

`fxai:progress` / `fxai:task_saved` 是主通道，任何监听者都能收到，data 字段全表见 references/01。`url` 按 `|` 切，第一段是 `类别/子目录`，其余是文件名列表；`类别=video 且 子目录=merged` 时该文件名记为 `merged`（成片名）。提交 `/prompt` 时带上 sid（`$WATCH sid`，或读 `state.json` 的 `client_id`），body 加顶层字段 `{"prompt": wf, "client_id": sid}`；`server.client_id` 是全局单值，多人/多会话同时提交会互相覆盖，判定完成一律以 `fxai:task_saved` 为准。

## 中断续跑 / 查分镜：先查再续

`$WATCH shots <pid>` 查已生成到第几镜，`$WATCH resume <pid>` 接着跑（返回新 `prompt_id`）。判定口径、快照回退顺序、逐段产物与自动合并规则、三种看进度方式见 references/02。

## 终态判据

`fxai:task_saved` 是过程事件，只记账不算完成。终态是 `execution_success`、静默 `settle` 秒（默认 30）、`execution_error` / `execution_interrupted`；`next` 退出前打印台账未读行 + 各类保存计数（如 `image 12、video 1`）+ 最终产物（`final_artifact()`：短剧 / MV 取成片 `video/merged|xxx.mp4`，单视频取 mp4，人声分离取 wav，生图 / 四视图 / 场景图取 png）。全表见 references/01。
