---
name: fxai-character-styling
description: 角色造型能力（用户级）。当用户要「选人物+服装+姿势生成角色造型图」「生成穿搭展示图/角色造型照」时使用。把 ai-pose-gallery-zh 项目的提示词写法、风格预设、素材检索整套吸收为自有程序：选人（走角色库 fxai-role-management）、选服装/姿势（本技能登记表检索）、构建提示词、调用场景工作流出图。不做：人物头像生成（走角色库）、视频、发布。
agent_created: true
---

# 角色造型能力（fxai-character-styling）

一句话：**选人物 + 选服装 + 选姿势 → 构建提示词 → 调用场景工作流 → 出角色造型图**。整套程序照搬 `ai-pose-gallery-zh` 的 `buildRecipePrompt()` 写法与风格预设，转为自有实现。

## 本技能文件清单（先读这里，再决定读哪册）

| 文件 | 什么时候读 |
|---|---|
| `SKILL.md` | 本文件：命令速查、铁律、文件清单 |
| `references/01-素材登记表规范.md` | 定字段含义、套系(SFW/NSFW)、三级目录命名、登记规则时 |
| `references/02-提示词与风格写法.md` | 改提示词构建逻辑、加减风格预设、核对与仓库原文一致性时 |
| `references/03-选图与出图接口.md` | 选人/选服装/选姿势、调工作流、路径传参、SFW 门禁时 |
| `scripts/registry.py` | 素材登记：init / add / scan / catalog / check |
| `scripts/prompt_builder.py` | 提示词构建器：`build_recipe_prompt()`，逐字对应仓库 TS 原文 |
| `scripts/select.py` | 选图：按套系+分类树筛选，输出 `图片` 路径 |
| `scripts/compose.py` | 编排入口：串起选图→提示词→工作流（只组织，不内嵌生图逻辑） |
| `scripts/styling_api.py` | **本技能 API 客户端**：三图上传→构建提示词→提交→下载，真出图走它 |
| `scripts/charstyling-qwen21-api.json` | 自带扁平 API 工作流随包副本（查阅用；真相源在 `fxai-workflow` 注册表） |
| `data/assets.csv` | 机读登记表；仅表头模板，初始为空，不含任何图片 |
| `data/styles.json` | 风格预设，逐字照抄仓库 `STYLE_PRESETS` |

## 配套工作流（三态，与凤希体系命名对齐）

| 形态 | 落点 | 用途 |
|---|---|---|
| UI 可编辑版 | `ComfyUI/workflows/凤希AI/角色造型合成V1.json` | ComfyUI 界面打开、调节点/模型 |
| UI API 版 | `ComfyUI/workflows/凤希AI/角色造型合成V1API.json` | 导出给 API 用的精简 UI 版 |
| 扁平 API 版 | `fxai-workflow/charstyling-qwen21-api.json`（注册表 `charstyling`→`qwen21`） | `submit_workflow` 实际提交用 |

确定本次要做的事之后，只读对应的那一册，其余一律不读。

## 直接开跑

```bash
S=~/.workbuddy/skills/fxai-character-styling/scripts

# 1) 素材登记（不搬图，只记路径）
python $S/registry.py init
python $S/registry.py add --类型 服装 --套系 SFW --一级分类 夏季 --名称 白色碎花裙 --图片 ../../img/costume/x.webp --标签 "白色 碎花"
python $S/registry.py scan <素材根目录> --类型 姿势 --套系 SFW   # 按目录树批量草稿
python $S/registry.py catalog

# 2) 选图（三级目录逐层过滤；SFW 默认，NSFW 需显式）
python $S/select.py pick --类型 姿势 --套系 SFW
python $S/select.py find --关键词 仰卧 --类型 姿势 --套系 NSFW

# 3) 构建提示词（照抄仓库原文，不改写法）
python $S/prompt_builder.py --人物 avatar/000.png --服装 <服装路径> --姿势 <姿势路径> --姿势名 仰卧V字开腿 --角度 正面 --风格 realistic

# 4) 出图编排（路径与提示词齐了再交给工作流；本步只组织，不下载不搬图）
python $S/compose.py --dry-run --人物 avatar/000.png --服装 <id> --姿势 <id> --风格 realistic

# 5) 真出图（本技能 API 客户端：三图上传→构建提示词→提交→下载）
python $S/styling_api.py --人物 avatar/000.png --服装 <服装路径> --姿势 <姿势路径> \
  --服装名 <名称> --姿势名 <名称> --角度 正面 --风格 realistic --dry-run   # 先预览
python $S/styling_api.py --人物 avatar/000.png --服装 <服装路径> --姿势 <姿势路径> \
  --服装名 <名称> --姿势名 <名称> --角度 正面 --风格 realistic            # 去 --dry-run 真提交
```

## 铁律速查

1. 🔴 **提示词照抄仓库**：`prompt_builder.py` 与 `data/styles.json` 逐字对应 `aiGenerator.ts` 的 `buildRecipePrompt()` / `STYLE_PRESETS`，不自行发挥（用户硬规矩）。
2. 🔴 **不写穿搭文字**：服装靠服装参考图，名称只在括号作辅助说明（原文本来如此，保持）。
3. 🔴 **人物走角色库**：`fxai-role-management` 管人物；本技能只选服装/姿势。
4. 🔴 **套系门禁**：默认只出 SFW；NSFW 需显式 `--套系 NSFW` 才出。
5. 🔴 **多级目录**：三级分类对应目录层级，选择逐层过滤，有几级填几级。
6. 🔴 **只记路径不搬图**：`add`/`scan` 只写表，不移动/复制/删除任何图片。
7. 🔴 **合规边界**：第三方图只作创作参考，禁商用/打包分发；NSFW 禁未成年人；满 18 才呈现着装性感。
8. 🔴 **本机出图尺寸**：本机固定按下游 `fxai-workflow` 的 `default_max_side()` 档位（16G→960），不自行抬档。

## 指针

- 人物：`fxai-role-management`；提示词原文：`repo/src/services/aiGenerator.ts`（只读不改）。
- 出图：`scripts/styling_api.py` → `fxai-workflow`（`charstyling`→`charstyling-qwen21-api.json`）。提交/轮询/下载全委托 `fxai-workflow/comfy_client.py`，本技能不重复实现协议。
- 通用规范：`fxai-standard`。
- 数据目录：`FXAI_POSE_COSTUME_DATA` > 技能内 `data/`；路径不写死盘符。
