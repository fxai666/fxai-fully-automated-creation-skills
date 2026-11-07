---
name: fxai-image-to-jpeg
description: 把 PNG/源图转成最高质量 JPEG（quality=95 + subsample=0/4:4:4 + optimize=True），用于公众号/文章配图、上传前压体积。跨项目公共技能，纯 PIL 实现，不依赖任何项目本地路径。当用户说"转 JPEG / 压成高清图 / 图片转 JPG 上传 / 公众号配图转格式"时使用。；不做：不做设计与排版，只转格式。
---

# 图片转最高质量 JPEG（fxai-image-to-jpeg）

## 本技能文件清单（先读这里，再决定读哪几册）

| 文件 | 什么时候读 |
|------|-----------|
| `SKILL.md` | 本文件：运行环境/用法（单文件/glob/目录）/调参 |
| `to_jpeg.py` | 转 JPEG 实现；按命令行调用，不读源码 |

**确定本次要做什么之后，只读对应的那一两册，其余一律不读。**

🔴 **转码含文字的图前必读 `fxai-standard/references/06-字体与素材授权.md`**：图上若有叠字 / 字幕，字体须来自 06 白名单（标题首选得意黑、正文/字幕首选 Noto Sans SC，微软雅黑随 Windows 预装亦可用），禁用商业字库。

把生成的 PNG 母版（或其他源图）转成公众号/文章可用的最高质量 JPEG。
核心参数：**quality=95 + subsample=0（4:4:4 无 chroma 子采样）+ optimize=True**，
体积压到 <2MB。PNG 母版默认保留不删。

## 运行环境

脚本用纯 PIL，跑在 WorkBuddy 的受管 Python venv 里（无需自行装包）：
```
python scripts/to_jpeg.py <输入图> <输出图>
```
> 用 WorkBuddy 自带 Python 跑即可；若你本地另装了 Python，确保已 `pip install pillow`。

## 用法

```
# 单文件
python to_jpeg.py --src <你的项目>/测试图/cover_song.png

# glob（一批）
python to_jpeg.py --src "<你的项目>/测试图/img*_song.png" --out <你的项目>/测试图

# 整个目录（目录下所有 png/webp/bmp/tif 都转）
python to_jpeg.py --src <你的项目>/测试图 --out <你的项目>/测试图/jpg

# 调参：压更小体积（4:2:0）/ 放宽体积告警
python to_jpeg.py --src "<你的项目>/测试图/img*.png" --subsample 2 --max-mb 1.5
```

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--src` | 必填 | 文件 / glob / 目录 |
| `--out` | 源同目录 | 输出目录 |
| `--quality` | 95 | JPEG 质量（1-100） |
| `--subsample` | 0 | 0=4:4:4 最高质量；2=4:2:0 更小 |
| `--no-optimize` | 关 | 默认 optimize=True |
| `--max-mb` | 2.0 | 超过则打印告警（公众号封面限制 2MB） |

## 约定

- 输出文件名 = 源文件名去后缀 + `.jpg`，与源同目录（除非 `--out` 指定）。
- PNG 母版不删；要删母版请脚本外自行处理。
- `Image.open().load()` 会触发完整解码：半张图/损坏 PNG 在此抛错，坏图在转码前就被拦下。
- 透明通道（RGBA）→ 转 RGB 后存 JPEG（JPEG 不支持 alpha）。
