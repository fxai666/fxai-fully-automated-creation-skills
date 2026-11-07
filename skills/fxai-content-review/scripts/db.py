# -*- coding: utf-8 -*-
"""fxai-content-review 台账库：经验 / 素材 / 标签 / 缺口。

SQLite 是唯一真相源。导出的 经验库_领域.md 是只读投影，不回写。
只存元数据与路径，不存文件本体。
经验按领域隔离：指纹 = 领域|分类|关键词，不同岗位的同名问题各计各的。
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

GLOBAL_CFG = os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_global.json")

# 领域 = 岗位。一条经验只属于一个领域，跨岗位通用的才用「通用」。
SCOPES = ["通用", "文章", "口播", "视频", "素材", "复盘", "小说"]
# 经验沉淀成规则时的落点：领域 → 该岗位的技能（规则写进它的 SKILL.md 硬约束）。
SCOPE_SKILL = {
    "通用": "fxai-common",
    "文章": "fxai-weixin-gzh-publisher",
    "口播": "fxai-voiceover-script",
    "视频": "fxai-video-editing",
    "素材": "fxai-scene-gen",
    "复盘": "fxai-content-review",
    "小说": "fxai-novel-writing",
}
EXP_CATS = ["钩子", "结构", "卖点", "结尾", "画面", "节奏", "呈现", "话题", "发布时间", "变现", "承接", "人设", "文笔", "合规", "其他"]
MAT_CATS = ["人设型", "信任型", "变现型"]
EXP_STATUS = ["待观察", "已确认", "已作废"]
MAT_STATUS = ["可用", "待补拍", "作废"]
GAP_STATUS = ["待补", "已补"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS experiences (
  id INTEGER PRIMARY KEY,
  created TEXT, updated TEXT, ref TEXT, scope TEXT,
  category TEXT, keyword TEXT, fingerprint TEXT UNIQUE,
  conclusion TEXT, evidence TEXT, action TEXT,
  status TEXT, hits INTEGER DEFAULT 1, promoted INTEGER DEFAULT 0, note TEXT
);
CREATE TABLE IF NOT EXISTS materials (
  id INTEGER PRIMARY KEY,
  created TEXT, name TEXT, path TEXT, scope TEXT,
  category TEXT, subcat TEXT, kind TEXT, ratio TEXT,
  duration REAL,
  uses INTEGER DEFAULT 0, last_used TEXT,
  status TEXT, verified INTEGER DEFAULT 1, note TEXT
);
CREATE TABLE IF NOT EXISTS tags (
  id INTEGER PRIMARY KEY, name TEXT, kind TEXT, alias TEXT, scope TEXT,
  UNIQUE(name, scope)
);
-- TAGS_SQL 占位：tags 建表语句单独定义，供旧库重建时使用
CREATE TABLE IF NOT EXISTS material_tags (
  material_id INTEGER, tag_id INTEGER, PRIMARY KEY (material_id, tag_id)
);
CREATE TABLE IF NOT EXISTS gaps (
  id INTEGER PRIMARY KEY, created TEXT, exp_id INTEGER, scope TEXT,
  need TEXT, priority TEXT, status TEXT
);
"""

