# 贴图消息（newspic，纯图集）

一条消息里放多张图，读者在会话里**左右滑动**。这就是后台那种「贴图 / 图片消息（小绿书）」。

## 2.1 接口链路

```
本地图集 ── draft-add-newspic ──▶ 每张 material/add(type=image) → 永久 media_id
 └─ 按序组装进 image_info.image_list[]（article_type=newspic）
 │
 ▼
 freepublish/publish（media_id）→ 已发表
```

贴图草稿的发布跟图文**完全同路**，`publish --media-id` 直接发。

## 2.2 字段规范
| 字段 | 必填 | 规范 |
|---|---|---|
| `article_type` | 是 | **`"newspic"`** |
| `title` | **是** | ≤32 字 |
| `content` | **是** | **纯文本**（可夹商品类特殊标签），不支持 HTML / `<img>`，塞 HTML 会被过滤；不填会被空格占位 |
| `author` | 否 | ≤16 字 |
| `image_info.image_list[].image_media_id` | **是** | **必须是永久 MediaID**（临时素材会被拒） |
| 图片数量 | — | **最多 20 张**，**首张即封面**，展示顺序 = 传入顺序 |
| 封面裁剪 | 否 | **一律不传**：封面 = 首图，微信按首图像素自算（见 2.3） |
| `need_open_comment` | 否 | 一律传 **1** |

## 2.3 尺寸与规格

**图片走永久素材（`material/add`，type=image）**
- 单张 **≤10MB**，格式 **bmp / png / jpeg / jpg / gif**。
- **输出竖图 3:4**（1080×1440 最佳，实际收敛成 1056×1440），长宽乘积别超 600 万像素。
- 首张决定封面：想要什么比例的首图，就按那个比例出图。**封面裁剪参数一律不传**（`--cover-info` / `--cover-ratio` / `--cover-percent` 默认不用），微信按首图像素自算；要三格并排就把 ≥3 张 3:4 竖图按顺序传进 `image_list`（详见 2.4 节）。

### 【永久素材索引 material_map】上传即记账，建稿前先查

上传成功后自动把「本地文件 ↔ media_id ↔ url ↔ 像素尺寸」写进索引，建稿上传前**先查索引、命中就复用**。
| 项 | 内容 |
|---|---|
| 索引文件 | 项目级：图片所在目录下的 **`素材索引.json`**；全局 `<scripts>/material_map.json`；可用环境变量 `WX_MATERIAL_MAP` 覆盖。两处都读，同一文件项目级优先 |
| key 形态 | **项目级只写文件名**；**全局账本写绝对路径**。对外 `load()/lookup()/ids_for()` 返回的 items 一律用绝对路径做 key |
| 写入时机 | `material/add` 成功、**图文封面 `uploadcover`**、**正文插图 `uploadimage` 全部自动记账**；`material-map` 命令可随时查看 |
| 复用逻辑 | `draft-add-newspic` 逐张 `lookup`：命中就不传，回包多返回 **`reused_from_index: N`**（N = 复用的张数）。入参给绝对路径/相对路径/带 `./` 的写法都能命中 |
| `verified` 字段 | `true`=本次上传确认过，可安全复用；`false`=对应关系未逐张校验，**别拿它当准** |
| 命令 | `python wx_api.py material-map <图片目录>` 打印账本（`[OK]` = 已确认，`[??]` = 待校验）；账本损坏不会崩流程，读失败按空索引处理。另有 `python material_map.py --migrate <目录>`（旧绝对路径账本就地改成文件名 key，幂等）、`--paths`（列出账本里本地文件的绝对路径） |
| 手工容错 | `material_map.revert(本地路径)` 可撤掉某条映射（素材作废时用它，防止下次误复用）。**注意：revert 会把全局+项目两本里的同一条一起删掉**，只删一条就用它之前先确认 entry 值 |

⚠️ 中转未开放 `material/getimg` / `batchget_material`，素材库查不了：media_id 只能靠上传时记账。历史草稿回包里的旧 media_id 不复用。

### 竖图集封面：原图直传 ≥3 张 3:4，封面参数一律不传

**执行规则**
- **出图竖版 3:4**（1080×1440 目标，实际收敛 1056×1440）。**≥3 张原图按展示顺序传进 `image_list`**，微信自动把前 3 张缩略并排成 `2.35:1` 横条封面——不用额外字段，**别裁、别拼**。
- **封面就是首图**（`image_list[0]`）。`thumb_media_id` 与 `cover_info.crop_percent_list` **一律不传**，微信按首图像素自算。
  唯一例外：自动裁剪把主体切歪时，才按首图像素先算坐标再传。
