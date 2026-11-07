---
name: fxai-comfyui-image-gen
description: 本地 ComfyUI 文生图能力（千问2.1 模板，原生中文提示词）。当用户要求在对话里生成图片、配图、画一张图、出张图、做封面图、用中文提示词生图、给文章/视频/设计配视觉素材时使用。生成的是用户自有角色形象的图（参考图 000 女 / 001 男，规避他人肖像侵权），产物经 HTTP 下载到本地并返回 PNG 路径。仅依赖 ComfyUI HTTP 服务（由 comfy_url 指定，本地或远端均可），提交/探测/下载全部经 HTTP 接口完成，绝不读取 ComfyUI 本机目录；无常驻进程。 # std:allow
---

# 本地 ComfyUI 生图（中文提示词）

## 本技能文件清单（先读这里，再决定读哪几册）

| 文件 | 什么时候读 |
|------|-----------|
| `SKILL.md` | 本文件：前置条件/本机优先/仙宫云开关机/三种调用方式/参数/长期约定/等结果 |
| `image_gen.py` | generate_image() 实现；按 SKILL.md 调，改行为才读对应函数 |
| `feed_screen.py` | 视频信息流（界面模板）：spec → 六段骨架提示词 → 提交 |
| `references/01-环境与路由.md` | 开工前查前置条件、本机优先路由、仙宫云自动开关机 |
| `references/02-调用与参数.md` | 查怎么调 generate_image、参数默认值、产物落哪 |
| `references/03-穿搭校验细则.md` | 人物图被 ValueError 拦下时查校验词与放行条件 |
| `references/04-批量等结果细则.md` | 一次出多张图时查两种等法与「界面没动静」判据 |
| `references/05-长期约定.md` | 写提示词前、传参考图前、遇穿搭校验或 `<imageN>` 报错时查红线判据 |
| `references/06-视频信息流.md` | 要做视频信息流这类界面模板（播放页 / 直播页 / 笔记详情 / 商品详情，含状态栏 / 导航栏 / 互动数 / 底部导航）时查段序、UI 五区写法、尺寸表与验收项 |

把"中文提示词"提交到本机部署的 ComfyUI（千问2.1 图片处理工作流），生成 PNG 并返回本地路径。
核心逻辑在 `image_gen.py`：`generate_image` 只做参数映射，真正的「占位符工作流提交 → 轮询 → 经 HTTP 下载」
由共享客户端 `fxai-workflow/comfy_client.py` 统一完成（与 fxai-scene-gen / fxai-fourview-gen /
fxai-mv-pipeline 共用同一套）。统一使用占位符模式（fxai_xxx 占位符由 comfy_client 注入）。

## 🚨 开工前置（先读，再动手）

0. 🔴 **开工第一步：读 `fxai-workflow/SKILL.md` 的「🚨 ComfyUI 服务端模式铁律」**——铁律写在被依赖件的 SKILL.md 里，不在 `comfy_client.py`（只取函数必漏）。
   核心：ComfyUI 由「凤希本地AI控制台」／启动器启停，本技能只按地址发 HTTP；本机 8188 探不通 → 走云或提示人启动；资源有无只认服务端接口。

## 🔴 字体授权门禁（出字前必读）

🔴 凡在本技能产物（封面 / 配图）上出现文字，出字前必须先读 `fxai-standard/references/06-字体与素材授权.md`：字体只从 06 白名单选（本机首选 Noto Sans SC / OFL，微软雅黑随 Windows 预装亦可用），禁用华文等商业字库；交付记录字体名与协议。

## 前置条件

其余四条前置条件见 references/01-环境与路由.md。

## 🔴 本机优先，其次才是仙宫云

路由细则与云端开关机见 references/01-环境与路由.md。

## 仙宫云：自动开机 + 收工关机

## 三种调用方式

### 方式 A：直接 import（对话内脚本首选）

见 02 册。

### 方式 B：CLI（Bash 直接跑，连 ComfyUI 需放行沙箱网络）

见 02 册。

### 方式 C：视频信息流（界面模板）

```bash
python feed_screen.py <spec.json> --tag <项目>_<界面> --save-dir <项目目录>
python feed_screen.py <spec.json> --print-only   # 只看拼出的提示词
```

段序、UI 五区写法、尺寸表与验收项见 `references/06-视频信息流.md`。

## 参数说明（generate_image）

## 长期约定（务必遵守，细则见 `references/05-长期约定.md`）

1. 🔴 **参考图**：人物一律用自有角色 000/001（`role=woman/man/both` 或自定义相对路径）；封面/海报类用该作品自己的角色形象图，不用全局头像库 `avatar/000、001`。
2. 🔴 **提示词**：中文直达图像，无需英文；画面里有人就必须写【穿搭/服装】+【头型/发型】，`check_outfit()` 硬校验。
3. 🔴 **多参考图**：传 N 张就必须逐个写 `<imageN>`，缺一个即 ValueError（`check_image_refs()`）；编号 = 参考图顺序，男主在前。
4. **着装红线**：胸部 / 私密部位保持遮挡，力度取「透而不露」。
5. **产物校验**：HTTP 下载后 `png_ok` 校验含 IEND，不完整即重试。本技能只管生图，发布交 `fxai-weixin-gzh-publisher`。

## 输出目录

产物落点见 references/02-调用与参数.md。

## 等结果：走全局广播监听（已内置）

`generate_image` 内部走 `comfy_client.submit_workflow`，提交后由**广播监听**等结果：
日志里 `queued <pid>` 之后是逐条实时进度，完成后自动取 url 并下载返回本地路径——
**等结果就用这条广播链路**（自己 sleep 轮询 `/history` 或 `/fxai/tasks/result` 会拿到重复状态）。
排队等 GPU 时监听会查 `/queue` 确认「还在队列里」并继续等。手动查：
`comfy_watch.py status <pid前缀>` / `comfy_watch.py top`（技能 `fxai-comfyui-watcher`）。

### 🔴 批量生图怎么等

收到「仍在运行 / 会被自动通知」后立刻停止查询；`block=true` 只在剩余 ≤60 秒时用。task_id 三态与「界面没动静≠卡住」判据见 `references/04-批量等结果细则.md`。

## 验证

改完 `image_gen.py` 后跑：`python image_gen.py "测试，纯场景山水" --role "" --tag smoke_test`，
确认返回有效 PNG 路径且文件完整（>100 字节、含 IEND）。

**确定本次要做什么之后，只读对应的那一两册，其余一律不读。**


## 项目清单（页面按它显示进度）

产物目录建好即建档、每完成一段更新；结构见 fxai-standard 01 册。

```bash
P=~/.workbuddy/skills/fxai-standard/scripts/fxai_project.py
python "$P" init  "<项目>" --title <名称> --category image --skill fxai-comfyui-image-gen
python "$P" stage "<项目>" --script 2 --image 1 --audio 0 --video 0
```
