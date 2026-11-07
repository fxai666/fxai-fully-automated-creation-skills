# ffmpeg 选型与字幕烧录要点（fxai-video-editing 技能参考）

## 1. ffmpeg 必须带 libass（subtitles 滤镜）
本机多份 ffmpeg，是否带 `subtitles` 滤镜直接影响能否烧录字幕：

| 路径 | 版本 | subtitles 滤镜 | 用途 |
|------|------|----------------|------|
| `<软件安装目录>/ffmpeg/ffmpeg.exe` | gyan full 2025-08-14 | ✅ 有 | **首选，烧字幕用这个** |
| `<某ffmpeg路径>/ffmpeg.exe` | 较旧 gyan | ✅ 有 | 备选 |
| `conda 自带的 ffmpeg（默认安装位置，无 libass）` | 7.0.1 conda | ❌ 无 | 仅能切分/抽帧/拼接，烧字幕会报 "subtitles filter not found" |
| `<你的 ffmpeg 路径>/imageio_ffmpeg/ffmpeg-win-x86_64-v7.1.exe` | 7.1 | ✅ 有 | 备选 |

自查命令：`ffmpeg -hide_banner -filters 2>/dev/null | grep -E "subtitles|ass "`

## 2. subtitles 滤镜写法（Windows 坑）
- 必须写显式 `filename=`：`subtitles=filename=xxx.ass`；只写 `subtitles=xxx.ass` 在 ffmpeg 7 会报 "subtitles filter not found"。
- Windows 盘符路径在滤镜字符串里转义极易出错（如 `C\:/...`）。规避办法：把 `.ass` 与输出放同一目录，执行时 `cwd` 切到该目录，用相对文件名 `subtitles=filename=seg0.ass`。
- ASS 文件必须 **UTF-8 无 BOM**，否则中文乱码。

## 3. ASS 字幕基础
- 字体：Windows 用 `Microsoft YaHei`，字号 Lyric 54 / Title 72。
- 段落时间用 `H:MM:SS.cc`（如 `0:00:03.20`）。
- 文本转义：`{` `}` 前加反斜杠；换行用 `\N`。
- 淡入淡出：`{\fad(150,150)}` 表示入 150ms 出 150ms。

## 4. 切分 / 拼接命令
- 精确切段（保持关键帧）：`ffmpeg -ss START -to END -i in.mp4 -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p -c:a aac out.mp4`
  - `-ss`/`-to` 放 `-i` 前是输入seek，更快；放 `-i` 后是输出seek，更精确但慢。
- 视频转场：`[0:v][1:v]xfade=transition=fade:duration=0.6:offset=OFFSET[v01]`；末段用 `fadeblack`。
- 音频转场：`[0:a][1:a]acrossfade=d=0.6[a01]`。
- 片段间 offset = 前 N 段累计时长 − 转场时长。
- 首尾淡入淡出：`fade=t=in:st=0:d=0.5` / `fade=t=out:st=(D-0.5):d=0.5`，音频 `afade` 同理。

## 5. 抽帧 / 探测
- 抽帧看结构：`ffmpeg -i in.mp4 -vf fps=1/8 out_%02d.png`（每 8 秒一帧）。
- 网格图：`ffmpeg -i in.mp4 -vf fps=1/8,scale=200:-1,tile=4x7 -frames:v 1 sheet.png`。
- 音频能量：`ffmpeg -i in.mp4 -af volumedetect -f null - 2>&1 | grep -E "mean_volume|max_volume"`。
- 时长：`ffprobe -v error -show_entries format=duration -of csv=p=0 in.mp4`。
