---
name: fxai-poster
description: 图片设计（全类型：视频封面 / 海报 / 文章配图 / 社交图 / 电商图 / 信息图 / 视频内嵌图）。主线是 AI 生图打底再叠加文案层：模式分 hybrid（默认，生图+叠字）/ image（纯画面）/ web（中文必须精确或大段文字时）；类型与必备要素见 `08` 册，三道质检门（规格预检 / 提示词要素 / 成图指标）见 `09` 册。封面另有 8 种样式（含竖排贴左），可自动选也可指定（`cover_styles.py`），产物 PNG 可直接铺到视频首帧。不做：视频本体生成（→fxai-audio-video / fxai-mv-pipeline / fxai-short-drama）、纯配音（→fxai-voiceover）。
agent_created: true
---

# fxai-poster · 海报与图片设计

一张设计好的图：**视频封面 / 海报 / 文章配图 / 社交图 / 电商图 / 信息图 / 视频内嵌图**都走这里。
本技能是 **AI 生图设计规范的唯一出处**：任何技能出图都按这里的规范与质检门禁走，不另写一套。

## 本技能文件清单（先读这里，再决定读哪册）

| 文件 | 什么时候读 |
|---|---|
| `references/01-模式选择.md` | **开工必读**：这次该走 web / image / hybrid |
| `references/02-网页模式版式.md` | web / hybrid 排版、主题、封面文案结构、吸睛判据 |
| `references/03-生图与混合模式.md` | 写生图提示词、调底图蒙板 |
| `references/04-接入视频首帧.md` | 把海报铺到 MP4 前（接入方法、两个致命点、校验） |
| `references/05-封面模板库.md` | **做视频封面必读**：8 种样式、封面段长度与音轨 |
| `references/06-图文信息图版式.md` | 信息块 ≥3：结构选型、编号卡片、图标配色 |
| `references/07-叙事与信息层级.md` | 定主张、情绪强度、画幅与可读性门禁（跨模式通用） |
| `references/08-图片类型矩阵.md` | **定类型必读**：15 类用途的尺寸、必备要素、质检重点 |
| `references/09-质检门禁.md` | **交付前必读**：三道门的判据与返修路径 |
| `references/10-千问2.1提示词规范.md` | **写提示词必读**：六段骨架、文字引号规则、人物绑定、尺寸、跨技能调用 |
| `references/11-封面多尺寸与平台规格.md` | 跨平台封面：尺寸 key / 留白侧 / 生图优先 / 验收 |
| `references/svg-icons.md`、`references/infographic-template.html` | 23 组内联 SVG 图标片段；信息图版式实例（只作参考） |
| `poster.py`、`cover_batch.py` | 出图入口；`--gen-cover` 带字 / `--gen-base` 无字底图 |
| `cover_styles.py`、`cover_side.py` | 8 种样式（`render` / `render_matrix`）；按底图留白判文字侧 |
| `cover_prompt.py` | 版式 → 生图提示词，六段骨架第三段写足排版 |
| `cover_sizes.json` | 尺寸与站点规格唯一真源 |
| `qc.py` | 三检 `pre` / `prompt` / `post` / `types` |

**其余一律不读。**

## 模式与样式

`hybrid`（生图打底 + 叠字）/ `image`（生图，文案写进提示词就落在画面上）/ `web`（纯文字排版）；**封面首选 image 带字**。
判据与决策顺序见 `01` 册，15 类用途默认模式见 `08` 册。8 种样式 key 见 `05` 册，多尺寸出齐见 `11` 册；**一条视频至少出 2 版**（有人 / 无人）。

## 质检门禁（交付前必过，三道门）

任何一道判 FAIL 不许往下走，按报告给的修法改完重跑。

```bash
python qc.py pre  --kind record_cover --w 1680 --h 1054 --mode hybrid --title "…" --hooks "数字前置,反差反转"
python qc.py post --img 封面.png --kind record_cover      # 另外两门：prompt（提示词）/ types（全类型规格）
```

1. **预检**：画幅 / 模式 / 必备要素 / 文案超宽 / 吸睛不足
2. **提示词门**：六段骨架 + 文字区四要素（引号·位置·字号·字色）+ 着装
3. **成图门**：画幅 / 分辨率 / 亮度 / 层次 / PSNR 为硬门；锐度分三档（`SHARP_TIER`），纯画面图加 `--no-text`

判据与返修路径见 `references/09-质检门禁.md`。

## 硬规则

1. 🔴 **尺寸随用途，一律以 `cover_sizes.json` 的 key 为准**：竖版视频封面 `vert_9x16`
   （1080×1920）、横版 `horiz_16x9`（1920×1080）；其余用途按需指定 key，本文件不写像素。
