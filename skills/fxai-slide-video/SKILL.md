---
name: fxai-slide-video
description: 幻灯片口播视频（别名：配图视频 / PPT 视频）。把口播文案做成「幻灯片式讲解」视频：画面一张张切换，像在放 PPT（步骤逐步展开、清单逐项点亮、对比表、流程图），声音是配音，不是数字人出镜。当用户要求「幻灯片视频 / PPT 视频 / 配图视频 / 图文讲解视频 / 教程视频 / 知识口播 / 不用数字人的口播 / 配图版教程 / 用幻灯片做一版教程」时使用；**写口播文案 / 写口播稿 / 写视频文案 / 给文案分段 / 改 `segments.json`** 同样命中本技能（文案规范见 fxai-voiceover-script 的 03/04 两册）。链路：文案 → 配音 → HTML 精确绘制配图 → 拼接 → 按音频时长排字幕 → 铺首帧封面（可选）→ 混 BGM（最后一道）→ 出片。纯 CPU，**不依赖 ComfyUI / 不吃 GPU**，几分钟出片，中文零错字。不做：数字人出镜（→fxai-digital-human-video）、唱歌 MV（→fxai-mv-pipeline）、短剧（→fxai-short-drama）。
agent_created: true
---

# fxai-slide-video · 幻灯片口播视频（别名：配图视频 / PPT 视频）

把口播文案变成**幻灯片式讲解视频**：画面随讲解逐张推进、配音解说、不出镜；要 IP 出镜 → `fxai-digital-human-video`。

## 本技能文件清单（先读这里，再决定读哪册）

**确定要干什么，只读对应那册，其余一律不读。**

| 文件 | 什么时候读 |
|---|---|
| `references/01-配图与文案.md` | 做配图、写文案（版式、`fields`、生图约束） |

配图版式与可读性按 `fxai-poster` `06`（信息图版式）/ `07`（信息层级与对比度）册，出图后跑 `qc.py post` 过门。
| `references/02-模板库.md` | 按模板名做一套、抄版式字段、做封面、横版 |
| `references/03-动画与录制.md` | 页内动效、开录制（`--rec`）、调滚动 |
| `references/04-字幕与合成.md` | 字幕 / 拼接 / 响度 / **背景配乐 BGM** / 烧录排查 |
| `references/05-配置与产物.md` | 配环境、找产物、查脚本职责 |
| `references/06-投稿规则.md` | 写稿阶段（引流红线、必选标签、违禁词、标题） |
| `references/07-样式集.md` | 换配色 / 新增样式 / 查变量（用默认样式不读） |
| 🔴 `references/08-成片质检.md` | **成片合成后、交付前必读**（最终验收：11 项清单 + 问题记录 + 返修回环） |
| 🔴 `fxai-voiceover-script/references/03、04` | **写稿前必读**（叙事范式 + 用词选题） |
| `slides.py` / `themes/*.css` | 配图脚本、版式模板、`--theme` 加载 |
| `sv_pipeline.py` | 整套入口（文案→配音→配图→拼接→字幕） |
| `sv_media.py` / `sv_layout.py` | 拼接与字幕（`--bgm` 混 BGM、`--cover` 铺首帧封面，**顺序固定：封面先、BGM 后**）；项目存储格式 + 交付清单 |
| `sv_bgm.py` | **配乐自动生成**（`--bgm auto` 的执行点）：走 `fxai-workflow` 的 ComfyUI 接口出片子、落 `配乐/`；默认纯器乐，可按内容改带唱（`--vocal` + 歌词） |
| `sv_compliance.py` / `sv_title.py` | 合规预检词表；发布标题取词与评分 |
| `sv_check.py` | **成片质检（最终验收）**：元数据 / 壳文字冻结 / 虚化 / slogan 单行 / 字幕位置与内容 / 素材登记，写 `质检报告.md` → 见 08 册 |
| 🔴 `../fxai-mixed-layout/` | **素材槽公共件**（图片轮播 / 视频 / 网页三种填法、转场表、`fit` 四档、版式登记表）。写 `frame` / `trio` 的 `media`、加新展示形式时读它 —— 槽的规格只有那一处，本技能只留指针 |

> 分发整目录一起拷（含 `references/` 与 `themes/`）。

🔴 **出字前必读 `fxai-standard` 06 册**：配图 HTML / 字幕字体只从白名单选（标题得意黑、正文/字幕
Noto Sans SC；微软雅黑随 Windows 预装可用），禁用商业字库。

## 直接开跑

