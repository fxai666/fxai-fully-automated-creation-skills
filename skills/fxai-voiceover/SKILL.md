---
name: fxai-voiceover
description: 数字人口播音频生成（文本转语音 TTS）。双引擎：CosyVoice3（8182）与千问 Qwen3-TTS（8086），改地址即切换。当用户要求「配音 / 口播稿配音 / 生成旁白 / 数字人配音 / 文字转语音 / TTS / 用参考音色生成口播 / 情绪方言语气朗读 / 多段脚本逐段配音 / 长文分句」时使用。本机 8182 优先、没起走线上；产物自动落项目目录并写交付清单。；不做：不写稿、不出画面，只产出音频。
agent_created: true
---

# fxai-voiceover · 数字人口播音频生成

> **链路定位**：口播链路第 2 段（文本转语音），人物出镜与幻灯片两种形态共用。第 1 段写稿见 `fxai-voiceover-script`（03/04 册），第 3 段展示由 `fxai-digital-human-video` / `fxai-slide-video` 负责。

传两样东西进接口，拿一条口播音频出来：

- **参考音色**（`ref_audio`）— 音色库里登记好的声音文件；统一走 **cross_lingual**（跨语种复刻）
- **情感 / 情绪 / 语气 / 方言全部写进 `text` 的语气标签**，和正文一起送

调用固定一种：`POST /synthesize`，音频由接口回 base64、本技能自己落盘。

## 本技能文件清单（先读这里，再决定读哪几册）

**不读完也能开工。** 确定这次要干什么，只读对应那册，其余一律不读。

| 文件 | 什么时候读 |
|------|-----------|
| `references/01-口播文案规范.md` | 写口播文案、贴语气标签、送参自检时 |
| `references/02-音色与服务.md` | 登记/换音色、改服务地址、换引擎（CosyVoice3 ⇄ 千问）、改全局配置时 |
| `references/03-参数与调用.md` | 程序化调用、多段顺序、接视频下游时 |
| `voice_gen.py` | 主入口（拼多段+合成）；按命令调，改行为才读 |
| `tts_client.py` | 服务接口封装（两个引擎通用）；改接口或排障才读 |
| `tests/qwen3tts_api_test.py` | 千问路由自测（7 项） |
| `tests/qwen3tts_len_test.py` | 千问文本长度压测（80/160/320 字） |
| `tests/client_compat_test.py` | 客户端等价自测（37 项）；自带 `--spawn` 起服务 |
| `speakers.example.json` | 音色库模板；照着建 speakers.json |

🔴 **服务端源码不在本技能目录**：千问与 CosyVoice3 的服务端各自独立部署，技能只按地址发 HTTP。
千问服务端源码在数据目录（全局配置 `software_data_dir`）下的 `aiproject/qwen3tts/`，要改服务端去那里改，改完部署到对应实例。

## 直接开跑

```bash
# 用全局登记好的女主000
python voice_gen.py --project <项目名> --text '大家好，今天这期讲三件事。' --speaker 女主000

# 多段脚本（空行分段）
python voice_gen.py --project <项目名> --script 分镜.txt --speaker 女主000 --name 镜头

# 长文分句 → 多段 → 自动拼成一条
python voice_gen.py --project <项目名> --text-file 口播稿.txt --speaker 女主000 --name 口播

# 换语气 / 换临时音色
python voice_gen.py --project <项目名> --speaker 女主000 --speed 1.1 \
       --text '用四川话自然口语的语气说<|endofprompt|>。各位老铁'
python voice_gen.py --project <项目名> --ref-audio voices/ref.wav \
       --text '用无奈心疼的语气说<|endofprompt|>。大家好，今天这期讲三件事。'
```

## 🚨 铁律（每次开工都守）

1. 🔴 **模式按引擎取，不手写**：CosyVoice3 克隆走 `zero_shot`、千问走 `cross_lingual`，读 `tts.engines.<名>.default_mode`。
   走克隆必给 `prompt_text`（参考音频原文），参考音频由客户端转 16kHz 单声道。→ `02-音色与服务.md`
2. 🔴 **语气标签格式** `用[语言][情感][情绪][语气](说|喊)<|endofprompt|>。<正文>`：
   分隔句号必带（占 1 字）；默认档 `用[语言]自然口语的语气说`；普通话不写。→ `01-口播文案规范.md`
3. 🔴 **单段正文 < 80 字**（含标点，只算正文）。提交前自动质检，不过即拒。→ `01-口播文案规范.md`
4. 🔴 **本机优先**：`--url` > 本机探测 > `cloud_url` > `local_url`；临时换地址用 `--url`。**本机优先只指"用哪台"**；服务启停归人，探不通报错提示人启动。→ `02-音色与服务.md`
5. 🔴 **音色路由只看「有没有参考音色」**：
   - 传了参考音色（`--ref-audio`，或音色库条目有 `ref_audio`）→ **克隆**，情绪标签被忽略（只念正文）
   - 没传（`--qwen-speaker <内置音色>`，或条目只配 `qwen_speaker`）→ **内置音色 + 情绪标签生效**
   - 默认（什么都不传）= 取音色库第一条（女主000）→ **克隆**
6. 🔴 **语速按引擎分工**：读 `tts.engines.<名>.speed_mode`。`server`（CosyVoice3）`speed` 原样透传，
   `local`（千问）服务端不做，客户端用 ffmpeg `atempo` 本地变速。禁用 librosa `time_stretch`。
   → `references/03-参数与调用.md`

## 产出（不用另传参数，默认就写）

```
<项目名>/
├─ <项目名>01.wav…NN.wav   逐段口播音频（编号从 01 起，零填充）
├─ video/    数字人口播视频母版（下一步产出）
└─ doc/      delivery.json + 交付清单.md +（--script 时）分镜副本
```

交付清单记：生成时间、服务地址、音色与全部生成参数、每条音频的路径/时长/采样率/大小/字数。
不传 `--project` 时退回 `--out-dir` 写 `manifest.json`。

🔴 **分段响度归一化（自动执行，无需传参）**：逐段 EBU R128 测量整合响度，线性增益拉齐到 **-16 LUFS**。
处理过的文件带 `<名>.wav.norm` 标记，重跑直接跳过。目标值可用全局配置 `audio_loudness` 改。


## 项目清单（页面按它显示进度）

产物目录建好即建档、每完成一段更新；结构与全部子命令见 fxai-standard `references/01-产出铁律.md`。

```bash
P=~/.workbuddy/skills/fxai-standard/scripts/fxai_project.py
python "$P" init  "<项目>" --title <名称> --category voiceover --skill fxai-voiceover
python "$P" stage "<项目>" --script 2 --image 1 --audio 0 --video 0
```
