---
name: fxai-physical-publish
description: 物理仿真发布通道。用系统级 SendInput 鼠标键盘操作真实浏览器窗口完成发布，不接管 CDP、不注入脚本、不读写 cookie 与登录态；首次录制站点操作区域，之后按区域真实点击，轨迹与间隔每次随机。适用于要求「完全像真人手动操作」的视频/图文/文章发布，或对自动化行为敏感的站点。触发词：物理仿真发布、真人操作发布、真实点击发布、模拟真人发布。
---

# fxai-physical-publish · 物理仿真发布

本技能定义**一条用系统级鼠标键盘驱动真实浏览器的发布通道**：程序不控制浏览器内部，只像人在屏幕上移鼠标、按下松开、敲键盘。

包内不预置任何站点、地址、坐标、账号与凭据；站点差异由使用方在**实例目录**里按本规范录制，命中即复用。

## 本技能文件清单（先读这里，再决定读哪册）

| 文件 | 什么时候读 |
|---|---|
| `references/00-铁律与执行细则.md` | **动手前必读**：39 条铁律全文、例外与处置、窗口复用 |
| `references/01-通道原理与边界.md` | 判断可否覆盖目标站点、是否换通道时 |
| `references/02-区域录制与定位规范.md` | 录制区域、定位控件、判区域失效时 |
| `references/03-真人节奏与行为仿真.md` | 调节奏参数、`gap` / `pause` 取值、排查「太假」时 |
| `references/04-站点接入台账与异常处理.md` | 接入新站点、回写动线、查异常处置 |
| `references/05-发布前合规清单.md` | **外发前必读**：AI 标记、原创声明逐站对照 + 人工验证 |
| `references/06-流程迭代与合集文案规范.md` | 三次迭代、合集标准、文案分层、**视频类标题生成规范（短剧/MV）**、人工验证 |
| `references/07-封面尺寸与版式规范.md` | 定封面 key、竖版标题排布时（**规范本体在 `fxai-poster`**） |
| `references/08-步骤类型与回执.md` | 写流程 JSON、查 `do` 取值与字段、定回执口径 |
| `references/09-底部锚定与局部校验.md` | **点不中 / 布局漂移时必读**：相对底边、局部截图、自动重定位、粘贴录入、诊断 |
| `scripts/publish_cli.py` | 命令入口：`env` / `launch` / `snap` / `mark` / `probe` / `reanchor` / `preflight` / `run` / `reveal` |
| `scripts/locate.py` | 按颜色扫描控件矩形，录区域前定位 |
| `scripts/human/` | 引擎本体（窗口、轨迹、输入、区域），改引擎才读 |
| `config.example.json` | 实例配置模板：节奏参数与字段名 |

**确定本次要做什么之后，只读对应的那一册，其余一律不读。**

## 什么时候用这条通道

要「完全像真人手动操作」、站点有强行为风控、或页面频繁改版 → **本通道**；要快、要批量、要稳定重放 → `fxai-browser-publish`。两条通道**共用实例目录**，互不覆盖：自动化通道写 `config/sites/<站点>.json`，本通道写 `sites/<站点>.json`；选型对照见 `01` 第七节。

## 铁律（不可退让）

40 条判据全文、例外与处置只写 `references/00-铁律与执行细则.md`；第 28~31 条见 `references/09-底部锚定与局部校验.md`，第 39 条（原创/合集必勾项校验）、第 40 条（AI 声明 / 原创声明 / 合集三项硬门对所有站点一律适用）为硬门。开工前必读 00 册，本文件不复写判据。

## 快速开始

```bash
PY="<python>"; S="$HOME/.workbuddy/skills/fxai-physical-publish/scripts"

$PY -B $S/publish_cli.py env                     # 自检
$PY -B $S/publish_cli.py launch --fit 1500x900   # 开浏览器
$PY -B $S/publish_cli.py preflight --site <站点>      # 开工诊断
$PY -B $S/publish_cli.py mark --site <站点> --name publish_btn --anchor bottom --color ksred --text 发布
$PY -B $S/publish_cli.py probe --site <站点> --name publish_btn [--learn|--relocate]
$PY -B $S/publish_cli.py reanchor --site <站点> --names publish_btn --anchor bottom
$PY -B $S/publish_cli.py run --site <站点> --flow video   # 跑流程（整条连跑，别拆段）
$PY -B $S/publish_cli.py run --site <站点> --flow video --audit      # 跑完看逐步耗时表
$PY -B $S/publish_cli.py run --site <站点> --flow video --from-step 23  # 异常修好后续跑
$PY -B $S/publish_cli.py reveal                  # 缩窗让位（不置顶）
$PY -B $S/publish_cli.py minimize                # 最小化，让屏给对话
$PY -B $S/publish_cli.py close --site <站点>     # 仅用户明确要求关窗时才用（不是流程步骤）
```

其余命令见 `08-步骤类型与回执.md`，目录归属见 `00` 第三节。
相邻技能：`fxai-browser-publish`、`fxai-poster`、`fxai-standard`。
