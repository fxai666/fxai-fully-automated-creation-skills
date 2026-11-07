---
name: fxai-yue2
description: 用 YuE2 生成歌曲的中文技能。当用户想「写歌 / 生成音乐 / 做一首中文歌 / 用风格+歌词产出歌曲 / 改歌词改风格 / 翻唱改编」时使用。覆盖风格(Style)撰写、歌词(Lyrics)分区、规划模式(Full/Melody/Off 即 ABC 规划)、最大时长(Max Duration)设置，以及全中文的提示词规范与歌曲描述规范。；不做：只生歌不出片，成片另有主线。
---

# YuE2 音乐生成（中文版）

## 本技能文件清单（先读这里，再决定读哪几册）

| 文件 | 什么时候读 |
|------|-----------|
| `SKILL.md` | 本文件：何时使用/流程/Style/Lyrics/ABC规划/Max Duration/请求结构/执行方式/常见故障 |
| `gen_song.py` | 歌曲生成实现；按 SKILL.md 调，改行为才读 |
| `references/01-执行与参数.md` | 查怎么跑 gen_song.py、参数默认值、占位符与节点号 |
| `references/02-取产物与环境.md` | 产物取不回来、远端环境、报错排查 |

把一段音乐需求（写歌 / 改歌词 / 改风格 / 翻唱改编 / 风格加歌词产音乐）变成一首可听、可复现的歌曲。本技能所有说明、用法与产出描述**一律使用中文**。

## 🚨 开工前置（先读，再动手）

🔴 **开工第一步：读 `fxai-workflow/SKILL.md` 的「🚨 ComfyUI 服务端模式铁律」**——铁律在被依赖件的 SKILL.md 里，不在 `comfy_client.py`（只取函数必漏）。
核心：ComfyUI 由「凤希本地AI控制台」／启动器启停，本技能只按地址发 HTTP；本机 8188 探不通 → 走云或提示人启动；资源有无只认服务端接口。

## 使用流程（How to Use）

1. 在「**Style（风格）**」里写下对音乐的描述。
2. 在「**Lyrics（歌词）**」里用分段标签写歌词，如 `[Verse]`、`[Chorus]`。
3. 选择规划「**Mode（模式）**」。
4. 设置「**Max Duration（最大时长）**」。
5. 跑 `gen_song.py` 生成歌曲（见下文「执行方式」）。

## 撰写 Style（风格）

描述主要音乐特征，例如：

- **流派（Genre）**：流行、爵士、摇滚、电子、民谣
- **人声（Vocals）**：温暖女声、有表现力的男声、轻柔人声
- **语言（Language）**：中文、英文、日文等
- **速度（Tempo）**：90 BPM、慢、轻快
- **乐器（Instruments）**：钢琴、电吉他、贝斯、鼓、合成器

中文写法：
> 中文，温暖女声，当代流行，96 BPM，钢琴、圆润电贝斯、克制鼓点，咬字清晰

音乐性的指令全部放**Style** 里；歌词只放可唱的词。

## 撰写 Lyrics（歌词）

用分段标签描述歌曲结构：

```text
[Verse]
窗外的晨光洒落
城市在脚下慢慢苏醒

[Chorus]
跟我一起奔向阳光里
把影子远远抛在身后
```

可用段落标签：

- `[Verse]` 主歌
- `[Chorus]` 副歌
- `[Bridge]` 桥段
- `[Outro]` 尾奏

歌词里**只放要被唱出来的词**，音乐说明归 Style。

## ABC 规划（Mode）

- **Full（完整）**：先生成一份包含「旋律 + 和弦」的符号化计划，再生成歌曲。通用文本生音乐推荐用这个。对应 YuE2 的 `cot="full"`。
- **Melody（旋律）**：只生成「旋律」计划，让伴奏更自由地生成。对应 `cot="melody"`。
- **Off（关闭）**：不做符号化计划，直接由 Style + Lyrics 生成。对应 `cot="off"`。

Off 时歌曲由风格与歌词直接生成，没有可编辑的符号计划。

## Max Duration（最大时长）

设置最大时长（秒）；按音乐结构与歌词，歌曲可能提前结束。

## 提示词指南（Prompt Guide）

为了更好的效果，清楚描述音乐意图，并把 Style 与 Lyrics 分开：

- **Style** = 语言 + 流派 + 人声特征 + 速度 + 乐器 + 音乐特征
- **Lyrics** = 用分段标签，只放要唱出来的词

## 生成后的中文描述规范

生成结果（无论音频、计划还是比较）一律用**中文**描述，说明：

- 这是什么风格、什么人声、什么速度、用了哪些乐器
- 歌曲结构（主歌 / 副歌 / 桥段 / 尾奏）
- 歌词大意
- 时长与实际结束点、是否截断

描述写法：
> 这是一首 96 BPM 的当代流行中文歌，温暖女声，钢琴铺底、电贝斯圆润、鼓点克制。结构为主歌—副歌—桥段—尾奏，歌词讲述清晨启程、奔向阳光的故事，时长约 3 分 12 秒，未截断。

## 请求结构（对接生成器时用）

当实际 YuE2 运行时可用，把上面四项组装成请求：

```json
{
 "style": "中文，温暖女声，当代流行，96 BPM，钢琴、圆润电贝斯、克制鼓点，咬字清晰",
 "lyrics": "[Verse]\n窗外的晨光洒落\n城市在脚下慢慢苏醒\n\n[Chorus]\n跟我一起奔向阳光里\n把影子远远抛在身后",
 "cot": "full",
 "max_duration": 210
}
```

- `cot` 取值：`full` / `melody` / `off`，对应上文三种模式。
- `max_duration` 为最大时长（秒）。

## 执行方式（gen_song.py）

执行方式、参数表、工作流节点硬约束见 references/01-执行与参数.md。

### 工作流侧的硬约束（改代码前先看）

### 取产物：一律走接口下载（默认且是唯一通用路径）

### 前置条件

#### 指向仙宫云（远端 ComfyUI）

### 常见故障

取产物路径、前置条件、仙宫云环境与常见故障处置见 references/02-取产物与环境.md。

## 参考资料

- YuE2 生成指南：https://github.com/multimodal-art-projection/YuE/blob/main/docs/generation.md
- YuE2 音乐技能（官方英文原版）：https://github.com/multimodal-art-projection/YuE/blob/main/skills/yue2-music/SKILL.md

**确定本次要做什么之后，只读对应的那一两册，其余一律不读。**


## 项目清单（页面按它显示进度）

产物目录建好即建档、每完成一段更新；结构与全部子命令见 fxai-standard `references/01-产出铁律.md`。

```bash
P=~/.workbuddy/skills/fxai-standard/scripts/fxai_project.py
python "$P" init  "<项目>" --title <名称> --category music --skill fxai-yue2
python "$P" stage "<项目>" --script 2 --image 1 --audio 0 --video 0
```
