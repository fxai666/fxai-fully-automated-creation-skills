---
name: fxai-workflow-publish
description: 工作流发布版生成与品牌条管理。把开发版（占位图 banner，不显示群号）工作流生成为对外发布版（真实品牌+logo+群号），管理「开发版⇄发布版」切换。当用户说「发布工作流」「生成发布版」「把品牌条加上」「去掉品牌条」「改 banner」「工作流对外发」时使用。仅做本地版本生成；推送到仙宫云/线上由 fxai-xiangongyun-deploy 的 sync_workflows.py 完成。
---

# 工作流发布（发布版生成 + 品牌条管理）

把本机「开发版」工作流（banner 是中性占位图，不显示品牌/群号，录教程时不被平台检测）
一键生成为「发布版」（banner 换成真实品牌+logo+群号图），供对外发布。
仙宫云只是发布渠道之一，本技能管的是**版本切换**，不负责上传。

## 本技能文件清单（先读这里，再决定读哪份）

| 文件 | 什么时候读 |
|---|---|
| `scripts/set_banner.py` | 切换某目录的开发版 ⇄ 发布版（改 banner 逻辑时读） |
| `scripts/make_release.py` | 生成发布版目录（幂等复制 + 切真实图） |
| `scripts/global_cfg.py` | 全局配置 `workflows` 块的解析口径；改配置结构时读 |

只读对应的那一份，其余一律不读；本节已覆盖的路径与命令不必再翻脚本正文。

## 概念（用户 2026-11-14 定）
- **开发版 / 录制版**：本机顶层 `user/default/workflows/*.json`。banner 节点挂**占位图**
  `fxai_workflow_banner_placeholder.png`——中性：品牌色 + 工作流标题（如「凤希AI · 无限时长工作流实操讲解」），
  **不带群号 / logo / 网址**。录屏时顶部可见标题，平台检测不到品牌信息。
- **发布版**：`user/default/workflows/发布版/*.json`（旧名「仙宫云」，2026-11-15 改名）。
  banner 节点换**真实品牌图** `fxai_workflow_banner.png`（品牌 + logo + 群号）。
  一切对外发布都用这版；仙宫云是其中一个发布渠道。

实现：**不动节点结构**，只替换 banner 节点（`properties.fxai_banner==true` 的 LoadImage）
里 `widgets_values[0]` 的图片文件名。对出图链路零影响，幂等。

## 目录铁律
- 本地 ComfyUI 工作区 `user/default/workflows/`：
  - 顶层 `*.json` = 开发版（占位图）
  - `发布版/` = 发布版（真实图）
- 占位图与真实图都在 `ComfyUI/input/`。

## 命令

```bash
D=/d/ProgramData/miniconda3/python.exe
SK=~/.workbuddy/skills/fxai-workflow-publish/scripts

# 1) 把某目录设为开发版（占位图）
"$D" "$SK/set_banner.py" --src <目录> --to placeholder

# 2) 把某目录设为发布版（真实图）
"$D" "$SK/set_banner.py" --src <目录> --to real

# 3) 生成发布版目录（复制+切真实图，幂等）
#    目录读全局配置 workflows 块，**不给参数也能跑**（命令行 --dev/--release 优先）：
"$D" "$SK/make_release.py"

# 4) 仅预览不写盘
"$D" "$SK/set_banner.py" --src <目录> --to real --dry-run
```

## 目录配置（最新标准：全局配置 workflows 块，不硬编码进代码）

所有工作流/发布版路径统一配在 `~/.workbuddy/fxai_global.json` 的 **`workflows` 块**，
本技能与仙宫云 sync 都读它（`global_cfg.py` / `_workflows_cfg()` 解析），**代码里不写死路径**：

```json
"workflows": {
  "comfy_root": "<本机 ComfyUI 安装目录，全局配置 comfyui.root 同义>",
  "dev_dir": "user/default/workflows",
  "release_dir": "user/default/workflows/发布版",
  "banner_real": "fxai_workflow_banner.png",
  "banner_placeholder": "fxai_workflow_banner_placeholder.png",
  "secrets": "<实例密钥 json 的绝对路径>"
}
```

- 子目录（`dev_dir`/`release_dir`）相对 `comfy_root` 拼接成绝对路径；`comfy_root` 可被环境变量 `FXAI_COMFY_ROOT` 覆盖。
- 发布版目录可被环境变量 `FXAI_WORKFLOW_RELEASE_DIR` 显式覆盖。
- **旧扁平键 `xgy_workflow_src` 已弃用**（2026-11-15 起只认 `workflows` 块）。

## 推送（不在本技能做，转仙宫云技能）
生成本地发布版目录后，上传到仙宫云实例用
`fxai-xiangongyun-deploy` 的 `sync_workflows.py`（**也读同一个 `workflows` 配置块**，`--src` 可显式覆盖；
来源若还是占位图会临时换真实图，幂等 no-op；并补传品牌图）：
```bash
"$D" ~/.workbuddy/skills/fxai-xiangongyun-deploy/scripts/sync_workflows.py --profile <实例ID>
```

## 铁律
- 版本切换只改图片文件名，**绝不增删/移动节点**，保证幂等与出图链路零影响。
- 占位图**永远不带**群号 / logo / 网址（录屏安全）；真实图才含群号。
- 改本地文件用 `set_banner.py`（开发版⇄发布版 切换同一目录）；要另存发布版目录用 `make_release.py`。
- **路径一律读全局配置 `workflows` 块，不硬编码进代码**；旧键 `xgy_workflow_src` 已弃。
- 上传线上统一走仙宫云同步脚本，本技能不直接传。
