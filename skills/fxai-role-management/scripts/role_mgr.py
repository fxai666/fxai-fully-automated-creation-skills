#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""角色库管理：本地 ai_digital_human 表的增删改查。

数据落点：<数据目录>/sqlite/user_<用户ID>.db 的 ai_digital_human 表。
该表同时被客户端「AI伙伴」页面与「多角色配音 - 选择角色」读取，写入即时可见。

命令：
  list    [--uid N] [--json]                       列出角色
  get     <id|角色名> [--uid N] [--json]            查看角色全部字段
  add     --title X [--字段 值 ...] [--uid N]       新增角色
  update  <id|角色名> --字段 值 [...] [--uid N]      修改角色
  delete  <id|角色名> [--uid N] [--yes]             删除角色（默认只预演）
  dbs     [--json]                                 列出本机用户库与角色数

路径来源（都不扫盘、不猜盘符）：
  · 数据目录  ← ~/.workbuddy/fxai_global.json 的 software_data_dir
  · 安装目录  ← 同一份全局配置的 software_install_dir（userinfo.json 就在它下面）
  · 两份全局配置由客户端「本机配置 → 技能运行环境」页生成；缺了就报错要用户补，不自行推断。

用户库：
  · 默认用 <安装目录>/userinfo.json 的 myid 定位当前登录用户库。
  · 该文件客户端每次登录成功都会整份重写（js/app.js → fxaitoolkit.updateUserInfo →
    UserInfo.SetInstance），所以换账号后 myid 自动跟着换，不用手填。
  · 全局配置缺失时退回最近修改的库；多个库都有角色且无全局配置时，写操作必须 --uid。
  · 写前把受影响行备份到 data/backup/，文件名含时间戳与操作名。
  · delete 不加 --yes 只打印将被删除的角色。
