# fxai-short-drama 短剧生成技能整合包 · 模块专用环境配置

> 本文件是「安装说明.md」里「二、本模块专用环境配置」的内容。安装者请逐条核对。
> 所有路径都按**你自己的电脑**实际位置走，盘符一律替换成本机真实位置；脚本会自动定位到你的技能/软件目录。

## 一、整合包包含什么

### 1. 主体技能 `fxai-short-drama/`（本包核心）
| 文件 | 作用 |
|---|---|
| `SKILL.md` | 规范、约束、调用方式、字幕/速度/档案约定 |
| `drama_pipeline.py` | 唯一真相源：组装/提交/轮询/合并 + 项目档案辅助函数 + 验收烧录 SOP |
| `gen_subtitle_aligned.py` | **内置**镜头锚定词级对齐（文字取剧本 `<d>` 真值、whisper 只借时间；不依赖 MV 的三段式补齐） |
| `platform_publish.py` | **多平台发布交付套件生成器**：按 `platform_publish_specs.json` 规格，一键产出红果/番茄/快手/火龙漫剧所需图片（按各平台 `required_images` 真实数量出图，poster 盖标题封面 + avatar 中心裁切头像/小图）+ 系列共享 `剧情介绍.md` + 每平台 `展示页.md`（投稿表单预填可复制）+ `总览_交付清单.md` |
| `platform_publish_specs.json` | 各平台 `required_images`(key/label/role/w/h/ratio/note)/必填字段/特别要求/AI声明 的真实规格表（改尺寸/加平台/调所需图片只动这里） |
| `examples/episode_template.py` | 新项目起步模板（参数化、无绝对路径） |
| 全局配置 `~/.workbuddy/fxai_global.json` | **唯一真相源**，全部 `fxai-*` 技能共用一份（地址/软件目录/数据目录），不随包发 |

### 2. 复用技能（整合包已一并打包，安装者需全部放到技能目录）
| 技能 | 用途 | 关系 |
|---|---|---|
| `fxai-workflow` | 共享 ComfyUI 客户端 `comfy_client.py` + 双采版工作流 `shortdrama-minimax-filmdrama-api.json`（Ref2VA 多参考 + For 循环批量 + 视频合并） | **必需**，短剧直接 `import` 它 |
| `fxai-fourview-gen` | 角色四视图 / 三视图生成（`gen_char_view` / `gen_threeview`） | 生图复用 |
| `fxai-scene-gen` | 场景图生成（空场景 / 含人物，`gen_scene_image`） | 生图复用 |
| `fxai-comfyui-image-gen` | 本地 ComfyUI 文生图（千问2.1 模板） | 备选生图 |
| `fxai-h3-video-prompt` | H3 六段式提示词规范（含字幕/对白写法） | 提示词依据 |
| `fxai-whisper-transcribe` | whisper 专用 venv（faster-whisper 词级） | `gen_subtitle_aligned` 运行时调它取时间轴 |
| `fxai-lyric-subtitle` | MV 风格字幕烧录参考（大段对白/歌词可参考其 SRT/ASS 流程） | 短剧自带 `gen_subtitle_aligned` 已封装镜头锚定，非必需 |

> 真人出镜项目后续还要：音频引用（`音色参考`/`原音频`），可借助 `fxai-whisper-transcribe` 提取原音频时间轴。

## 二、运行依赖（安装者需先准备好）

1. **ComfyUI 服务端在跑**，且装好 `fxai-toolkit` 自定义节点（提供 `/fxai/tasks/result` 与双采版节点）。默认地址 `http://127.0.0.1:8188`。
2. **凤希AI伴侣（软件）已安装**，并在其界面里指定好「软件安装目录」与「软件数据目录」。这两个目录会被写入 `~/.workbuddy/fxai_global.json` 的 `software_install_dir` 与 `software_data_dir`：
   - ffmpeg（**带 libass 的 full_build**，含去噪滤镜）位于 `{software_install_dir}\ffmpeg\ffmpeg.exe`。
   - whisper 环境是独立 conda env：`{software_data_dir}/conda/env/whisper`（解释器 `python.exe` 在环境根目录，按规范置于软件数据目录下的 conda/env 中）；脚本统一按 `software_data_dir` 定位它，字幕识别前先使用此环境；传统 venv 布局不适用。
   - 自查：`ffmpeg -filters | grep -w subtitles` 应能看到 `subtitles`；`{software_data_dir}/conda/env/whisper\python.exe -c "import faster_whisper"` 不应报错。
