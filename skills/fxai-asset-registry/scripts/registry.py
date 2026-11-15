#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""素材登记台账 · 统一入口。

一个项目目录一份台账，两个文件同源：
  素材登记.json  —— 机器可读，字段固定，供脚本查询/增量更新
  素材登记.md    —— 人可读，由 json 渲染，不要手改结构（改用途/标签改 json 或直接改 md 后跑 sync 会以 json 为准）

用法：
  python registry.py scan   <目录>                 # 只扫不写，屏幕上出清单
  python registry.py init   <目录> --name 项目名    # 建台账骨架（已存在则不覆盖）
  python registry.py sync   <目录>                 # 增量登记：新文件追加为「待标注」，消失的标 missing
  python registry.py md     <目录>                 # 只按 json 重渲染 md
  python registry.py check  <目录>                 # 校验台账里每条路径还在不在
  python registry.py index  <根目录>               # 汇总根下所有台账，出总索引

约定：
  · 相对路径一律相对台账所在目录，跨机器搬目录不失效
  · 不做去重删除、不动素材本体，只读文件元信息（大小/尺寸/时长/首 8 位 md5）
"""

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime

LEDGER_JSON = "素材登记.json"
LEDGER_MD = "素材登记.md"
SCHEMA = 1


def _gc(key: str) -> str:
    """读 fxai-common 全局配置的项目空间根 / 素材库根（本机路径唯一来源）。"""
    common = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "fxai-common"))
    if common not in sys.path:
        sys.path.insert(0, common)
    try:
        import global_config
    except Exception:
        return ""
    fn = {"projects": global_config.projects_root,
          "library": global_config.asset_library_root}.get(key)
    return fn() if fn else ""

# 类型 → 扩展名。归不进这三个的（如工程文件、html）默认不收，除非 --all
KIND_EXT = {
    "视频": {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".ts"},
    "图": {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".psd"},
    "音频": {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus"},
}
OTHER_EXT = {".json", ".md", ".txt", ".html", ".py", ".js", ".ass", ".srt", ".zip", ".safetensors"}

# 扫描时跳过的目录名
SKIP_DIRS = {
    "_tmp", "_frames", "_frames_v2", "__pycache__", ".git", ".workbuddy",
    "node_modules", ".workbuddy-ai", "backup", "removed_片头",
}

# 过程产物的判定特征：按段切片、中间渲染、字幕/音频分段。
# 命中者不删不跳过，只是归进「过程产物」一节，别挡着真素材。
DERIVED_DIR = re.compile(r"(^|/)(anim|_frames|_frames_v2|配图|music|音频|配乐|_tmp|fftest_out)(/|$)", re.I)
DERIVED_FILE = re.compile(
    r"(^p\d{1,3}\.(mp4|wav)$|^clip\.mp4$|^burned|^final\.mp4$|^merged|^_concat|"
    r"^S\d{2}_|\d{2}\.wav$|\d{2}\.mp4$|^seg_\d+)",
    re.I,
)


def is_derived(rel):
    return bool(DERIVED_DIR.search(rel) or DERIVED_FILE.search(os.path.basename(rel)))


def jload(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def jdump(p, o):
    tmp = p + ".writing"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f}{u}" if u == "B" else f"{n:.1f}{u}"
        n /= 1024.0
    return f"{n:.1f}GB"


def which_ffprobe():
    for c in ("ffprobe", "ffprobe.exe"):
        p = shutil.which(c)
        if p:
            return p
    return None


FFPROBE = which_ffprobe()


def probe(path, kind):
    """返回规格字符串。视频/音频带时长，图带分辨率。失败返回空串，不阻断登记。"""
    if not FFPROBE:
        return ""
    if kind == "图":
        cmd = [FFPROBE, "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height", "-of", "csv=p=0", path]
    else:
        cmd = [FFPROBE, "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height", "-show_entries", "format=duration",
               "-of", "default=nw=1", path]
        if kind == "音频":
            cmd = [FFPROBE, "-v", "error", "-show_entries", "format=duration",
                   "-of", "default=nw=1", path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=25).stdout
    except Exception:
        return ""
    w = h = d = ""
    for line in out.splitlines():
        if line.startswith("width="):
            w = line.split("=", 1)[1].strip()
        elif line.startswith("height="):
            h = line.split("=", 1)[1].strip()
        elif line.startswith("duration="):
            d = line.split("=", 1)[1].strip()
        elif re.match(r"^\d+,\d+$", line.strip()):
            w, h = line.strip().split(",")
    parts = []
    if w and h and w != "N/A":
        parts.append(f"{w}x{h}")
    if d and d != "N/A":
        try:
            parts.append(f"{float(d):.1f}s")
        except ValueError:
            pass
    return " · ".join(parts)


def quick_md5(path, limit=1 << 20):
    """首 1MB 的 md5，用来识别同一素材的多份拷贝。不做全文件校验（素材动辄几百 MB）。"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read(limit))
    return h.hexdigest()[:10]


