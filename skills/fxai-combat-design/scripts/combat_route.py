#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fxai-combat-design 资料库检索入口（明文版）。

用法：
    python combat_route.py <scenes|designs|moves|skills|scripts|glossary> --query "用户原文与关键词"
    python combat_route.py --read <domain>/<id>
    python combat_route.py --list <domain>
    python combat_route.py --terms [类别|关键词] [--tier high|mid|low]
    python combat_route.py --validate
    python combat_route.py --about

资料全部是明文 JSON，放在 data/<domain>.json，可直接打开编辑维护。
本脚本只做确定性检索：不联网、不写盘、无第三方依赖，Python 3.8+ 可跑。
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

# 六个资料域：短名 -> 中文显示名
DOMAINS = {
    "scenes": "场景库",
    "designs": "动作方案库",
    "moves": "招式库",
    "skills": "技能库",
    "scripts": "案例库",
    "glossary": "招式术语字典",
}

# 术语档位（glossary 专用）：频次越高响应越强
TIER_NAME = {
    "high": "高频",
    "mid": "中频",
    "low": "低频",
    "ref": "参考",
}

# 排序用：命中越靠前，权重越高
W_KEYWORD = 2   # 每个强关键词命中
W_NAME = 3      # 条目名被用户原文直接点到

ABOUT = {
    "skill": "fxai-combat-design",
    "title": "战斗设计与动作分镜（fxai-combat-design）",
    "author": "凤希 AI · fxai 系列",
    "note": "本技能资料为原创，明文存储，可直接编辑。",
}


def die(msg, code=2):
    sys.stderr.write(msg + "\n")
    raise SystemExit(code)


def load_domain(domain):
    """读取一个资料域，返回 dict；域不存在直接报错。"""
    if domain not in DOMAINS:
        die("未知资料域：%s（可选：%s）" % (domain, "、".join(DOMAINS)))
    path = DATA_DIR / (domain + ".json")
    if not path.is_file():
        die("资料文件缺失：%s" % path)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def entry_keywords(entry):
    """强关键词（有区分度，能单独构成命中）。"""
    return list(entry.get("keywords") or [])


def entry_weak(entry):
    """弱关键词（单字 / 高频通用词，只在全库无强命中时回退使用）。"""
    return list(entry.get("weak_keywords") or [])


def score_entry(entry, query):
    """对单条资料打分，返回 (强命中列表, 弱命中列表, 分数)。"""
    strong = [k for k in entry_keywords(entry) if k and k in query]
    weak = [k for k in entry_weak(entry) if k and k in query]
    name = entry.get("name") or ""
    name_hit = bool(name) and (name in query or (entry.get("short") or "") in query)
    score = len(strong) * W_KEYWORD + (W_NAME if name_hit else 0)
    return strong, weak, score, name_hit


def brief(entry):
    """候选的精简视图。"""
    return {
        "id": entry.get("id"),
        "name": entry.get("name"),
        "summary": entry.get("summary", ""),
        "medium": entry.get("medium"),
        "avoid_when": entry.get("avoid_when") or [],
    }


def cmd_query(domain, query, top):
    """检索：先强关键词，全库无强命中再回退弱关键词。"""
    doc = load_domain(domain)
    entries = doc.get("entries") or []
    if not query or not query.strip():
        die("--query 需要非空字符串")

    strong_rows, weak_rows = [], []
    for e in entries:
        strong, weak, score, name_hit = score_entry(e, query)
        if strong:
            row = brief(e)
            row["hit_count"] = len(strong)
            row["matched_keywords"] = strong
            row["score"] = score
            row["name_hit"] = name_hit
            strong_rows.append(row)
        elif weak:
            row = brief(e)
            row["hit_count"] = len(weak)
            row["matched_keywords"] = weak
            row["score"] = score
            weak_rows.append(row)

    weak_fallback = not strong_rows and bool(weak_rows)
    rows = strong_rows if strong_rows else weak_rows
    rows.sort(key=lambda r: (-r["score"], -r["hit_count"], str(r["id"])))

    result = {
        "domain": domain,
        "display": doc.get("display", DOMAINS[domain]),
        "query": query,
        "match_rule": doc.get("match_rule", ""),
        "weak_fallback": weak_fallback,
        "primary": rows[0] if rows else None,
        "eligible": rows,
        "available": [brief(e) for e in entries],
    }
    if top and len(result["eligible"]) > top:
        result["eligible"] = result["eligible"][:top]
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_read(target):
    """读取一条正文：<domain>/<id>。招式命中后附带推荐术语。"""
    if "/" not in target:
        die("用法：--read <domain>/<id>")
    domain, eid = target.split("/", 1)
    doc = load_domain(domain)
    for e in doc.get("entries") or []:
        if str(e.get("id")) == eid.strip():
            print(e.get("body", ""))
            terms = e.get("terms") or []
            if terms:
                print("")
                print("## 推荐术语（取自 glossary，可直接取用）")
                for t in terms:
                    print("- %s" % t)
            return
    die("未找到条目：%s（域 %s）" % (eid, domain))


