# 判据与解析规则（本技能自身）

只记本技能自身的执行规则与判据；项目侧笔记写进项目自己的 `.workbuddy/memory/`。

## 老写法清单（老项目回扫会遇到）

| 写法 | 例子 | 脚本行为 |
|------|------|----------|
| 标准式 | `## 镜头N · 名（场景 · Ns）` | 全项硬查，缺小节 = ERROR |
| 老标题无秒数 | `### 镜头N｜名` | legacy：只查镜数/序号，缺小节降 WARN |
| 老标题带秒数 | `### 镜头N（Ns）名 — 场景` | 同上，但能算出总时长 |
| markdown 表格 | `\| 镜 \| 时长 \| ... \|` 里带 `Ns` 列 | legacy |
| 表格提纲 | `## 二、分镜表（10 镜 · 约 64s）` 无行标题 | 解析不到 → 报 ERROR |

老格式允许第 0 镜（片头镜）起编号，序号校验按 `range(seq[0], n+seq[0])`。

## 常驻误报（人工确认即可）

- `server_index`、`shortdrama_xxx`、`H3`、`shots` 这类**技术串**走 TECH_WORDS 白名单，不算中英混排。
- 判据改动只动脚本，规范条目写进对应分册。

## check_storyboard.py 解析规则

- 标题嵌套全角括号也能解析：`（(.+?) ·?\s*(\d+)s）`。
- 无秒数老格式按「无秒数（legacy）」降级校验：遍历 LOOSE_RES 取第一个 ≥3 命中的写法，秒数全有或全无。

## 标准格式六项硬查判据（自己新写的集）

| 项 | 判据 |
|----|------|
| 镜数 | ≥7，10 镜为常态 |
| 序号 | 连续（允许从第 0 镜"片头镜"起） |
| 总时长 | ≥ `--min-seconds`（默认 60），达标才准往下走 |
| 钩子在位 | 第 1 镜时长 >0 |
| 小节 | 资产清单 + 本集技术注意两份都在 |
| 写作禁令 | 中英混排（技术词白名单内放过） |

**全绿才进渲染。** 已知误报：`server_index`、`shortdrama_xxx` 这类技术串算白名单，不报中英混排。

## 提交前自检（run_*.py 固定写法，在 `from drama_pipeline import ...` 之前）

```python
import comfy_client as C
C.prefer_local(quiet=True)
import drama_pipeline as _D
_D.COMFY = C.COMFY
_D.check_comfyui = lambda timeout=5: C.check_comfyui(timeout=timeout)
_D.require_comfyui = C.require_comfyui
```

- `drama_pipeline.COMFY` 是 import 时定死的模块常量，`prefer_local()` 改的是 `comfy_client.COMFY`，须手动同步 `_D.COMFY`；`check_comfyui` 默认走系统代理会探不通本机，须换成关代理版本。
- 参考图探测用令牌形式 + `down_base()`，拼法 `down_base() + "?url=image|" + rel`；判据：HTTP 200 且 Content-Type=image/png、字节数 > 0 才算就位。

## 后台跑长任务

- 长跑脚本用 `python -u`（无缓冲）重定向到 log。
- 抓 pid 之后盯 `/queue`（`queue_running`/`queue_pending`）判断是否入队；`/history` 只有生图任务（视频走 `/fxai/tasks/result`，不写 history）。
- 提交成功标志：`PROMPT_ID <uuid>` 落盘 + `epN_pid.txt` 写出；没有 pid 文件 = 没入队。

## 通用性验证

- 新增项目跑一次双项目回归。

## 六段式交付文档（export_shot_prompts.py）

- 每集必交：默认落 `<run 同目录>/镜头六段式提示词.md`。
- 用法：`python export_shot_prompts.py --run "<本集>/run_x_epN.py" [-o 输出.md]`。
- 导出文档每段正文包进围栏代码块（` ```text `），让 H3 标签按原样输出、不被 markdown 当 HTML 吞掉；参考图那行用反引号包中文名。
- 项目里统一调技能里那份，不另存副本。

## H3 锁脸（已在 SKILL.md「三之四」）

- 同框参考图数量过多会互相污染 → `images` 把主角排第一；脸锁定句插进摘要 + 详细描述各一次；显式否定"不是网红脸/明星脸"。
- 「酒液」会被画成红色液体/血 → 写死"清冽米酒、透明发白的酒珠"，颜色限定在透明/发白这一档；「旧刀疤」会被画成鲜红新伤 → 写"陈年浅色痕迹、不红肿、无血"。
