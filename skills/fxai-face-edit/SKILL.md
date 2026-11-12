---
name: fxai-face-edit
description: 人脸替换 / 修复 / 增强 / 整片角色替换：换脸、唇形同步、老照片修复上色、人脸增强、背景移除、整片视频人脸精修（本机 ComfyUI 分段链）、整片视频换人换装（MiniMax H3 分段角色替换，保留原动作与运镜）、全场景替换（人、场景、道具、配乐整套换掉）。当用户要求「把 A 的脸换成 B」「给这段视频换脸」「让这个人说这段话」「人脸更清晰一点」「整条片子精修一遍」「把这个视频里的人换成参考图那个人」「角色替换」「换掉主角」「全场景替换 / 换全部场景 / 场景道具都换掉」「主体替换 / 仅替换主体 / 只换主体」「删掉画面里某个人」「去掉另一个人」「只留一位」「删除指定人物」时使用。三条链路：FaceFusion REST（云端，换脸 / 对嘴 / 单图）、本机分段精修（只修脸）、本机分段角色替换（H3 换人 LoRA，身份与服装一起换）。只按地址发 HTTP，不启停服务。不做：生图配图（→fxai-comfyui-image-gen）、数字人口播（→fxai-digital-human-video）、建实例与开关机（→fxai-xiangongyun-deploy）。
agent_created: true
---

# 人脸替换 / 修复 / 增强

三条链路，先按「做什么」分派：

| 要做什么 | 走哪条 | 入口 |
|---|---|---|
| 换脸 / 对嘴 / 老照片 / 单张图增强 / 抠像 | 云端 FaceFusion REST | `scripts/face_edit.py` |
| 整片视频逐段精修（只修脸，不改身份与服装） | 本机 ComfyUI 分段精修链 | `scripts/face_restore.py` |
| 整片视频**换人**（身份 + 服装 + 发型一起换，保留原动作与运镜） | 本机 ComfyUI 分段角色替换链 | `scripts/char_swap.py` |

云端链五步与处理器表见 `references/02`；本机链见 `05`、`06`。

## 工作流模式与按需规则（具名注册）

按名字路由：模式名写进 `需求.工作流模式`；新增一项登记一行 + `references/` 加一册。

| 类型 | 名字 | 触发词 | 规则册 |
|---|---|---|---|
| 模式 | 默认角色替换 | 换人、角色替换、换套衣服 | `references/06` |
| 模式 | 全场景替换 | 全场景替换、换全部场景、场景道具都换掉、按原片动作复刻并换景 | `references/08`（含配乐，必做） |
| 模式 | 主体替换 | 主体替换、仅替换主体、只换主体、只换这一个、场景不动只换主体 | `references/10`（场景/机位/画质/音轨不动） |
| 按需规则 | 指定人物删除 | 删掉画面里某个人、去掉另一个人、只留一位、删除指定人物 | `references/09`（挂接到任一模式） |

模式口径见 `references/08`、`10`；未写模式名按需求判定。按需规则与模式叠加，不写进 `需求.工作流模式`；删谁留谁写进 `需求.人物改写`。

## 本技能文件清单（先读这里，再决定读哪册）

| 文件 | 什么时候读 |
|---|---|
| `references/01-环境与路由.md` | 开工定地址、定位实例、素材库复用 |
| `references/02-任务与参数.md` | 选处理器、填素材位、调高级参数 |
| `references/03-指定换哪张脸.md` | 画面里有多个人、要指定换哪一张时 |
| `references/04-等结果与排障.md` | 等任务、看进度、产物黑屏 / 没声音 / 任务失败时 |
| `references/05-人脸精修ComfyUI链.md` | 走本机整片精修时：工作流、占位符、调参、接缝、排障 |
| `references/06-角色替换ComfyUI链.md` | 本机整片换人：LoRA、提示词模板、主体绑定、接缝、排障 |
| `references/07-交付清单存储规范.md` | 建档 / 交付：目录、清单字段、对比片画幅、命名 |
| `references/08-全场景替换工作流.md` | 全场景替换：拆镜头、分组模板、配乐、验收 |
| `references/09-指定人物删除.md` | 删人物 / 只留一位：源片预处理、验收、清单 |
| `references/10-主体替换工作流.md` | 主体替换：只换主体不动场景、模板、保留段、验收 |
| `assets/全场景替换提示词-*.txt`、`assets/纯器乐配乐caption-*.txt` | 全场景替换选提示词模板与 BGM caption 档位时 |
| `assets/主体替换提示词-*.txt` | 主体替换选提示词模板时（默认＝人物、动物＝动物与静物） |

