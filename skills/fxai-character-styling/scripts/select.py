# -*- coding: utf-8 -*-
"""选图：按套系+三级分类树筛选，pick 逐层选择，输出 图片 路径（接口不变）。"""
import os
import csv
import argparse

FIELDS = ["id", "类型", "套系", "一级分类", "二级分类", "三级分类",
          "名称", "标签", "角度", "图片", "来源", "备注"]


def data_dir():
    return os.environ.get("FXAI_POSE_COSTUME_DATA") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data")


def _load():
    p = os.path.join(data_dir(), "assets.csv")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _gate(rows, suite):
    """套系门禁：默认只出 SFW；NSFW 需显式。"""
    return [r for r in rows if r.get("套系") == suite]


def _ask(title, options):
    """逐层选择。非交互（无 tty）时打印候选并报提示。"""
    if not options:
        raise SystemExit("[pick] 该层无候选，换上层筛选")
    print("\n== %s ==" % title)
    for i, opt in enumerate(options, 1):
        print("  %d) %s" % (i, opt))
    try:
        raw = input("选编号 (回车=1): ").strip()
    except EOFError:
        raise SystemExit("[pick] 需要交互终端；非交互请用 find / list")
    n = int(raw) if raw.isdigit() else 1
    if not 1 <= n <= len(options):
        raise SystemExit("[pick] 编号越界")
    return options[n - 1]


def cmd_list(a):
    rows = _gate(_load(), a.套系)
    if a.类型:
        rows = [r for r in rows if r.get("类型") == a.类型]
    levels = ["一级分类", "二级分类", "三级分类"]
    for lv in levels:
        vals = sorted({r.get(lv) for r in rows if r.get(lv)})
        print("%s (%d): %s" % (lv, len(vals), " | ".join(vals)))
    print("# 条目 %d" % len(rows))


def cmd_find(a):
    rows = _gate(_load(), a.套系)
    if a.类型:
        rows = [r for r in rows if r.get("类型") == a.类型]
    kw = a.关键词 or ""
    if kw:
        rows = [r for r in rows if kw in (r.get("名称", "") + " " + r.get("标签", "") + " " + r.get("角度", ""))]
    for r in rows[:a.limit]:
        print("%s\t%s\t%s\t%s\t%s" % (
            r["id"], r.get("名称", ""), r.get("角度", ""),
            "/".join(x for x in (r.get("一级分类"), r.get("二级分类"), r.get("三级分类")) if x),
            r["图片"]))
    print("# 命中 %d 条" % len(rows))


def cmd_pick(a):
    rows = _gate(_load(), a.套系)
    if a.类型:
        rows = [r for r in rows if r.get("类型") == a.类型]
    # 逐层过滤：类型 → 套系（已 gate）→ 一级 → 二级 → 三级 → 条目
    for lv in ("一级分类", "二级分类", "三级分类"):
        vals = sorted({r.get(lv) for r in rows if r.get(lv)})
        if not vals:
            break
        chosen = _ask(lv, vals)
        rows = [r for r in rows if r.get(lv) == chosen]
    names = sorted({r.get("名称", "") for r in rows})
    picked = _ask("名称", names)
    rows = [r for r in rows if r.get("名称") == picked]
    if not rows:
        raise SystemExit("[pick] 无匹配条目")
    r = rows[0]
    print("\n[选中] %s | %s | %s" % (r["id"], r.get("名称"), r.get("角度", "")))
    print(r["图片"])  # 接口不变：原样输出相对路径


def cmd_path(a):
    for r in _load():
        if r.get("id") == a.id:
            rel = r["图片"]
            if a.resolve:
                rel = rel if os.path.isabs(rel) else os.path.normpath(os.path.join(data_dir(), rel))
            print(rel)
            return
    raise SystemExit("[path] 未找到 id: " + a.id)


def main():
    ap = argparse.ArgumentParser(description="选图（SFW 默认，NSFW 需显式）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list")
    p.add_argument("--类型", dest="类型", default="")
    p.add_argument("--套系", dest="套系", default="SFW")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("find")
    p.add_argument("--关键词", dest="关键词", default="")
    p.add_argument("--类型", dest="类型", default="")
    p.add_argument("--套系", dest="套系", default="SFW")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(fn=cmd_find)

    p = sub.add_parser("pick")
    p.add_argument("--类型", dest="类型", default="")
    p.add_argument("--套系", dest="套系", default="SFW")
    p.set_defaults(fn=cmd_pick)

    p = sub.add_parser("path")
    p.add_argument("id")
    p.add_argument("--resolve", action="store_true")
    p.set_defaults(fn=cmd_path)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
