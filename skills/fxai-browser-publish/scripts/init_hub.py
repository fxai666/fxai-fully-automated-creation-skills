#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""幂等初始化发布实例目录，并把指针登记到全局配置。

归属原则：
  - 技能包只写「怎么建」，不写「建在哪」；包内零站点名、零账号、零凭据、零本机绝对路径。
  - 实例目录由使用方当次指定；站点条目、账号注册表与登录态只留本机实例目录。
  - 全局配置只放一个指针；环境变量仅作本次进程覆盖。
  - 目标文件已存在即跳过，绝不覆盖、绝不截断使用者手填内容。
  - 技能包只给骨架与校验脚本，不携带实例实现代码。

用法：
    python -B init_hub.py --hub "<发布目录>"
    python -B init_hub.py --hub "<发布目录>" --check-only
    python -B init_hub.py --hub "<发布目录>" --json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True

SKILL_NAME = "fxai-browser-publish"
SKILL_DIR = Path(__file__).resolve().parent.parent
SKELETON = SKILL_DIR / "templates" / "实例骨架.json"
GLOBAL_CONFIG_REL = Path(".workbuddy") / "fxai_global.json"
GLOBAL_SECTION = "publish_hub"
POINTER_NOTE = "实例目录指针：站点条目与账号信息都在 hub_dir 内，不在本文件、不在技能包"

ACCOUNTS = {
    "version": 1,
    "note": "只存站点标识、默认账号、账号别名与备注；不存任何凭据",
    "accounts": {},
}

CHECKLIST = """# 站点接入清单

> 本文件由初始化脚本生成，是「一个站点要接进来到什么程度才算打通」的固定检查表。
> 表里不含任何具体站点：站点由使用者自己命名，接入时逐格填、逐格验。

## 一、先想清楚三类内容

| 内容类型 | 键 | 判据 | 媒体形态 |
|---|---|---|---|
| 视频 | `video` | 单个视频文件 | 一个视频文件 |
| 图文 | `image_text` | 多图 + 短文案 | 一个图片目录 |
| 文章 | `article` | 长正文 + 标题 | 文档或 Markdown |

类型是路由键：它同时决定去哪些站点、去每个站点的哪个地址、填哪些字段。

**只给确实支持的类型建键。** 判据是实际打开过该类型的投稿页、看到该类型的媒体上传控件；
导航菜单、内容管理列表、数据统计项里的类型名只说明平台有这类内容，不代表网页端能发，
不得据此建键。凭菜单名建出的空地址格会把不支持的站点算进按类型展开的渠道。
业务上当下不需要发的类型（例如以多张图片为主、只在做图集时才用得上的类型）同样不建键，
理由一并写进 `notes`；勿把业务决策写成「平台不支持」的事实断言，也勿留空地址格冒充待打通。
发现误建即删键，并把判断依据写进该站 `notes` 与动作台账。

## 二、一个站点要记两层

**站点层（与内容无关）**：登录页地址、登录方式（扫码 / 手动）、判定登录态的凭据名、
登录页入口动作、二维码候选、投稿按钮文案、成功提示文案、AI 声明入口文案、
**文案档位 `copy_profile`（`tech` / `mass`，缺省 `mass`）**。

**内容类型层（每类各一份）**：该类型的发布页地址、入口动作、上传控件候选、
标题 / 正文 / 标签字段候选、命中状态与视口指纹、**档位覆盖 `copy_profile`**。

文案档位决定标题与详情的写法：`tech` 技术名词前置且可写实现，`mass` 娱乐化钩子且
不得写技术名词与制作过程；两档均保留 AI 显式标识。细则见 `references/13-标题与详情生成规范.md`。
未定档按 `mass` 处理。

同一站点的视频页与图文页往往不是同一个地址，必须各记各的、各验各的。

## 三、每个「站点 × 内容类型」走一遍

1. 登录：扫码优先；只有短信验证码的站点用 `login --manual` 开有头窗口，由使用者亲手登。
2. 打开候选发布页，确认最终落地地址（不含会话令牌、一次性参数、账号标识）。
3. 记到达判据：上传控件候选里至少一个能命中。
4. 记入口动作：落地被重定向到工作台时，补一条能进发布页的点击动作。
5. 写回站点条目的 `types.<类型>`：地址、入口动作、媒体控件、字段、`entry_state`。
6. 置 `state=verified` 并记 `verified_at` 与视口指纹；之后每次 `goto` 命中即复用，零分析零写盘。
7. 地址为空说明「支持但还没走到过」，保持 `unverified`，不算失效。

## 四、每格跑通后回写动作台账

`types.<类型>.path_log` 一步一条：目的、动作、手法、定位、坑、校验、验证时间。

手法写**实际生效的那个**，按此优先级选，不许跳级：
元素级真实鼠标点击 → 坐标点击 → 键盘 → 强制点击 → （禁用）JS 合成点击。

台账写全之后，下次照台账执行只校选择器，既不重新分析页面，也不产生额外消耗。

## 五、公共文案块

站点共用的固定信息（资料下载地址、固定话题、品牌行）放顶层 `公共/公共文案块.json`：
`descr_footer` 自动追加到每条简介末尾，`tags` 合并进标签列表并去重。
改内容只改这一个文件，不动任务、不动代码；站点外链受限时对该站置 `common_block: false`。

`descr_footer` 里的资料下载 / 开源地址 / 网盘链接属**技术向信息**：`mass` 档站点须置
`footer_in_desc: false`（保留公共标签）或 `common_block: false`（连标签一起关）。
公共标签同样须过「无技术名词」检查；标签会并进标题的站点，带工艺 / 自动化意味的标签
不得进大众向标题。开关按站点 / 按类型落，勿为单站收口改公共文件。见 `references/13`。

## 六、判定打通

- `goto <站点> --type <类型>` 输出命中，且第二次运行输出「零写盘、零分析」。
- `platforms --type <类型>` 里该站点出现，且该类型标记为已验证。
- 该组合在直达清单里独占一行，地址与状态齐全。
- 该格 `copy_profile` 已确定（未填按 `mass`），发布物料按 `references/13` 自检通过。

## 七、日常顺序

先 `goto` 复用地址 → 只有失败才导出页面取证 → 校准后回写站点条目。
选择器与地址只改实例配置，不改实现代码。
"""

