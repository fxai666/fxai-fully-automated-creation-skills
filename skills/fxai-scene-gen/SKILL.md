---
name: fxai-scene-gen
description: 生成场景图（可选把人物直接放进场景）。跨项目通用的「场景图」技能——抽象出两种模式：空场景（role=None，只画环境不含人物，供 MV 多参考 multiref 模式用）与含人物（role="woman"/"man" 或自定义参考图，把人物画进场景，供 single/multi MV、短剧用）。不依赖 fxai-mv-pipeline，依赖共享客户端 fxai-workflow/comfy_client.py 与 fxai-workflow/img-qwen21-process.json。
---

# fxai-scene-gen —— 场景图生成

## 本技能文件清单（先读这里，再决定读哪几册）

| 文件 | 什么时候读 |
|------|-----------|
| `SKILL.md` | 本文件：服务端模式铁律/开工前读经验库/何时调用/调用方式/参数/空场景铁律/等任务 |
| `scene_gen.py` | generate_scene() 实现；按 SKILL.md 调，改行为才读 |

**确定要做什么后只读对应那一两册，其余按需取用。**


独立的「场景图」技能，不依赖 fxai-mv-pipeline，任何项目都能直接调。底层复用公共目录 `fxai-workflow/` 的共享 ComfyUI 客户端 `comfy_client.py` 与工作流 `img-qwen21-process.json`。

## 🚨 开工前置（先读，再动手）

🔴 **第一步：读 `fxai-workflow/SKILL.md` 的「🚨 ComfyUI 服务端模式铁律」**——铁律在被依赖件的 SKILL.md 里，不在 `comfy_client.py`（只取函数必漏）。要点：ComfyUI 由「凤希本地AI控制台」／启动器启停，本技能只按地址发 HTTP；本机 8188 探不通 → 走云或提示人启动。

- 🔴 资源有无只认服务端接口：本机磁盘扫到的「有/无」不代表服务端。确认方式：① 直接提交工作流，被服务端 `queued` 即证明已读到参考图；② 或 `GET /fxai/down/file?url=<category/subdir|file>` 探测。

## 开工前：先读「素材」领域的经验库

出图之前先读一遍 `{data_dir}/经验库_素材.md`（由 `fxai-content-review` 导出）；文件不存在说明还没复过盘，直接开工。

- 每条都带「第几次出现」和「下次该怎么改」，把「下次」带进本轮的提示词与构图。
- 标「顽疾」「已确认」的优先遵守；标「待观察」的按判断取舍。
- 目录解析：环境变量 `FXAI_DATA_DIR` > `fxai_global.json.data_dir` > `工作目录/_fxai_data`。

## 何时调用本技能
- **MV 多参考模式（空场景）**：场景图必须是【空场景·不含任何人物】，人物靠 char_rel 在 MV 生成阶段注入 → `role=None`。
- **single/multi MV、短剧（含人物）**：人物直接画进场景 → `role="woman"/"man"` 或自定义参考图。
- 任何项目需要「按提示词出一张场景/空镜/带人物场景图」时。

## 调用方式（其它技能导入）
```python
import sys, os
sys.path.insert(0, os.path.join(SKILL_DIR, "..", "fxai-scene-gen"))
from scene_gen import gen_scene_image

# 空场景（MV 多参考用，只画环境）
p, rel = gen_scene_image("汉未央宫高台，云海翻涌，晨光斜照，气势恢宏", role=None,
 w=1440, h=1920, tag="scene1", subdir="mv_scene", num=0)

# 含人物（single/multi MV、短剧用）
p, rel = gen_scene_image("现代开放式办公室，明亮忙碌，女子立于窗前", role="woman",
 w=1280, h=768, tag="office", subdir="场景图")
```

## 参数说明
- `prompt`：场景/环境描述（场景、氛围、光线、构图；含人物时再写人物在做什么与穿搭）。**不追加任何后缀**。
- `role`：
 - `None` → 空场景（纯环境，不含人物）—— **MV 多参考铁律：场景图必须空场景**；
 - `"woman"`/`"man"` → 用自有角色参考图 000/001 把人物画进场景；
 - 自定义相对路径字符串 → **原样作为参考图传入**：可以是角色三视图、四视图的相对路径，或单张五官参考图（如 `avatar/000_facial.png`、`charview/角色A.png`）。场景生成阶段据此把人物画进场景。
- `w`, `h`：规范尺寸（竖屏 1080×1920 / 1440×1920，横屏宽高交换）。
- `subdir`：ComfyUI 端保存子目录（MV 用 `mv_scene`，短剧可自定义）。
- `num`：固定文件序号（配合节点「启用文件名覆盖=True」实现同名覆盖）。
- `timeout`：等待秒数，默认 **3600**（同一台 ComfyUI 还有别人的任务在跑时排队可能十几分钟，600s 会误判超时）。
- `force`：默认复用本地已有同名图（不重画）；`force=True` 才重新生成。
- 返回 `(本地png路径, ComfyUI相对路径)`。

## ⚠️ 空场景铁律（MV 多参考模式）
场景图 `img` 只写环境（场景/光线/氛围/构图）；「一位女子立于…/穿…」之类人物描述写在分镜的 `act`（MV 提示词消费），不进场景图——否则场景图带人，与参考人物重复/冲突，MV 里出现双人或错位。

## 等任务

生图走共享客户端的 `submit_workflow`，内部等 `fxai:task_saved`（＝**已保存**）才返回，返回即已过终态门。

| 阶段 | 规则 |
|---|---|
| 等待期·**正常** | ✅ 只查健康（服务器/进程还活着吗）<br>❌ 不做数据交互：不查 `/history`、不拉文件列表、不取结果、不下载、不反复 curl `/queue` |
| 等待期·**报错** | ✅ **这时才做数据交互**：取错误详情与日志，分析原因并解决。异常里带 `prompt_id`，用 `comfy_watch.py status <pid>` 查详情 |
| **终态门** | 日志出现 `✅ 合并成功，已保存：<url>` 之后，才做后续：取产物 → 归档 → 交付 |

- 要在对话里只在完成时播报一句 → `comfy_watch.py next <pid>`（只出终态，不刷过程）。
- 要全程看进度 → 双击 `fxai-comfyui-watcher/progress.html`（直连 WS，绕过对话，不耗 token）。

## 依赖
- `fxai-workflow/comfy_client.py`（共享客户端）
- `fxai-workflow/img-qwen21-process.json`（工作流，去前缀 + 模型 token 命名，内部 `fxai_` 占位符不变）
- ComfyUI 常驻在线（`comfy_url`，默认 `http://127.0.0.1:8188`）
