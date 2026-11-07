---
name: fxai-lyric-subtitle
description: (视频字幕) 给 MV / 视频加标准歌词字幕。当用户说"给视频加字幕 / 歌词字幕 / 字幕烧录 / 卡拉OK 字幕 / 给 MV 加歌词"时使用。覆盖：whisper 提取时间戳 → 歌词文字校正 → 生成 SRT/ASS → 用带 libass 的 ffmpeg 烧录。全程本地处理，不上传视频。
agent_created: true
---

# 视频歌词字幕技能（fxai-lyric-subtitle）

## 本技能文件清单（先读这里，再决定读哪几册）

| 文件 | 什么时候读 |
|------|-----------|
| `SKILL.md` | 本文件：约束/流水线/三道归一/自检 |
| `scripts/gen_lyric_subtitle.py` | 提取+对齐+烧录实现（31.9KB）；按命令调，改行为才读 |
| `references/01-对齐与自检.md` | 字幕错位、对齐失败、时间轴自检报错，或要查归一规则时 |

**确定要做什么后，只读对应那几册，其余不读。**

🔴 **出字前必读 `fxai-standard/references/06-字体与素材授权.md`**：ASS 字幕字体只从 06 白名单选（标题首选得意黑、正文/字幕首选 Noto Sans SC；微软雅黑随 Windows 预装可用），禁用商业字库。本技能默认 `Microsoft YaHei` 已属白名单 B。

## 前置与关键约束（必读）
1. **ffmpeg 带 libass（`subtitles` 滤镜）**：用 gyan full build（含 subtitles/drawtext），凤希AI伴侣自带即带；conda 自带 ffmpeg 未编 libass 时中文成**豆腐块（tofu）**。路径读 `~/.workbuddy/fxai_global.json` 的 `software_install_dir`（默认目录名 `<盘>:\fxai`），按 `<软件安装目录>/ffmpeg/ffmpeg.exe` 定位，缺则回退 PATH。自查：`ffmpeg -hide_banner -filters 2>/dev/null | grep -iE "subtitles|libass"`。
2. **`subtitles` 滤镜写显式 `filename=` + 相对路径**：`.ass` 与输出同目录、`cwd` 切到该目录执行，避开 Windows 盘符 `C\:/` 转义。**ASS 用 UTF-8 无 BOM**（`open(path,"w",encoding="utf-8")`）。
3. **字幕样式 = MV 默认款（薄荷青），源 `fxai-mv-pipeline/lyrics_align.py` 的 `ass_header()`**，import 复用，MV 改配色字号自动同步。默认：`Microsoft YaHei` **加粗**、主字色 `&H00F5FBE8`、描边 `&H003A3F0E`、宽 2.5、投影 1.2、字距 0.8、下边距 ∝ 高度。`STYLE_PRESETS` 六款 A~F（白/奶油暖黄/薄荷青/香槟金/樱粉/冰蓝），`DEFAULT_STYLE="mint"`。**PlayResX/Y 必须等于视频真实分辨率**（ffprobe 自动读），字号 ∝ 短边（横 0.050、竖 0.042）即 38 / 32。事件行 Text 用纯文本（`\fad` 等内联标签会当字面烧）。
4. **逐字卡拉OK需字级对齐**：whisper 只给**句级**时间戳，逐字变色要 whisperx / whisper-timestamped。无字级对齐时二选一：平均 `\k` 卡拉OK 或降级**普通字幕**（整句显示不变色，默认选它）。`\k` 单位是**厘秒**（1/100s），每字 `per=round((end-start)*100/n)`。

