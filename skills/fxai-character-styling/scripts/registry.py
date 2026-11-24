# -*- coding: utf-8 -*-
"""素材登记表：init / add / scan / catalog / check。只记路径，不搬图。

纯壳口径：本技能不判定素材内容；用户自建任意子目录，scan 按目录层级索引。
"""
import os
import csv
import argparse

FIELDS = ["id", "类型", "一级分类", "二级分类", "三级分类",
          "名称", "标签", "角度", "图片", "来源", "备注"]
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MAX_LEVELS = 3


def data_dir():
    return os.environ.get("FXAI_CHARACTER_STYLING_DATA") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data")


def table_path():
    return os.path.join(data_dir(), "assets.csv")


def _read():
    p = table_path()
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write(rows):
    p = table_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def cmd_init(a):
    p = table_path()
    if os.path.exists(p):
        print("[init] 已存在，保留原表:", p)
        return
    _write([])
    print("[init] 已建空表:", p)


def cmd_add(a):
    rows = _read()
    if any(r.get("id") == a.id for r in rows):
        raise SystemExit("[add] id 已存在，未覆盖: " + a.id)
    row = dict(id=a.id, 类型=a.类型, 一级分类=a.一级分类 or "",
               二级分类=a.二级分类 or "", 三级分类=a.三级分类 or "", 名称=a.名称,
               标签=a.标签 or "", 角度=a.角度 or "", 图片=a.图片,
               来源=a.来源 or "", 备注=a.备注 or "")
    rows.append(row)
    _write(rows)
    print("[add] 已登记:", a.id)


def cmd_scan(a):
    """按目录树批量生成草稿。默认只打印不落表；--write 才追加。"""
    root = os.path.abspath(a.root)
    rel_base = data_dir()
    draft = []
    for dirpath, _dirs, files in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        parts = [] if rel_dir == "." else rel_dir.split(os.sep)
        # 超出三级的子目录折叠进第三级，保持列结构不变
        lv = (parts + ["", "", ""])[:MAX_LEVELS]
        if len(parts) > MAX_LEVELS:
            lv[MAX_LEVELS - 1] = "/".join(parts[MAX_LEVELS - 1:])
        for fn in sorted(files):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in IMG_EXT:
                continue
            stem = os.path.splitext(fn)[0]
            img_rel = os.path.relpath(os.path.join(dirpath, fn), rel_base)
            draft.append(dict(id="", 类型=a.类型, 一级分类=lv[0],
                              二级分类=lv[1], 三级分类=lv[2], 名称=stem,
                              标签="", 角度="", 图片=img_rel.replace("\\", "/"),
                              来源=a.来源 or "", 备注="scan 草稿·待确认 id"))
    print("[scan] 草稿 %d 条:" % len(draft))
    for d in draft[:a.limit]:
        print("  %s | %s/%s/%s | %s" % (
            d["类型"], d["一级分类"], d["二级分类"], d["三级分类"], d["图片"]))
    if a.write:
        rows = _read()
        exist = {r.get("图片") for r in rows}
        added = 0
        for d in draft:
            if d["图片"] in exist:
                continue
            d["id"] = "%s_%04d" % (a.类型, added + 1)
            rows.append(d)
            added += 1
        _write(rows)
        print("[scan] 已落表 %d 条" % added)
    else:
        print("[scan] 未落表（加 --write 写入；id 自动生成）")


def cmd_catalog(a):
    rows = _read()
    if a.类型:
        rows = [r for r in rows if r.get("类型") == a.类型]
    for r in rows:
        print("\t".join(r.get(k, "") for k in FIELDS))
    print("# 共 %d 条" % len(rows))


def cmd_check(a):
    rows = _read()
    bad = 0
    base = data_dir()
    for r in rows:
        missing = [k for k in ("id", "类型", "名称", "图片") if not r.get(k)]
        rel = r.get("图片", "")
        fp = rel if os.path.isabs(rel) else os.path.normpath(os.path.join(base, rel))
        no_file = bool(rel) and not os.path.exists(fp)
        if missing or no_file:
            bad += 1
            print("[check] %s | 缺列:%s | 文件缺失:%s (%s)" % (
                r.get("id", "?"), ",".join(missing) or "-", "是" if no_file else "否", rel))
    print("[check] 共 %d 条，异常 %d 条" % (len(rows), bad))


def main():
    ap = argparse.ArgumentParser(description="素材登记表（只记路径，不搬图）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init)

    p = sub.add_parser("add")
    p.add_argument("--id", dest="id", required=True)
    p.add_argument("--图片", dest="图片", required=True)
    for name in ("类型", "一级分类", "二级分类", "三级分类", "名称",
                 "标签", "角度", "来源", "备注"):
        p.add_argument("--" + name, dest=name, default="")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("scan")
    p.add_argument("root")
    p.add_argument("--类型", dest="类型", default="姿势")
    p.add_argument("--来源", dest="来源", default="")
    p.add_argument("--write", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("catalog")
    p.add_argument("--类型", dest="类型", default="")
    p.set_defaults(fn=cmd_catalog)

    sub.add_parser("check").set_defaults(fn=cmd_check)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