def kind_of(name):
    ext = os.path.splitext(name)[1].lower()
    for k, exts in KIND_EXT.items():
        if ext in exts:
            return k
    if ext in OTHER_EXT:
        return "其他"
    return ""


def scan_dir(root, include_other=False):
    """遍历目录，返回 [{rel, abs, kind, size, mtime}]，跳过 _tmp 与过程目录。"""
    out = []
    root = os.path.abspath(root)
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in fns:
            if fn.startswith("."):
                continue
            k = kind_of(fn)
            if not k or (k == "其他" and not include_other):
                continue
            if k == "其他" and include_other:
                pass
            ap = os.path.join(dp, fn)
            try:
                st = os.stat(ap)
            except OSError:
                continue
            out.append({
                "rel": os.path.relpath(ap, root).replace("\\", "/"),
                "abs": ap,
                "kind": k,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"),
            })
    out.sort(key=lambda x: (x["kind"], x["rel"]))
    return out


def new_ledger(root, name):
    return {
        "schema": SCHEMA,
        "项目": name or os.path.basename(os.path.abspath(root)),
        "根目录": os.path.abspath(root).replace("\\", "/"),
        "更新": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "说明": "可复用素材台账。用途/标签/可复用 三个字段需人工确认，其余由 sync 自动维护。",
        "素材": [],
    }


def assign_id(ledger, kind):
    prefix = {"视频": "V", "图": "G", "音频": "A", "其他": "X"}.get(kind, "M")
    n = 0
    for it in ledger["素材"]:
        if it.get("id", "").startswith(prefix):
            try:
                n = max(n, int(it["id"][len(prefix):]))
            except ValueError:
                pass
    return f"{prefix}{n + 1:03d}"


def find_ledger(d):
    p = os.path.join(os.path.abspath(d), LEDGER_JSON)
    return p if os.path.exists(p) else None


def cmd_scan(a):
    items = scan_dir(a.dir, a.all)
    if not items:
        print("（没有可登记的素材）")
        return
    print(f"{'类型':<4}{'大小':>9}  {'规格':<18} 文件")
    print("-" * 96)
    tot = 0
    for it in items:
        spec = probe(it["abs"], it["kind"])
        tot += it["size"]
        print(f"{it['kind']:<4}{human(it['size']):>9}  {spec:<18} {it['rel']}")
    print("-" * 96)
    for k in ("视频", "图", "音频", "其他"):
        c = sum(1 for x in items if x["kind"] == k)
        if c:
            print(f"{k}: {c} 个", end="  ")
    print(f"\n合计 {len(items)} 个 / {human(tot)}")


def cmd_init(a):
    root = os.path.abspath(a.dir)
    jp = os.path.join(root, LEDGER_JSON)
    if os.path.exists(jp):
        print(f"台账已存在，不覆盖：{jp}")
        return
    lg = new_ledger(root, a.name)
    jdump(jp, lg)
    render_md(root, lg)
    print(f"已建台账：{jp}")
    print(f"          {os.path.join(root, LEDGER_MD)}")
    print("接着跑 sync 把现有素材扫进去。")


