---
name: fxai-role-management
description: 角色库管理（用户级）。当用户要「新建角色 / 加一个角色 / 列出所有角色 / 看看有哪些角色 / 改角色设定 / 删角色 / 查角色档案 / 给配音准备角色 / 批量整理角色」时使用。直接读写本机角色表 ai_digital_human，客户端「AI伙伴」与「多角色配音 - 选择角色」即时可见。不做：角色立绘与头像生成、音色克隆、服务端同步。
agent_created: true
---

# 角色库管理（fxai-role-management）

一句话：**把角色的增删改查落到本机角色表**。入口只有 `scripts/role_mgr.py`，数据只有一张表 `ai_digital_human`。
角色设定文本（性格、外貌、背景、系统提示词）由对话产出，脚本只负责落库与回读。

## 本技能文件清单（先读这里，再决定读哪册）

| 文件 | 什么时候读 |
|------|-----------|
| `SKILL.md` | 本文件：命令速查、铁律、文件清单 |
| `references/01-角色字段与命令.md` | **干活必读**：34 个可写字段、命令全参数、输出格式、回滚办法 |
| `references/02-数据落点与页面联动.md` | 写到哪、几个用户库怎么选、页面为什么看不到、写入限制 |
| `scripts/role_mgr.py` | 唯一入口；只跑命令，不通读源码 |
| `data/backup/` | 每次写操作自动生成的单行备份 |

确定本次要做的事之后，只读对应的那一册。

## 直接开跑

```bash
python scripts/role_mgr.py dbs                      # 本机有哪几个账号库、哪个是当前登录
python scripts/role_mgr.py list                     # 列出当前登录用户的角色
python scripts/role_mgr.py get 阿杰                  # 看单个角色全部字段
python scripts/role_mgr.py add --title 新角色 --gender 女 --age 26
python scripts/role_mgr.py update 阿杰 --hobby 篮球
python scripts/role_mgr.py delete 阿杰               # 预演，不删
python scripts/role_mgr.py delete 阿杰 --yes         # 真删
python scripts/role_mgr.py list --uid 1             # 切到别的账号库查看
```

两个目录与账号都不用填：`software_install_dir` / `software_data_dir` 读 `~/.workbuddy/fxai_global.json`，账号读 `<安装目录>\userinfo.json` 的 `myid`（客户端每次登录成功整份重写，换账号自动跟随）。四步判定、缺失时的提示话术见 `fxai-standard` 08 册。

任意字段都能用 `--字段名 值` 传，字段表见 01 册。`--json` 拿机器可读结果。

## 🚨 铁律（每次开工都守）

1. **目录与账号都从全局配置取**：`software_data_dir` / `software_install_dir` 读 `~/.workbuddy/fxai_global.json`，账号读 `<安装目录>\userinfo.json` 的 `myid`；四步判定见 `fxai-standard` 08 册。要动别的账号库才带 `--uid`，且先 `dbs` 看清。全局配置缺了就去「本机配置 → 技能运行环境」复制一次口令补上。
2. **删除三步**：先跑一次不带 `--yes` 的预演 → 把「角色名 + id + 库」报给用户 → 用户点头才加 `--yes`。
3. **只动本机这一张表**：不写服务端、不改 `fxai.db`、不碰其它表。
4. **不编造字段**：用户没给的性格、外貌、背景一律留空，不替用户脑补人设。
5. **同名先查**：新增前 `list` 看有没有同名，撞名改称谓或用 `--force`。
6. **路径要真实**：`--voice` / `--avatar` / `--portrait` 传本机绝对路径，写入后回读确认文件存在，不存在要明说。
7. **写完要提醒刷新**：客户端页面不自动刷新，让用户切页或重进「AI伙伴」再回来。
8. **回滚靠备份**：每次写操作都在 `data/backup/` 留单行快照；要还原就读出来用 `update` 写回。

## 与其他能力的分工

| 想做的 | 归谁 |
|---|---|
| 生成角色立绘 / 四视图 | `fxai-fourview-gen`、`fxai-poster` |
| 把文字做成音色样音 | `fxai-voiceover` |
| 用角色出镜说话的视频 | `fxai-digital-human-video` |
| 多角色配音分句合成 | 页面 `tool/tts`（本技能只管角色配置） |

公共件与规范：路径、数据目录口径见 `fxai-common`；产物落点与文档写法见 `fxai-standard`。