# 索引单独建：旧库要先补 scope 列，否则建索引时报 no such column。
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_exp_fp ON experiences(fingerprint);
CREATE INDEX IF NOT EXISTS idx_exp_scope ON experiences(scope);
CREATE INDEX IF NOT EXISTS idx_mat_cat ON materials(category);
"""

TAGS_SQL = """CREATE TABLE tags (
  id INTEGER PRIMARY KEY, name TEXT, kind TEXT, alias TEXT, scope TEXT,
  UNIQUE(name, scope)
)"""


def _cfg(key, default=""):
    try:
        with open(GLOBAL_CFG, encoding="utf-8") as f:
            return json.load(f).get(key, default)
    except Exception:
        return default


def data_dir():
    """库目录：环境变量 FXAI_DATA_DIR > fxai_global.json.data_dir > 工作目录/_fxai_data。"""
    d = os.environ.get("FXAI_DATA_DIR", "") or _cfg("data_dir", "")
    if not d:
        d = os.path.join(os.getcwd(), "_fxai_data")
    os.makedirs(d, exist_ok=True)
    return d


def db_path():
    return os.path.join(data_dir(), "fxai.db")


def connect():
    c = sqlite3.connect(db_path())
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _rebuild_tags(c):
    """旧库 tags 是 name 单列唯一，同名的不同领域标签存不进去，整表重建。"""
    sql = c.execute("SELECT sql FROM sqlite_master WHERE name='tags'").fetchone()
    if sql and "UNIQUE(name, scope)" in (sql[0] or ""):
        return
    old = [dict(x) for x in c.execute("SELECT * FROM tags").fetchall()]
    c.execute("DROP TABLE IF EXISTS tags_old")
    c.execute("ALTER TABLE tags RENAME TO tags_old")
    c.execute(TAGS_SQL)
    for r in old:
        c.execute("INSERT OR IGNORE INTO tags(id,name,kind,alias,scope) VALUES(?,?,?,?,?)",
                  (r["id"], r["name"], r.get("kind", ""), r.get("alias", ""), r.get("scope") or "通用"))
    c.execute("DROP TABLE tags_old")


def _migrate(c):
    """给旧库补 scope / promoted 列并回填；已有指纹重算为 领域|分类|关键词。"""
    _rebuild_tags(c)
    for t in ("experiences", "materials", "tags", "gaps"):
        cols = [r[1] for r in c.execute("PRAGMA table_info(%s)" % t).fetchall()]
        if "scope" not in cols:
            c.execute("ALTER TABLE %s ADD COLUMN scope TEXT" % t)
        c.execute("UPDATE %s SET scope='通用' WHERE scope IS NULL OR scope=''" % t)
    cols = [r[1] for r in c.execute("PRAGMA table_info(experiences)").fetchall()]
    if "promoted" not in cols:
        c.execute("ALTER TABLE experiences ADD COLUMN promoted INTEGER DEFAULT 0")
    c.execute("UPDATE experiences SET promoted=0 WHERE promoted IS NULL")
    try:
        c.execute("UPDATE experiences SET fingerprint=scope||'|'||category||'|'||keyword")
    except Exception:
        pass


def init_db():
    c = connect()
    c.executescript(SCHEMA)
    _migrate(c)
    c.executescript(INDEXES)
    c.commit()
    c.close()
    return db_path()


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def _check(v, allowed, what):
    if v not in allowed:
        raise SystemExit("[FAIL] %s 必须是 %s 之一，收到：%s" % (what, "/".join(allowed), v))


def _tag_ids(c, names, scope="通用"):
    """标签名或别名解析成 tag id；只看「通用」与当前领域的标签；未登记直接报错。"""
    ids = []
    for n in [x.strip() for x in names if x.strip()]:
        r = c.execute("SELECT id FROM tags WHERE (name=? OR alias LIKE ?) AND (scope='通用' OR scope=?) ORDER BY id LIMIT 1",
                      (n, "%" + n + "%", scope)).fetchone()
        if not r:
            have = [x[0] for x in c.execute(
                "SELECT name FROM tags WHERE scope='通用' OR scope=? ORDER BY name", (scope,)).fetchall()]
            raise SystemExit("[FAIL] 标签未登记：%s\n当前领域(%s)可见：%s\n先跑：db.py tag add --名称 %s --类别 <类别> --领域 %s"
                             % (n, scope, "、".join(have) or "(空)", n, scope))
        ids.append(r[0])
    return ids


# ---------- 经验 ----------

def exp_add(ref, category, keyword, conclusion, evidence, action, scope="通用",
            status="待观察", note=""):
    _check(scope, SCOPES, "领域")
    _check(category, EXP_CATS, "分类")
    _check(status, EXP_STATUS, "状态")
    fp = "%s|%s|%s" % (scope, category, keyword.strip())
    c = connect()
    old = c.execute("SELECT id,hits FROM experiences WHERE fingerprint=?", (fp,)).fetchone()
    if old:
        c.execute("UPDATE experiences SET hits=hits+1, updated=?, ref=?, conclusion=?, evidence=?, action=?, status=?, note=? WHERE id=?",
                  (_now(), ref, conclusion, evidence, action, status, note, old["id"]))
        c.commit()
        n = old["hits"] + 1
        c.close()
        return {"id": old["id"], "领域": scope, "hits": n, "new": False}
    cur = c.execute("INSERT INTO experiences(created,updated,ref,scope,category,keyword,fingerprint,conclusion,evidence,action,status,note)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_today(), _now(), ref, scope, category, keyword.strip(), fp, conclusion, evidence, action, status, note))
    c.commit()
    rid = cur.lastrowid
    c.close()
    return {"id": rid, "领域": scope, "hits": 1, "new": True}


def exp_list(scope="", category="", status="", min_hits=0, limit=50):
    c = connect()
    sql = "SELECT * FROM experiences WHERE 1=1"
    args = []
    if scope:
        sql += " AND scope=?"
        args.append(scope)
    if category:
        sql += " AND category=?"
        args.append(category)
    if status:
        sql += " AND status=?"
        args.append(status)
    if min_hits:
        sql += " AND hits>=?"
        args.append(min_hits)
    sql += " ORDER BY hits DESC, updated DESC LIMIT ?"
    args.append(limit)
    r = _rows(c.execute(sql, args))
    c.close()
    return r


def exp_stat(scope=""):
    c = connect()
    args = []
    w = ""
    if scope:
        w = " WHERE scope=?"
        args.append(scope)
    by_cat = _rows(c.execute("SELECT category, COUNT(*) n, SUM(hits) hits FROM experiences%s GROUP BY category ORDER BY hits DESC" % w, args))
    by_scope = _rows(c.execute("SELECT scope, COUNT(*) n, SUM(hits) hits FROM experiences GROUP BY scope ORDER BY hits DESC"))
    chronic = _rows(c.execute("SELECT * FROM experiences%s ORDER BY hits DESC, updated DESC LIMIT 10"
                              % (w if w else " WHERE hits>=2"), args))
    total = c.execute("SELECT COUNT(*) n, COALESCE(SUM(hits),0) hits FROM experiences%s" % w, args).fetchone()
    c.close()
    return {"总数": total["n"], "累计命中": total["hits"], "按领域": by_scope, "按分类": by_cat, "顽疾": chronic}


def exp_export(scope=""):
    """按领域各导出一份 经验库_领域.md；岗位技能只读自己那份，不读别人的。"""
    scopes = [scope] if scope else SCOPES
    out = {}
    for sc in scopes:
        rows = exp_list(scope=sc, limit=1000)
        if not rows:
            continue
        lines = ["# 经验库 · %s" % sc, "",
                 "> 由 fxai-content-review 从 fxai.db 导出，只读投影，不回写。只含「%s」领域的经验。" % sc, ""]
        for cat in EXP_CATS:
            sub = [r for r in rows if r["category"] == cat]
            if not sub:
                continue
            lines.append("## %s" % cat)
            for r in sub:
                mark = "（第 %d 次 · 顽疾）" % r["hits"] if r["hits"] >= 2 else ""
                if r.get("promoted"):
                    mark += "（已沉淀为规则）"
                lines.append("- [%s] %s%s" % (r["status"], r["conclusion"], mark))
                if r["evidence"]:
                    lines.append("  - 证据：%s" % r["evidence"])
                lines.append("  - 下次：%s" % (r["action"] or "（未写）"))
            lines.append("")
        p = os.path.join(data_dir(), "经验库_%s.md" % sc)
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        out[sc] = p
    return out


def exp_pending(scope=""):
    """该沉淀成规则却还没沉淀的：已成顽疾 + 已确认 + 未 promotion。"""
    c = connect()
    sql = "SELECT * FROM experiences WHERE hits>=2 AND status='已确认' AND promoted=0"
    args = []
    if scope:
        sql += " AND scope=?"
        args.append(scope)
    sql += " ORDER BY hits DESC, updated DESC"
    r = _rows(c.execute(sql, args))
    c.close()
    return r


def exp_promote(eid):
    """把一条经验沉淀成规则：标记 promoted=1，并追加进 已沉淀规则.md。

    规则写进哪个技能由 SCOPE_SKILL 决定——本函数只负责记账与留痕，
    真正改写 SKILL.md 由人（或上层技能）照着 已沉淀规则.md 做，不自动改别人的技能。
    """
    c = connect()
    r = c.execute("SELECT * FROM experiences WHERE id=?", (eid,)).fetchone()
    if not r:
        c.close()
        raise SystemExit("[FAIL] 没有 id=%s 的经验" % eid)
    row = dict(r)
    c.execute("UPDATE experiences SET promoted=1, updated=? WHERE id=?", (_now(), eid))
    c.commit()
    c.close()
    skill = SCOPE_SKILL.get(row["scope"], "")
    p = os.path.join(data_dir(), "已沉淀规则.md")
    block = ["", "## [%s] %s · %s（命中 %d 次）" % (row["scope"], row["category"], row["keyword"], row["hits"]),
             "- 规则：%s" % row["action"],
             "- 反例：%s" % (row["evidence"] or "（无）"),
             "- 沉淀日期：%s ｜ 落点技能：%s" % (_today(), skill or "未映射"), ""]
    head = ""
    if not os.path.exists(p):
        head = "# 已沉淀规则\n\n> 由 fxai-content-review 从经验库晋升而来。规则应写进「落点技能」的 SKILL.md 硬约束。\n"
    with open(p, "a", encoding="utf-8") as f:
        f.write(head + "\n".join(block))
    return {"id": eid, "领域": row["scope"], "落点技能": skill, "已追加到": p}


# ---------- 素材 ----------

def mat_add(path, category, subcat="", kind="", ratio="", duration=0,
            scope="通用", status="可用", tags="", note=""):
    """只登记本地最终产物。ComfyUI 服务端的参考图/中间帧属生产过程，不入库。"""
    _check(scope, SCOPES, "领域")
    _check(category, MAT_CATS, "分类")
    _check(status, MAT_STATUS, "状态")
    c = connect()
    ids = _tag_ids(c, tags.split(","), scope) if tags else []
    exist = os.path.exists(path)
    cur = c.execute("INSERT INTO materials(created,name,path,scope,category,subcat,kind,ratio,duration,status,verified,note)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_today(), os.path.basename(path), path, scope, category, subcat, kind, ratio,
                     duration, status, 1 if exist else 0, note))
    mid = cur.lastrowid
    for t in ids:
        c.execute("INSERT OR IGNORE INTO material_tags(material_id,tag_id) VALUES(?,?)", (mid, t))
    c.commit()
    c.close()
    return {"id": mid, "领域": scope, "verified": 1 if exist else 0}


def mat_list(category="", tag="", status="", scope="", limit=100):
    """查某领域的素材时带上「通用」的——通用素材是跨岗位共用的。"""
    c = connect()
    sql = ("SELECT m.*, GROUP_CONCAT(t.name) tags FROM materials m "
           "LEFT JOIN material_tags mt ON mt.material_id=m.id "
           "LEFT JOIN tags t ON t.id=mt.tag_id WHERE 1=1")
    args = []
    if scope:
        sql += " AND (m.scope=? OR m.scope='通用')"
        args.append(scope)
    if category:
        sql += " AND m.category=?"
        args.append(category)
    if status:
        sql += " AND m.status=?"
        args.append(status)
    sql += " GROUP BY m.id"
    if tag:
        sql += " HAVING tags LIKE ?"
        args.append("%" + tag + "%")
    sql += " ORDER BY m.id DESC LIMIT ?"
    args.append(limit)
    r = _rows(c.execute(sql, args))
    c.close()
    return r


def mat_use(mid):
    c = connect()
    c.execute("UPDATE materials SET uses=uses+1, last_used=? WHERE id=?", (_today(), mid))
    c.commit()
    c.close()
    return mid


# ---------- 标签 ----------

def tag_add(name, kind="", alias="", scope="通用"):
    _check(scope, SCOPES, "领域")
    c = connect()
    c.execute("INSERT OR REPLACE INTO tags(name,kind,alias,scope) VALUES(?,?,?,?)",
              (name.strip(), kind, alias, scope))
    c.commit()
    c.close()
    return {"名称": name.strip(), "领域": scope}


def tag_list(scope=""):
    c = connect()
    if scope:
        r = _rows(c.execute("SELECT * FROM tags WHERE scope='通用' OR scope=? ORDER BY scope,kind,name", (scope,)))
    else:
        r = _rows(c.execute("SELECT * FROM tags ORDER BY scope,kind,name"))
    c.close()
    return r


# ---------- 缺口 ----------

def gap_add(need, priority="中", exp_id=0, scope="通用"):
    _check(scope, SCOPES, "领域")
    c = connect()
    cur = c.execute("INSERT INTO gaps(created,exp_id,scope,need,priority,status) VALUES(?,?,?,?,?,?)",
                    (_today(), exp_id, scope, need, priority, "待补"))
    c.commit()
    rid = cur.lastrowid
    c.close()
    return {"id": rid, "领域": scope}


def gap_list(status="待补", scope=""):
    c = connect()
    sql = "SELECT * FROM gaps WHERE status=?"
    args = [status]
    if scope:
        sql += " AND scope=?"
        args.append(scope)
    sql += " ORDER BY id DESC"
    r = _rows(c.execute(sql, args))
    c.close()
    return r


def gap_done(gid):
    c = connect()
    c.execute("UPDATE gaps SET status='已补' WHERE id=?", (gid,))
    c.commit()
    c.close()
    return gid


def _out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _add_common(p, with_scope=True):
    if with_scope:
        p.add_argument("--领域", default="通用")
    return p


def main():
    ap = argparse.ArgumentParser(prog="db.py", description="fxai-content-review 台账库（按领域隔离）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="建库建表 / 给旧库补领域列")

    sc = sub.add_parser("scopes", help="列出受控领域")
    sc.add_argument("--noop", action="store_true", help=argparse.SUPPRESS)

    e = sub.add_parser("exp", help="经验")
    es = e.add_subparsers(dest="act", required=True)
    p = es.add_parser("add")
    p.add_argument("--领域", default="通用")
    p.add_argument("--分类", required=True)
    p.add_argument("--关键词", required=True)
    p.add_argument("--结论", required=True)
    p.add_argument("--证据", default="")
    p.add_argument("--动作", required=True)
    p.add_argument("--关联", default="")
    p.add_argument("--状态", default="待观察")
    p.add_argument("--备注", default="")
    p = es.add_parser("list")
    p.add_argument("--领域", default="")
    p.add_argument("--分类", default="")
    p.add_argument("--状态", default="")
    p.add_argument("--min", type=int, default=0)
    p = es.add_parser("stat")
    p.add_argument("--领域", default="")
    p = es.add_parser("export")
    p.add_argument("--领域", default="")
    p = es.add_parser("pending", help="该沉淀成规则但还没沉淀的")
    p.add_argument("--领域", default="")
    p = es.add_parser("promote", help="把一条经验沉淀成规则")
    p.add_argument("--id", type=int, required=True)

    m = sub.add_parser("material", help="素材")
    ms = m.add_subparsers(dest="act", required=True)
    p = ms.add_parser("add")
    p.add_argument("--领域", default="通用")
    p.add_argument("--path", required=True)
    p.add_argument("--分类", required=True)
    p.add_argument("--子类", default="")
    p.add_argument("--类型", default="")
    p.add_argument("--画幅", default="")
    p.add_argument("--时长", type=float, default=0)
    p.add_argument("--状态", default="可用")
    p.add_argument("--标签", default="")
    p.add_argument("--备注", default="")
    p = ms.add_parser("list")
    p.add_argument("--领域", default="")
    p.add_argument("--分类", default="")
    p.add_argument("--标签", default="")
    p.add_argument("--状态", default="")
    p = ms.add_parser("use")
    p.add_argument("--id", type=int, required=True)

    t = sub.add_parser("tag", help="标签")
    ts = t.add_subparsers(dest="act", required=True)
    p = ts.add_parser("add")
    p.add_argument("--领域", default="通用")
    p.add_argument("--名称", required=True)
    p.add_argument("--类别", default="")
    p.add_argument("--别名", default="")
    p = ts.add_parser("list")
    p.add_argument("--领域", default="")

    g = sub.add_parser("gap", help="素材缺口")
    gs = g.add_subparsers(dest="act", required=True)
    p = gs.add_parser("add")
    p.add_argument("--领域", default="通用")
    p.add_argument("--缺什么", required=True)
    p.add_argument("--优先级", default="中")
    p.add_argument("--经验", type=int, default=0)
    p = gs.add_parser("list")
    p.add_argument("--领域", default="")
    p.add_argument("--状态", default="待补")
    p = gs.add_parser("done")
    p.add_argument("--id", type=int, required=True)

    a = ap.parse_args()

    if a.cmd == "init":
        _out({"db": init_db(), "领域": SCOPES})
    elif a.cmd == "scopes":
        _out(SCOPES)
    elif a.cmd == "exp":
        if a.act == "add":
            _out(exp_add(a.关联, a.分类, a.关键词, a.结论, a.证据, a.动作, a.领域, a.状态, a.备注))
        elif a.act == "list":
            _out(exp_list(a.领域, a.分类, a.状态, a.min))
        elif a.act == "stat":
            _out(exp_stat(a.领域))
        elif a.act == "export":
            _out(exp_export(a.领域))
        elif a.act == "pending":
            _out(exp_pending(a.领域))
        elif a.act == "promote":
            _out(exp_promote(a.id))
    elif a.cmd == "material":
        if a.act == "add":
            _out(mat_add(a.path, a.分类, a.子类, a.类型, a.画幅, a.时长, a.领域, a.状态, a.标签, a.备注))
        elif a.act == "list":
            _out(mat_list(a.分类, a.标签, a.状态, a.领域))
        elif a.act == "use":
            _out({"id": mat_use(a.id)})
    elif a.cmd == "tag":
        if a.act == "add":
            _out(tag_add(a.名称, a.类别, a.别名, a.领域))
        else:
            _out(tag_list(a.领域))
    elif a.cmd == "gap":
        if a.act == "add":
            _out(gap_add(a.缺什么, a.优先级, a.经验, a.领域))
        elif a.act == "list":
            _out(gap_list(a.状态, a.领域))
        else:
            _out({"id": gap_done(a.id)})


if __name__ == "__main__":
    main()
