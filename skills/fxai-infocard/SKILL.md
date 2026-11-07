---
name: fxai-infocard
description: 给任意视频叠加「信息贴片」——半透明形状（卡片/气泡/胶囊/便签/绶带/尖标签/书签/纸签/下划线/括号）的辅助性补充内容层，按时间码出现在画面任意位置，带淡入/滑入/弹入 + 停留 + 淡出。当用户要求「加信息贴片 / 贴个说明 / 加补充提示 / 名词解释卡 / 加提示卡片 / 补充信息卡 / 加对话气泡 / 加关键词胶囊 / 把补充说明贴上去」时使用。与产线无关，任何视频都能叠。不负责字幕（fxai-lyric-subtitle）、不负责标题卡（fxai-title-card）、不负责封面。
agent_created: true
---

# fxai-infocard · 信息贴片

把一个视频 + 一份「什么时候贴什么补充内容」的清单，压成一条带信息贴片的视频。
只认两样输入：视频文件、时间码 + 内容。数字人口播 / MV / 短视频 / 标题卡产线都能直接调用。

## 本技能文件清单（先读这里，再决定读哪册）

| 文件 | 什么时候读 |
|---|---|
| `references/01-内容设计.md` | 写贴片文字、定内容类型、定密度与位置时机时 |
| `references/02-形状与配色.md` | 选形状、气泡尾巴、指示箭头、主题、字体、动效时 |
| `references/03-数据结构与叠加.md` | 写 json 全字段、跑两个脚本、定项目归属、排 ffmpeg 错时 |

**确定本次要做什么之后，只读对应的那一册，其余一律不读。**

## 三层叠加，别混

| 层 | 位置 | 跟什么走 | 谁做 |
|---|---|---|---|
| 字幕 | 画面底部 | 语音逐句 | `fxai-lyric-subtitle` |
| 标题卡 | 画面顶部 | 节拍 | `fxai-title-card` |
| 信息贴片 | 画面任意位置 | 讲解节奏 | **本技能** |
| 封面 | 片头静态帧 | 不跟时间 | `fxai-poster` |

贴片内容是配合口播另外写的辅助层——说明、名词解释、操作提示、注意事项、数据补充，
不从口播文案提炼。放什么、放几条见 01 册。

## 直接开跑

```bash
# ① 内容 → 紧致半透明 PNG + render.json
python -B scripts/make_infocards.py --infocards infocards.json --size 768x1280 --out _tmp/infocards

# ② 叠到视频上（保留原音轨，只重编码画面）
python -B scripts/overlay_infocards.py --video in.mp4 \
       --render _tmp/infocards/render.json --out out.mp4
```

`--size` 用目标视频尺寸；叠加阶段自动 ffprobe 真实尺寸。中间产物写 `_tmp/`。
第一步加 `--preview` 额外输出 `preview.png` 总览图，先看字号与配色再叠视频。

## 铁律速查

1. 🔴 内容是另外写的辅助补充，不从口播文案自动提炼。
2. 🔴 背景半透明，透出底层画面；`bg_alpha` 按主题默认值。
3. 🔴 视频帧从不逐帧编辑：只做一次 overlay 合成，原视频与原音轨原样保留。
4. 🔴 `fade` 排在 `scale` 之前，链序固定 `format=rgba → fade → scale → overlay`。
5. 🔴 `-loop 1` 输入配 `-shortest`，否则编码不结束。
6. 🔴 overlay 的 x/y 不写嵌套 `if()`；pop 缩放放 `scale`，overlay 用 `overlay_w` / `overlay_h` 居中。
7. 🔴 信息贴片是可选步骤：没有补充内容就不做。
8. 🔴 字体只走白名单：得意黑 → 微软雅黑 → Noto Sans SC → PIL 默认；交付清单记录字体名与协议。
9. 🔴 加工归属走项目清单：开工 `fxai_project.py context` 定位项目，产物写回项目目录，成片登记 `video` 段。

## 指针

- 通用规范（产物落点、目录整洁、字体授权、文档写作）见 `fxai-standard`。
- 公共层 `scripts/cardkit.py`：字体解析、画布缩放、ffmpeg 定位，与 `fxai-title-card` 共用。
