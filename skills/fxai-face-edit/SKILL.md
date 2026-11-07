---
name: fxai-face-edit
description: 人脸替换 / 修复 / 增强 / 整片角色替换：换脸、视频换脸、唇形同步、老照片修复与上色、人脸增强、背景移除、整片视频人脸精修（本机 ComfyUI 分段链）、整片视频换人换装（MiniMax H3 分段角色替换，保留原片动作与运镜）、全场景替换（人、场景、道具、配乐整套换掉）。当用户要求「把 A 的脸换成 B」「给这段视频换脸」「让这个人说这段话」「人脸更清晰一点」「整条片子精修一遍」「把这个视频里的人换成参考图那个人」「角色替换」「换掉主角」「全场景替换 / 换全部场景 / 场景道具都换掉」「主体替换 / 仅替换主体 / 只换主体」「删掉画面里某个人」「去掉另一个人」「只留一位」「删除指定人物」时使用。三条链路：FaceFusion Studio REST（云端，换脸 / 对嘴 / 单张图）、本机 ComfyUI 分段精修（只修脸）、本机 ComfyUI 分段角色替换（H3 换人 LoRA，身份与服装一起换）。只按地址发 HTTP，不启停服务。不做：生图配图（→fxai-comfyui-image-gen）、数字人口播（→fxai-digital-human-video）、建实例与开关机（→fxai-xiangongyun-deploy）。
agent_created: true
---

# 人脸替换 / 修复 / 增强

三条链路，先按「做什么」分派：

| 要做什么 | 走哪条 | 入口 |
|---|---|---|
| 换脸 / 对嘴 / 老照片 / 单张图增强 / 抠像 | 云端 FaceFusion REST | `scripts/face_edit.py` |
| 整片视频逐段精修（只修脸，不改身份与服装） | 本机 ComfyUI 分段精修链 | `scripts/face_restore.py` |
| 整片视频**换人**（身份 + 服装 + 发型一起换，保留原动作与运镜） | 本机 ComfyUI 分段角色替换链 | `scripts/char_swap.py` |

云端链路：上传 → 选处理器 → 提交 → 等终态 → 下载；本机链见 `references/05`、`06` 与铁律 6。

## 替换工作流模式与按需规则（具名注册）

按名字路由：模式名写进 `需求.工作流模式`；新增一项在此登记一行 + `references/` 加一册 `NN-<名字>.md`。

| 类型 | 名字 | 触发词 | 规则册 |
|---|---|---|---|
| 模式 | 默认角色替换 | 换人、角色替换、换套衣服 | `references/06` |
| 模式 | 全场景替换 | 全场景替换、换全部场景、场景道具都换掉、按原片动作复刻并换景 | `references/08`（含纯器乐配乐，必做） |
| 模式 | 主体替换 | 主体替换、仅替换主体、只换主体、只换这一个、场景不动只换主体 | `references/10`（场景/机位/画质/音轨一律不动） |
| 按需规则 | 指定人物删除 | 删掉画面里某个人、去掉另一个人、只留一位、删除指定人物 | `references/09`（挂接到任一模式） |

模式口径见 `references/08`、`references/10` 第一节；未写明模式名时按需求判定。按需规则与模式叠加执行，不写进 `需求.工作流模式`；删谁留谁写进 `需求.人物改写`。

## 本技能文件清单（先读这里，再决定读哪册）

| 文件 | 什么时候读 |
|---|---|
| `references/01-环境与路由.md` | 开工定地址、按名字定位实例、素材库复用 |
| `references/02-任务与参数.md` | 选处理器、填素材位、调高级参数 |
| `references/03-指定换哪张脸.md` | 画面里有多个人、要指定换哪一张时 |
| `references/04-等结果与排障.md` | 等任务、看进度、产物黑屏 / 没声音 / 任务失败时 |
| `references/05-人脸精修ComfyUI链.md` | 走本机整片精修时：工作流、占位符、调参、接缝、排障 |
| `references/06-角色替换ComfyUI链.md` | 本机整片换人：LoRA、提示词模板、白名单主体绑定、分段接缝、排障 |
| `references/07-交付清单存储规范.md` | 建档 / 交付时：目录结构、清单字段、对比片画幅、命名 |
| `references/08-全场景替换工作流.md` | 走全场景替换时：拆镜头、分组模板、纯器乐配乐、验收 |
| `references/09-指定人物删除.md` | 要物理删除画面里某一位人物 / 只留一位时：源片预处理、验收、交付清单 |
| `references/10-主体替换工作流.md` | 走主体替换时：只换主体不动场景、模板选择、光学与特效保留段、验收 |
| `assets/全场景替换提示词-*.txt`、`assets/纯器乐配乐caption-*.txt` | 全场景替换选提示词模板与 BGM caption 档位时 |
| `assets/主体替换提示词-*.txt` | 主体替换选提示词模板时（默认＝人物、动物＝动物与静物） |

