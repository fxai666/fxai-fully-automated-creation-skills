---
name: fxai-auto-update
description: 凤希AI 技能包自动更新：请求官网 upgrade/skill.json 拿到版本号与压缩包相对路径，下载整包 zip，解压后逐文件落盘；本地改过的文件不覆盖，交给智能体做三方合并（基线/本地/上游）再落盘；包内专家不落技能根，未安装或版本落后时装进宿主专家市场；支持回滚。当用户说「更新技能包 / 升级技能 / 技能包更新一下 / 检查新版本 / 有没有新版本 / 拉最新版 / 自动更新 / 合并更新 / 回滚技能 / 专家没装 / 把专家也装上 / 检查专家」，或技能包要跟随官网升级时使用。不需要口令或激活码，说人话即触发。不生成技能内容、不动用户项目产物。
agent_created: true
---

# fxai-auto-update · 技能包自动更新

官网 `upgrade/` 下放一份 `skill.json`（版本号 + 压缩包相对路径），本技能按它取版本、下整包、
解压后逐文件落盘。本地被改过的文件不覆盖，进待合并队列，由智能体读基线 / 本地 / 上游三份
合并后再落盘。整包内的 `专家包/` 单独处理：装进宿主专家市场，不落技能根。

## 本技能文件清单（先读这里，再决定读哪册）

| 文件 | 什么时候读 |
|---|---|
| `references/01-接口与目录约定.md` | 配官网域名 / 路径前缀、改服务端 `skill.json`、确认本机落点与专家落点时 |
| `references/02-更新与合并规程.md` | 执行更新、做三方合并、处理冲突、专家安装与回滚时 |
| `scripts/update.py` | 查版本 / 下整包 / 建待合并任务 / 专家检查安装 / 回滚（命令行直接跑） |
| `scripts/apply_merge.py` | 合并完一个文件后落盘并刷新基线 |
| `scripts/make_index.py` | 作者侧：由整包 zip 生成 `skill.json` 并摆好发布目录 |

确定本次要做什么之后，只读对应的那一册，其余一律不读。

## 用户怎么说会触发（无口令）

**更新不设口令、不设激活码，直接说人话就触发。** 命中后先跑 `check`，把「官网版本 vs 本机版本」报给用户，
由用户确认后再落盘。

| 用户可能这么说 | 理解为 |
|---|---|
| 更新技能包 / 升级技能 / 技能包更新一下 | `update`（先 check 再更新） |
| 检查新版本 / 有没有新版本 / 官网是不是出新包了 | `check`（只报版本，不动文件） |
| 拉最新版 / 更新到最新那一版 | `update`，版本号不同就升 |
| 更新完有冲突 / 合并更新 | `tasks` → 三方合并 → `apply_merge.py apply` |
| 专家没装 / 把专家也装上 / 检查专家装没装 | `expert --check` → 需要时 `expert --install` |
| 更新出问题了 / 想退回更新前 | `rollback` |

用户如果在别的 `fxai-*` 技能里顺口提「顺便看看有没有新版本」，同样按本技能处理。

**同版本重打也认**：发布方在同一个版本号上重打了包（`skill.json` 的 `version` 不变、`sha256` 变了），
`check` 会报「官网同版本内容有更新」，`update` 照样按更新处理 —— 不必为一次修正单独跳版本号。

## 直接开跑

```bash
python scripts/update.py check      # 官网版本 vs 本机版本（含本机专家状态）
python scripts/update.py update     # 下整包：本地没动过的直接替换，动过的进待合并；顺带装专家
python scripts/update.py tasks      # 列出待合并任务（含基线/本地/上游三份路径）
python scripts/apply_merge.py apply --file <相对路径> --merged <合并后文件>
python scripts/update.py expert --check                    # 只报专家现状
python scripts/update.py expert --install                 # 未装 / 版本落后 / 未登记时安装
python scripts/update.py expert --install --from <解压目录>  # 指定整包解压目录
python scripts/update.py rollback   # 回滚到上一份备份
```

## 铁律速查（常驻）

1. 🔴 **压缩包路径只认服务端返回的 `path`**：下载地址 = `https://<域名>/<前缀>/<path>`，文件名与目录层级一律不在本机写死。
2. 🔴 **只有本技能可以写技能目录**：其余技能目录一律只读（运行期零写入见 `fxai-standard` 硬规则 4 / 5）。
3. 🔴 **整包落盘前先过校验**：zip 打不开或 `testzip()` 不过即中止；索引给了 `sha256` 就比对，不符不落盘。
4. 🔴 **本地改过的文件不直接覆盖**（本地 sha256 与基线不符）：进待合并，由智能体按 02 册合并后经 `apply_merge.py` 落盘。
5. 🔴 **落盘前先备份**：旧文件先进 `<备份目录>/<时间戳>/`，`rollback` 才回得去；仅本机有的文件保留不删。
6. 🔴 **本机服务只走接口调用，启停权在人**（`fxai-standard` 通用法则 6）：本技能只发 HTTPS 请求，不拉起、不管理任何服务。
7. 🔴 **`专家包/` 不落技能根**：专家走 `<配置根>/plugins/marketplaces/my-experts/plugins/`，配置根由环境变量或技能根反推，不得写死产品目录名；未安装、版本落后、市场清单未登记时，才执行包内 `install_expert.py`。执行安装脚本只做本地文件复制与清单注册，不启动服务。
8. 🔴 **专家不进回滚**：备份只收技能文件，`rollback` 不还原专家目录；专家异常单独跑 `expert --install`。

## 指针

- 官网域名取 `~/.workbuddy/fxai_global.json` 的 `update.site`，缺则取 `brand.official_site`，仍缺用 `www.fxai.site`（读法同 `fxai-common/env_guard.py`）。
- 版本号按数字段比较（官网 `1.2` 与随包 `V1.2` 同值）。
- 写作与目录规范见 `fxai-standard`。