**确定本次要做什么之后，只读对应的那一册，其余一律不读。**

## 直接开跑

```bash
P=~/.workbuddy/skills/fxai-face-edit/scripts/face_edit.py
python "$P" run --target-file 目标.mp4 --source-file 头像.jpg \
                --processors 换脸 --out-dir .
python "$P" faces --target 目标.mp4 --frame 0
```

```bash
R=~/.workbuddy/skills/fxai-face-edit/scripts/face_restore.py
S=~/.workbuddy/skills/fxai-face-edit/scripts/char_swap.py
python "$R" 源视频.mp4 --out-dir .                      # 整片精修（只修脸）
python "$S" 源视频.mp4 --ref avatar/000.png --out-dir . # 整片换人
python "$S" 源视频.mp4 --start-index 0 --end-index 2    # 试片：只跑 3 段
```

两条本机链「分段 → 逐段重绘 → 合并」，交付物是整片：精修链 10 秒一段只修脸，换人链 4 秒一段连服装一起换。`--ref` / `--prompt`、路径、模板与接缝见 `references/05`、`06`、`08`。

## 铁律速查（常驻）

1. 🔴 **只按地址发 HTTP，启停权在人**：服务探不通就把开机命令打回给人。
2. 🔴 **素材先上传、拿服务端名字再提交**：`submit` 收素材库名字；本地文件走 `upload` 或 `run`。
3. 🔴 **素材位按处理器定**：换脸 / 深度换脸必须给 `--source`；唇形同步必须给 `--audio` 且目标是视频；其余只用目标。
4. 🔴 **多人画面先 `faces` 再指定**：`--face-pos N` 指定换哪张；不给即全部人脸。
5. 🔴 **处理器名中英文都收**，只认 `02` 册登记项，写错报可用清单。
6. 🔴 **配置按「能力 + 云商」两级命名**：`face.*` 为能力入口，字段与取值顺序见 `references/01`；换云只加一档，切回只改 `face.active`。
7. 🔴 **产物下载后校验完整性**：PNG 查 IEND、JPEG 查文件头、MP4 查 ftyp；体积过小判坏片。
8. 🔴 **等结果不长时间阻塞**：收到「仍在运行 / 你会被自动通知」即停，`block=true` 改用 `status` 单次查。
9. 🔴 **产物只落当前工作区**：`--out-dir` 缺省取当前目录，产物名由 `output_name` 定。
10. **他人肖像**：默认只处理自有素材；他人照片当头像属权利自负，交付时说明来源。
11. 🔴 **整片视频先问片长再选链路**：精修走 `face_restore.py`，换人走 `char_swap.py`；开工前报耗时预估。
12. 🔴 **本机两条链的输入都是「服务端可读路径」**：传 ComfyUI 那台机器上的路径；服务装在本机时两者一致。
13. 🔴 **角色替换换的是整个人**：脸、发型、肤色、服装、配饰一起换。「脸对、衣服别动」走 `face_restore.py`；`--ref` 多张只认第一张。
14. 🔴 **配乐按 `references/08` 第五节**：要新配乐每次重新生成，caption 写完整三段式含 `Vocal Details` 并过无人声验收；沿用源片音轨为默认口径。
15. 🔴 **分辨率按显存档位给默认，不追源片长边**：`char_swap.py` 不传 `--max-side` 时由 `default_max_side()` 取档（≤16G→960 / 其余→1280）；**不得因源片分辨率高就抬档**，也不得写回 1080。

## 指针（跨技能依赖）

- 建实例、开关机、存镜像 → `fxai-xiangongyun-deploy`（`resolve` 调它的 `xgy_api.py`）。
- 本机优先的路由口径、等后台任务纪律 → `fxai-common`。
- 规范体检、项目清单 → `fxai-standard`（`references/01-产出铁律.md`）。

## 项目清单（页面按它显示进度）

产物目录建好即建档、每段更新；子命令见 fxai-standard `references/01-产出铁律.md`。

```bash
P=~/.workbuddy/skills/fxai-standard/scripts/fxai_project.py
python "$P" init  "<项目>" --title <名称> --category remake --skill fxai-face-edit
python "$P" stage "<项目>" --script 0 --image 1 --audio 0 --video 0
```
