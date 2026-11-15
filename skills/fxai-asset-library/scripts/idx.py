#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""素材库检索引擎（SQLite 倒排索引）。

为什么不用 FTS5：
  FTS5 的 unicode61 不切中文（整段 CJK 当一个 token），trigram 又要求查询 ≥3 字，
  「录屏」「开实例」这种 1~2 字中文查询直接失配。所以自建倒排：
  CJK 段 → 全串 + 全部二元组（bigram），拉丁/数字 → 整词。
  这样「录屏」命中「录屏」，「仙宫云」可用 bigram「仙宫/宫云」命中「仙宫云镜像广场」的长句。

存哪：<库根>/_库/.index/lib.db（可随时删，`lib.py reindex` 重建）
表：
  assets  每条素材一行（结构化字段，用于过滤与展示）
  terms   倒排表 (term, code, w)，w = 该词在该条里的最高字段权重
  refs    项目引用 (code, project, file)，由扫项目台账得到
  meta    账本指纹（mtime+size）与构建时间，用于判新鲜度
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime

DB_DIR = ".index"
DB_NAME = "lib.db"
SCHEMA_VER = 2      # 改了表/索引结构就 +1，旧库自动补建

# 字段权重：同一个词出现在多处，取最高权重那一处的分
FIELD_W = (
    ("编号", 12),
    ("文件", 8),
    ("原名", 6),
    ("主题", 6),
    ("标签", 6),
    ("形态", 4),
    ("用途", 3),
    ("规格", 2),
    ("来源", 1),
)

CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af"
SEG_RE = re.compile(rf"[{CJK}]+|[a-z0-9]+(?:[._+\-][a-z0-9]+)*")


# ---------------------------------------------------------------- 分词

def terms_of(text: str) -> set:
    """把一段文本切成检索词集合。CJK 段出全串 + 二元组。"""
    if not text:
        return set()
    out = set()
    for seg in SEG_RE.findall(str(text).lower()):
        seg = seg.strip()
        if not seg:
            continue
        out.add(seg)
        if len(seg) > 2 and re.match(rf"[{CJK}]", seg[0]):
            for i in range(len(seg) - 1):
                out.add(seg[i:i + 2])
    return out


def query_terms(word: str) -> set:
    """一个查询词 → 候选词集合。全串本身也留着（可能直接命中短字段）。"""
    return terms_of(word)


def row_terms(it: dict) -> dict:
    """一条素材 → {term: 最高权重}。"""
    w = {}
    for field, wt in FIELD_W:
        v = it.get(field)
        if not v:
            continue
        if isinstance(v, (list, tuple)):
            v = " ".join(str(x) for x in v)
        for t in terms_of(v):
            if w.get(t, 0) < wt:
                w[t] = wt
    return w


# ---------------------------------------------------------------- 连接与库

def db_path(libdir: str) -> str:
    return os.path.join(libdir, DB_DIR, DB_NAME)