**确定本次要做什么之后，只读对应的那一册，其余一律不读。**

## 直接开跑（最常用）

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

两条本机链「分段 → 逐段重绘 → 合并」，交付物是合并后的整片：精修链 10 秒一段只修脸，换人链 4 秒一段连服装一起换。`--ref` / `--prompt` 取值、输入路径、模板与接缝见 `references/05`、`06`、`08`。

## 铁律速查（常驻）

1. 🔴 **只按地址发 HTTP，启停权在人**：服务探不通就把开机命令打回给人，不由 AI 开机。
2. 🔴 **素材先上传、拿服务端名字再提交**：`submit` 收的是素材库里的名字，不是本地路径；本地文件走 `upload` 或 `run`。
3. 🔴 **素材位按处理器定**：换脸 / 深度换脸必须给 `--source`（头像）；唇形同步必须给 `--audio` 且目标必须是视频；其余处理器只用目标。
4. 🔴 **多人画面先 `faces` 再指定**：`--face-pos N` 指定换哪一张；不给就是全部人脸都换。
5. 🔴 **处理器名中文英文都收**，但只能是 `02` 册表里登记的那些，写错了脚本报可用清单。
6. 🔴 **配置按「能力 + 云商」两级命名**：`face.*` 为能力入口，地址档位字段与取值顺序见 `references/01`；**换云或换机器只加一档，绝不覆盖老档**，切回来只改 `face.active`。
7. 🔴 **产物下载后过完整性校验**：PNG 查 IEND、JPEG 查文件头、MP4 查 ftyp 且体积过小判坏片。
8. 🔴 **等结果不长时间阻塞**：`wait` 只打印进度推进；收到「仍在运行 / 你会被自动通知」立刻停止查询，`block=true` 长等一律改用 `status` 单次查。
9. 🔴 **产物只落当前工作区**：`--out-dir` 缺省取当前目录，产物名由服务端 `output_name` 定。
10. **换脸涉及他人肖像**：默认只处理自有素材；他人照片当头像属素材权利自负，交付时说明来源。
11. 🔴 **整片视频先问片长再选链路**：整片精修走 `face_restore.py`（10 秒一段），换整个人走 `char_swap.py`（4 秒一段）；耗时随片长线性增长，开工前报预估。
12. 🔴 **本机两条链的输入都是「服务端可读路径」**：传的是 ComfyUI 那台机器上的视频路径，不是本机客户端路径；服务装在本机时两者一致。
13. 🔴 **角色替换换的是整个人**：脸、发型、肤色、服装、配饰一起换。「脸对、衣服别动」走 `face_restore.py`；`--ref` 多张只有第一张是 `<Picture 1>`。
14. 🔴 **配乐按 `references/08` 第五节**：任务要新配乐时每次重新生成，caption 写完整三段式且含 `Vocal Details`，无人声验收达标；沿用源片音轨属默认角色替换口径。

## 指针（跨技能依赖）

- 建实例、开机、关机、存镜像 → `fxai-xiangongyun-deploy`（`resolve` 子命令直接调它的 `xgy_api.py`）。
- 本机优先的路由口径、等后台任务纪律 → `fxai-common`。
- 规范体检、项目清单 → `fxai-standard`（`references/01-产出铁律.md`）。

## 项目清单（页面按它显示进度）

产物目录建好即建档、每完成一段更新；子命令见 fxai-standard `references/01-产出铁律.md`。

```bash
P=~/.workbuddy/skills/fxai-standard/scripts/fxai_project.py
python "$P" init  "<项目>" --title <名称> --category remake --skill fxai-face-edit
python "$P" stage "<项目>" --script 0 --image 1 --audio 0 --video 0
```
