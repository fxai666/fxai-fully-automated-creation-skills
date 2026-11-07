---
name: fxai-mv-pipeline
description: 音乐→图片→MV 一体化生成管线（用户级公共能力）。当用户说「做首歌出个 MV / 生成音乐并配视频 / 用 MiniMax 生成数字人 MV / 单场景·多场景·多参考 MV / 给我做个音乐短视频」时使用。覆盖：音乐生成（Yue2 默认 / MiniMax Music 3 支持男女对唱）→ 千问2.1 生成人物与场景图 → MiniMax 三种模式（单场景/多场景/多参考）MV 视频生成，所有提示词中文。不做：口播视频（走 fxai-digital-human-video）、短剧（走 fxai-short-drama）、纯生图（走 fxai-comfyui-image-gen）、只生歌不出片（走 fxai-yue2）。
---

# 音乐 → 图片 → MV 一体化生成管线

把「一段音乐需求」变成「带数字人演唱的 MV 视频」。核心模块 `mv_pipeline.py`（唯一真相源），本技能与 MCP 入口都调它。

## 🚨 开工前置

🔴 **第一步读 `fxai-workflow/SKILL.md` 的「ComfyUI 服务端模式铁律」**（本技能经 `fxai-audio-video` 走它）：
服务端由控制台启停，本技能只按地址发 HTTP；8188 探不通 → 走云或提示人启动。
开工前读 `{data_dir}/经验库_视频.md`，「顽疾」「已确认」优先遵守。

🔴 **烧字幕出字前必读 `fxai-standard` 06 册**：字体只从白名单选（标题得意黑、正文/字幕 Noto Sans SC），商业字库禁用。

## 🚨 铁律（每次开工都守）

1. **全程 HTTP，状态只看接口**：进程管理归保活方（无 PID / tasklist / kill）；资源存在性只走接口（本机 `ls` 只作参考）；地址只从 `~/.workbuddy/fxai_global.json` 读（`comfy_url` / `comfy_cloud_url`），仙宫云端口段用 `8188`（`-80` 是管理端口）。
2. **等任务只等 `fxai:task_saved`**（＝合并/保存完毕）；等待期只查健康，取结果 / 拉列表 / 查 `/history` 留到报错时。
3. **提交 `POST /prompt` 前必须过格式自检**：所有提交走 `run_workflow`。规范表见 `references/01-提交与节点.md`。
4. **提示词职责**：三/四视图只写穿搭（发型/妆容/服装），全身入画/白底/8k 交给节点；场景图写完整描述（`05` 册）。
5. **三种模式**：`single` 单场景贯穿全曲、`multi` 按段落切场景（人物画进场景图）、`multiref` 四视图 + 空场景分别喂入（**默认**）；multiref 场景图须为空场景，人物靠 `char_rel` + 当歌穿搭注入（`02` 册）。
6. **三个 MV 工作流的 `925` 索引恒为 `开始=0 / 结束=-1`**，多场景与多参考导出默认的 `0/0` 会截断，由代码强制覆盖。
7. **创作时序**：音乐 → 真实时长 → 歌词先行对齐 → H3 公式算分段数 → 这时才逐段写提示词（`05` 册）。
8. **成片带分辨率后缀**（`成片_768x1280.mp4`），字幕不带后缀（跨尺寸复用）。
9. 🔴 **单段返修优先**：某一段不对只重生成那段 —— `produce_song.py --mv-segment=N`（0 起，
   隐含 `--mv-only`）。整曲几十分钟，单段几分钟。判定与定位走 `fxai-common/rework.py`。
   ⚠️ 产物是**该段裸片**（时间从 0 起），必须 `rework.splice_segment(成片, 裸片, 段号)` 拼回再烧字幕。
   多段一次提交走 `../fxai-common/rework_batch.py --shots 3,7,9`（串行，全部渲完才返回）；
   服务端每渲完一段都会合并回完整成片 → 并行互相覆盖，只认最后一段渲完那次。

10. **缺参考图先问用户要图**，拿到再上传；参考图只认用户给的那一批。
11. **成片抽帧目测验收**：每段中点一帧拼联络表，核对人物在场 + 穿搭符合当歌设定；日志 success 不算通过。
## 文件清单（先读这里）

**不读完也能开工**：定下要干什么，只读对应那册。

| 文件 | 什么时候读 |
|---|---|
| `references/01-提交与节点.md` | 提交报错、改工作流 JSON、查节点号 |
| `references/02-三种模式.md` | 定下 single / multi / multiref 后（开工必读其一） |
| `references/03-分段与字幕.md` | 时长、人声判定、歌词对齐、时间轴、烧录 |
| `references/04-配置与归档.md` | 配环境、目录结构、补归档、加新歌 |
| `references/05-生产标准.md` | 写分镜、定场景数、逐段提示词、审美 |
| `references/07-双人对唱.md` | 双人 / 多人对唱（两套模板、`- sing:`、f0 验证） |
| `references/08-音乐生成-Yue2.md` | Yue2 出歌（独唱默认） |
| `references/09-音乐生成-MiniMaxMusic3.md` | MiniMax Music 3 出歌（对唱默认） |
| `references/06-安装与分发.md` | 打包分发、装机（日常生产不读） |
| `scripts/*.py` | 要跑才看参数 |

> 分发整目录一起拷（含 `references/`）。

## 入口：一键生产 `produce_song.py`

两段式（音乐与渲染分开跑，中间人工创作分镜）：

```
python produce_song.py --song=<歌名> --phase music     # 出音乐 + 歌词对齐 + 分镜草稿
python produce_song.py --song=<歌名> --phase render    # 出图 + MV + 烧字幕
```

常用开关与语义见 `05-生产标准.md`：`--reuse-music`（render 阶段必带）、`--mv-only`、`--mv-segment=<N>`、`--regen-subtitle`。

渲染中问进度：`shot_query.py <pid>` 出已生成 N/M 镜、还剩几镜；判据见 fxai-standard 01 册。

> 🎵 **音乐模型**：独唱默认 Yue2、对唱默认 MiniMax Music 3，按人物数自动切换，`制作.json` 的 `music_model` 可覆盖；格式各读 08 / 09 册。

## 项目目录

每首歌一个以歌名命名的文件夹：单文件放项目根（中文名），图片进 `图片\` 子文件夹（`人物1.png`、`场景1.png`…）。完整目录树与清理白名单见 `04-配置与归档.md`。


## 项目清单（页面按它显示进度）

产物目录建好即建档、每完成一段更新；结构见 fxai-standard 01 册。

```bash
P=~/.workbuddy/skills/fxai-standard/scripts/fxai_project.py
python "$P" init  "<项目>" --title <名称> --category mv --skill fxai-mv-pipeline
python "$P" stage "<项目>" --script 2 --image 1 --audio 0 --video 0
```
