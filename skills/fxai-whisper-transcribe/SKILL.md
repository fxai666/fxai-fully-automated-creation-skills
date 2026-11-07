---
name: fxai-whisper-transcribe
description: 本地离线语音转文本（STT）。把录音/视频音轨转成中文文案。当用户说“音频转文字/语音识别/转录/把录音整理成稿/whisper/STT/视频提取文案”时使用；也适用于把录屏、会议录音、口播视频变成可编辑文本，再交给写作或公众号发布流程。；不做：不配音、不剪辑，只产出文本。
---

# 本地音频转文本技能 (fxai-whisper-transcribe)

## 本技能文件清单（先读这里，再决定读哪几册）

| 文件 | 什么时候读 |
|------|-----------|
| `SKILL.md` | 本文件：服务地址 / 两种用法 / 行为约定 |
| `whisper_client.py` | 主入口，全程走 HTTP 接口；改行为才读 |
| `transcribe.py` | 本地离线 CLI（不常驻），**仅排障 / 无服务时手动用**；技能链路一律不调 |

**确定本次要做什么之后，只读对应的那一两册，其余一律不读。**

## 服务与地址
- whisper 是常驻 HTTP 服务，端口 **8185**；懒加载，首次请求才加载模型，识别结束后空闲 `whisper.idle_timeout` 秒自动卸载释放显存。
- 地址读全局配置 `~/.workbuddy/fxai_global.json` 的 `whisper` 段：
  `local_url` / `cloud_url` / `prefer_local` / `default_language` / `default_beam` / `idle_timeout` / `timeout`。
- 🔴 **本机 API 优先**（与 ComfyUI / TTS 同一套）：函数参数 `url` > 本机 `local_url`（探得通）> 线上 `cloud_url` > 本机地址兜底。
  本机 8185 的 HTTP 服务只要在线就走它，探不通才走线上；`force_cloud=True`（CLI `--cloud`）可强制走线上。
- 🔴 **全程 HTTP 接口，不起本地进程**：链路里任何一步都不 subprocess 调 whisper。本地 CLI `transcribe.py` **仅供排障**，技能链路一律走 `whisper_client`。
- 服务探不通就报错提示人启动；本机服务由人启停，技能只发 HTTP，不代管开关机。

## 并发与排队（2026-10-31 实测口径）

**whisper 是三个 AI 服务里唯一能靠一份权重做真并发的**：ctranslate2 支持
`num_workers`，多个 worker 共享同一份权重（只多工作空间），不像 CosyVoice 要复制整份模型。

| 实测（large-v3 float16 / 5060Ti 16G / 45.8s 音频） | 单发 | 双发 | 三发 | 四发 |
|---|---|---|---|---|
| 墙钟 / 单发 | 14.5s（1.00） | 16.8s（**1.15**） | 19.2s（**1.34**） | 22.1s（**1.55**） |

- 档位 `--workers N` 或环境变量 `FXAI_WH_WORKERS`；不给就**按显存自动**（`(可用-3.1-1.5)÷1`，上限 4）。
  `/health` 的 `workers` 是能同时转写的人数，`free` 是现在还能接几个。
- 🔴 **`num_workers=1` 时多线程调 transcribe 是完全串行的**：实测双发墙钟 = 单发 ×2.00，
  且各自耗时都被拖长（21.7s / 29.0s）。想并发就必须让服务端按 worker 数放开，
  光靠 HTTP 每请求一线程没用。
- 显存：nw=4 增量约 3.9~4.0G（权重只算一份），空闲仍按 `idle_timeout` 整体卸载，
  卸载后重载自动恢复同样的 worker 数（加载仅 1.8s）。

**异步任务（长音频必走）**：`POST /transcribe/async` → 202 `{task_id, ahead, workers}`；
`GET /task/<id>` 查状态；`GET /task/<id>/result` 取结果（没做完回 **409**）。
客户端 `transcribe()` 会自动优先走异步，老服务端没有该端点时退回同步。
- 为什么要它：同步转写要等整段做完才回包，几分钟内一个字节都不发，nginx 60s /
  Cloudflare 100s 会当成死连接掐掉（用户看到 504，其实任务还在跑）。异步模式下每次
  请求都是几十毫秒，排多久都不会断线。
- `ahead` 是**扣掉并行窗口后的等待轮数**（4 个 worker 空着时提交 3 个，三个人都是 0）。

### 批量转写（2026-10-31 加，纯文本默认走它）

faster-whisper 自带 `BatchedInferencePipeline`：先用 VAD 把长音频切成小段，
再把**多个段一起**送进同一次 forward。这跟千问 TTS 的「攒批」是同一个思路——
单条解码时 GPU 大量空转，合批才填满。

| 实测（79s 真实中文 / 394 字原文 / 5080 级卡） | 单条 | 3 人并发 |
|---|---|---|
| 普通 `model.transcribe`（nw=4） | 21.9s | 29.7s |
| **批量 `pipe.transcribe(bs=8)`** | **9.1~10.1s** | **14.7~16.0s** |