def cmd_terms(target, tier):
    """术语取词：按类别或关键词列术语，带频次与档位。

    不传 target 时列出全部类别；传类别 id 列该类全部术语；
    传术语关键词则模糊匹配术语名与关键词。
    """
    doc = load_domain("glossary")
    entries = doc.get("entries") or []
    cats = {c.get("id"): c for c in (doc.get("categories") or [])}

    if not target:
        print("术语类别（%d 类 / %d 条）：" % (len(cats), len(entries)))
        for cid, c in cats.items():
            n = sum(1 for e in entries if e.get("category") == cid)
            print("  %-16s %-28s %d 条" % (cid, c.get("name"), n))
        print("\n用法：--terms <类别|关键词> [--tier high|mid|low]")
        return

    rows = [e for e in entries if e.get("category") == target]
    if not rows:
        t = target.strip()
        rows = [e for e in entries
                if t in (e.get("name") or "")
                or any(t in k for k in entry_keywords(e))]
    if not rows:
        die("没有匹配的类别或术语：%s（可用 --terms 查看类别）" % target)
    if tier:
        rows = [e for e in rows if e.get("tier") == tier]
        if not rows:
            die("该类别下没有档位为 %s 的术语" % tier)

    rows.sort(key=lambda e: (-(e.get("freq") or 0), str(e.get("id"))))
    for e in rows:
        freq = e.get("freq")
        badge = str(freq) if freq else TIER_NAME.get(e.get("tier"), e.get("tier") or "-")
        print("%-20s %-12s %-5s %s" % (e.get("name"), e.get("category"), badge, e.get("summary", "")))


def cmd_list(domain):
    """列出某域全部条目的编号与名称。"""
    doc = load_domain(domain)
    for e in doc.get("entries") or []:
        print("%-8s %s" % (e.get("id"), e.get("name")))


def cmd_validate():
    """自检：文件齐全、编号唯一、正文非空、关键词可用。"""
    problems = []
    for domain, display in DOMAINS.items():
        path = DATA_DIR / (domain + ".json")
        if not path.is_file():
            problems.append("缺文件：%s" % path)
            continue
        with path.open("r", encoding="utf-8") as f:
            doc = json.load(f)
        entries = doc.get("entries") or []
        if not entries:
            problems.append("%s：没有条目" % domain)
            continue
        seen = set()
        cats = set()
        if domain == "glossary":
            cats = {c.get("id") for c in (doc.get("categories") or [])}
            if not cats:
                problems.append("glossary：缺 categories 类别表")
        for e in entries:
            eid = str(e.get("id", ""))
            tag = "%s/%s" % (domain, eid or "?")
            if not eid:
                problems.append("%s：缺 id" % tag)
            if eid in seen:
                problems.append("%s：id 重复" % tag)
            seen.add(eid)
            if not e.get("name"):
                problems.append("%s：缺 name" % tag)
            if not entry_keywords(e):
                problems.append("%s：缺 keywords" % tag)
            if domain == "glossary":
                cat = e.get("category")
                if not cat:
                    problems.append("%s：缺 category" % tag)
                elif cat not in cats:
                    problems.append("%s：category 未在 categories 中登记（%s）" % (tag, cat))
            body = (e.get("body") or "").strip()
            if not body:
                problems.append("%s：正文为空" % tag)
            for ph in ("TODO", "待补", "占位", "PLACEHOLDER", "xxx", "XXX"):
                if ph in body:
                    problems.append("%s：正文含占位符「%s」" % (tag, ph))
    if problems:
        print("校验未通过，共 %d 处：" % len(problems))
        for p in problems:
            print("  - " + p)
        raise SystemExit(1)
    total = 0
    for domain in DOMAINS:
        with (DATA_DIR / (domain + ".json")).open("r", encoding="utf-8") as f:
            total += len(json.load(f).get("entries") or [])
    print("校验通过：%d 个资料域，共 %d 条。" % (len(DOMAINS), total))


def cmd_about():
    for k in ("title", "skill", "author", "note"):
        print("%s：%s" % (k, ABOUT[k]))


def main():
    ap = argparse.ArgumentParser(add_help=True, description="fxai-combat-design 资料库检索")
    ap.add_argument("domain", nargs="?", help="scenes|designs|moves|skills|scripts|glossary")
    ap.add_argument("--query", "-q", help="检索词（用户原文与提炼关键词）")
    ap.add_argument("--read", "-r", help="读取正文：<domain>/<id>")
    ap.add_argument("--list", "-l", nargs="?", const="__all__", help="列出某域条目")
    ap.add_argument("--terms", "-t", nargs="?", const="",
                    help="术语取词：传类别或关键词；不传则列全部类别（glossary）")
    ap.add_argument("--tier", help="术语档位过滤：high|mid|low")
    ap.add_argument("--top", type=int, default=0, help="eligible 最多返回几条（0=全部）")
    ap.add_argument("--validate", action="store_true", help="自检资料库")
    ap.add_argument("--about", action="store_true", help="显示技能信息")
    args = ap.parse_args()

    if args.about:
        return cmd_about()
    if args.validate:
        return cmd_validate()
    if args.read:
        return cmd_read(args.read)
    if args.terms is not None:
        return cmd_terms(args.terms, args.tier)
    if args.list is not None:
        if args.list == "__all__":
            for d in DOMAINS:
                print("## %s" % d)
                cmd_list(d)
        else:
            return cmd_list(args.list)
        return
    if args.domain and args.query:
        return cmd_query(args.domain, args.query, args.top)

    ap.print_help()
    raise SystemExit(2)


if __name__ == "__main__":
    main()
