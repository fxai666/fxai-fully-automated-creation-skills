# PACKAGE.md — fxai-novel-writing 打包清单

## 目录

| 路径 | 说明 |
|---|---|
| `SKILL.md` | 技能入口与路由 |
| `references/01-短篇小说规范.md` | 短篇规范（单文件 / 章节标题 / 字数 / 结构） |
| `references/02-长篇小说规范.md` | 长篇规范（目录 / 创作思路 / 逐章梗概 / 分章正文） |
| `references/03-人物画像规范.md` | 人物画像规范（字段模板） |
| `references/04-封面图与平台尺寸.md` | 封面与平台尺寸（提示词 / 调用生图） |
| `references/05-作品分类.md` | 作品分类（主分类 / 平台映射 / 档案字段） |
| `references/06-模型终检.md` | 模型终检（检测项 / 做法） |
| `references/07-创作方法论.md` | 创作方法论（钩子 / 节奏 / 反转 / 断章 / 叙述对话） |
| `references/08-签约验收.md` | 签约验收（番茄内容否决项 / 五项打分 / 验收报告） |
| `scripts/check_novel.py` | 验收脚本 |
| `scripts/check_signing.py` | 签约验收脚本（内容信号检查） |
| `scripts/reflow_paragraphs.py` | 段落重排（单句成段 / 一句一个 `---`） |
| `scripts/normalize_punct.py` | 标点与标记规范化（场景分隔符 / 标题标记 / 引号体例 / Markdown 标记 / 正文空格） |
| `scripts/crop_cover.py` | 封面按平台尺寸裁切 |

## 依赖技能

| 技能 | 用途 |
|---|---|
| `fxai-comfyui-image-gen` | 封面出图 |
| `fxai-workflow` | 生图底层客户端（间接依赖） |
| `fxai-common` | 公共函数（间接依赖） |
| `fxai-image-to-jpeg` | 封面转 JPG（按需） |

## 分发

随技能包分发；装包时纳入依赖清单，依赖技能与本技能目录同级。