质量：汉字数完全一致（351 / 351），CER 相对原文 1.1% → 2.3%，差异全是同音字
（返工→反攻、段→顿）；批量与普通之间只差一处「验一个稳→咽一个吻」。**不是串段。**

- 开关：`FXAI_WH_BATCH`（默认 **8**，0=关闭）。`/health` 的 `batch` 报当前档位。
- 🔴 **批量默认 `without_timestamps=True`，会连标点一起丢掉**（输出变成
  「各位朋友大家好欢迎来到…」），字幕没法断句。服务端已显式传 `False`，
  代价 0.9s（9.3→10.2s）。
- 只在**纯文本转写**时启用。以下三种一律走原路径，因为依赖分段粒度：
  `word_ts`（词级时间戳）、`clip_seg`（分片校验）、`vad=True`（显式要 VAD 分段）。
- 多个线程同时调同一个批量器是安全的（3 人并发全部 ok、字数一致），不用加锁。
- 卸载模型时必须同时丢掉批量器（`_pipe`），否则它绑着旧模型的引用，显存释放不掉。


## 两种用法

```python
# 方式 A：对话内 import（全程接口）
import sys
sys.path.insert(0, r"<技能目录>/fxai-whisper-transcribe")
from whisper_client import transcribe
text = transcribe(r"<音频文件>")                    # 纯文本
segs = transcribe(r"<音频文件>", timestamps=True)   # 带时间轴
```

```bash
# 方式 B：CLI（视频先抽音轨）
PY="<软件数据目录>/conda/env/whisper/python.exe"
# 抽音轨：ffmpeg -i in.mp4 -ar 16000 -ac 1 -c:a pcm_s16le out.wav
$PY "<技能目录>/fxai-whisper-transcribe/whisper_client.py" out.wav --lang zh --out transcript.md
# 带时间轴：加 --ts ；启用静音过滤：加 --vad ；查服务状态：--status
```

## 行为约定
- 全程走接口：本机与线上同一套代码，差别只在地址。
- 语言缺省读 `whisper.default_language`（zh）；`--lang auto` 让模型自判。
- 返回：纯文本模式给 `str`；`timestamps=True` 给 `list[{start,end,text}]`。
- 长音频（>10 分钟）GPU 数十秒、CPU 数分钟；给足超时（`whisper.timeout`，默认 600s）。
- 服务状态看 `GET /health`：`state`（idle / loading / transcribing / unloading）、`busy`（是否正在识别）、`model_loaded`（模型是否在显存）。

## 字幕 / 对齐类技能的统一调法

下游技能（`fxai-lyric-subtitle`、`fxai-mv-pipeline`、`fxai-slide-video`、`fxai-short-drama`）**一律走 `whisper_client`，不起本地进程**：

```python
from whisper_client import write_transcript, transcribe_passes
write_transcript(audio, "ts.txt")                                   # 每段一行 `[start->end] 文本`
write_transcript(audio, "words.txt", words=True, condition=False)   # 每词一行（词级）
write_transcript(audio, "seg.txt", bounds_only=True)                # 每段一行，无文本
transcribe_passes(audio, [{"clip_seg": 9, "word_ts": True, "condition": False}, ...])
```

- 输出格式与本地 CLI 完全一致（`[start->end] 文本`），下游 `parse_transcript` / `parse_words` 不用改。
- 多版交叉（9/11/13s 硬切 + beam/句级差异版）用 `transcribe_passes`：**音频只上传一次**，服务端连跑多版；老服务端不支持时自动退化为逐版请求。
- `condition=False` 用于歌曲（减少幻觉复读）；`clip_seg` 硬切时服务端自动关 VAD。

## 本地离线 CLI（不常驻）
`transcribe.py` 直接吃音频文件、进程内跑 faster-whisper，**不依赖服务**；仅排障或服务起不来时手动用，下游技能不调它：

- 环境：`<软件数据目录>/conda/env/whisper/python.exe`（独立 conda env，faster-whisper 已装，GPU 走 ctranslate2，不依赖 torch）。
- 模型：whisper 程序目录 `<whisper_dir>/model`（全局配置 `whisper_dir`；用哪个模型由程序自己定，当前 large-v3 满血版）；未配置时回退 `HF_HOME` 或 `<软件数据目录>` 下的 `models--Systran--faster-whisper-large-v3` 快照，强制离线读取。
- 用法：`$PY transcribe.py out.wav --lang zh --words --vad --out transcript.txt`。
- 字幕 / 歌词类技能不调它，一律走上面的 `write_transcript` / `transcribe_passes`。

## 与其他技能配合
- 生图：`fxai-comfyui-image-gen`；公众号发布：`fxai-weixin-gzh-publisher`。
- 典型链路：录屏→ffmpeg 抽音轨→本技能转稿→据此写 `article_*.html`→生图→发布。
