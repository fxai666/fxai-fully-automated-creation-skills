---
name: fxai-rvc-convert
description: 音色转换 / 换声（SVC·RVC）：保留原曲伴奏、旋律、节奏，只把演唱者音色换成目标音色；也含音色训练（用素材练自己的声音）。当用户说「把这首歌换成 X 的声音」「换个声音唱」「音色转换 / 换声 / 翻唱换人」「RVC / SVC」「练一个音色 / 克隆音色 / 用我的声音唱」时使用。任务提交到本机常驻服务（端口 8187），产物下载回本机并返回路径。只按地址发 HTTP，不启停服务。不做：人声分离单独出轨（→fxai-vocal-separate）、文本转语音配音（→fxai-voiceover）、MV 合成与成片（→fxai-mv-pipeline）。
agent_created: true
---

# 音色转换 / 换声

一句话链路：**上传原曲 → 分离人声 → 换音色 → 等响混音 → 下载成品**。
服务常驻 `0.0.0.0:8187`，模型懒加载、空闲自动卸载；技能只发 HTTP，不碰进程与显存。

## 本技能文件清单（先读这里，再决定读哪册）

| 文件 | 什么时候读 |
|---|---|
| `references/01-环境与路由.md` | 开工第一步：全局配置怎么填、地址从哪来、服务没起怎么办、显存何时释放 |
| `references/02-接口手册.md` | 自己写脚本调服务、查字段含义、对错误码 |
| `references/03-换声与调参.md` | 选参数、调听感、判断该不该跳过分离、伴奏比例 |
| `references/04-音色训练.md` | 练新音色：素材要求、epoch、耗时、底模、产物落点 |
| `references/05-等结果与排障.md` | 任务卡住 / 失败 / 成品不对 / 显存不放 |
| `scripts/rvc_client.py` | 全部子命令的实现；改调用行为时才读对应函数 |

**确定本次要做什么之后，只读对应的那一册，其余一律不读。**

## 直接开跑（最常用）

```bash
P=<技能目录>/scripts/rvc_client.py

python "$P" health                                  # 探活 + 显存 + 队列
python "$P" voices                                  # 音色清单（id / 中文名 / 索引）
python "$P" convert <音频> --voice <音色id> --out <目录>          # 上传 → 等 → 下载
python "$P" convert <音频> --voice <音色id> --pitch -12 --out <目录>   # 跨性别
python "$P" convert <干声.wav> --voice <音色id> --vocal-only --out <目录>  # 已是干声
python "$P" job <id> --wait                         # 跟随进度
python "$P" jobs --limit 20                         # 作品库
python "$P" download <id> --out <目录>              # 补下载
python "$P" train <素材...> --name <显示名>          # 练音色（素材自动归库）
python "$P" sets                                    # 训练素材库（可复用，不用重传）
python "$P" train --set <素材库id> --name <显示名>    # 用素材库重训
python "$P" set-new <库名> --owner <归属>            # 建空素材库
python "$P" set-move <from_id> <to_id> --items 1,3   # 跨库移动素材
python "$P" ws                                      # 训练中间文件占多少（占盘大头）
python "$P" ws-clean <slug>                         # 清中间文件，不影响已训好的音色
python "$P" unload                                  # 释放显存
```

`convert` 返回成品绝对路径。只要相对地址与参数字典时加 `--json`。

## 铁律速查（常驻）

1. 🔴 **全程 HTTP，启停权在人**：服务探不通就把启动命令交回给人执行，等 `health` 返回 `healthy` 再继续；技能不代管开关机、不加载模型。
2. 🔴 **地址按配置解析**：`--url` > `FXAI_RVC_URL` > `rvc.prefer_local` 决 `local_url` / `cloud_url` 先后 > 缺省本机。主机端口一律从配置取，脚本不拼地址。
3. 🔴 **提交前先校音色**：`voices` 取 `id` 再提交；中文名查不到时改用 ASCII `id`，磁盘音色名一律 ASCII。
4. 🔴 **干声才跳过分离**：素材自带伴奏就让它走①分离（`vocal_only=0`）；传的已是干净干声才置 `vocal_only=1`，整条链路快一半。
5. 🔴 **训练与转换互斥**：转换进行中提交训练返回 `409`；训练占用显卡时提交转换会自动排队，`stage_text` 显示「等待训练结束」，不重复提交。
5.1 🔴 **多人同时提交＝排队，不得为了"并发"多开**：提交即返回 `job_id`（异步任务，不挂长连接，
    永远不断线），服务端固定 1 个 worker 顺序处理。实测（60 秒歌 / 16G 卡）两个进程各跑一份模型、
    同时换声，墙钟 **52.9s**，而单发只要 17s —— 是单发的 **3.11 倍，比排队（约 2 倍）还差**，
    因为 RVC + 分离模型两份抢显存和算力（显存峰值 12G）。另外 `VoiceConverter` 是进程内单例、
    `convert_audio` 每次都会 `get_vc()` 切当前音色，多线程共用必然**串音色**（出来的音频是别人的声音且不报错）。
    真要多并发只能多卡 / 多机各起一个实例。