2. 🔴 截图与 ffmpeg 临时文件落系统临时目录下的 `fxai_burn/poster/`（脚本自动建、用 ASCII 路径），产物拷回项目——中文路径会让 `file://` 与 ffmpeg 滤镜解析失败。
3. 🔴 配色只改 `themes/*.css` 的 CSS 变量，HTML 内不硬写颜色。
4. 🔴 字体走 `fxai-standard/references/06-字体与素材授权.md` 白名单；本机缺失时回退系统已装中文字体并在交付清单登记。
5. 🔴 **媒体平台物料默认裸出**：不带网址、联系方式、二维码、引导关注语、品牌署名与 logo 位（判据：发媒体平台的图不带任何导流信息）。仅做品牌官网海报 / 对外物料时显式 `--brand` / `--site`。
6. 🔴 **吸睛是封面第一要求**：判据是划过去能否让人停一下；每张至少命中 2 项吸睛元素（热点词 / 数字前置 / 反差反转 / 身份标签 / 结果承诺 / 紧迫感）。
7. 🔴 **封面主标必须「正面 → 反转」两层**：第一行写做到的面，第二行（强调色）写代价/真相；胶囊点名热点技术词；主标每行 ≤ 7 字（含标点）。
8. 🔴 纯文字海报冲击力最强：不堆装饰图、不塞人物特写、标题不超两行。
9. 🔴 封面段长度（帧数）与音轨垫法只在 `references/05-封面模板库.md`「封面段时长」定，本文件不写数值。
   🔴 **铺封面必须排在混 BGM 之前**：封面段自带的是垫音不是配乐，BGM 要作为最后一道工序作用于已铺封面的整片。规范见 `04` 册 1d 节。
10. 🔴 本技能不生成视频：视频由 `fxai-audio-video` / `fxai-mv-pipeline` / `fxai-short-drama` 出，本技能只交图或铺首帧。
11. 🔴 **信息块 ≥3 走信息图版式**（`06` 册）；单张封面只承载一个主张。
12. 🔴 **可读性门禁**（`07` 册）：对比度实测正文 ≥4.5:1、大字 ≥3:1，缩略图 200px 主标可读，换画幅重构图不裁切；图标内联 SVG 禁 emoji，色值写死十六进制。
13. 🔴 **交付前必过三道质检门**（`09` 册）：任一 FAIL 不交付；底图不合格重出图，不靠修图救。
14. 🔴 **封面尺寸只认 key，默认只出两张**：像素与各站规格只写 `cover_sizes.json`；
    台账/项目只引用 `vert_9x16` / `horiz_16x9`，项目 `封面.json` 的 `sizes` 只列这两个 key，
    其余 key 仅在用户指定时生成。🔴 **平台上传框比例（抖音竖 3:4 / 横 4:3）**
    是平台自己的裁切框，不是另出第三张的理由；竖排文字须落在 3:4 安全区内。细则见 `references/11`。
15. 🔴 **封面文字默认由生图直接渲染**：版式排版说明经 `cover_prompt.py` 写进提示词第三段
    （`--gen-cover`）；「画面无任何文字」只准用于叠加层路线（`--gen-base`）。见 `11` 册。

16. 🔴 **单一职责**：生图一律委托 `fxai-scene-gen`，服务寻址归 `fxai-workflow`；浏览器走 `fxai-slide-video` 的 `gpu.chrome_exe()`，ffmpeg 走 `fxai-audio-video` 的 `av.ffmpeg_libass()`。本技能不感知、不配置服务地址，不写死可执行文件路径。

以上 5–8 条的写法与判据见 `references/02-网页模式版式.md`。

## 直接开跑

```bash
# hybrid（默认：生图打底 + 叠字）
python poster.py --mode hybrid --out-dir <项目> --name cover --size 1680x1054 \
  --prompt "…" --title "她回来了|这一次绝不退让" --tag "古风短剧 · 第 3 集" --overlay 0.5

# image（封面：文案写进提示词，生图直接落字）
python poster.py --mode image --out-dir <项目> --name mv_cover \
  --prompt "…（六段骨架，第三段写足排版）…" --size 1080x1920

# 铺到视频首帧
python poster.py --mode web --out-dir <项目> --name cover --title "…" \
  --attach-video <项目>/成片.mp4 --cover-frames 10
```

`web` 模式排版参数（`--theme` / `--kicker` / `--sub` / `--keys`）见 `02-网页模式版式.md`。

## 片头信息贴片

铺首帧时要叠「出品 / 剧名 / 集名 / 卖点 / 署名」，用 `intro_card.py`；命令、蒙版与裁切口径见
`references/04-接入视频首帧.md`「片头信息贴片」。

## 项目清单（页面按它显示进度）

产物目录建好即建档、每完成一段更新；结构见 fxai-standard 01 册。

```bash
P=~/.workbuddy/skills/fxai-standard/scripts/fxai_project.py
python "$P" init  "<项目>" --title <名称> --category image --skill fxai-poster
python "$P" stage "<项目>" --script 2 --image 1 --audio 0 --video 0
```