3. **whisper 模型**：由 whisper 程序自己管（`whisper_server.py` 顶部 `MODEL_NAME`，当前 large-v3 满血版，随程序包分发在程序目录 `model\` 下）；`fxai_global.json` 只记 `whisper_dir`（程序安装位置），模型不进全局配置。
4. **Python 依赖 Pillow**：多平台发布套件（`platform_publish.py`）需要 `PIL`（封面合成）。在跑脚本的 Python 环境装 `pip install Pillow` 即可；若用 WorkBuddy 托管 Python，在它的 venv 里装。封面中文字体走 `%WINDIR%/Fonts/simhei.ttf`（黑体，Windows 自带），缺失则退回系统默认字体（中文可能渲染异常）。

## 三、安装步骤（使用者侧）

1. 把本包里所有「技能文件夹」（`fxai-short-drama/`、`fxai-workflow/`、…）复制到 WorkBuddy 技能目录，二选一：
   - **用户级（推荐，对所有项目生效）**：`<你的用户名>/.workbuddy/skills/`
   - **项目级（仅当前项目生效）**：`<你的项目>/.workbuddy/skills/`
2. 确认 `~/.workbuddy/fxai_global.json` 的 `comfy_url` 指向你的 ComfyUI 服务端（默认 `http://127.0.0.1:8188`）。这份是全局唯一配置，**技能包只走全局配置，不带 `config.local.json`**。
   - 短剧资源全部在服务端 `fxai/image/shortdrama_<slug>/` 下（每系列独立目录，由 `project_def.server_subdir` 指定），按相对路径引用，无本机图片根。
   - 地址不读环境变量（想临时换服务器用 `comfy_client.comfy_url(override=...)` 传参）。
3. 确认 `~/.workbuddy/fxai_global.json` 已写入正确的 `software_install_dir` / `software_data_dir`（凤希AI伴侣安装时生成；没有就手动建一个 JSON 填上）。
4. 确认 ComfyUI（8188）已启动、`fxai-toolkit` 节点已装。
5. 跑通自检：`python drama_pipeline.py --check` → 显示 ComfyUI online、模板存在（服务器模式，资源按 `project_def.server_subdir` 相对路径引用，无本机根目录）。

## 四、去绝对路径约定（分享硬要求）

- 技能内**路径一律写占位符**：机器路径、`<某ComfyUI本机路径>`、`<某数据目录>`、`<你的用户名>` 这类省略写法都不落到代码里。
- 机器相关：统一走全局 `~/.workbuddy/fxai_global.json`（不读地址类环境变量）。
- 项目相关：统一走 `project_root` 参数 + 相对路径（如 `角色三视图/juzi.png`）。
- 工作流模板共享自 `fxai-workflow/shortdrama-minimax-filmdrama-api.json`（与各技能同级的公共目录，fxai_ 前缀统一维护），不引用外部绝对路径文件。
- 工作流 JSON 内部节点若引用了本机路径（如保存目录），由 `build_workflow` 在运行时按参数覆盖（保存目录 `sucai`、尺寸、结束索引 `-1` 等），不依赖写死路径。

## 五、最小可运行流程

```bash
# 1) 新系列：先在系列根建 project_def.json（server_subdir + 角色/场景→server_index 映射）
# 2) 在新一集目录下，照 examples/episode_template.py 写 shots，然后：
python examples/episode_template.py --project "<你的项目目录>/短剧项目"

# 3) 渲染完成后验收+烧录（镜头锚定字幕）：
python acceptance_burn_epN.py        # 内部调 drama_pipeline.acceptance_burn(...)
```

## 六、硬约束与约定

- **生成速度**：16G 显存（如 RTX 5060 Ti）≈ 1 秒视频 / 1 分钟生成。总时长 N 秒 ≈ 预计 N 分钟。
- **结束索引=-1**、**镜头时长≤10s**、**横屏 1280×768** 已强制写入代码（模板默认 0 会截断）。
- **ffmpeg 路径**：以 `software_install_dir/ffmpeg/` 为准，`_resolve_bin` 自动定位，代码里写占位符。环境变量 `FFMPEG_PATH` 若指向**目录**而非 exe，会让 subprocess 报 `WinError 5`；`_resolve_bin` 已忽略指向目录的 env 覆盖。
- **SRT 路径必须相对 basename**：烧录时 `subprocess.run(..., cwd=SRT所在目录)`，滤镜里只写 `subtitles=文件名.srt`（os.path.basename）——`subtitles` 滤镜里 `:` 是选项分隔符、`\` 是转义符，Windows 绝对路径会被误解析。
- **字幕规范**：对白用 `<d>[Chinese]台词</d>`（语音+口型）；可见字幕条用双引号「画面弹出字幕："xxx"」（宜短句）；大段对白字幕走后期烧录（`gen_subtitle_aligned` 镜头锚定，文字取剧本真值，whisper 只借时间）。
- **项目档案**：新项目首次生成→存项目档案，缺什么补什么；`sync_project_to_comfyui` 仅用于从本地备份恢复服务端资源（HTTP 覆盖回传），正常生图流程不必。
- **多平台发布套件**：封面底图走 ComfyUI 生图或系列最亮场景/角色图，中文标题/集数由本地 PIL 盖（模型只出画面，中文留给 PIL）；按各平台 `required_images` 真实需要的图片数量出图（poster 盖标题封面、avatar 中心裁切头像/小图），不重复不漏。平台尺寸会变，改尺寸/加平台只动 `platform_publish_specs.json`。系列共享 `剧情介绍.md`；`project_def.json` 加 `synopsis`/`one_liner`/`tags`/`category` 可自动带全。
