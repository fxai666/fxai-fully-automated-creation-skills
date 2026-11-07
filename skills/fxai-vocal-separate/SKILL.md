---
name: fxai-vocal-separate
description: 人声分离（服务端 ComfyUI 模型工作流 · Mel-Band RoFormer）。输入歌曲音频，输出纯净「人声.wav」（可选伴奏），全部经 HTTP 接口下载；供 MV 管线 / 短剧 / 翻唱 / 对口型复用。；不做：不换音色、不生成视频，只交付音轨。
---

# fxai-vocal-separate · 人声分离（模型分离）

## 本技能文件清单（先读这里，再决定读哪几册）

| 文件 | 什么时候读 |
|------|-----------|
| `SKILL.md` | 本文件：铁律/概述/调用方式/CLI |
| `references/接口与路径.md` | 提交前对节点与占位符；查服务端状态、装依赖、等任务 |
| `references/产物命名规则.md` | 下载与落地产物、核对产物名、MV 管线内的命名 |
| `references/ffmpeg与模型位置.md` | 做二次去噪、配 ffmpeg 与 RNNoise 模型位置 |
| `references/失败回退行为表.md` | 某步报错、要判断主流程是否受影响 |
| `separate_vocals.py` | vocal_separate() 实现；按 SKILL.md 调，改行为才读 |

**确定本次要做什么之后，只读对应的那一两册，其余一律不读。**

## 铁律（不看分册也要守）

1. **去本机化**：输入音频走服务端相对路径；产物一律经 `GET /fxai/down/file` 下载，资源有无一律查接口。
2. **令牌来自接口**：下载用 `/fxai/tasks/result` 返回的令牌，落地时再改名。
3. **失败自动回退用原音乐**，主流程照常推进到结束。
4. **本地已有人声即复用**：`out_dir/<vocal_name>` 存在就跳上传与服务端分离，算力零消耗。
5. **路径只走全局配置**（`fxai_global.json`），文档与代码都不写死本地路径。
6. 🔴 **开工前读 `fxai-workflow/SKILL.md` 的「🚨 ComfyUI 服务端模式铁律」节**（铁律原文在被依赖件的 SKILL.md 里，不在 `comfy_client.py`）：**服务与技能解耦（API 模式）**——ComfyUI 由「凤希本地AI控制台」／启动器启停，本技能只按地址发 HTTP；本机 8188 探不通 → 走云或提示人启动。

## 概述

- **用途**：把一首歌的人声与伴奏分离，得到纯净人声轨。供 whisper 字幕对齐（减少伴奏误检）、翻唱、对口型、短剧配音等复用。
- **实现方式**：**调用服务端 ComfyUI 的人声分离模型工作流**（Mel-Band RoFormer），模型在服务器 GPU 上跑（与 RTX 5060 Ti sm_120 一致），本机不装 torch/demucs。
- **默认产物**：去噪成品与分离原始人声各一份，命名规则见分册。

## 调用方式（代码）

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fxai-vocal-separate"))
import separate_vocals as sv   # 模块内部会自动把 ../fxai-workflow 加进 sys.path
r = sv.separate_vocals(audio_rel="audio/<歌名>/音乐.wav", out_dir="<歌曲项目目录>/<歌名>")
```

- `audio_rel`：服务端音频相对路径（与 MV 同一份 `音乐.wav`，服务端已存在）。
- `out_dir`：本地落地目录（通常是歌曲项目目录）；人声下载后落地为 `人声.wav`。
- `model`：工作流模型 slug，默认 `melband`（Mel-Band RoFormer）。换模型改 `fxai-workflow/comfy_client.py` 的 `WORKFLOWS["vocalsep"]` 即可，技能层零改动。
- `accomp_name`：默认 `None`（当前工作流只保存人声一路）；传 `"伴奏.wav"` 时按服务端目录里「非主产物」的最新一个音频下载。
- `denoise=True`（默认）：拿到人声后自动再跑一次 RNNoise 去噪。

### 输入两种都吃（自动判断）

- **本地音频文件**：先经 `POST /fxai/audio/upload` 传到服务端再分离（分离在服务端跑，本地只管下载+去噪）。
- **服务端相对路径**（如 `音乐生成/051_audio.wav`，MV 管线里就是这种）：直接用，省掉上传这一步。

### 单独当工具用（CLI）

```bash
python separate_vocals.py <音频(本地文件或服务端路径)> <输出目录>   # 分离 + 去噪
python separate_vocals.py --denoise <音频> [输出路径] [mix]          # 只去噪（本地 ffmpeg，不动服务端）
```

## 已接入 MV 管线（默认开启）

- `produce_song.py` 的 **1.4/6 步骤**：音乐生成完毕 → 分离人声 → 下载 → `arnndn` 去噪 → 用**去噪人声**跑 whisper 歌词识别（1.5/6）。
- 开关：`fxai_global.json` 的 `vocal_separation`（默认 **true**；设 false 回到「直接用音乐对齐」）。
- 交付物命名与中间件清理规则见 `references/产物命名规则.md`。

## 项目清单（页面按它显示进度）

产物目录建好即建档、每完成一段更新；结构与全部子命令见 fxai-standard `references/01-产出铁律.md`。

```bash
P=~/.workbuddy/skills/fxai-standard/scripts/fxai_project.py
python "$P" init    "<项目>" --title <名称> --category music --skill fxai-vocal-separate
python "$P" stage "<项目>" --script 2 --image 0 --audio 0 --video 0
python "$P" memory  "<项目>" --add "<一条结论>" --tag 决策
```

记忆随建档生成，开工先读它；写法见 fxai-standard `references/01-产出铁律.md`「项目记忆」。
