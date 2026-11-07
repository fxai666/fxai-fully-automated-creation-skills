# fxai-short-drama-script · 短剧剧本层技能包（安装 / 使用说明）

> 纯写作技能：不碰 ComfyUI、不碰 GPU、不写提交脚本，只产出**项目档案 + 逐集分镜**。
> 生视频那一步交给 `fxai-short-drama`（换项目时两个技能一起装）。

## 一、本包包含什么

| 文件 | 作用 |
|------|------|
| `SKILL.md` | 总入口：产出物与目录结构、六步流程、爆款结构硬规则、分镜固定格式、写作禁令、验收、设定变更 |
| `references/storyformula.md` | **爆款公式查询表**：3 秒钩子 6 型、每集 10 镜结构模板、反转须埋线、结尾卡点 3 型、三条核心情报法、五幕与续季规划法、台词写法 |
| `references/storyboard-template.md` | project_def.json / 剧集档案 / 角色库 / 场景库 / 单集分镜 全套模板 + 目录命名与服务端编号规则 |
| `references/h3-link.md` | 分镜 → shots 的映射表、交接口（`check_episode_duration` / `render_episode`）、H3 防坑表（画不出汉字、参考图带字、群演糊脸…）、出片验收与单镜返修 |
| `scripts/check_storyboard.py` | 通用分镜验收 CLI（**只用 Python 标准库**），换项目只改 `--root` |
| `references/check-rules.md` | 技能自有规则（老写法清单、常驻误报、解析判据）。**与项目记忆完全隔离**：这里只写技能自己，某部剧写了什么一律写回那部剧项目的 `.workbuddy/memory/` |

## 二、装到哪

- **用户级（推荐，对所有项目生效）**：把 `fxai-short-drama-script/` 整个文件夹复制到 `<你的用户名>/.workbuddy/skills/`
- **项目级（仅当前项目生效）**：复制到 `<你的项目>/.workbuddy/skills/`

装完无需任何配置、无需装依赖、无需起服务。

## 三、怎么起一个新项目

```bash
# 1. 建项目目录：项目根/
#    project_def.json / 剧集档案.md / 角色库.md / 场景库.md / assets/ / N、集名/完整分镜脚本.md
#    目录与字段照 references/storyboard-template.md 抄模板

# 2. 写第 1 集 → 照着写后面的集（每集 10 镜 × 6-8s ≈ 65-70s）

# 3. 自检（全绿才往下走）
python scripts/check_storyboard.py --root "<项目根>"

# 4. 交给 fxai-short-drama 出片（分镜 → shots 的规则见 references/h3-link.md）
```

改门槛／换文件名：

```bash
python scripts/check_storyboard.py --root <项目根> --min-seconds 60 --pattern "*完整分镜脚本.md"
```

## 四、依赖边界

- **本技能零外部依赖**：`check_storyboard.py` 只用 argparse/glob/os/re/sys，任何 Python 3.8+ 都能跑。
- **跨技能依赖只在出片那一步**：分镜写完后由 `fxai-short-drama`（依赖 ComfyUI + fxai-workflow + Fabric 生图技能）接手；本技能不 import 它们。
- **服务端编号**（`server_subdir` / `server_index`）是给生图对齐用的，本技能只负责在 project_def 里维护映射，不连服务器。

## 五、换项目改什么

只改**项目自己那份**的东西，技能一行都不用动：

1. `project_def.json` 的 `server_subdir`、`characters`/`scenes` 的 `server_index`、`world_constraint`。
2. 剧集档案/角色库/场景库里的人物、场景、集名。
3. 验收时传 `--root "<新项目根>"`。

## 六、三条硬规则（写之前先看）

1. **不带 3 秒钩子别开写**：每集第 1 镜必须是危机/背刺/身份揭晓/数字冲击/物件指认/认知推翻之一，且不连续两集同型（查 `references/storyformula.md` 的表）。
2. **总时长先算后写**：单集 ≥60 秒，规划期就得凑够 8-10 镜 × 6-8 秒，别等成片再返工。