"""

import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

TABLE = "ai_digital_human"

# 可写字段：库列名 → 中文名
FIELDS = {
    "typeid": "类型ID",
    "category": "分类",
    "title": "角色名",
    "nickname": "昵称",
    "wakeword": "唤醒词",
    "callme": "称呼我",
    "systemprompt": "系统提示词",
    "gender": "性别",
    "age": "年龄",
    "birthday": "生日",
    "voicelang": "语音语言",
    "voicespeed": "语速",
    "avatar": "头像",
    "portrait": "形象照",
    "voice": "音色音频路径",
    "voicetext": "音色参考文本",
    "appearancefeature": "外貌特征",
    "figure": "身材",
    "dressstyle": "服装风格",
    "voicefeature": "声音特征",
    "hobby": "爱好",
    "taboo": "禁忌",
    "coreidentity": "核心定位",
    "exclusivepersonality": "性格",
    "coreskills": "核心技能",
    "behaviorboundary": "行为边界",
    "abilitybottomLine": "能力底线",
    "expressionstyle": "表达风格",
    "memoryanchor": "记忆锚点",
    "interactionrhythm": "互动节奏",
    "emotionaltendency": "情绪倾向",
    "bginfo": "背景信息",
    "otherrequirements": "其他要求",
    "json": "扩展JSON",
}

GROUPS = [
    ("基础信息", ["title", "nickname", "gender", "age", "birthday", "category", "wakeword", "callme", "typeid"]),
    ("性格与身份", ["coreidentity", "exclusivepersonality", "emotionaltendency", "hobby", "taboo", "bginfo"]),
    ("外貌与声音", ["appearancefeature", "figure", "dressstyle", "voicefeature", "voicelang", "voicespeed"]),
    ("能力与边界", ["coreskills", "abilityboundary", "abilitybottomLine", "expressionstyle", "memoryanchor", "interactionrhythm"]),
    ("素材", ["avatar", "portrait", "voice", "voicetext"]),
    ("其他", ["systemprompt", "otherrequirements", "json"]),
]


# ---------- 路径与库 ----------

GLOBAL_NAME = os.path.join(".workbuddy", "fxai_global.json")

# 08 册口径：缺目录 / 缺登录时，提示一次说清「装软件 + 按教程配 + 登录 + 复制口令」四件事
SETUP_HINT = (
    "按这个顺序补：1) 装凤希AI伴侣，按站点「软件配置（全面使用凤AI必看）」与「视频教程」走一遍；"
    "2) 在客户端登录一次；3) 打开客户端「本机配置 → 技能运行环境」，确认两个目录都有值后点"
    "「复制智能体安装口令」（会重写 ~/.workbuddy/fxai_global.json）。"
)


def global_config():
    """全局配置：~/.workbuddy/fxai_global.json。

    由客户端「本机配置 → 技能运行环境」页生成，里面就有 software_install_dir /
    software_data_dir 两条，本技能的目录一律从这两条取，不扫盘。
    """
    cands = []
    env = os.environ.get("FXAI_GLOBAL_CONFIG", "").strip()
    if env:
        cands.append(env)
    home = os.path.expanduser("~")
    cands.append(os.path.join(home, GLOBAL_NAME))
    for path in cands:
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            data["_file"] = path
            return data
    return {}


def norm_dir(value):
    """配置里的路径转本机目录：去引号、统一分隔符、去尾斜杠。"""
    p = str(value or "").strip().strip('"').strip("'").replace("/", "\\")
    while p.endswith("\\") and len(p) > 3:
        p = p[:-1]
    return p


def data_dir():
    """数据目录：全局配置 software_data_dir > 环境变量 FXAI_DATA_DIR。"""
    g = global_config()
    tried = []
    for src, raw in (("%s 的 software_data_dir" % (g.get("_file") or "全局配置"),
                      g.get("software_data_dir")),
                     ("环境变量 FXAI_DATA_DIR", os.environ.get("FXAI_DATA_DIR"))):
        p = norm_dir(raw)
        if not p:
            tried.append("%s：未配置" % src)
            continue
        if os.path.isdir(os.path.join(p, "sqlite")):
            return p
        tried.append("%s：%s 下没有 sqlite 目录" % (src, p))
    die("找不到数据目录。逐条查过：%s\n%s" % ("；".join(tried), SETUP_HINT))


def db_files():
    root = os.path.join(data_dir(), "sqlite")
    out = []
    for name in os.listdir(root):
        if not name.lower().startswith("user_") or not name.lower().endswith(".db"):
            continue
        path = os.path.join(root, name)
        if os.path.getsize(path) == 0:
            continue
        uid = name[5:-3]
        if not uid.isdigit():
            continue
        out.append({"uid": uid, "path": path, "name": name, "mtime": os.path.getmtime(path)})
    return sorted(out, key=lambda x: x["uid"])


def app_dir():
    """客户端安装目录：全局配置 software_install_dir > 环境变量 FXAI_APP_DIR。

    判定标准是目录里有 userinfo.json —— 客户端把它写在 Application.StartupPath。
    """
    g = global_config()
    for raw in (g.get("software_install_dir"), os.environ.get("FXAI_APP_DIR")):
        p = norm_dir(raw)
        if p and os.path.isfile(os.path.join(p, "userinfo.json")):
            return p
    return ""


def current_user():
    """当前登录用户：取全局配置 userinfo.json 的 myid，只读身份字段，不读 token。"""
    folder = app_dir()
    if not folder:
        return None
    path = os.path.join(folder, "userinfo.json")
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    uid = str(data.get("myid") or "").strip()
    if not uid:
        return None
    return {
        "uid": uid,
        "nickname": str(data.get("nickname") or "").strip(),
        "account": str(data.get("account") or "").strip(),
        "file": path,
    }


def user_hint():
    """没读到 userinfo.json 时给人看的排查提示。"""
    g = global_config()
    install = norm_dir(g.get("software_install_dir"))
    if not g:
        return ("没找到全局配置 ~/.workbuddy/fxai_global.json：去客户端「本机配置 → 技能运行环境」"
                "点一次「复制智能体安装口令」生成它。")
    if not install:
        return "全局配置里没有 software_install_dir，去「本机配置 → 技能运行环境」补上再点复制口令。"
    return ("全局配置的 software_install_dir = %s，但该目录下没有 userinfo.json："
            "确认客户端已装在该目录并登录过一次。" % install)


def who(uid):
    """一行身份说明，用于输出。"""
    cur = current_user()
    if cur and cur["uid"] == str(uid):
        name = cur["nickname"] or "未命名"
        return "当前登录：%s（用户 %s，取自 %s）" % (name, cur["uid"], cur["file"])
    if cur:
        return "指定库：user_%s.db（当前登录是用户 %s）" % (uid, cur["uid"])
    return "指定库：user_%s.db（未读到当前登录用户：%s）" % (uid, user_hint())


def connect(path, readonly=False):
    if readonly:
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True, timeout=8)
    else:
        con = sqlite3.connect(path, timeout=8)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=8000")
    return con


def has_table(con):
    row = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()
    return bool(row)


def role_count(path):
    try:
        con = connect(path, readonly=True)
        try:
            if not has_table(con):
                return None
            return con.execute("SELECT COUNT(*) FROM %s" % TABLE).fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return None


def resolve_db(uid=None, write=False):
    files = db_files()
    if not files:
        die("数据目录里没有 user_*.db。\n%s" % SETUP_HINT)
    if uid:
        for f in files:
            if f["uid"] == str(uid):
                return f
        die("没有 user_%s.db，可用：%s" % (uid, "、".join(f["uid"] for f in files)))
    cur = current_user()
    if cur:
        for f in files:
            if f["uid"] == cur["uid"]:
                if role_count(f["path"]) is None:
                    die("当前登录用户 %s 的 user_%s.db 还没有 %s 表，先在客户端打开一次「AI伙伴」页面"
                        % (cur["uid"], cur["uid"], TABLE))
                return f
        die("当前登录用户是用户 %s，但 user_%s.db 不存在；现有库：%s"
            % (cur["uid"], cur["uid"], "、".join("user_%s" % f["uid"] for f in files)))
    cands = [f for f in files if (role_count(f["path"]) or 0) > 0]
    if not cands:
        die("所有用户库都没有 %s 表或表为空，先在客户端打开一次「AI伙伴」页面建表" % TABLE)
    if len(cands) == 1:
        return cands[0]
    if write:
        die("没读到当前登录用户（%s），且多个用户库都有角色（%s），写操作必须用 --uid 指定"
            % (user_hint(), "、".join("user_%s" % f["uid"] for f in cands)))
    return max(cands, key=lambda x: x["mtime"])


def find_role(con, key):
    key = str(key).strip()
    if key.startswith("#"):
        key = key[1:]
    rows = []
    if key.isdigit():
        rows = con.execute("SELECT * FROM %s WHERE id=?" % TABLE, (int(key),)).fetchall()
    if not rows:
        rows = con.execute("SELECT * FROM %s WHERE title=?" % TABLE, (key,)).fetchall()
    if not rows:
        rows = con.execute("SELECT * FROM %s WHERE markerid=?" % TABLE, (key,)).fetchall()
    if not rows:
        rows = con.execute("SELECT * FROM %s WHERE nickname=?" % TABLE, (key,)).fetchall()
    if not rows:
        die("没有找到角色：%s" % key)
    if len(rows) > 1:
        die("「%s」匹配到 %d 个角色，请用 --id 或 #编号：%s"
            % (key, len(rows), "；".join("id=%s %s" % (r["id"], r["title"]) for r in rows)))
    return rows[0]


# ---------- 备份 ----------

def backup(row, action, uid):
    folder = os.path.join(data_dir(), "backup")
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(folder, "%s_%s_user%s.json" % (stamp, action, uid))
    payload = {"action": action, "uid": uid, "time": stamp, "rows": [dict(row)]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    return path


# ---------- 输出 ----------

def die(msg):
    print("错误：%s" % msg, file=sys.stderr)
    sys.exit(1)


def cell(v, dash="—"):
    s = "" if v is None else str(v).strip()
    return s if s else dash


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x).replace("|", "\\|") for x in r) + " |")
    return "\n".join(out)


def mark(v):
    return "✓" if str(v or "").strip() else "—"


# ---------- 命令 ----------

def cmd_dbs(args):
    cur = current_user()
    rows = []
    for f in db_files():
        n = role_count(f["path"])
        rows.append({
            "uid": f["uid"],
            "db": f["name"],
            "roles": n,
            "modified": datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d %H:%M"),
            "current": bool(cur and cur["uid"] == f["uid"]),
        })
    if args.get("json"):
        print(json.dumps({"current": cur, "dbs": rows}, ensure_ascii=False, indent=1))
        return
    if not rows:
        print("没有可用的用户库")
        return
    print(table(["", "用户ID", "库文件", "角色数", "最近修改"],
                [["✓" if r["current"] else "—", r["uid"], r["db"],
                  "无此表" if r["roles"] is None else r["roles"], r["modified"]]
                 for r in rows]))
    print("")
    if cur:
        print("当前登录：%s（用户 %s）" % (cur["nickname"] or "未命名", cur["uid"]))
        print("身份来源：%s" % cur["file"])
    else:
        print("当前登录：未读到身份，按最近修改的库处理")
        print("原因：%s" % user_hint())
    print("数据目录：%s" % data_dir())


def cmd_list(args):
    f = resolve_db(args.get("uid"), write=False)
    con = connect(f["path"], readonly=True)
    try:
        if not has_table(con):
            die("user_%s.db 没有 %s 表，先在客户端打开一次「AI伙伴」页面" % (f["uid"], TABLE))
        rows = con.execute(
            "SELECT id,title,nickname,gender,age,category,voice,voicetext,avatar,addtime "
            "FROM %s ORDER BY id" % TABLE).fetchall()
    finally:
        con.close()
    data = [dict(r) for r in rows]
    if args.get("json"):
        print(json.dumps({"uid": f["uid"], "db": f["name"], "roles": data}, ensure_ascii=False, indent=1))
        return
    if not data:
        print("user_%s.db 里还没有角色" % f["uid"])
        return
    print("%s · 共 %d 个角色\n" % (who(f["uid"]), len(data)))
    print(table(["ID", "角色名", "昵称", "性别", "年龄", "分类", "音色", "参考文本", "头像"],
                [[r["id"], cell(r["title"]), cell(r["nickname"]), cell(r["gender"]), cell(r["age"]),
                  cell(r["category"]), mark(r["voice"]), mark(r["voicetext"]), mark(r["avatar"])]
                 for r in data]))
    others = [x for x in db_files() if x["uid"] != f["uid"] and (role_count(x["path"]) or 0) > 0]
    if others:
        print("\n其他也有角色的库：%s（加 --uid 切换）"
              % "、".join("user_%s(%s个)" % (x["uid"], role_count(x["path"])) for x in others))


def cmd_get(args):
    key = args["_pos"][0] if args.get("_pos") else ""
    if not key:
        die("用法：get <id|角色名>")
    f = resolve_db(args.get("uid"), write=False)
    con = connect(f["path"], readonly=True)
    try:
        row = find_role(con, key)
    finally:
        con.close()
    d = dict(row)
    if args.get("json"):
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return
    print("# %s（id=%s）\n" % (cell(d.get("title")), d.get("id")))
    print("%s\n" % who(f["uid"]))
    for name, keys in GROUPS:
        lines = []
        for k in keys:
            v = d.get(k)
            if v is None or str(v).strip() == "":
                continue
            lines.append("- **%s**：%s" % (FIELDS.get(k, k), str(v).replace("\n", " / ")))
        if lines:
            print("## %s\n" % name)
            print("\n".join(lines))
            print()
    print("## 系统字段\n")
    print("- id：%s\n- markerid：%s\n- addtime：%s" % (d.get("id"), cell(d.get("markerid")), cell(d.get("addtime"))))


def cmd_add(args):
    fields = {k: v for k, v in args.items() if k in FIELDS}
    if not str(fields.get("title", "")).strip():
        die("新增角色必须给 --title")
    f = resolve_db(args.get("uid"), write=True)
    con = connect(f["path"])
    try:
        if not has_table(con):
            die("user_%s.db 没有 %s 表" % (f["uid"], TABLE))
        same = con.execute("SELECT id FROM %s WHERE title=?" % TABLE,
                           (fields["title"].strip(),)).fetchall()
        if same and not args.get("force"):
            die("同名的「%s」已存在（id=%s），改名或加 --force 再建一个"
                % (fields["title"].strip(), ",".join(str(r["id"]) for r in same)))
        fields["title"] = fields["title"].strip()
        fields["markerid"] = uuid.uuid4().hex
        fields["addtime"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fields["issync"] = 0
        cols = ", ".join(fields.keys())
        marks = ", ".join("?" * len(fields))
        cur = con.execute("INSERT INTO %s (%s) VALUES (%s)" % (TABLE, cols, marks),
                          list(fields.values()))
        con.commit()
        new_id = cur.lastrowid
        row = con.execute("SELECT * FROM %s WHERE id=?" % TABLE, (new_id,)).fetchone()
    finally:
        con.close()
    bpath = backup(row, "add", f["uid"])
    if args.get("json"):
        print(json.dumps({"ok": True, "uid": f["uid"], "id": new_id, "backup": bpath}, ensure_ascii=False))
        return
    print("已新增角色：id=%s「%s」" % (new_id, fields["title"]))
    print(who(f["uid"]))
    print("客户端页面刷新后即可看到；备份：%s" % bpath)


def cmd_update(args):
    key = args["_pos"][0] if args.get("_pos") else ""
    if not key:
        die("用法：update <id|角色名> --字段 值 ...")
    fields = {k: v for k, v in args.items() if k in FIELDS}
    if not fields:
        die("没有要给的新值，例：update 阿杰 --age 30 --hobby 篮球")
    f = resolve_db(args.get("uid"), write=True)
    con = connect(f["path"])
    try:
        row = find_role(con, key)
        before = dict(row)
        sets = ", ".join("%s=?" % k for k in fields)
        con.execute("UPDATE %s SET %s WHERE id=?" % (TABLE, sets), list(fields.values()) + [before["id"]])
        con.commit()
    finally:
        con.close()
    bpath = backup(before, "update", f["uid"])
    if args.get("json"):
        print(json.dumps({"ok": True, "uid": f["uid"], "id": before["id"],
                          "changed": list(fields.keys()), "backup": bpath}, ensure_ascii=False))
        return
    print("已更新「%s」（id=%s）：%s" % (cell(before.get("title")), before["id"], "、".join(fields.keys())))
    print("备份：%s" % bpath)


def cmd_delete(args):
    key = args["_pos"][0] if args.get("_pos") else ""
    if not key:
        die("用法：delete <id|角色名> [--yes]")
    f = resolve_db(args.get("uid"), write=True)
    con = connect(f["path"])
    try:
        row = find_role(con, key)
        before = dict(row)
        if not args.get("yes"):
            print("将要删除：「%s」（id=%s）" % (cell(before.get("title")), before["id"]))
            print(who(f["uid"]))
            print("确认无误后加 --yes 执行。")
            return
        con.execute("DELETE FROM %s WHERE id=?" % TABLE, (before["id"],))
        con.commit()
    finally:
        con.close()
    bpath = backup(before, "delete", f["uid"])
    if args.get("json"):
        print(json.dumps({"ok": True, "uid": f["uid"], "deleted_id": before["id"], "backup": bpath},
                         ensure_ascii=False))
        return
    print("已删除「%s」（id=%s）。备份：%s" % (cell(before.get("title")), before["id"], bpath))


# ---------- 参数解析 ----------

def parse(argv):
    if not argv:
        die("用法见 SKILL.md：list / get / add / update / delete / dbs")
    cmd = argv[0]
    args = {"_pos": []}
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("--"):
            name = tok[2:]
            if "=" in name:
                name, val = name.split("=", 1)
                args[name] = val
                i += 1
                continue
            if name in ("json", "yes", "force"):
                args[name] = True
                i += 1
                continue
            if i + 1 >= len(argv):
                die("--%s 缺值" % name)
            args[name] = argv[i + 1]
            i += 2
            continue
        args["_pos"].append(tok)
        i += 1
    return cmd, args


def main():
    cmd, args = parse(sys.argv[1:])
    table_map = {
        "list": cmd_list, "ls": cmd_list,
        "get": cmd_get, "show": cmd_get,
        "add": cmd_add, "create": cmd_add,
        "update": cmd_update, "set": cmd_update,
        "delete": cmd_delete, "del": cmd_delete, "rm": cmd_delete,
        "dbs": cmd_dbs,
    }
    fn = table_map.get(cmd)
    if not fn:
        die("不认识的命令：%s" % cmd)
    fn(args)


if __name__ == "__main__":
    main()
