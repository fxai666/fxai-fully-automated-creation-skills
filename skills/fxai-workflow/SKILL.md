---
name: fxai-workflow
description: ComfyUI 客户端公共层（工作流 JSON + comfy_client.py），被 fxai-mv-pipeline / fxai-short-drama / fxai-scene-gen / fxai-fourview-gen / fxai-comfyui-image-gen / fxai-yue2 等技能依赖。本文件沉淀「ComfyUI 服务端模式」的通用铁律，调用方技能都必须遵守。本技能由其他技能内部引用，普通对话直接用自然对话即可。
---

# fxai-workflow —— ComfyUI 客户端公共层

## 文件清单（先读这里）

| 文件 | 什么时候读 |
|------|-----------|
| `SKILL.md` | 本文件：服务端铁律/地址传参/本机优先/占位符/接口/等任务/仙宫云 |
| `comfy_client.py` | 统一客户端（submit/download/占位符注入/校验）；参考图 `ref` 路径语义真相源在 `fxai-common`。**改逻辑或排障才读** |
| `xgy_guard.py` | 仙宫云看门狗（探活/开机/关机）——**配仙宫云或关机才读** |
| `output_layout.py` | 产物目录命名 |
| `*.json` | 工作流（`resolve_wf` 解析文件名；`_api` 版自包含模板，非 `_api` 版走占位符注入），改节点才看 |
| `README.md` | 公共层总览；铁律细则、开机流程与进度日志、关机两档、子命令、新增工作流 |
| `references/01-仙宫云实例.md` | 要开关仙宫云实例、解析实例 ID、关机前确认档位时 |

**确定要做什么后只读对应那一两册，其余按需取用。**各技能通过 `comfy_client.submit_workflow` 提交任务，可变参数以 `fxai_xxx` 占位符预埋、提交时注入；文件名由 `resolve_wf(任务, 模型)` 解析。

## 🚨 ComfyUI 服务端模式铁律（最高优先级 · 所有调用方技能必守）

**ComfyUI 跑在服务器端，服务器常在另一台机器；本机磁盘 ≠ 服务端。**

- 🔴 **资源存在性只认服务端**：参考图 `avatar/*.png`、服务端 `output/`、`fxai/image/...` 的有无都由服务端回答，本机 `find`/`ls` 只代表本机。确认动作＝提交工作流看是否返回 `prompt_id` + `queued`，或调 `GET /fxai/down/file?url=<category/subdir|file>`、`POST /fxai/tasks/result`。
- 🔴 **进程管理归「凤希本地AI控制台」**：状态判定只走接口（`GET /queue`、任务结果接口），杀进程硬断渲染。
- 🔴🔴 **服务与技能解耦（API 模式）**：ComfyUI 由「凤希本地AI控制台」／启动器启停；本技能只按地址发 HTTP。
  - **要做的**：探活（`system_stats` / `/queue`）→ 提交任务 → 取结果；释放显存走 `/interrupt` + `/queue clear` + `/free`；收工只 `TaskStop` 自己提交的后台 shell。
- 🔴 **中断/放弃一个渲染任务：按顺序调这三个接口**
  1. `POST /interrupt`（空 body）——断 running，不动进程、不清队列
  2. `POST /queue` body `{"clear":true}` ——清排队中的残留
  3. `POST /free` ——释放显存

  本地那条提交命令（后台 shell）保持不动，任务被接口中断后自行返回。
