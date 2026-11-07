---
name: fxai-video-editing
description: (视频剪辑) 把长视频自动剪成精华合集并烧录字幕。当用户说"自动剪视频/剪精彩片段/视频加字幕/做精华合集/视频切片/挑重要片段/视频自动剪辑"时使用。覆盖：本地 whisper 转写语音或歌词 → 按内容重要性/音频能量/场景边界挑段 → 切段烧录 ASS 字幕（章节标题+歌词）→ 交叉溶解转场拼接成带字幕的精简版 mp4。全程本地处理，不上传用户的视频。
agent_created: true
---

# 视频剪辑技能（fxai-video-editing）

## 本技能文件清单（先读这里，再决定读哪几册）

| 文件 | 什么时候读 |
|------|-----------|
| `SKILL.md` | 本文件：开工前读经验库/何时使用/前置关键坑/5步流水线/交付 |
| `scripts/build_highlight.py` | 自动剪+烧字幕实现；按命令调，改行为才读 |
| `references/ffmpeg_notes.md` | ffmpeg 参数/滤镜笔记——**调参或排障时查** |

**确定本次要做什么之后，只读对应的那一两册，其余一律不读。**


把一段长视频自动裁剪、加字幕、加转场，输出一个带字幕的精华合集 mp4。
工具链（全部本机处理）：**ffmpeg（gyan 全量版，带 libass）+ 本地 faster-whisper 转写**。

## 开工前：先读「视频」领域的经验库

开剪之前先读一遍 `{data_dir}/经验库_视频.md`（由 `fxai-content-review` 导出）；文件不存在说明还没复过盘，直接开工。

- 每条都带「第几次出现」和「下次该怎么改」，把「下次」带进本轮的字幕、镜头与时长取舍。
- 标「顽疾」「已确认」的优先遵守；标「待观察」的按判断取舍。
- 目录解析：环境变量 `FXAI_DATA_DIR` > `fxai_global.json.data_dir` > `工作目录/_fxai_data`。

## 何时使用
- “帮我自动剪一下这个视频，挑精彩片段”
- “给视频加字幕/字幕烧录”
- “做个精华合集/视频切片/短视频切片”
- 用户丢来一个长视频（讲话、MV、录屏、课程）想要精简版

🔴 **出字前必读 `fxai-standard/references/06-字体与素材授权.md`**：烧录字幕字体只从 06 白名单选（标题首选得意黑、正文/字幕首选 Noto Sans SC；微软雅黑随 Windows 预装可用），禁用商业字库。本技能默认 `Microsoft YaHei` 已属白名单 B。

## 前置与关键坑（必读）
1. **ffmpeg 必须带 `subtitles` 滤镜（libass）**。
   - ✅ 用 `<软件安装目录>/ffmpeg/ffmpeg.exe`（gyan full build，带 libass/subtitles/drawtext；随凤希AI伴侣安装自带，由全局配置 `software_install_dir` 自动定位，无需写死路径）。
   - ❌ 不用 conda 默认安装目录的 ffmpeg（多数未编 libass，烧字幕会报 "subtitles filter not found"）。
   - 其它候选 ffmpeg 用 `ffmpeg -hide_banner -filters 2>/dev/null | grep subtitles` 自查。
2. **subtitles 滤镜要写显式 `filename=`**，且用相对路径：把 `.ass` 与输出放同一工作目录，再 `cwd` 切到该目录执行，避开 Windows 盘符 `C\:/` 转义报错。
3. 中文渲染用 `Microsoft YaHei`（本机 Windows 自带）。ASS 文件务必 **UTF-8 无 BOM**。
4. 转写走 `fxai-whisper-transcribe` 技能：`<软件数据目录>/conda/env/whisper/python.exe`（独立 conda env）+ large-v3-turbo，离线；字幕识别前先使用该环境（等价于 conda activate），不依赖本机 Python 版本。

## 流水线（5 步）
1. **探查素材**：`ffprobe` 拿时长/分辨率/音轨；`ffmpeg -vf fps=1/N` 抽帧看场景结构；`volumedetect` 看音频能量。
   - ⚠️ 用户给的文件可能很短（<30s 不够剪），先扫同目录时长确认是否给错文件。
2. **语音转写（找“重要”）**：抽音轨 `ffmpeg -i in.mp4 -ar 16000 -ac 1 -c:a pcm_s16le out.wav`，再 `--lang zh --ts` 转写，产出文字稿（兼作字幕源）。
3. **挑片段打分**：按内容密度（观点/数字/问答/转折）、音频能量（副歌/掌声/笑点）、场景边界综合，挑出高分段；每段对齐句子边界（切点落在句边界上）；排除水印/广告/片尾音轨。
4. **生成片段配置**：把每段写成 `segments.json`（见下），歌词字幕按转写修正明显识别错误。
5. **剪辑合成**：调用脚本 `scripts/build_highlight.py` 切段+烧字幕+转场拼接，输出成品 mp4；抽帧验证字幕后交付，附片段清单。

## 复用脚本
`scripts/build_highlight.py` —— 给定视频 + segments.json，产出带字幕转场的精华合集。

```bash
PY="<软件数据目录>/conda/env/whisper/python.exe"
$PY "<技能目录>/fxai-video-editing/scripts/build_highlight.py" \
    --video "<ComfyUI 视频根>/merged/fxai_xxx.mp4" \
    --config segments.json \
    --out highlight.mp4
# 可选：--ffmpeg "<软件安装目录>/ffmpeg/ffmpeg.exe"  --ffprobe "<软件安装目录>/ffmpeg/ffprobe.exe"（缺省自动由 software_install_dir 推导）
```

`segments.json` 结构：
```json
{
  "resolution": [960, 1280],   // 可选，缺省自动 ffprobe 探测
  "xfade": 0.6,                // 转场时长（秒）
  "afade": 0.5,                // 首段淡入/末段淡出时长
  "segments": [
    {
      "start": 96.1, "end": 129.1,
      "title": "中秋不中秋",
      "lines": [[96.3, 101.3, "中秋不中秋，与我也不过是"], [101.3, 106.6, "有一页月光替我守着床"]]
    }
  ]
}
```
- `lines` 时间戳是**原片绝对时间**，脚本内部会减去 `start` 转为段内时间。
- 多段自动 `xfade` 交叉溶解拼接（末段用 `fadeblack` 黑场），音频 `acrossfade`。
- 单段则直接复制（纯加字幕场景也好用）。

## 交付
成品 mp4 + 一份片段清单（时间戳 + 章节标题 + 入选理由）。先跑 1–2 分钟样本确认节奏和字幕，再跑全片。

## 与其它技能配合
- 转写：`fxai-whisper-transcribe`；数字人/生视频：`fxai-comfyui-image-gen`、`fxai-mv-pipeline`。
- 详见 `references/ffmpeg_notes.md`（subtitles 写法、ASS 字体与路径坑）。