def connect(libdir: str) -> sqlite3.Connection:
    p = db_path(libdir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    # 冷启动判断放在一次 sqlite_master 查询里，避免每次检索都跑建表语句
    conn = sqlite3.connect(p, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=NORMAL")   # 每次连接都要设，否则默认 FULL 会 fsync
    try:
        has = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assets'").fetchone()
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError:
        has, ver = None, 0
    if not has or ver < SCHEMA_VER:
        conn.execute("PRAGMA journal_mode=WAL")
        init_schema(conn)
        conn.execute("PRAGMA user_version=%d" % SCHEMA_VER)
    return conn


def init_schema(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS assets(
      code TEXT PRIMARY KEY, file TEXT, kind TEXT, form TEXT, theme TEXT,
      spec TEXT, size INTEGER, human TEXT, added TEXT, thumb INTEGER,
      desc_ TEXT, tags TEXT, src TEXT, hash TEXT);
    CREATE TABLE IF NOT EXISTS terms(term TEXT, code TEXT, w INTEGER,
      PRIMARY KEY(term, code)) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS ix_terms_term ON terms(term);
    CREATE INDEX IF NOT EXISTS ix_terms_code ON terms(code);
    CREATE TABLE IF NOT EXISTS refs(code TEXT, project TEXT, file TEXT);
    CREATE INDEX IF NOT EXISTS ix_refs_code ON refs(code);
    CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
    """)
    conn.commit()


def fingerprint(libdir: str) -> str:
    """账本指纹：mtime + size。变了就重建。"""
    p = os.path.join(libdir, "库内清单.json")
    try:
        st = os.stat(p)
        return f"{int(st.st_mtime)}:{st.st_size}"
    except OSError:
        return "0:0"


def meta_get(conn, k, d=""):
    r = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r["v"] if r else d


def meta_set(conn, k, v):
    conn.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                 (k, str(v)))


def is_fresh(conn, libdir) -> bool:
    return meta_get(conn, "fingerprint") == fingerprint(libdir) and \
        conn.execute("SELECT COUNT(*) c FROM assets").fetchone()["c"] > 0


# ---------------------------------------------------------------- 构建

def _asset_row(it: dict, libdir: str) -> tuple:
    code = it.get("编号", "")
    f = it.get("文件", "")
    th = os.path.join(libdir, ".thumbs", code + ".jpg")
    return (
        code, f, it.get("类型", ""), it.get("形态", ""), it.get("分类", ""),
        it.get("规格", ""), int(it.get("大小字节") or 0), it.get("大小", ""),
        it.get("入库", ""), 1 if os.path.exists(th) else 0,
        it.get("用途", ""), " / ".join(it.get("标签") or []),
        it.get("来源", ""), it.get("md5_1m", ""),
    )


def build(libdir: str, lib: dict, refs: bool = True, refs_root: str = "", quiet=False) -> dict:
    t0 = time.time()
    conn = connect(libdir)
    rows = [(_asset_row(it, libdir), it) for it in lib.get("素材", [])]
    term_rows = []
    for r, it in rows:
        for t, w in row_terms(it).items():
            term_rows.append((t, r[0], w))
    with conn:
        conn.execute("DELETE FROM assets")
        conn.execute("DELETE FROM terms")
        conn.executemany(
            "INSERT OR REPLACE INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [r for r, _ in rows])
        conn.executemany("INSERT OR IGNORE INTO terms(term,code,w) VALUES(?,?,?)", term_rows)
        meta_set(conn, "fingerprint", fingerprint(libdir))
        meta_set(conn, "built_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        meta_set(conn, "count", len(rows))
        if refs:
            n = refs_build(conn, refs_root)
            meta_set(conn, "refs_root", refs_root)
            meta_set(conn, "refs_count", n)
    ms = int((time.time() - t0) * 1000)
    meta_set(conn, "build_ms", ms)
    conn.commit()
    conn.close()
    if not quiet:
        print(f"索引已建：{len(rows)} 条素材 / {len(term_rows)} 个倒排项 / {ms} ms")
    return {"assets": len(rows), "terms": len(term_rows), "ms": ms}


def fresh(libdir: str) -> bool:
    """只看账本指纹，不读台账。用于避免每次检索都解析整份 JSON。"""
    p = db_path(libdir)
    if not os.path.exists(p):
        return False
    try:
        conn = sqlite3.connect(p, timeout=10)
        conn.row_factory = sqlite3.Row
        ok = (meta_get(conn, "fingerprint") == fingerprint(libdir)) and \
            conn.execute("SELECT COUNT(*) c FROM assets").fetchone()[0] > 0
        conn.close()
        return ok
    except Exception:
        return False


def upsert_row(libdir: str, it: dict):
    """单条增量更新（add / set 后调用）。"""
    conn = connect(libdir)
    code = it.get("编号", "")
    with conn:
        conn.execute("DELETE FROM terms WHERE code=?", (code,))
        conn.execute("DELETE FROM assets WHERE code=?", (code,))
        conn.execute("INSERT OR REPLACE INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     _asset_row(it, libdir))
        conn.executemany("INSERT OR IGNORE INTO terms(term,code,w) VALUES(?,?,?)",
                         [(t, code, w) for t, w in row_terms(it).items()])
        meta_set(conn, "count", conn.execute("SELECT COUNT(*) c FROM assets").fetchone()["c"])
        # 账本已变，指纹跟着走，避免下次 find 误判过期重建
        meta_set(conn, "fingerprint", fingerprint(libdir))
    conn.close()


def delete_row(libdir: str, code: str):
    conn = connect(libdir)
    with conn:
        conn.execute("DELETE FROM terms WHERE code=?", (code,))
        conn.execute("DELETE FROM assets WHERE code=?", (code,))
        conn.execute("DELETE FROM refs WHERE code=?", (code,))
        meta_set(conn, "fingerprint", fingerprint(libdir))
    conn.close()


# ---------------------------------------------------------------- 项目引用

def refs_build(conn, root: str) -> int:
    conn.execute("DELETE FROM refs")
    if not root or not os.path.isdir(root):
        return 0
    skip = {".git", "node_modules", "__pycache__", "_tmp", ".workbuddy", DB_DIR, ".thumbs"}
    base_depth = root.rstrip("\\/").count(os.sep)
    n = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [x for x in dns if x not in skip and not x.startswith(".")]
        if "素材登记.json" in fns:
            try:
                with open(os.path.join(dp, "素材登记.json"), encoding="utf-8") as f:
                    lg = json.load(f)
            except Exception:
                lg = {}
            proj = os.path.relpath(dp, root).replace(os.sep, "/")
            for it in lg.get("素材", []):
                code = it.get("库内")
                if code:
                    conn.execute("INSERT INTO refs(code,project,file) VALUES(?,?,?)",
                                 (code, proj, it.get("文件", "")))
                    n += 1
        if dp.count(os.sep) - base_depth > 4:
            dns[:] = []
    return n


def refs_of(libdir: str, code: str) -> list:
    p = db_path(libdir)
    if not os.path.exists(p):
        return []
    conn = connect(libdir)
    rows = conn.execute("SELECT project, file FROM refs WHERE code=?", (code,)).fetchall()
    conn.close()
    return [f"{r['project']} → {r['file']}" for r in rows]


# ---------------------------------------------------------------- 检索

def _filter_sql(form="", theme="", tag=""):
    where, args = [], []
    if form:
        where.append("a.form=?")
        args.append(form)
    if theme:
        where.append("a.theme=?")
        args.append(theme)
    if tag:
        where.append("a.tags LIKE ?")
        args.append(f"%{tag}%")
    return (" AND " + " AND ".join(where)) if where else "", args


def _codes(conn, pairs, nword, minw, cap):
    """只在倒排表里聚合出编号与分数，不回查 assets（最贵的就是那一步）。"""
    vals = ",".join(["(?,?)"] * len(pairs))
    args = [x for p in pairs for x in p]
    lim = " LIMIT %d" % int(cap) if cap else ""
    sql = (
        "WITH q(wi, term) AS (VALUES %s) "
        "SELECT t.code AS code, SUM(t.w) AS sc, COUNT(DISTINCT q.wi) AS nw "
        "FROM q JOIN terms t ON t.term = q.term "
        "WHERE t.w >= ? "
        "GROUP BY t.code HAVING nw = ? "
        "ORDER BY sc DESC, t.code%s" % (vals, lim)
    )
    return [(r["code"], r["sc"])
            for r in conn.execute(sql, args + [minw, nword]).fetchall()]


def _hydrate(conn, codes):
    out = {}
    CH = 400
    for i in range(0, len(codes), CH):
        chunk = codes[i:i + CH]
        ph = ",".join("?" * len(chunk))
        for r in conn.execute("SELECT * FROM assets WHERE code IN (%s)" % ph, chunk):
            out[r["code"]] = dict(r)
    return out


def search(libdir: str, words: list, form="", theme="", tag="",
           limit: int = 0, lib: dict | None = None, refs_root="") -> list:
    """返回 [ (score, asset_dict), ... ]。words 之间 AND，词内子词 OR。

    两步走：① 倒排里聚合出编号分数（先只碰高权重字段，不够再放宽到全文）
    ② 只回查这批编号的资产行。多关键词查询因此不会被 assets 反复回查拖慢。
    """
    conn = connect(libdir)
    if lib is not None and not is_fresh(conn, libdir):
        build(libdir, lib, refs=bool(refs_root), refs_root=refs_root, quiet=True)
        conn.close()
        conn = connect(libdir)

    pairs = []
    for i, w in enumerate(words):
        for t in sorted(query_terms(w)):
            pairs.append((i, t))
    if not pairs:
        conn.close()
        return []
    pairs = pairs[:200]

    filtered = bool(form or theme or tag)
    want = limit or 0
    # 有结构化过滤时多取一些候选，过滤后还能填满 limit
    cap = (want * 10 if filtered else want * 3) if want else 0

    codes = _codes(conn, pairs, len(words), 4, cap)
    if not want or len(codes) < want:
        wide = _codes(conn, pairs, len(words), 0, cap)
        if len(wide) > len(codes):
            seen = {c for c, _ in codes}
            codes = codes + [(c, sc) for c, sc in wide if c not in seen]
            codes.sort(key=lambda x: (-x[1], x[0]))
            codes = codes[:cap] if cap else codes

    assets = _hydrate(conn, [c for c, _ in codes])
    conn.close()

    out = []
    for code, sc in codes:
        a = assets.get(code)
        if not a:
            continue
        if form and a.get("form") != form:
            continue
        if theme and a.get("theme") != theme:
            continue
        if tag and tag not in (a.get("tags") or ""):
            continue
        out.append((sc, a))
        if want and len(out) >= want:
            break
    return out


def stats(libdir: str) -> dict:
    p = db_path(libdir)
    if not os.path.exists(p):
        return {}
    conn = connect(libdir)
    d = {
        "库": libdir,
        "数据库": p,
        "大小": os.path.getsize(p),
        "素材": conn.execute("SELECT COUNT(*) c FROM assets").fetchone()["c"],
        "倒排项": conn.execute("SELECT COUNT(*) c FROM terms").fetchone()["c"],
        "去重词": conn.execute("SELECT COUNT(DISTINCT term) c FROM terms").fetchone()["c"],
        "引用": conn.execute("SELECT COUNT(*) c FROM refs").fetchone()["c"],
        "构建": meta_get(conn, "built_at"),
        "耗时": meta_get(conn, "build_ms") + " ms",
        "新鲜": is_fresh(conn, libdir),
        "引用扫描根": meta_get(conn, "refs_root"),
    }
    conn.close()
    return d


# ---------------------------------------------------------------- 独立 CLI

def main():
    import argparse
    ap = argparse.ArgumentParser(prog="idx.py", description="素材库索引（供 lib.py 调用，也可单跑）")
    ap.add_argument("cmd", choices=("build", "stats", "find"))
    ap.add_argument("libdir", help="库内目录，即 <库根>/_库")
    ap.add_argument("words", nargs="*")
    ap.add_argument("--refs-root", default="")
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args()

    libdir = os.path.abspath(a.libdir)
    if a.cmd == "build":
        with open(os.path.join(libdir, "库内清单.json"), encoding="utf-8") as f:
            lib = json.load(f)
        print(json.dumps(build(libdir, lib, refs_root=a.refs_root), ensure_ascii=False))
    elif a.cmd == "stats":
        print(json.dumps(stats(libdir), ensure_ascii=False, indent=2, default=str))
    else:
        with open(os.path.join(libdir, "库内清单.json"), encoding="utf-8") as f:
            lib = json.load(f)
        for s, r in search(libdir, a.words, limit=a.limit, lib=lib, refs_root=a.refs_root):
            print(f"{s:>4}  {r['code']}  {r['file']}")


if __name__ == "__main__":
    main()
