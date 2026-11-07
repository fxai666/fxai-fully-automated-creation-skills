# 06 · 运镜 LoRA（Camera Motion）与运镜写法

> 何时读：要写运镜、觉得 H3 原生运镜不够、或要挂 Camera Motion LoRA 时。
> 与 04 册的分工：04 册讲**原则**（「运镜只写一条主线，写全类型+幅度+速度+跟拍对象」）；本册给**词表 + LoRA 挂载规范**。

## 一、这是什么

一个专门为 MiniMax H3 训练的**运镜 LoRA**（Camera Motion LoRA），用来补 H3 原生缺失的运镜方式。
模型卡信息：`base_model: Comfy-Org/MiniMax-H3`、`license: apache-2.0`、标签含 `camera-movement` / `cinematography`。

## 二、触发词（必须）

```
camera motion
```

- **必须放在 prompt 开头**——它是激活本 LoRA 运镜控制能力的开关。
- 后面接：具体运镜 + 场景。
- ⚠️ 与本技能既有约定不冲突：触发词在最前，正文仍按 01 / 02 册的格式写。

## 三、提示词结构（训练素材标注格式 = 最匹配写法）

```
camera motion, [主体], [场景], [运镜描述], [光线/风格], [画质]
```

**三种用法**：

| 用法 | 怎么做 | 什么时候用 |
|---|---|---|
| **直接套素材提示词** | 整条照用 | 效果与训练数据最接近 |
| **换场景、保留运镜** | 只改 `[主体], [场景]`，逗号后面的运镜短语不动 | 有自己想拍的内容 |
| **换运镜、保留场景** | 只替换运镜短语（`slow push-in` → `slow orbit`） | 场景固定、要换镜头感 |

**格式写法**（素材风格，我们只借运镜短语，主体/场景换成自己的）：

```
camera motion, [你的主体], [你的场景], slow push-in with subtle lateral tracking, cinematic color grading, 4k, high quality
```

## 四、文件与加载

| 版本 | 文件名 |
|---|---|
| 1000 步 | `camera_motion_h3_lora_v1_1000_pruned.safetensors` |
| 3000 步 | `camera_motion_h3_lora_v1_3000_pruned.safetensors` |

- `_pruned` 文件放 `ComfyUI/models/loras/`
- LoraLoader 的 `strength_model` 建议 **0.8 ~ 1.0**：
  - `0.8` 运镜温和 ｜ `1.0` 运镜明确 ｜ **`>1.2` 画面可能不稳**
- 底模用 MiniMax-H3 视频工作流（pruned + int8 + convrot 版本）

## 五、运镜类型词表（★ = 稳定度，越高越稳）

| 类型 | 稳定度 | 建议写法 |
|---|---|---|
| 推近 | ★★★ 最稳 | `slow push-in` / `dolly in` / `push in` |
| 拉远 | ★★★ 最稳 | `pull-back` / `dolly out` |
| 手持跟拍 | ★★★ 最稳 | `handheld tracking shot` / `dynamic handheld` |
| 环绕 | ★★☆ 稳 | `slow orbit` / `orbit around` |
| 航拍 | ★★☆ 稳 | `aerial drone shot` / `flyover` |
| 升降 | ★★☆ 稳 | `crane reveal` / `tilt up/down` |
| 推拉结合 | ★★☆ 稳 | `push-in then pull-back` |
| 摇镜 pan | ★☆☆ **较弱** | `pan left` / `pan right`（样本少，建议叠加其他词） |
| 微距/特写 | ★★☆ 稳 | `close-up` / `macro` / `extreme close-up` |

## 六、强度与方向控制

| 需求 | 写法 |
|---|---|
| 更舒缓 | 加 `slow`：`slow push-in` |
| 更强烈 | `dynamic handheld` / `aggressive push-in` |
| 指定方向 | `push-in from left` / `orbit right` / `pan left` |

## 七、怎么接到我们的管线

1. **触发词照写、效果自测**：我们短剧工作流走 Ref2VA，本 LoRA 触发词照写即可，但效果要自己验。
2. **运镜仍守 04 册的「一镜一条」**：本册词表是用来把**那一条**运镜写准，不是让你堆多个。
3. **幅度贴合参考图**：我们写的是图生视频，只能做小幅缓推 / 缓拉 / 轻横移 / 极轻手持——本 LoRA 的 `push-in` / `pull-back` 正好对应。
4. **与 Combat LoRA 可叠加**：`camera motion` 管**运镜**、`prfight1` 管**打斗动作**，两个触发词可同时出现（写法：`camera motion, prfight1`，整行放在 `detailed_description` 第一行）。**挂载规则以 `07-触发词挂载规范.md` 为准**——本册只管运镜词表与强度。

## 八、常见误用

- ❌ 触发词没放开头 → 运镜控制不激活。
- ❌ `strength_model > 1.2` → 画面不稳。
- ❌ 一镜堆多个运镜（`orbit + zoom + handheld` 同句）→ 与 04 册铁律冲突。
- ❌ 单靠 `pan left/right` 撑一镜 → 样本少、较弱，需叠加其他运镜词。
- ❌ 把参考片里的现代都市 / 健身服场景照搬进古装镜 → 只借运镜短语，主体场景换成自己的。

> 完整素材提示词库（55 条，覆盖上述 9 类运镜）随该 LoRA 发布页附带，可直接复制使用。
