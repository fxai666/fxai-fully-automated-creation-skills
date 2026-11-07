---
name: fxai-poster
description: 图片设计（全类型：视频封面 / 海报 / 文章配图 / 社交图 / 电商图 / 信息图 / 视频内嵌图）。主线是 **AI 生图打底再叠加文案层**：先按目标选模式——hybrid（默认，生图+叠字）/ image（纯画面）/ web（纯文字排版，中文必须精确或大段文字时）；再按 `08` 册定类型与必备要素，按 `09` 册过三道质检门（规格预检 / 提示词要素 / 成图指标）。封面另有 6 种样式模板，可按主题自动选也可指定（`cover_styles.py`）。产物 PNG 既进交付清单，也可直接铺到视频首帧。不做：视频本体生成（→fxai-audio-video / fxai-mv-pipeline / fxai-short-drama）、纯配音（→fxai-voiceover）。
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
| `references/05-封面模板库.md` | **做视频封面必读**：6 种样式、封面段长度与音轨（唯一出处）、五个坑 |
| `references/06-图文信息图版式.md` | 叠加层版式 / 信息块 ≥3：结构选型、编号卡片、图标配色 |
| `references/07-叙事与信息层级.md` | 定主张、情绪强度、画幅与可读性门禁（跨模式通用） |
| `references/08-图片类型矩阵.md` | **定类型必读**：15 类用途的尺寸、必备要素、质检重点 |
| `references/09-质检门禁.md` | **交付前必读**：三道门的判据与返修路径 |
| `references/10-千问2.1提示词规范.md` | **写提示词必读**：六段骨架、文字引号规则、人物绑定、尺寸、跨技能调用 |
| `references/svg-icons.md` | 23 组内联 SVG 图标，直接复制片段 |
| `references/infographic-template.html` | 信息图版式实例，只作参考 |
| `poster.py` | 实现；按命令调，改行为才读 |
| `cover_styles.py` | 6 种封面样式的实现；按 `render(style, ...)` 调 |
| `qc.py` | 三检脚本；`pre` / `prompt` / `post` / `types` |

**其余一律不读。**

## 三种模式

`hybrid`（默认：生图打底 + 叠加文案层）/ `image`（纯画面，不落字）/ `web`（纯文字排版）。
判据与决策顺序见 `references/01-模式选择.md`；15 类用途的默认模式见 `08-图片类型矩阵.md`。

## 封面样式库（视频首帧专用，6 种）

**一条视频至少出 2 版**（一版有人、一版无人/弱人）。六种 key、各自适配主题、`CS.render()` /
`CS.auto_style()` 调用与打分表见 `references/05-封面模板库.md`。

## 质检门禁（交付前必过，三道门）

任何一道判 FAIL 不许往下走，按报告给的修法改完重跑。

```bash
python qc.py pre  --kind record_cover --w 1680 --h 1054 --mode hybrid --title "…" --hooks "数字前置,反差反转"
python qc.py prompt --text "<提示词全文>" --role     # 挂了角色参考图才加 --role
python qc.py post --img 封面.png --kind record_cover
python qc.py types                                     # 查全类型规格
```

1. **预检**：画幅 / 模式 / 必备要素 / 文案超宽 / 吸睛不足 → 改规格与文案
2. **提示词门**：画布质感 / 构图落位 / 文字区（引号·位置·字号·字色）/ 主体 / 场景 / 光线 / 着装 → 改提示词
3. **成图门**：画幅 / 分辨率 / 亮度 / 层次 / 高频真实性（PSNR）为硬门；锐度 / 色彩 / 文字量 / 安全区为提示。锐度按类型分三档（`SHARP_TIER`），768 与 4K 同档；纯画面图加 `--no-text`

判据与返修路径见 `references/09-质检门禁.md`。

## 边界（单一职责）

- 本技能只负责「出这张图」：生图（image）、底图+叠字（hybrid）、纯排版（web）、铺首帧。
- 🔴 生图一律委托 `fxai-scene-gen`；服务寻址由 `fxai-workflow` 负责，本技能不感知、不配置、不写服务地址。
- 🔴 浏览器走 `fxai-slide-video` 的 `gpu.chrome_exe()`，ffmpeg 走 `fxai-audio-video` 的 `av.ffmpeg_libass()`；本技能不写死可执行文件路径。

## 硬规则

1. 🔴 尺寸随用途：竖版视频封面默认 `768x1280`；横版 `1280x768`；方形按需指定。
2. 🔴 截图与 ffmpeg 临时文件落系统临时目录下的 `fxai_burn/poster/`（脚本自动建、用 ASCII 路径），产物拷回项目——中文路径会让 `file://` 与 ffmpeg 滤镜解析失败。
3. 🔴 配色只改 `themes/*.css` 的 CSS 变量，HTML 内不硬写颜色。
4. 🔴 字体走 `fxai-standard/references/06-字体与素材授权.md` 白名单；本机缺失时回退系统已装中文字体并在交付清单登记。
5. 🔴 **媒体平台物料默认裸出**：不带网址、微信/QQ/电话/邮箱、二维码、引导关注语、品牌署名与 logo 位（判据：发媒体平台的图不带任何导流信息）。仅做品牌官网海报 / 对外物料时显式 `--brand` / `--site`（`auto` 取全局配置）。
6. 🔴 **吸睛是封面第一要求**：判据是划过去能否让人停一下；每张至少命中 2 项吸睛元素（热点词 / 数字前置 / 反差反转 / 身份标签 / 结果承诺 / 紧迫感）。
7. 🔴 **封面主标必须「正面 → 反转」两层**：第一行写做到的面，第二行（强调色）写代价/真相；胶囊点名热点技术词；主标每行 ≤ 7 字（含标点）。
8. 🔴 纯文字海报冲击力最强：不堆装饰图、不塞人物特写、标题不超两行。
9. 🔴 封面段长度（帧数）与音轨垫法：**只在 `references/05-封面模板库.md`「封面段时长」定，
   本文件与其他册子都不写具体数值**。
9b. 🔴 **铺封面必须排在混 BGM 之前**：封面段自带的是垫音不是配乐，BGM 要作为最后一道工序
   作用于已铺封面的整片，才能从封面第一帧响到片尾。规范见 `references/04-接入视频首帧.md` 1d 节。
10. 🔴 本技能不生成视频：视频由 `fxai-audio-video` / `fxai-mv-pipeline` / `fxai-short-drama` 出，本技能只交图或铺首帧。
11. 🔴 **信息块 ≥3 走信息图版式**（`06` 册）；单张封面只承载一个主张。
12. 🔴 **可读性门禁**（`07` 册）：对比度实测正文 ≥4.5:1、大字 ≥3:1，缩略图 200px 主标可读，换画幅重构图不裁切；图标内联 SVG 禁 emoji，色值写死十六进制。
13. 🔴 **交付前必过三道质检门**（`09` 册）：任一 FAIL 不交付；底图不合格重出图，不靠修图救。

以上 5–8 条的写法与判据见 `references/02-网页模式版式.md`。

## 直接开跑

```bash
# hybrid（默认：生图打底 + 叠字，封面 / 海报主力）
python poster.py --mode hybrid --out-dir <项目> --name cover --size 1680x1054 \
  --prompt "…" --title "她回来了|这一次绝不退让" --tag "古风短剧 · 第 3 集" --overlay 0.5

# image（纯画面，不落字）
python poster.py --mode image --out-dir <项目> --name mv_cover \
  --prompt "霓虹夜色下的城市天台，远处灯火，电影级光影" --size 768x1280

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