COMMON_BLOCK = {
    "version": 1,
    "note": "公共文案块：所有站点、所有作品共用的固定信息。descr_footer 追加到每条简介末尾，tags 合并进标签列表。站点条目置 common_block=false 可对单站关闭。",
    "descr_footer": "",
    "tags": [],
    "links": {},
}

GITIGNORE = """profiles/
cookies/
logs/qr/
config/accounts.json
tasks.db
tasks.db-journal
__pycache__/
*.pyc
*.log
videos/
*.mp4
*.mov
*.jpg
*.png
"""

TASKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    platform      TEXT NOT NULL,
    account       TEXT NOT NULL DEFAULT 'main',
    content_type  TEXT NOT NULL DEFAULT 'video',
    video         TEXT NOT NULL,
    title         TEXT NOT NULL,
    descr         TEXT DEFAULT '',
    tags          TEXT DEFAULT '',
    cover         TEXT DEFAULT '',
    ai_generated  INTEGER DEFAULT 0,
    schedule_at   TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    attempts      INTEGER NOT NULL DEFAULT 0,
    result_url    TEXT DEFAULT '',
    preview_path  TEXT DEFAULT '',
    error         TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, schedule_at);
CREATE INDEX IF NOT EXISTS idx_tasks_account ON tasks(platform, account, status);
CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(content_type, status);
"""


def result_skeleton(hub: Path, dry: bool) -> dict:
    return {
        "skill": SKILL_NAME,
        "hub": str(hub),
        "dry_run": dry,
        "created": [],
        "reused": [],
        "global_config": {"path": str(Path.home() / GLOBAL_CONFIG_REL), "written": False, "note": ""},
    }


def ensure_dir(p: Path, hub: Path, dry: bool, res: dict) -> None:
    rel = p.relative_to(hub).as_posix() + "/"
    if p.is_dir():
        res["reused"].append(rel)
        return
    if not dry:
        p.mkdir(parents=True, exist_ok=True)
    res["created"].append(rel)


def ensure_file(p: Path, hub: Path, content: str, dry: bool, res: dict) -> None:
    rel = p.relative_to(hub).as_posix()
    if p.is_file():
        res["reused"].append(rel)
        return
    if not dry:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    res["created"].append(rel)


def ensure_db(p: Path, hub: Path, dry: bool, res: dict) -> None:
    rel = p.relative_to(hub).as_posix()
    if p.is_file():
        res["reused"].append(rel)
        return
    if not dry:
        p.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(p) as con:
            con.executescript(TASKS_SCHEMA)
    res["created"].append(rel)


def ensure_global_pointer(hub: Path, dry: bool, res: dict) -> None:
    """只增 / 只改 publish_hub 一段；写盘前备份，写盘后校验其余顶层字段值零改动。"""
    p = Path.home() / GLOBAL_CONFIG_REL
    if p.is_file():
        text = p.read_text(encoding="utf-8")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            res["global_config"]["note"] = "全局配置解析失败，未改动"
            return
    else:
        text = "{}\n"
        data = {}

    if not isinstance(data, dict):
        res["global_config"]["note"] = "全局配置不是 JSON 对象，未改动"
        return

    before = {k: v for k, v in data.items() if k != GLOBAL_SECTION}
    sec = data.get(GLOBAL_SECTION)
    if not isinstance(sec, dict):
        sec = {}
    patch: dict = {}
    if not sec.get("hub_dir"):
        patch["hub_dir"] = str(hub)
    if sec.get("skill") != SKILL_NAME:
        patch["skill"] = SKILL_NAME
    if not sec.get("note"):
        patch["note"] = POINTER_NOTE

    if not patch:
        res["global_config"]["note"] = "指针已存在且指向一致，零写入"
        return

    sec.update(patch)
    data[GLOBAL_SECTION] = sec
    after = {k: v for k, v in data.items() if k != GLOBAL_SECTION}
    if before != after:
        res["global_config"]["note"] = "其余顶层字段值发生变化，已放弃写入"
        return

    res["global_config"]["note"] = "待写入字段：" + ", ".join(sorted(patch))
    if dry:
        return

    if p.is_file():
        backup = p.with_name(p.name + ".bak-" + datetime.now().strftime("%Y%m%d"))
        if not backup.is_file():
            shutil.copy2(p, backup)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        check = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        res["global_config"]["note"] = "写入后校验失败，请核对备份"
        return
    if {k: v for k, v in check.items() if k != GLOBAL_SECTION} != before:
        res["global_config"]["note"] = "写入后其余字段值不一致，请核对备份"
        return
    res["global_config"]["written"] = True


def main() -> int:
    ap = argparse.ArgumentParser(description="幂等初始化发布实例目录")
    ap.add_argument("--hub", required=True, help="实例目录路径，由使用方当次指定")
    ap.add_argument("--check-only", action="store_true", help="只报告将创建 / 将复用的项，零写入")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    hub = Path(a.hub).expanduser()
    hub = hub.resolve() if hub.is_absolute() else (Path.home() / hub).resolve()
    dry = a.check_only
    res = result_skeleton(hub, dry)

    if not SKELETON.is_file():
        print("骨架模板缺失：" + str(SKELETON), file=sys.stderr)
        return 2
    skeleton = SKELETON.read_text(encoding="utf-8")

    for name in ("config", "config/sites", "发布单", "logs", "logs/qr", "cookies", "profiles", "公共"):
        ensure_dir(hub / name, hub, dry, res)
    ensure_file(hub / "config" / "platforms.json", hub, skeleton, dry, res)
    ensure_file(hub / "config" / "接入清单.md", hub, CHECKLIST, dry, res)
    ensure_file(hub / "公共" / "公共文案块.json", hub,
                json.dumps(COMMON_BLOCK, ensure_ascii=False, indent=2) + "\n", dry, res)
    ensure_file(hub / "config" / "accounts.json", hub,
                json.dumps(ACCOUNTS, ensure_ascii=False, indent=2) + "\n", dry, res)
    ensure_file(hub / ".gitignore", hub, GITIGNORE, dry, res)
    ensure_db(hub / "tasks.db", hub, dry, res)
    ensure_global_pointer(hub, dry, res)

    res["ok"] = True
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"技能        : {res['skill']}")
        print(f"实例目录    : {res['hub']}")
        print(f"模式        : {'只检查（零写入）' if dry else '初始化'}")
        print(f"将创建 / 已创建 : {res['created'] or '（无）'}")
        print(f"复用        : {len(res['reused'])} 项")
        for item in res["reused"]:
            print(f"  - {item}")
        g = res["global_config"]
        print(f"全局配置    : {g['note']}；写盘={g['written']}")
        print("提示        : 先读 config/接入清单.md，再按 references/03 逐站逐类型接入；骨架 sites 为空属正常初始状态。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
