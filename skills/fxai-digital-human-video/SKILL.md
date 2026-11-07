---
name: fxai-digital-human-video
description: 数字人口播视频生成（口播音频 → 数字人出镜讲话的视频）。当用户要求「口播视频 / 数字人视频 / 数字人出镜 / 单场景·多场景·多段口播」时使用。音频来自 fxai-voiceover 或用户提供；单音频出一条，多音频逐段各一条再拼接。生成复用 fxai-audio-video（与 MV 同一套 H3 实现）。不做：MV（→fxai-mv-pipeline）、纯配音（→fxai-voiceover）、短剧（→fxai-short-drama）。
agent_created: true
---

# fxai-digital-human-video · 数字人口播视频

## 文件清单

| 文件 | 什么时候读 |
|---|---|
| `01-参数与命令.md` | 参数、跑法、返修判据 |
| `02-人物动作与场景图.md` | 动作判据、场景图、四视图 |
| `03-音频分段与提示词.md` | 段数、多音频、提示词 |
| `04-产出与等待.md` | 产物落点、交付定义、等任务 |
| `05-封面.md` 🔴 | 出封面前 |
| `06-模板库.md` 🔴 | 做口播前 |
| `07-字幕.md` | 烧字幕 |
| `08-镜头语言.md` | 景别 / 机位 / 运镜 / B-roll |
| `09-分镜设计与验收.md` 🔴 | 出分镜前（规范 + 验收 + 返修闭环） |
| `templates/T1、T2/` | 按所选模板做一套 |

🔴 **只读对应的那一册，其余一律不读**（整管线任务读齐 `01~07` + 模板册）。
写稿见 `fxai-voiceover-script` 03/04/05。
字体只用 `fxai-standard` 06 册白名单（得意黑 / Noto Sans SC）。

## 执行标准

`dh_pipeline.py` 产出 `成片_{W}x{H}.mp4` 为中间成片；交付物是 `成片_最终_{W}x{H}.mp4`。

| 步 | 做什么 / 怎么跑 |
|---|---|
| **S0** | 选模板 + 模型设计分镜：06+09 册 → `storyboard.json`，过 `preflight.py` |
| **S1** | 场景图（空实景，按段语义焦点）：`dh_video --scene-only`，图对了再渲染 |
| **S2** | 标题卡 / 信息贴片（可选）→ `image/cards.json` / `infocards.json` |
| **S3** | 渲染 + 烧字幕：`dh_pipeline.py`（**必须带 `--storyboard`**） |
| **S4** | 叠卡 + 封面 + 拼接：`scripts/finalize.py --project <项目>` |
| **S5** | 交付自检（含分镜门禁）：`scripts/check_delivery.py`，不过即走 R |
| **R** | 🔴 返修环（**回环，不是最后一步**）：`scripts/refix.py --shots 3,7,9` |

可选：S3.5 插 B-roll 用 `insert_broll.py`；S4.5 后期运镜用 `camera_move.py`。
S4 默认全开，`--no-cover` / `--no-titles` / `--no-infocards` 单独关；两层 `t` 按口播起点算，
封面最后拼，整体后移封面帧数。**封面段多长不在本技能规定**，规则见
`fxai-poster/references/05-封面模板库.md`「封面段时长」。画面上屏后按 `09` 册过三道门，跑 `qc.py post`。

## 🔴 R 返修环

**触发**：`preflight.py` 有未过项、`check_delivery.py` 非零退出、抽帧看到问题、用户指出秒数
（`rework.shot_no_of(秒)` 给镜号）。命中任一条就进 R，先交付即违规。

```bash
python scripts/refix.py --project <项目> --shots 3,7,9   # 多镜串行；单镜用 --shot 7
```

固定顺序：逐镜体检（硬伤即拦，`--allow-bad` 才强渲）→ 串行计划 → 一镜渲完才交下一镜 →
核对已合并 → 抽 4 帧 → 打印下一步，全部返回 0 才退出 0。

串行是硬要求：每渲完一段服务端都会拿其它段缓存拼回完整成片，并行互相覆盖；只有最后一段
渲完那次是终版，全部渲完才烧字幕 / 做封面 / 交付（09 册末章）。

## 跑法

跑法与参数见 `01-参数与命令.md`；`--script` 与 `--audio/--audio-dir` 二选一，默认 `multiref`。

## 铁律

1. 🔴 `multiref` 场景 ≥2 且 ≤段数（`scene_count_for()`），段数用 `av.h3_segment_count()`。
2. 场景图画空实景：只留环境与背景人物，文字与主角不进画面（人物由 `char_rel` 注入）。
3. 🔴 动作换类型不只换幅度（02 册），位移慢、短、有终点。
4. 人物图 = 四视图，参考 `avatar/000.png`(女) / `001.png`(男)，提示词只写穿搭。
5. 场景图按段语义焦点配景，相邻段换景；道具写出来，无依据不加。
6. 等待期只查健康；问第几镜用 `shot_query.py <pid>`；见 `✅ 合并成功，已保存：` 再烧字幕。
7. 产物落 `<工作区>/<项目名>/`，目录扁平；已有段视频与场景图默认复用，`--force` 重烧。
8. 🔴 字幕调 `fxai-lyric-subtitle`，只喂时间轴（07 册）。
9. 🔴 封面按 05 册三条验收；做口播先读 06 册选模板。
10. 🔴 写稿经历段用「我」；固定程序放 `scripts/`，项目只放数据与产物。
11. 程序自动、人只读结果：场景图取回、时间码、叠卡、封面、字幕校验。
12. 🔴 **分镜由模型设计**（09 册）：景别 / 运镜 / 动作 / B-roll 全在 `storyboard.json`。
13. 🔴 **`--storyboard` 必须进提示词**｜判据：日志 `已按分镜/文案生成 N 条逐段提示词`，
    且 `口播提交提示词.json` 能搜到该镜 `size` 与 `motion` 原文。
14. 🔴 **场景图取自缓存**：手工改图须写回 `~/.workbuddy/fxai_cache/<项目>_口播_sceneN.png`，
    再 `upload_image` 覆盖 `dh_scene/NNN.png`；判据日志 `[scene] 复用已有场景图`。
15. 🔴 **出片后只返修那几镜**：整片 88 s 要 70 min，单镜 9.4 s 只要 8~10 min，`(0,-1)`
    只留给分镜整体重来；判据 `[dh] 返修模式` + `[dh] 角色四视图复用缓存`。
16. 🔴 **人物怎么演不写进代码**：站位 / 动作由模型定，具体写在分镜 `motion` / `action`。
17. 🔴 **朝向锁死**：只允许走近 / 走远 / 左右横移，转向词全禁（02 册三重拦截）。
18. 🔴 **说话强度与头部动作同一道门禁**：讲话三项要素缺一即拦（02 册第 4 条）；
    `motion` 只准写脚 / 腿 / 重心 / 手 / 臂 / 掌 / 指；冻结词禁写。

## 项目清单（页面按它显示进度）

产物目录建好即建档，每完成一段更新（结构见 fxai-standard 01 册）。

```bash
P=~/.workbuddy/skills/fxai-standard/scripts/fxai_project.py
python "$P" init "<项目>" --title <名称> --category voiceover --skill fxai-digital-human-video
python "$P" stage "<项目>" --script 2 --image 1 --audio 0 --video 0
```
