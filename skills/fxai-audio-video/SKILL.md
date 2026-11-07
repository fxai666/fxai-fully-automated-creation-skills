---
name: fxai-audio-video
description: 音频驱动视频生成（公共件，MV 与数字人口播共用）。拿一段音频 + 参考图 + 提示词，按 MiniMax H3 三模式（single 单场景 / multi 多场景 / multiref 多参考）出视频。音频是唱歌还是说话与流程无关，只有提示词措辞不同（kind=sing 演唱 / kind=speak 口播）。当需要「用音频生成视频 / 音频驱动的视频 / 复用 MV 那套视频生成 / 逐段音频逐段出视频 / 多段视频拼接」时使用。依赖 fxai-workflow/comfy_client.py 与 fxai-workflow/mv-minimax-*.json。
agent_created: true
---

# fxai-audio-video · 音频驱动视频（公共件）

## 本技能文件清单（先读这里，再决定读哪几册）

| 文件 | 什么时候读 |
|------|-----------|
| `SKILL.md` | 本文件：两种调用/三种模式选择/提示词/音频分段精确公式/参数/等任务——本技能全部决策信息都在这里 |
| `audio_video.py` | 提交 ComfyUI 的实现；**不用通读**，按 SKILL.md 命令调用，只有改行为或排障才读对应函数 |

**确定本次要做什么之后，只读对应的那一两册，其余一律不读。**

| references/01-提示词构造.md | 写 multiref 六段式 / single·multi 三段式提示词 |
| references/02-分段与参数.md | 算分段数、备场景图、按段写提示词、查参数 |
| references/03-函数与依赖.md | 查某函数在哪个文件、本件依赖哪些工作流 |

🔴 **烧字幕出字前必读 `fxai-standard/references/06-字体与素材授权.md`**：字幕字体只从 06 白名单选（标题首选得意黑、正文/字幕首选 Noto Sans SC；微软雅黑随 Windows 预装可用），禁用商业字库。

给一段音频，按 MiniMax H3 出视频。**MV 和数字人口播走的是同一份实现**：
音频是唱歌还是说话，只影响提示词里「怎么描述人在干什么」（`kind`），其余
提交、尺寸、分段、下载、拼接全部共用。

## 两种调用

```python
import audio_video as av

# 单段音频 → 一条视频
dst, rel = av.gen_video("multiref", "口播.wav",        # 本地音频会自动上传
                        char_rel=["avatar/000.png"],
                        scene_ref=["dh_scene/000.png"],
                        kind="speak")                    # 口播措辞

# 多段音频 → 逐段各一条视频（口播逐段 / 一整曲 MV 都走这个）
rs = av.gen_videos(["01.wav", "02.wav", "03.wav"], "multiref",
                   name="口播", out_dir="<输出目录>", kind="speak")
av.concat_videos([r["video"] for r in rs], "<输出目录>/口播_完整.mp4", tone=True)
```

`tone=True` 会在拼之前做**影调/接缝对齐**（`fxai-common/video_tone.py`）：
分段生成必然段间跳色——模型没有跨段记忆，实测接缝处掉 1.6~2.0 灰阶，是正常帧间变化的近 3 倍，帧差指标看不出来但眼睛一眼就看见。
传 dict 可细调，如 `{"mode": "不启用", "max_offset": 20}`；不传（默认）保持老的直接拼接。

## 三种模式怎么选

| mode | 图怎么给 | 场景图铁律 | 尺寸 | 提示词格式 |
|---|---|---|---|---|
| `single` | 一张参考图（`fxai_imageinput`） | 含人物 | `fxai_width` = 最大边长，封顶 1300、只缩不放 | FL2VA 三段式（首尾帧） |
| `multi` | 一个图片目录（服务端按分段轮换） | 含人物 | 同上 | FL2VA 三段式（首尾帧），每段一行 |
| `multiref` | 人物图 + 场景图，**人物在前场景在后** | **空场景，人物由参考图注入** | 固定给 `fxai_width/height`：**竖屏 768×1280（默认，口播一般是竖版）/ 横屏 1280×768**（`multiref_landscape=True`；最大边 1280 < 1300，且均整除 32 过生成控制器对齐不变）；不走 single/multi 的「最大边长」逻辑。MV 侧固定横屏 | Ref2VA 六段式，每段一行 |

`single` / `multi` **画面偏静止**；要明显运镜与动作走 `multiref`（镜头语言写得进去）。
没给提示词时本模块会按对应格式自动生成一条，但仍建议显式写——提示词要契合这一段在讲什么
（见 `fxai-h3-video-prompt` 第 1.5 节：场景按内容定、道具明确写出）。

## 提示词

提示词构造函数与行数据格式见 references/01-提示词构造.md。

## 音频分段（精确公式，MV / 口播 / 短剧共用）

分段精确公式、音频入参与参数表见 references/02-分段与参数.md。

## 音频入参

## 参数

## 等任务

`submit_workflow` 内部等 `fxai:task_saved`（＝**合并/保存完毕**）才返回，返回即已过终态门。

| 阶段 | 规则 |
|---|---|
| 等待期·**正常** | ✅ 只查健康（服务器/进程还活着吗）<br>❌ 不做数据交互：不查 `/history`、不拉文件列表、不取结果、不下载、不反复 curl `/queue` |
| 等待期·**报错** | ✅ **这时才做数据交互**：取错误详情与日志，分析原因并解决。异常里带 `prompt_id`，用 `comfy_watch.py status <pid>` 查详情 |
| **终态门** | 日志出现 `✅ 合并成功，已保存：<url>` 之后，才做后续：取产物 → 烧字幕 → 归档 → 交付 |

- 要在对话里只在完成时播报一句 → `$WATCH next <pid>`（只出终态，不刷过程）。
- 要全程看进度 → 双击 `fxai-comfyui-watcher/progress.html`（直连 WS，绕过对话，不耗 token）。
- 用户问「到第几个分镜了 / 还要多久」→ `shot_query.py <pid>`，一条命令出已生成 N/M 镜、还剩几镜；
  判据（非 `merged/` 的分镜文件数、共用目录按任务窗口过滤）见 fxai-standard 01 册。

## 文件

模块函数清单与外部依赖见 references/03-函数与依赖.md。

**确定本次要做什么之后，只读对应的那一两册，其余一律不读。**