## 流水线
1. **抽音轨**：`ffmpeg -i in.mp4 -ar 16000 -ac 1 -c:a pcm_s16le out.wav`
2. **whisper 提取时间戳**（`fxai-whisper-transcribe`）：走接口，`whisper_client.write_transcript(out.wav, "transcript.txt")`（词级加 `words=True`）。服务地址读全局配置 `whisper` 段（本机 8185 / 线上），**不起本地进程**。输出 `[start->end] 文本`，每行一段（前奏幻觉段丢）。
3. **校正歌词**：whisper 对**唱歌**识别常错，前奏/间奏无词段幻觉。丢无词段，错字改对歌词。
4. **生成字幕文件**：SRT（通用）+ ASS（歌词样式），见 `scripts/gen_lyric_subtitle.py`。
4.5 **一句字幕 = 一个完整窗口**：逐 phrase 显示字幕半句就没。`--lyrics` 对齐用 `align_lyrics_spans()`：每句与 phrase 字符级匹配，窗口 = 首个命中 phrase 的 start → 末个命中的 end；匹配不上的行按剩余时长均分兜底。口播只识别一次 + 句级合并。
5. **烧录**：`.ass` 与输出同目录、`cwd` 切到该目录执行 `ffmpeg -y -i in.mp4 -vf "subtitles=filename=xxx.ass" -c:a copy out_sub.mp4`

### 单条字幕的上限（按画面方向分档）

| 画面 | 字号 | 一行可容 | 上限 |
|---|---|---|---|
| 竖屏 768×1280 | 32 | 约 20 字 | **4.0 秒 / 16 字** |
| 横屏 1280×768 | 38 | 约 28 字 | **6.0 秒 / 24 字** |

按真实分辨率**自动选档**（`sub_limits()`），覆盖用 `--sub-max-sec` / `--sub-max-chars`。长句**优先按标点断**（。！？；，、：…—），其次按字数硬断；时间按字数比例分配。**画面上不显示标点**（标点只参与断句）。

🔴 **两档固定，不按比例、不随手调**：竖屏 32 / 横屏 38 就是终值，不因为「这次字看着小」改成 28 或 36。

🔴 **字号只走 ASS，不用 SRT + `force_style` 的 `FontSize=`**：SRT 没有 PlayRes，libass 按 288 基准
把字号放大 ×(视频高/288)——竖屏 1280 就是 ×4.44，`FontSize=28` 实际烧成 124px em（汉字 91px），
这就是「字幕突然巨大」的根因。ASS 的 `PlayResX/Y` = 视频真实分辨率，`Fontsize` 就是真实像素。

只有 SRT 没有 ASS 时用 `scripts/srt2ass.py` 一步转（自动按方向取 32 / 38，PlayRes 写真实分辨率）：

```bash
python "<技能>/scripts/srt2ass.py" in.srt out.ass --size 768x1280
# 输出：[srt2ass] ... PlayRes 768x1280 | 字号 32（竖屏档）
```

### ★ 文字来源：文案是唯一真相源，whisper 给时间

有文案时文字取自 `口播文案.md` / `--lyrics`（ASR 会错字），时间由 whisper 提供且只对位置，省掉另存识别稿；无文案时文字取自语音识别，另存 `语音识别稿.md` 供改字。

### ★ 口播质检：逐字对齐 + 落盘前自检，只识别一次

`--asr-mode char`（默认）用 whisper **逐字时间戳**（`--words`，中文 word 级≈字级），每字查自己时间，对多字/漏字免疫；`--asr-mode phrase` 退回句级。

### ★ 对齐与时间轴自检

对齐靠逐字符顺序匹配，一处对不上后面全句错位；三道归一规则与时间轴自检判据见 `references/01-对齐与自检.md`。

## 复用脚本
`lyrics.txt` 每行一句（`[verse]` 等标记被忽略），按时间顺序与 whisper 段对应替换。

```bash
$PY "<技能>/scripts/gen_lyric_subtitle.py" --video "<视频.mp4>" \
 --lyrics "<歌词.txt>" --out-dir "<输出目录>" --out-name song_multiref --no-burn
# --karaoke 做平均卡拉OK；去掉 --no-burn 烧录成 *_sub.mp4
```
