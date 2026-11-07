# 图文消息（news）

## 1.1 接口链路

```
本地 HTML ── draft-add ──▶ freepublish/draftadd（微信 draft/add，article_type=news）
 ├─ 封面：freepublish/uploadcover → thumb_media_id（永久）
 └─ 正文图：freepublish/uploadimage → 换回 mmbiz URL 塞进 <img>
 │
 ▼
 freepublish/publish（微信 freepublish/submit 的 media_id）→ 已发表
```

正文 HTML 里的图写**本地绝对路径**即可，`draft-add` 会逐个上传、自动替换成微信图床 URL，不用自己先传素材。建完草稿核对回包的 `images_uploaded`，**为 0 就不发布**。

## 1.2 字段规范（官方 draft/add，逐条照抄）
| 字段 | 必填 | 规范 |
|---|---|---|
| `article_type` | 否 | 图文消息填 `"news"`（不填默认就是它） |
| `title` | **是** | **≤32 字**（接口按 32 字校验） |
| `author` | 否 | ≤16 字 |
| `digest` | 否 | ≤120 字；不填自动取正文前 54 字 |
| `content` | **是** | HTML；支持标签，必须少于 2 万字符且 <1M；**JS 会被去除**，图 URL 必须是上传接口返回的 |
| `content_source_url` | 否 | 「阅读原文」链接，≤1KB（保留 `https://www.fxai.site/`） |
| `thumb_media_id` | **是**（news） | 图文封面，**必须是永久 MediaID** |
| `need_open_comment` | 否 | 0 不打开（默认）/ 1 打开。**我们一律传 1** |
| `only_fans_can_comment` | 否 | 0 所有人可评论（默认）/ 1 仅粉丝 |
| `cover_info.crop_percent_list[]` | 否 | 封面裁剪：`ratio` 图文仅支持 `2.35_1`、`1_1`；`x1/y1/x2/y2` 是 0~1 的百分比坐标，左上 (0,0)、右下 (1,1)。**news 出图已是 2.35:1，默认不传** |

## 1.3 尺寸与规格（固定规格，跑脚本，不手工量）

🔴 **规格只在 `scripts/make_cover.py` / `scripts/export_figures.py` 的常量里写一次**：改常量即全局继承，每篇复用同一份脚本，尺寸按脚本输出取。

```bash
# 封面：出 封面.png 母版 + 封面.jpg 发布版（1152x512 写死）
python scripts/make_cover.py --title "配置低，|也能做 AI" \
  --subtitle "智能体 + 云端 GPU + 技能包" --motif cloud --out <项目>/image
# 正文图批量导 JPEG（1152 宽，q95 4:4:4）
python scripts/export_figures.py --dir <项目>/image
```

- `--title` 用 `|` 显式分行（最多 3 行），最后一行自动上强调色；超长自动缩字号。
- `--motif`：`cloud`（电脑+云）/ `blocks`（三层堆叠）/ `none`（几何）；`--accent` 换主色（默认品牌橙 `#ff6b35`）。
- `--tag ""` 去掉左上角小字。

**封面图**
- 展示区是 **2.35:1**，统一出 **1152×512**（脚本写死，别手改）。
- 格式 JPG/PNG，体积 2MB 内。
- 配一张横版封面源图放在 `image/`，标题行下面的正文第一屏就是它。

**正文图**
- 微信正文图片（uploadimage）仅支持 jpg/png，单张 ≤1MB，不占用永久素材库配额。
- 发布版一律走 `export_figures.py`：宽度 1152、`quality=95`、`subsample=0`（4:4:4）、`optimize=True`，单张 ≤1.5MB（超了脚本会告警）。
- PNG 母版保留在 `image/`，另出一份 jpg 给公众号用。

**生成侧（ComfyUI）**
- 工作流宽高 512~4096 且 32 的倍数。

## 1.4 正文 HTML 规范
- **必须是 HTML 文件**，内联样式；微信会过滤 `<script>` 和外链 CSS。
- 图居中、图注灰色小字；段与段之间加 1px `#eee` 分隔线。
- **配图粒度 = 小节**（不是自然段）：每个小节至少一张，落在该节最需要图示的位置；配图服务于内容，逐段铺图属凑数（见 SKILL.md -3 节）。
- 文末统一署名：`— 文章内容及图片皆由凤希AI生成 —`。

模板：

```html
<section style="text-align:center;margin:0 0 10px 0;">
<img src="<你的项目>/image/<图>.jpg" style="max-width:100%;height:auto;vertical-align:top;" />
<div style="margin-top:8px;font-size:13px;color:#999;line-height:1.7;">图注，≤14 字</div>
</section>
<section style="height:1px;background:#eee;margin:28px 0;"></section>
<section style="text-align:center;margin:0 0 10px 0;">
<div style="font-size:13px;color:#bbb;line-height:1.7;margin-top:34px;">— 文章内容及图片皆由凤希AI生成 —</div>
</section>
```

## 1.5 命令

```bash
# 建图文草稿（正文里的本地图会自动上传）
python scripts/wx_api.py draft-add --title "标题" --content article/正文.html \
 --cover image/封面.jpg --digest "摘要" --author "凤希AI" --comments 1
# 拿返回的 media_id 发布【默认不跑这一条】
# 只有用户明确要求「发布 / 群发 / 推送 / 自动发」才执行，且先确认走 publish 还是 mass。
python scripts/wx_api.py publish --media-id <media_id>
```

## 改已有草稿（news）

`draft-update` 对 news / newspic 通吃，news 形态才需要 `--thumb <media_id>`。详见 `02-贴图消息newspic.md` 的「2.4b 修改已有草稿」——**draft/update 是「整体替换」不是「局部改字段」**，字段必须整套照抄再改要改的那一项。

**已发文章想改内容**：`publish-delete` 删掉再重发。
