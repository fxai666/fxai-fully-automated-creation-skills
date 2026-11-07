---
name: fxai-short-drama
description: 短剧自动化生成管线（用户级，可分享整合包）。当用户说「做一集短剧/自动生成短剧/用双采版工作流出短剧/出新一集/批量生成多镜头短剧/短剧发布交付物料」时使用。流程：剧本→分镜→**（打斗镜先向 fxai-combat-design 取招式术语）**→角色四视图+场景图（复用 fxai-fourview-gen/fxai-scene-gen）→H3六段式对白提示词（**打斗镜触发词由 build_lines_data 自动挂载**）→组装 lines_data→提交双采版 Ref2VA 工作流批量生成→自动合并→字幕烧录→多平台交付。不做：MV/唱歌视频（→fxai-mv-pipeline）、口播配音（→fxai-digital-human-video）、纯生图（→fxai-comfyui-image-gen）、剧本创作（→fxai-short-drama-script）。
---

# 短剧自动化管线（fxai-short-drama）

核心模块 `drama_pipeline.py`（唯一真相源）；视频工作流复用 `fxai-workflow/shortdrama-minimax-filmdrama-api.json`（Ref2VA 多参考 + For 循环批量 + 视频合并）；角色四视图/场景图复用 `fxai-fourview-gen` / `fxai-scene-gen`。

## 本技能文件清单（先读这里，再决定读哪册）
| 文件 | 什么时候读 |
|---|---|
| `references/01-提示词规范.md` | 写镜头提示词前（数据规范、六段式、排版、六要素） |
| `references/02-自查门禁.md` | 写完提示词、提交前（三轮 `_audit.py` 判据） |
| `references/03-字幕与对齐.md` | 做字幕时（抑制、`<d>` 写法、烧录、镜头锚定） |
| `references/04-分段与时长.md` | 算时长、判段位、定重渲镜时（H3 公式、段映射表） |
| `references/05-项目档案与资源.md` | 建项目 / 新一集、配目录、搬资源、换服务器 |
| `references/06-生成与交付.md` | 提交渲染、单镜重生成、发布物料（含封面）、验收 |

**确定本次要做什么之后，只读对应的那一两册，其余一律不读。**

## 铁律速查（常驻）

1. **单集 ≥60 秒、单镜 3–10 秒**（10 是 H3 上限）；镜数不预设、台词一字不删，一句装不下就拆镜（02 册）。
   🔴 **提交前必跑 `check_lines_against_storyboard.py`**：`<d>` 台词与「完整分镜脚本.md」**逐字一致**，
   不符即报错，处理只有**拆分镜**。
2. **结束索引必须 `-1`**（`build_workflow` 已强制写入）。
3. **主体引用唯一写法 `<Subject N> (SN)`**，代词换主体编号；场景也是主体（01 册）。
4. **外貌句逐字照抄生图 prompt**（`project_def.characters[].prompt`），可缩略（01 册）。
5. **提交前必跑 `_audit.py` 三轮自查**，全绿才提交（`audit_gate()`）；改自检器做负向验证（02 册）。
6. `suppress_subtitles` **默认开**（不传即开），关要显式 `False`；范围＝对白字幕条 + 乱码文字（03 册）。
7. 🔴 **单镜返修优先**：某一镜不对只重生成那镜 —— `drama_pipeline.py --single-shot N`（1 起，
   内部设为 开始=结束=N-1）。整集几十分钟，单镜几分钟。判定与定位走 `fxai-common/rework.py`。
   ⚠️ 产物是**该镜裸片**，双采版 merger 不会自动替换，必须 `rework.splice_segment(成片, 裸片, 镜号)` 拼回。
   多镜一次提交走 `../fxai-common/rework_batch.py --shots 3,7,9`（串行，全部渲完才返回）；
   服务端每渲完一段都会合并回完整成片 → 并行互相覆盖，全部渲完才能烧字幕 / 交付。

8. **视线写落点**（01 册）。
9. **资源存在性只走接口**：`GET /fxai/down/file` 或提交被 `queued`（06 册）。
10. **上传走 `comfy_client` 的 opener（关系统代理）**，手写 multipart 会被代理改包报 500（05 册）。
11. **长任务用 `comfy_watch.py wait <pid>`**；同一 `prompt_id` 只提交一次（06 册）。
12. **whisper 只借时间，文字用台词真值**（短剧 `<d>` / MV 歌词）；中文 ASR 只做镜头级锚定。
13. **SRT 路径用相对 basename + `cwd`**，ffmpeg `subtitles=` 只收文件名（03 册）。
14. **封面必交付**：全剧一张 + 每集一张，全部走 `fxai-comfyui-image-gen`；`COVER_PROMPT` / `COVER_ROLE`（+可选 `COVER_HOOK`）写进该集 `run_qsl1_epN.py` 顶部，缺则 `_audit` 第 1 轮硬卡（06 册）。
   设计规范与质检门禁见 `fxai-poster`：类型与必备要素按 `08` 册、三道门按 `09` 册、提示词按 `10` 册，封面出图后跑 `qc.py post` 过门。
15. **尺寸 / 音频模式 / 服务端目录 / 路径占位 / 打斗取词 / 封面模板路由 / 按集落盘**：判据见 06 册「交付铁律」节。

## 开工前

读 `{data_dir}/经验库_视频.md`，标「顽疾」「已确认」的优先遵守。🔴 封面 / 字幕出字前读
`fxai-standard` 06 册：字体只从白名单选（标题得意黑、正文/字幕 Noto Sans SC），商业字库禁用。

## 🚨 开工前置

🔴 先读 `fxai-workflow/SKILL.md` 的「ComfyUI 服务端模式铁律」：服务由控制台启停，本技能只按地址发
HTTP；8188 探不通走云或提示人启动。地址取 `~/.workbuddy/fxai_global.json` 的 `comfy_url` +
`comfy_cloud_url`；模板取 `fxai-workflow/shortdrama-minimax-filmdrama-api.json`；参考图一律四视图
（自有角色传 `ref="avatar/000.png"` / `001.png`）。配置口径见 05 册。

## 入口命令

`python drama_pipeline.py --check`（自检）/ `examples/episode_template.py --project "<项目目录>"`（新项目模板）/ `comfy_watch.py wait <pid> --timeout 5400`（等合并）。单镜重渲、字幕、发布物料见 06 册。

**渲染中问进度**：`shot_query.py <prompt_id>`（不记 pid 用 `now`），判据见 fxai-standard 01 册。

🔴 **技能脚本定位**：`comfy_watch.py`、`shot_query.py` 在 `~/.workbuddy/skills/`；先按本 SKILL 写的路径直接调。

## 项目清单（页面按它显示进度）

产物目录建好即建档、每完成一段更新；结构与子命令见 fxai-standard 01 册。

```bash
P=~/.workbuddy/skills/fxai-standard/scripts/fxai_project.py
python "$P" init    "<项目>" --title <名称> --category drama --skill fxai-short-drama
python "$P" episode "<项目>" --no 1 --name <集名> --dir "1、<目录名>" --script 2 --image 0 --audio 0 --video 0
```