def cmd_sync(a):
    root = os.path.abspath(a.dir)
    jp = find_ledger(root)
    if not jp:
        lg = new_ledger(root, a.name)
    else:
        lg = jload(jp)

    items = scan_dir(root, a.all or bool(lg.get("含其他类型")))
    by_rel = {it["文件"]: it for it in lg["素材"] if it.get("文件")}
    added = updated = 0
    # 「仅库内」条目：文件已提炼进素材库 _库，项目里不留副本，sync 既不重扫也不标失联
    lib_only = {it["文件"] for it in lg["素材"] if it.get("库内") and it.get("仅库内")}

    for f in items:
        if f["rel"] in lib_only:
            continue
        cur = by_rel.get(f["rel"])
        spec = probe(f["abs"], f["kind"])
        if cur:
            changed = (cur.get("大小字节") != f["size"]) or (cur.get("规格") != spec)
            cur["大小字节"] = f["size"]
            cur["大小"] = human(f["size"])
            cur["规格"] = spec
            cur["类型"] = f["kind"]
            cur["日期"] = f["mtime"]
            cur.pop("missing", None)
            # 只把「未评」降级为「过程」，人工标成「精选」的绝不覆盖
            if cur.get("档", "未评") == "未评" and is_derived(f["rel"]):
                cur["档"] = "过程"
            if changed:
                updated += 1
        else:
            lg["素材"].append({
                "id": assign_id(lg, f["kind"]),
                "文件": f["rel"],
                "类型": f["kind"],
                "规格": spec,
                "大小": human(f["size"]),
                "大小字节": f["size"],
                "日期": f["mtime"],
                "md5_1m": quick_md5(f["abs"]),
                "档": "过程" if is_derived(f["rel"]) else "未评",
                "用途": "",
                "标签": [],
                "可复用": None,
                "来源": "",
                "备注": "",
            })
            added += 1

    seen = {f["rel"] for f in items}
    gone = 0
    for it in lg["素材"]:
        if it["文件"] not in seen and not it.get("仅库内"):
            if not it.get("missing"):
                it["missing"] = True
                gone += 1

    lg["更新"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    lg["素材"].sort(key=lambda x: (x.get("类型", ""), x.get("id", "")))
    jdump(os.path.join(root, LEDGER_JSON), lg)
    render_md(root, lg)

    todo = sum(1 for it in lg["素材"]
               if not it.get("用途") and not it.get("missing") and it.get("档") != "过程")
    print(f"新增 {added} 条 / 更新 {updated} 条 / 失联 {gone} 条 / 待标注用途 {todo} 条")
    print(f"台账：{os.path.join(root, LEDGER_JSON)}")


def esc(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def render_md(root, lg):
    allit = lg["素材"]
    live = [i for i in allit if not i.get("missing")]
    picked = [i for i in live if i.get("档") == "精选"]
    normal = [i for i in live if i.get("档", "未评") == "未评"]
    derived = [i for i in live if i.get("档") == "过程"]

    lines = []
    lines.append(f"# 素材登记 · {lg.get('项目')}")
    lines.append("")
    lines.append(f"- 根目录：`{lg.get('根目录')}`")
    lines.append(f"- 更新时间：{lg.get('更新')}")
    lines.append(
        f"- 收录 {len(live)} 项：精选 {len(picked)} / 未评 {len(normal)} / 过程产物 {len(derived)}"
        + (f"（另有失联 {len(allit) - len(live)}）" if len(allit) != len(live) else "")
    )
    lines.append("")
    lines.append("> 本文件由 `fxai-asset-registry` 的 `registry.py` 渲染，**结构不要手改**。")
    lines.append("> 要补「用途 / 标签 / 可复用」，改同目录 `素材登记.json` 里对应的条目（把 `档` 改成 `精选`），再跑 `registry.py md <目录>`。")
    lines.append("")

    def table(items, title, note=""):
        if not items:
            return
        lines.append(f"## {title}（{len(items)}）")
        lines.append("")
        if note:
            lines.append(f"> {note}")
            lines.append("")
        lines.append("| ID | 文件 | 类型 | 规格 | 大小 | 用途 | 标签 | 复用 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for it in items:
            tags = " / ".join(it.get("标签") or [])
            re_ = {True: "✅", False: "—"}.get(it.get("可复用"), "?")
            lines.append(
                f"| {it.get('id','')} | `{esc(it.get('文件',''))}` | {esc(it.get('类型',''))} "
                f"| {esc(it.get('规格',''))} | {esc(it.get('大小',''))} "
                f"| {esc(it.get('用途') or '**待标注**')} | {esc(tags)} | {re_} |"
            )
        lines.append("")

    table(picked, "精选 · 可直接复用")
    table(normal, "待评 · 还没判定用途")

    if derived:
        lines.append("## 过程产物（留档备查）")
        lines.append("")
        lines.append(f"> 自动识别出的分段切片、中间渲染、分段配音等，通常**不要**当素材复用；{len(derived)} 项只列前 60 条。")
        lines.append("")
        lines.append("| ID | 文件 | 规格 | 大小 |")
        lines.append("|---|---|---|---|")
        for it in derived[:60]:
            lines.append(
                f"| {it.get('id','')} | `{esc(it.get('文件',''))}` | {esc(it.get('规格',''))} | {esc(it.get('大小',''))} |"
            )
        if len(derived) > 60:
            lines.append(f"| … | 另 {len(derived) - 60} 条，见 `{LEDGER_JSON}` | | |")
        lines.append("")

    miss = [i for i in allit if i.get("missing")]
    if miss:
        lines.append(f"## ⚠️ 失联（路径已不存在，{len(miss)}）")
        lines.append("")
        for it in miss[:40]:
            lines.append(f"- {it.get('id','')} `{it['文件']}`")
        lines.append("")

    dups = {}
    for it in live:
        h = it.get("md5_1m")
        if h:
            dups.setdefault(h, []).append(it)
    dup_groups = {h: v for h, v in dups.items() if len(v) > 1}
    if dup_groups:
        lines.append("## 疑似重复（首 1MB 相同）")
        lines.append("")
        for h, v in list(dup_groups.items())[:15]:
            lines.append("- " + " ≡ ".join(f"`{x['文件']}`" for x in v))
        lines.append("")

    with open(os.path.join(root, LEDGER_MD), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def cmd_md(a):
    root = os.path.abspath(a.dir)
    jp = find_ledger(root)
    if not jp:
        print("没有台账，先 init。")
        return
    lg = jload(jp)
    render_md(root, lg)
    print(f"已重渲染：{os.path.join(root, LEDGER_MD)}")


def cmd_check(a):
    root = os.path.abspath(a.dir)
    jp = find_ledger(root)
    if not jp:
        print("没有台账。")
        return
    lg = jload(jp)
    miss = [it for it in lg["素材"] if not os.path.exists(os.path.join(root, it["文件"]))]
    nose = [it for it in lg["素材"]
            if not it.get("用途") and not it.get("missing") and it.get("档", "未评") != "过程"]
    print(f"台账 {len(lg['素材'])} 条")
    if miss:
        print(f"路径失效 {len(miss)} 条：")
        for it in miss:
            print(f"  {it.get('id')}  {it['文件']}")
    else:
        print("路径全部有效 ✓")
    if nose:
        print(f"待标注用途 {len(nose)} 条：")
        for it in nose[:40]:
            print(f"  {it.get('id')}  {it['文件']}")
        if len(nose) > 40:
            print(f"  …… 另 {len(nose) - 40} 条")


CATALOG_JSON = "素材总清单.json"
CATALOG_MD = "素材总清单.md"
BOOK_DIR = "分册"


def collect_ledgers(root):
    """遍历 root 下所有 素材登记.json，返回 [(abs_dir, ledger)]。"""
    out = []
    root = os.path.abspath(root)
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
        if LEDGER_JSON in fns:
            try:
                out.append((dp, jload(os.path.join(dp, LEDGER_JSON))))
            except Exception as e:
                print(f"跳过（读不了）：{dp} · {e}")
    return out


def book_of(it, default="未分类"):
    """按标签首词分册；没标签就进未分类。"""
    tags = it.get("标签") or []
    return tags[0] if tags else default


def cmd_catalog(a):
    """把各项目台账里「精选」的条目汇成统一素材库，按标签分册。"""
    root = os.path.abspath(a.src)
    casa = os.path.abspath(a.dir)
    os.makedirs(os.path.join(casa, BOOK_DIR), exist_ok=True)
    bdir = os.path.join(casa, BOOK_DIR)

    ledgers = collect_ledgers(root)
    if not ledgers:
        print(f"{root} 下没有任何 素材登记.json，先在各项目里 init + sync。")
        return

    lib_idx = {}
    lib_json = os.path.join(casa, LIB_DIRNAME, LIB_JSON)
    if os.path.exists(lib_json):
        for it in jload(lib_json).get("素材", []):
            lib_idx[it.get("编号")] = it

    books = {}
    for base, lg in ledgers:
        proj = lg.get("项目") or os.path.basename(base)
        for it in lg.get("素材", []):
            if it.get("档") != "精选" or it.get("missing"):
                continue
            if it.get("可复用") is False:
                continue
            name = book_of(it)
            libno = it.get("库内")
            lrec = lib_idx.get(libno) if libno else None
            if lrec:
                path = os.path.join(casa, LIB_DIRNAME, lrec["文件"]).replace("\\", "/")
            else:
                path = os.path.join(base, it["文件"]).replace("\\", "/")
            books.setdefault(name, []).append({
                "用途": it.get("用途") or "",
                "类型": it.get("类型", ""),
                "规格": it.get("规格", ""),
                "大小": it.get("大小", ""),
                "路径": path,
                "库内": libno or "",
                "项目": proj,
                "标签": it.get("标签") or [],
                "md5_1m": it.get("md5_1m", ""),
                "备注": it.get("备注", ""),
            })

    # 编号 + 排重提示
    for name, items in books.items():
        items.sort(key=lambda x: (x["类型"], x["用途"]))
        for i, it in enumerate(items, 1):
            it["编号"] = f"{name}-{i:03d}"
        seen = {}
        for it in items:
            h = it.get("md5_1m")
            if h:
                seen.setdefault(h, []).append(it["编号"])
        dups = {h: v for h, v in seen.items() if len(v) > 1}
        for it in items:
            if it.get("md5_1m") in dups:
                it["重复"] = " / ".join(x for x in dups[it["md5_1m"]] if x != it["编号"])

    catalog = {
        "schema": SCHEMA,
        "统一素材库": casa.replace("\\", "/"),
        "来源根": root.replace("\\", "/"),
        "更新": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "册": {k: v for k, v in sorted(books.items(), key=lambda kv: -len(kv[1]))},
    }
    jdump(os.path.join(casa, CATALOG_JSON), catalog)

    # —— 总清单 ——
    lines = ["# 素材总清单", ""]
    lines.append(f"- 统一素材库：`{catalog['统一素材库']}`")
    lines.append(f"- 来源根：`{catalog['来源根']}`")
    lines.append(f"- 更新时间：{catalog['更新']}")
    lines.append(f"- 册数 {len(books)} / 条目 {sum(len(v) for v in books.values())}（只汇各项目台账里标了「精选」的）")
    lines.append("")
    lines.append("> 分册按条目「标签」的第一个词自动归类；条目里记的是**实际文件路径**，不搬文件。")
    lines.append("> 加新素材 → 在所属项目里 `registry.py sync` 并标「精选」→ 回来重跑 `registry.py catalog`。")
    lines.append("")
    lines.append("| 册 | 条目 | 视频 | 图 | 音频 | 清单 |")
    lines.append("|---|---|---|---|---|---|")
    for name, items in catalog["册"].items():
        v = sum(1 for i in items if i["类型"] == "视频")
        g = sum(1 for i in items if i["类型"] == "图")
        au = sum(1 for i in items if i["类型"] == "音频")
        lines.append(f"| {name} | {len(items)} | {v} | {g} | {au} | [{BOOK_DIR}/{name}.md]({BOOK_DIR}/{name}.md) |")
    lines.append("")
    with open(os.path.join(casa, CATALOG_MD), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # —— 各册 ——
    for name, items in catalog["册"].items():
        bl = [f"# 分册 · {name}", ""]
        bl.append(f"- 条目 {len(items)} 条")
        bl.append(f"- 更新：{catalog['更新']}")
        bl.append(f"- 返回：[素材总清单](../{CATALOG_MD})")
        bl.append("")
        bl.append("| 编号 | 用途 | 类型 | 规格 | 大小 | 路径 | 项目 |")
        bl.append("|---|---|---|---|---|---|---|")
        for it in items:
            note = f" ⚠️与 {it['重复']} 重复" if it.get("重复") else ""
            bl.append(
                f"| {it['编号']} | {esc(it['用途']) or '**待补**'}{note} | {esc(it['类型'])} "
                f"| {esc(it['规格'])} | {esc(it['大小'])} | `{esc(it['路径'])}` | {esc(it['项目'])} |"
            )
        bl.append("")
        with open(os.path.join(bdir, f"{name}.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(bl))

    print(f"已生成素材库：{casa}")
    print(f"  总清单 {CATALOG_MD} · {len(books)} 册 / {sum(len(v) for v in books.values())} 条")
    for name, items in catalog["册"].items():
        print(f"  · {name}：{len(items)} 条 → {BOOK_DIR}/{name}.md")


# ---------- 实体库：提炼入库 ----------
# 素材库分两层：
#   索引层  素材总清单.md / 分册/*.md      —— 只记路径，不搬文件（catalog 生成）
#   实体层  _库/{视频,图,音频}/*           —— 唯一定版母版，按规范改名（promote 生成）
# 提炼规则：分类_内容_规格.ext。不带版本号；规格是训练/渲染参数以外的硬指标（分辨率、帧数、时长）。

LIB_JSON = "库内清单.json"
LIB_MD = "库内清单.md"
LIB_DIRNAME = "_库"
LIB_FORM_DIRS = ("录屏", "演示", "口播", "对比", "实拍", "配乐", "图", "其它")


def probe_full(path, kind):
    """返回 (spec_token, spec_text)。spec_token 进文件名，spec_text 进清单。"""
    w = h = 0
    dur = 0.0
    frames = 0
    fps = ""
    if FFPROBE:
        if kind == "音频":
            cmd = [FFPROBE, "-v", "error", "-show_entries", "format=duration",
                   "-of", "default=nw=1", path]
        else:
            cmd = [FFPROBE, "-v", "error", "-select_streams", "v:0",
                   "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
                   "-show_entries", "format=duration", "-of", "default=nw=1", path]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
        except Exception:
            out = ""
        for line in out.splitlines():
            k, _, v = line.partition("=")
            v = v.strip()
            if k == "width" and v.isdigit():
                w = int(v)
            elif k == "height" and v.isdigit():
                h = int(v)
            elif k == "duration":
                try:
                    dur = float(v)
                except ValueError:
                    pass
            elif k == "nb_frames" and v.isdigit():
                frames = int(v)
            elif k == "r_frame_rate" and "/" in v:
                try:
                    x, y = v.split("/")
                    fps = f"{float(x) / float(y):.0f}"
                except Exception:
                    pass
    if not frames and dur and fps:
        try:
            frames = int(round(dur * float(fps)))
        except ValueError:
            pass

    if kind == "音频":
        tok = f"{dur:.2f}s" if dur else ""
        return tok, (f"{dur:.2f}s" if dur else "")
    if kind == "图":
        tok = f"{w}x{h}" if w and h else ""
        return tok, tok
    parts_tok = []
    parts_txt = []
    if w and h:
        parts_tok.append(f"{w}x{h}")
        parts_txt.append(f"{w}x{h}")
    if fps:
        parts_txt.append(f"{fps}fps")
    if frames:
        parts_tok.append(f"{frames}f")
        parts_txt.append(f"{frames}帧")
    if dur:
        parts_txt.append(f"{dur:.2f}s")
    return "_".join(parts_tok), " · ".join(parts_txt)


def lib_load(lib_root):
    p = os.path.join(lib_root, LIB_DIRNAME, LIB_JSON)
    if os.path.exists(p):
        return jload(p)
    return {"更新时间": "", "素材": []}


def lib_next_id(lib, book):
    n = 0
    for it in lib["素材"]:
        if it.get("分类") == book:
            try:
                n = max(n, int(str(it.get("编号", "")).rsplit("-", 1)[-1]))
            except ValueError:
                pass
    return f"{book}-{n + 1:03d}"


def render_lib(lib_root, lib):
    lib_dir = os.path.join(lib_root, LIB_DIRNAME)
    lines = ["# 库内清单（实体母版）", ""]
    lines.append(f"- 实体库：`{lib_dir}`")
    lines.append(f"- 更新时间：{lib.get('更新时间', '')}")
    lines.append(f"- 条目：{len(lib['素材'])}")
    lines.append("")
    lines.append("> 这里是**唯一定版**：同一段素材只存一份，改名按 `分类_内容_规格.ext`。")
    lines.append("> 技能包只放系统级零件，应用级素材一律在本库；同一段素材只存这一份。")
    lines.append("")
    groups = {}
    for it in lib["素材"]:
        groups.setdefault(it.get("分类", "未分类"), []).append(it)
    for book in sorted(groups):
        g = groups[book]
        lines.append(f"## {book}（{len(g)}）")
        lines.append("")
        lines.append("| 编号 | 文件 | 形态 | 规格 | 大小 | 用途 | 来源 |")
        lines.append("|---|---|---|---|---|---|---|")
        for it in sorted(g, key=lambda x: x.get("编号", "")):
            lines.append(
                f"| {it.get('编号','')} | `{esc(it.get('文件',''))}` | {it.get('形态','')} "
                f"| {esc(it.get('规格',''))} | {esc(it.get('大小',''))} "
                f"| {esc(it.get('用途',''))} | {esc(it.get('来源',''))} |"
            )
        lines.append("")
    with open(os.path.join(lib_dir, LIB_MD), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def cmd_promote(a):
    """把一个文件提炼进实体库：复制 + 规范改名 + 登记。"""
    src = os.path.abspath(a.dir)
    if not os.path.isfile(src):
        print(f"文件不存在：{src}")
        sys.exit(2)
    kind = kind_of(src)
    if not kind or kind == "其他":
        print(f"不认识的素材类型：{src}")
        sys.exit(2)

    lib_root = os.path.abspath(a.lib)
    sub = (a.form or ("图" if kind == "图" else "其它")).strip()
    if sub not in LIB_FORM_DIRS:
        print(f"--form 只能是 {'/'.join(LIB_FORM_DIRS)}")
        sys.exit(2)
    os.makedirs(os.path.join(lib_root, LIB_DIRNAME, sub), exist_ok=True)

    book = (a.book or "未分类").strip()
    base = (a.name or os.path.splitext(os.path.basename(src))[0]).strip()
    tok, txt = probe_full(src, kind)
    stem = f"{book}_{base}" + (f"_{tok}" if tok else "")
    ext = os.path.splitext(src)[1].lower()
    rel = f"{sub}/{stem}{ext}"
    dst = os.path.join(lib_root, LIB_DIRNAME, rel)

    lib = lib_load(lib_root)
    existed = os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src)
    if existed:
        print(f"已存在同名同大小，跳过复制：{rel}")
    else:
        shutil.copy2(src, dst)
        print(f"已入库：{rel}")

    # 同一份源重复 promote 就更新原条目，不新增
    hit = None
    for it in lib["素材"]:
        if it.get("文件") == rel:
            hit = it
            break
    if hit is None:
        hit = {"编号": lib_next_id(lib, book), "文件": rel}
        lib["素材"].append(hit)

    hit.update({
        "原名": os.path.basename(src),
        "类型": kind,
        "形态": sub,
        "分类": book,
        "规格": txt,
        "大小": human(os.path.getsize(dst)),
        "大小字节": os.path.getsize(dst),
        "用途": a.desc or hit.get("用途", ""),
        "标签": [t for t in re.split(r"[,，\s]+", a.tags) if t] or hit.get("标签", []),
        "来源": a.from_ or (hit.get("来源") or src),
        "md5_1m": quick_md5(dst),
        "入库": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    lib["更新时间"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    jdump(os.path.join(lib_root, LIB_DIRNAME, LIB_JSON), lib)
    render_lib(lib_root, lib)
    print(f"{hit['编号']} | {rel} | {txt}")


def cmd_index(a):
    root = os.path.abspath(a.dir)
    rows = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
        if LEDGER_JSON in fns:
            try:
                lg = jload(os.path.join(dp, LEDGER_JSON))
            except Exception as e:
                print(f"跳过（读不了）：{dp} · {e}")
                continue
            total = len(lg.get("素材", []))
            v = sum(1 for i in lg["素材"] if i.get("类型") == "视频")
            g = sum(1 for i in lg["素材"] if i.get("类型") == "图")
            au = sum(1 for i in lg["素材"] if i.get("类型") == "音频")
            ready = sum(1 for i in lg["素材"] if i.get("可复用") is True)
            rows.append({
                "项目": lg.get("项目", os.path.basename(dp)),
                "路径": dp.replace("\\", "/"),
                "更新": lg.get("更新", ""),
                "总数": total, "视频": v, "图": g, "音频": au, "可复用": ready,
            })
    if not rows:
        print("根目录下没有找到任何台账。")
        return
    print(f"{'项目':<24}{'更新':<18}{'总':>4}{'视频':>5}{'图':>5}{'音':>4}{'可复用':>6}  路径")
    print("-" * 110)
    for r in sorted(rows, key=lambda x: x["更新"], reverse=True):
        print(f"{r['项目']:<24}{r['更新']:<18}{r['总数']:>4}{r['视频']:>5}{r['图']:>5}{r['音频']:>4}{r['可复用']:>6}  {r['路径']}")
    print("-" * 110)
    print(f"共 {len(rows)} 份台账 / {sum(r['总数'] for r in rows)} 条素材")


def main():
    ap = argparse.ArgumentParser(description="素材登记台账")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, fn, help_ in (
        ("scan", cmd_scan, "只扫不写，列出目录下可登记的素材"),
        ("init", cmd_init, "建台账骨架"),
        ("sync", cmd_sync, "增量登记"),
        ("md", cmd_md, "按 json 重渲染 md"),
        ("check", cmd_check, "校验台账路径与待标注"),
        ("index", cmd_index, "汇总根目录下所有台账"),
        ("catalog", cmd_catalog, "把各项目精选素材汇成统一素材库（按标签分册）"),
        ("promote", cmd_promote, "把一个文件提炼进实体库 _库（复制+规范改名+登记）"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("dir", help="台账目录 / 汇总根目录 / 素材库目录（按命令而不同）")
        p.add_argument("--name", default="", help="项目名（init 时用）")
        p.add_argument("--all", action="store_true", help="连 json/md/html/py 等工程文件一起收")
        p.add_argument("--src", default=_gc("projects"), help="catalog 用：到哪找各项目台账（缺省取全局配置 projects.root）")
        p.add_argument("--lib", default=_gc("library"), help="promote 用：素材库根目录（缺省取全局配置 asset_library.root）")
        p.add_argument("--book", default="", help="promote 用：主题=分册名，如 仙宫云")
        p.add_argument("--form", default="", help="promote 用：素材形态：录屏/演示/口播/对比/实拍/配乐/图/其它")
        p.add_argument("--desc", default="", help="promote 用：用途描述（画面内容+关键识别信息+规格）")
        p.add_argument("--tags", default="", help="promote 用：标签，逗号分隔，首词决定分册")
        p.add_argument("--from", dest="from_", default="", help="promote 用：来源说明")
        p.set_defaults(func=fn)

    a = ap.parse_args()
    if a.cmd == "catalog":
        os.makedirs(a.dir, exist_ok=True)
    if a.cmd != "promote" and not os.path.isdir(a.dir):
        print(f"目录不存在：{a.dir}")
        sys.exit(2)
    a.func(a)


if __name__ == "__main__":
    main()