- **本机 8188 探不通时**：走云地址，或抛错提示人启动（`require_comfyui` 已内置）。**本机 DOWN ≠ 由我来起。**
- ✅ **产物落地是「下载」语义**：本机部署从 `<技能目录>\output\` **复制**进项目；远端走下载接口按返回相对路径拉取。

## ComfyUI 地址：按任务传参，配置只是兜底

地址按三级取：`comfy_client` 各入口的 `url=` 参数（传了就打那台）> `fxai_global.json` 的 `comfy_url` > 回落 `127.0.0.1:8188`；取值走 `comfy_url(override="")`，下载基址走 `down_base(url)`。**地址只从参数与配置取**：临时换服务器走参数（`export` 换终端就失效）。

### 🔴 本机优先（默认路由，任何调用方技能开工前必守）

**能在本机跑的一律走本机，本机没启动 / 忙到探不通才用云**（本机 GPU 不计费，云端按秒计费）。`comfy_client.prefer_local()`：探 `127.0.0.1:8188`，通了切本机；探不通返回 `False`，照旧走云。强制走云用 `url=` 或 `C.comfy_url("<云地址>")` 覆盖，**全局配置 `comfy_url` 保持原样**。走本机不触发关机询问。
- ⚠️ **「本机优先」只指"用哪台"，不授权"自己去起"**：本机探不通就走云或报错让人启动，不是 AI 自己 `python main.py`（见服务端铁律）。

## 占位符约定

- 工作流 JSON 内部预埋 `fxai_xxx` 占位符，如 `fxai_seed` / `fxai_prompt` / `fxai_lyrics` / `fxai_outputdir` / 参考图路径，由 `submit_workflow` 带引号 token 注入。
- **锁死的参数焊死在节点里**（如音乐工作流 node45 的 `max_duration: 360`）。

## 接口速查

`POST /prompt` 提交｜`GET /queue` 查队列｜`POST /fxai/tasks/result` 查结果｜`GET /fxai/down/file?url=<category/subdir|file>` 下载产物｜`POST /interrupt` 中断当前任务（空 body，只断 running）｜`POST /free` 释放显存｜`GET /system_stats` 系统状态｜`GET /object_info[/<节点名>]` 节点定义与可选值（模型下拉列表从这取）。

## 服务器换了模型名 → 自动纠正（出错才动）

工作流里的模型文件名是写死的。服务器升级后（`xxx_int8` → `xxx_v2_int8`、大小写/分隔符变了、多加了 `_convrot`），提交会被 ComfyUI 以 `value_not_in_list` 拒收。`comfy_client` 自己处理，**调用方什么都不用做**：

1. **提交前**：`repair_models(wf)` 拉 `/object_info` 比对，「服务器上没有这个名字」就换成本地能确认的对应项；
2. **被拒后**：解析拒绝响应里的 `node_errors`（ComfyUI 直接点名节点 id、`input_name`、`received_value`），纠正后**立即重发**，不等网络重试的 5 秒；
3. **改正了就回写**：`submit_workflow` 把新名字写回工作流文件，改前存 `<文件>.bak`，`fxai_xxx` 占位符原样保留。**下次直接是对的。**

`repair_models(wf, url)` 返回 `(changes, unresolved)` 可直接调来做体检（`unresolved` 是拿不准、只给了候选的）。

**自动替换的边界**（宁可停下问，不猜）：文件名完全一致 → 归一化后一致（大小写/分隔符）→ 同族同精度且唯一（版本升级）→ 同精度且互为前缀只差 ≤8 字符（加了尾缀）。**精度不同（int8 ↔ fp8）永远不自动换**，`pruned`/`turbo` 这类不同变体也不会被当成新版 —— 这些情况只打印服务器候选让人定，并中断提交。

只认模型文件名类输入（`ckpt_name`/`unet_name`/`lora_name`/`vae_name`/`clip_name` 等，或值以 `.safetensors` 等模型后缀结尾）；采样器名、调度器名这类改了就变生成行为的枚举一律不动。

- 🔴 **服务端连候选都找不到＝旧实例**：V1.9 换了模型，旧实例补不齐（重下 / 改名 / 重启无效）。镜像 ID 不变（覆盖更新），但旧实例是旧快照，必须**新建实例**，确认后才建（实时扣费）。`_fmt_model_fix()` 已打印指引，细则见 `README.md`。

## 等任务结果

`run_workflow` / `submit_workflow` 等 `fxai:task_saved`（＝合并/保存完毕）才返回，**返回即已过终态门**；监听器起不来回落 `/history` 轮询。
**三态**：等待期·正常 → 只查健康（`GET /queue`）；等待期·报错 → 取错误详情与日志并解决；终态门（日志出现 `✅ 合并成功，已保存：<url>`）→ 取产物 → 烧字幕 → 归档 → 交付。广播见 `README.md`。

## 渲染中看第几镜 / 断点续跑

`shot_query.py now` 查此刻正在跑的那个（任务串行，不用记 pid）；`shot_query.py <pid>` 出「已生成 N/M 镜 · 还剩 K 镜」；`resume <pid>` 从下一镜续跑。结果接口 `/fxai/tasks/result` 只在任务结束后写一条结果；进度口径与兜底见 `README.md`「查已生成分镜」。

## 仙宫云：开工前自动开机、收工后问着关机

`comfy_url` 指向仙宫云容器（`*.container.x-gpu.com`）时实例大概率关机。`xgy_guard.py` 统一管两头，所有调用方技能自动生效；本机与非仙宫云地址不触发。开机流程、实例 ID 解析顺序、关机两档口径见 `references/01-仙宫云实例.md`。