6. 🔴 **等终态按状态轮询**：2 秒一次（训练 5 秒一次），上限 `rvc.timeout`（默认 900s）；超时只报「仍在运行 + 进度 + job_id」。
7. 🔴 **交付前核对成品**：`status=done` 且文件能被播放器读出时长；0 字节、半截、时长对不上一律重跑，不当成果交。
8. 🔴 **分离后端默认 `melband`**：内置 Mel-Band RoFormer，离线自包含；`skill` 仅在外层 ComfyUI 在线时用。
9. 🔴 **产物只落当前工作区**：成品归 `<工作区>/<项目名>/`，中间件进 `_tmp/`；`models/voices/` 与 `train/` 属服务侧数据，按需读写。
10. **参数越界由服务端夹取**，客户端只做取值校验；`pitch` 超 `±24`、`index_rate` 超 `0..1` 不必自行截断。
11. 🔴 **重训先查素材库**：同一批素材要再练一轮，先 `sets` 拿 id 再 `train --set`，
    不让用户重传一遍；换 epoch / batch 重训是最常见的需求。素材库按「一次训练」分开存，
    不同批次的素材不往同一个库里混，否则哪一次都复现不了。
12. 🔴 **没有游离素材**：素材一律先入库再训练。`POST /api/train` 要么给 `set_id`，
    要么给文件 + `set_name`；别在设计里留"临时上传、不存"这条路。整理素材（改名 / 换库）
    去 `/sets` 页面或 `set-move`，别让用户重新传一遍。
13. 🔴 **训练完提醒清中间文件**：`engine/logs/<slug>/` 一轮 1~2GB，是占盘大头。
    先 `ws` 看占用，再告诉用户可清、问一句是否清理（`ws-clean` 或 `/train` 右栏卡片），
    未问即删、放着不管都算不合格。删前确认它不是 `running` 状态；
    内置的 `mute` / `mute_spin` / `mute_spin-v2` / `reference` 永不删（`ws` 不会列出它们）。
    用户嫌每次都要点时，可建议开「训练完自动清理」（`POST /api/train/prefs` `{auto_clean:true}`，
    默认关）；开了就没现场可查，排障期别开。

## 项目清单（页面按它显示进度）

产物目录建好即建档、每完成一段更新；结构与全部子命令见 `fxai-standard/references/01-产出铁律.md`。

```bash
P=<技能目录>/../fxai-standard/scripts/fxai_project.py
python "$P" init  "<项目>" --title <名称> --category music --skill fxai-rvc-convert
python "$P" stage "<项目>" --audio 1 --video 2
```

## 指针（跨技能依赖）

- 公共配置读取、本机优先路由口径、后台任务纪律 → `fxai-common`。
- 单独抽人声 / 伴奏、只要分离结果 → `fxai-vocal-separate`。
- 换完音色之后做 MV / 成片 → `fxai-mv-pipeline`；配字幕 → `fxai-lyric-subtitle`。
- 文本转语音（不是换声）→ `fxai-voiceover`。
- 完整接口契约（字段级全表、每个接口的调用片段）：线上 <https://www.fxai.site/tool/rvc/svc-api.html>；
  机器可读 <https://www.fxai.site/tool/rvc/openapi.json>（OpenAPI 3.1）。
- 网页入口：服务自带工作台 <http://127.0.0.1:8187>（含音色训练页 `/train`、作品库 `/library`）；
  线上工具页 <https://www.fxai.site/tool/rvc/>，同一条链路，供人手动操作与试听。
- 规范体检、项目清单 → `fxai-standard`。