- **发布前一律 PIL 转 JPEG**（`quality≥95` + `subsample=0` + `optimize=True`），单图压到 **<1.5MB**，不拿 PNG 母版直传。
- **加字段一律走 `--ext`**：任意 JSON 原样 merge 进 `articles[0]`。
- CLI 的 `--cover-ratio` / `--cover-percent` / `--cover-info` 默认不碰。
- 横版单图首图（如 1152×512）只适合「单张图集 / 想要纯横版封面」，与「三格拼贴」二选一，别混着传。
- 判接口成败**只看 `errcode` / `errmsg`**：回包 `hint` 是通用托底文案，别信。

**生成侧（ComfyUI）**：宽高 512~4096 且 32 的倍数（同图文消息规格）。

## 2.4 命令

```bash
# 建贴图草稿：--images 按展示顺序传，逐张转永久素材后组装；--content 可直传纯文本文件路径
python scripts/wx_api.py draft-add-newspic --title "六张照片，一个晚上" \
 --images image/写真-晨窗.jpg image/写真-书店.jpg image/写真-雨街.jpg \
 image/写真-咖啡馆.jpg image/写真-黄昏.jpg image/写真-夜室.jpg \
 --content "一段纯文本说明（贴图正文只有纯文本，别写 HTML）" --author "凤希AI"
# 拿返回的 media_id 发布，跟图文一样
python scripts/wx_api.py publish --media-id <media_id>
```

## 2.4b 修改已有草稿（draft/update，news / newspic 通吃）

```bash
# newspic：整篇替换（8 图原值 + 换标题/正文/图序；封面裁剪参数不传，微信按首图自算）
python scripts/wx_api.py draft-update --media-id <media_id> --title "原标题" \
 --image-media-id "<id1,id2,id3,...>" \ # 直接给 ID，跳过本地图/不重传
 --content "新正文"
# 也可传本地图（命中素材索引就不重传）：--images a.jpg b.jpg ...
# news 形态才需要封面：--thumb <media_id>
```

> **⚠️ draft/update 是「整体替换」不是「局部改字段」**：目标位序的 `title` / `content` / 图 必须整套照抄再改一项。
> **⚠️ newspic 更新必须带 `article_type=newspic` + `image_info.image_list`**；newspic **省去** `thumb_media_id`（封面就是首图）。
> **⚠️ 封面类字段走 `--ext`**。
> **🚨 `draft/update` 的 `articles` 是「对象」不是数组**。
> 参数：`media_id` / `index`(多图文位序,默认0) / `title` / `content` / `images` / `image-media-id` / `thumb` / `article-type` / `author` / `digest` / `comments` / `cover-ratio` / `cover-percent` / `cover-info` / `ext`

## ⚠️ --images 只能一个标志带多个路径

一个 `--images` 后面跟全部路径，路径之间空格分隔：

```bash
--images a.jpg b.jpg c.jpg ... # ✅ 一个 --images，路径列在后面
```

**建完草稿核对回包 `image_count` 是否等于传入的图片张数，不等就把草稿删掉重来再发布。**
（删草稿 `freepublish/draftdelete --media-id`；删已发布 `freepublish/publishdelete --article-id <article_id>`；查发布状态用 `freepublish/publishget`。）

**服务端参数形态**：`freepublish/draftaddnewspic` 接扁平参数——多张图用 **`image_media_id` 逗号分隔**，服务端负责组装成 `image_info.image_list[]`。

## 2.5 图集编排纪律
- **正文只有纯文本**：一句话说明 + 可选的图注写进 `content` 的纯文本里；`content` 收纯文本，HTML 塞不进去。
- **图注用可见分隔符排版**（如 `01窗边｜一切从这个工作台开始`），别指望换行分段。
- **图注是贴图的一部分**，每条 ≤14 字，写场景/情绪（「早上七点，窗帘还没拉紧」），别写说明文。
- **竖图 3:4** 一张占满手机屏，横图刷屏没有质感。
- 图与图之间的分隔靠滑动本身带来的留白，不额外加分隔线。
- 标题 ≤32 字，摘要 ≤120 字。
- 发布后用 `publish-status` 查结果，并 `draftget` / `batchget` 回看 `article_type` 是否真是 `newspic`。