```bash
# 单一规格同时驱动文案与配图
python sv_pipeline.py --project <项目名> --root <工作区> --spec segments.json

# 按参考模板做（模板名见 02 册）
python sv_pipeline.py --project <项目名> --spec segments.json --preset 快嘴爆款
# 换配色（样式名见 07 册）
python sv_pipeline.py --project <项目名> --spec segments.json --theme 樱花粉
# 横屏成片（16:9）：不用手算 --w/--h，--size 预设一步到位
python sv_pipeline.py --project <项目名> --spec segments.json --size 横屏     # 1366×768
python sv_pipeline.py --project <项目名> --spec segments.json --size 横屏高清 # 1920×1080
# 带背景配乐（BGM）：auto = 音乐生成器现场出一首（落 配乐/）；也可给现成文件
python sv_pipeline.py --project <项目名> --spec segments.json --bgm auto
python sv_pipeline.py --project <项目名> --spec segments.json --bgm 配乐/氛围01.wav --bgm-volume 0.20
# 带封面 + 带配乐：一条命令。铺封面排在 BGM 之前，配乐从封面第一帧铺满整片
python sv_pipeline.py --project <项目名> --spec segments.json --bgm auto --cover 封面/封面_主推.png
python sv_media.py --project <项目名> --bgm auto --cover 封面/封面_主推.png   # 已出片、后补
# 按内容要带唱（默认纯器乐；带唱须给歌词，音量自动降到 0.15）
python sv_pipeline.py --project <项目名> --spec segments.json --bgm auto \
    --bgm-vocal "轻柔女声轻唱，气声，靠后" --bgm-lyrics-file 配乐/歌词.txt
# 自己精修配图时：先出音频 → 出图 → 合成
python sv_media.py --project <项目名> --segs <段落画面映射.json> --size 横屏
```

尺寸预设：`竖屏`(768×1280 默认) / `竖屏1366`(768×1366，精确 9:16) / `竖屏高清`(1080×1920) /
`横屏`(1366×768) / `横屏小`(1280×720) / `横屏高清`(1920×1080)，也可直写 `--size 1600x900`。
`--w`/`--h` 显式给会覆盖预设。详见 `02-模板库.md`「横版」。

参数见各脚本 `--help`（`--dry-run` 只打印命令）。

## 🚨 铁律（每次开工都守，不看其余册子也要守）

1. 🔴 **配图 HTML/CSS 绘制 → Chrome 截图**（中文零错字）；生图做主视觉走 `showcase`。→ `01-配图与文案.md`
2. 🔴 **写稿只管把内容写好讲透**（画布默认竖屏 768×1280；横屏用 `--size 横屏` = 1366×768，别手算 `--w/--h`）。
3. 🔴 **一段 = 一图 = 一音频**；`segments.json` 同时定义文案(`text`)与配图(`template/fields`)。
4. 🔴 **首帧即封面**：第 1 段 `"anim": false`，做成完整封面海报；胶囊（`chapter`）只写内容属性。→ `02-模板库.md`
5. 🔴 **页内元素动效才是主体**，整页淡入/淡出只占小部分；按这段讲什么安排动效。→ `03-动画与录制.md`
6. 🔴 **文案与叙事四条**：每篇 ≥2 段第一人称亲历、互动锚 ≥2 处、结尾「提问 + 关注动作」；动笔前先读 `fxai-voiceover-script` 03/04/05 三册出「叙事拍子规划表」；口播数字写中文读法（配图 `fields` 照旧阿拉伯数字）；选题锚定当下热门技术词或工具名，首帧点名，开篇共同经历式叙事。→ `01-配图与文案.md`「文案与叙事铁律」

7. 🔴 **单段返修优先**：某一段配图 / 口播不对，只重做那一段（一段 = 一图 = 一音频，天然可拆）：
   改 `segments.json` 那段 → 只重渲那段的图 / 音 → `rework.splice_segment(成片, 新段, 段号)` 拼回。
   整片重录十几分钟，单段几十秒。多段一次提交用 `../fxai-common/rework_batch.py --shots 3,7,9`。
   判定与定位走 `fxai-common/rework.py`（`shot_no_of(秒数)` 由「某某秒不对」直接给段号）。
8. 🔴 **`segments.json` 写完即合规预检**（`--strict` 阻断 ERROR），同一步定标题：写稿模型给 `--main` + `--alts`，`sv_title.py` 只校验落盘。→ `06-投稿规则.md`
9. 🔴 **项目建在当前工作区，一个项目一个文件夹**；落盘由 `sv_layout.py` 强制执行 → `05-配置与产物.md`。
    - 🔴 `--spec` / `--script` 启动即拷进项目；日志只写项目 `_tmp/`；`--export` 只指向独立交付目录。→ `05-配置与产物.md`「落盘三条」
## 链路

文案 → 配音 → 配图 → 拼接 → 字幕烧录 → **铺首帧封面（可选，`--cover`）** → **混 BGM（最后一道，从封面第一帧铺满整片）** → 出片；落位见 05 册，BGM 与顺序口径见 04 册「BGM 环节」0 节。

渲染中问进度：`shot_query.py <pid>`；判据见 fxai-standard 01 册。


## 项目清单（页面按它显示进度）

产物目录建好即建档、每完成一段更新；结构见 fxai-standard 01 册。

```bash
P=~/.workbuddy/skills/fxai-standard/scripts/fxai_project.py
python "$P" init  "<项目>" --title <名称> --category voiceover --skill fxai-slide-video
python "$P" stage "<项目>" --script 2 --image 1 --audio 0 --video 0
```
