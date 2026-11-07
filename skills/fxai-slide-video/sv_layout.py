# -*- coding: utf-8 -*-
"""fxai-slide-video · 项目存储格式 + 交付清单（唯一执行点）

两条规则，没有例外：

    ① 项目文件夹建在**当前工作区**下 —— 在哪个工作区跑，就在那里按项目名建文件夹。
    ② 一个项目 = 一个独立文件夹：**单件产物放项目根**（打开项目第一眼看到成品），
       成堆的同类东西才建子目录（多张配图 → 配图/，多段音频 → 音频/），
       中间产物一律进 _tmp/。

    <工作区>/<项目名>/
    ├─ 成片_<W>x<H>.mp4        ★ 最终成品（烧好字幕）
    ├─ 合并_<W>x<H>.mp4          无字幕母版
    ├─ 口播文案.md / 口播文案_标签.md
    ├─ 字幕.srt / 字幕.ass
    ├─ 交付清单.md / delivery.json
    ├─ 配图/   多张幻灯片配图 S01_*.png + anim/
    ├─ 音频/   多段音频 <项目名>NN.wav
    └─ _tmp/   中间产物（html / 分段 mp4 / _concat.txt），可随时删

项目根目录优先级（与 fxai-workflow/output_layout.py 一致）：
    1. --root（显式指定）
    2. 环境变量 FXAI_PROJECTS_ROOT（会透传给子脚本，保证全链路同根）
    3. 工作区级 <工作区>/_fxai_global.json 的 projects_root
    4. 当前工作目录

用法（脚本内）：
    import sv_layout
    L = sv_layout.layout("设计能力展示")     # -> {root, image, music, tmp}
    sv_layout.write_delivery("设计能力展示")  # 重建 交付清单.md + delivery.json

用法（命令行）：
    python sv_layout.py <项目名> [--files] [--migrate [--dry]] [--delivery] [--export DIR]
"""
import json, os, pathlib, re, shutil, sys, time, wave

KIND = "slide-video"          # 交付清单标记：本清单由幻灯片视频技能产出

# 成组类别 -> (目录名, 其他技能写入时的兼容名, 说明)。只有这些会建子目录，其余一律项目根。
GROUP_DIRS = [
    ("image", "配图", "image", "多张幻灯片配图 S01_*.png + anim/"),
    ("music", "音频", "music", "多段音频 <项目名>NN.wav"),
    ("tmp",   "_tmp", "_tmp",  "中间产物（可随时删）"),
]

_SKILLS = pathlib.Path(__file__).parent.parent


# ---------- 画布尺寸预设 ----------
# 竖屏 = 9:16 族，横屏 = 16:9 族。`--size` 只认这里的名字，其余一律直写 WxH。
#
# 🔴 「竖屏 768×1280」是历史默认值，全项目配图/录制/字幕基准都按它算过 —— **禁用改动**。
#    它的真实比例是 3:5（1.667），不是精确 9:16（1.778），属于凑整档；要精确 9:16 用「竖屏高清」。
#
# 🔴 横屏默认 1366×768：高度正好 768，比例 1.7786，与精确 16:9 只差 0.05%
#    （16:9 素材铺进去上下各裁 0.4px，等于零裁切），宽高皆偶数。
#    ⚠️ 1344×768 **不是 16:9**，是 7:4（1344 宽若按 16:9，高应是 756）。那是 SDXL 出图的
#       常用横版尺寸，别拿来当视频标准 —— 16:9 素材进去左右各被裁 0.8%。
SIZES = {
    "竖屏":     (768, 1280),    # 默认。3:5 凑整档，全项目基准，别动
    "竖屏1366": (768, 1366),    # 768 边 + 精确 9:16（1365.33 取偶）。比默认更贴竖屏平台
    "竖屏高清": (1080, 1920),   # 精确 9:16，平台原生竖屏
    "横屏":     (1366, 768),    # 默认横屏。高度 768，与 16:9 差 0.05%
    "横屏小":   (1280, 720),    # 精确 16:9，720p 标准档
    "横屏高清": (1920, 1080),   # 精确 16:9，1080p
}

# 别名 -> 正式名。英文 / 比例写法都收，免得每次查表。
# 🔴 比例别名只指**精确**那几档（9:16 → 1080×1920、16:9 → 1280×720），
#    别指 768 边那两档 —— 那两档是为了凑 768 才取偶数的，差 0.05% 不是精确 16:9。
_SIZE_ALIAS = {
    "portrait": "竖屏", "vertical": "竖屏", "768x1280": "竖屏",
    "768x1366": "竖屏1366", "竖屏9:16": "竖屏1366",
    "竖屏hd": "竖屏高清", "1080x1920": "竖屏高清", "9:16": "竖屏高清",
    "landscape": "横屏", "horizontal": "横屏", "1366x768": "横屏",
    "720p": "横屏小", "1280x720": "横屏小", "16:9": "横屏小",
    "1080p": "横屏高清", "1920x1080": "横屏高清",
}


def size_of(name):
    """尺寸预设名 -> (w, h)。也接受 `1366x768` / `1366*768` / `1366,768` 直接写。

    None 或空 -> 默认竖屏 768×1280。名字不认识直接退出并列出可选值（不猜）。
    """
    if not name:
        return SIZES["竖屏"]
    k = str(name).strip().lower().replace("×", "x").replace("*", "x").replace("，", ",")
    if k in _SIZE_ALIAS:
        return SIZES[_SIZE_ALIAS[k]]
    if k in SIZES:
        return SIZES[k]
    m = re.match(r"^(\d+)\s*[x,]\s*(\d+)$", k)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        if w < 64 or h < 64:
            raise SystemExit("[size] 尺寸太小：%dx%d（至少 64×64）" % (w, h))
        return w, h
    raise SystemExit("[size] 不认识「%s」。可选：%s；或直写 1366x768"
                     % (name, " / ".join(SIZES)))


def resolve_size(name=None, w=None, h=None):
    """显式 --w/--h 优先，其次 --size 预设，最后默认竖屏。

    返回 (w, h, 来源说明)。只给一边（比如只给 --w）时，另一边按预设比例补齐，不得出现 1366×1280 这种两边都对不上的怪尺寸。
    """
    bw, bh = size_of(name)
    if w and h:
        return int(w), int(h), "--w/--h"
    if w:
        return int(w), int(round(w * bh / bw)), "--w（高按 %s 比例补齐）" % (name or "竖屏")
    if h:
        return int(round(h * bw / bh)), int(h), "--h（宽按 %s 比例补齐）" % (name or "竖屏")
    return bw, bh, "--size %s" % (name or "竖屏(默认)")


# ---------- 项目根 ----------
def _workspace_cfg_root():
    """工作区级配置：cwd 往上 3 层找 _fxai_global.json 的 projects_root。"""
    d = pathlib.Path(os.getcwd()).resolve()
    for _ in range(3):
        f = d / "_fxai_global.json"
        if f.is_file():
            try:
                with open(f, encoding="utf-8") as fh:
                    v = json.load(fh).get("projects_root", "")
                if v:
                    return str(pathlib.Path(v).resolve())
            except Exception:
                pass
        if d.parent == d:
            break
        d = d.parent
    return ""


def resolve_root(root=None):
    """项目建在哪：--root > FXAI_PROJECTS_ROOT > 工作区级配置 > 当前工作区。"""
    if root:
        return str(pathlib.Path(root).resolve())
    env = os.environ.get("FXAI_PROJECTS_ROOT", "")
    if env:
        return str(pathlib.Path(env).resolve())
    w = _workspace_cfg_root()
    if w:
        return w
    return str(pathlib.Path(os.getcwd()).resolve())


def layout(project, root=None, create=True):
    """返回项目目录字典：{root, image, music, tmp}。

    同时把根写进 FXAI_PROJECTS_ROOT —— 子脚本（script_gen / voice_gen）按同一个根解析，
    保证一个项目的产物不会散到两个目录。
    """
    name = str(project or "").strip()
    if not name:
        raise ValueError("项目名为空（--project）：一个项目必须有一个独立文件夹名")
    r = resolve_root(root)
    os.environ["FXAI_PROJECTS_ROOT"] = r
    base = pathlib.Path(r) / name
    if create:
        base.mkdir(parents=True, exist_ok=True)
    out = {"root": base}
    for key, dname, _old, _desc in GROUP_DIRS:
        d = base / dname
        if create:
            d.mkdir(parents=True, exist_ok=True)
        out[key] = d
    return out


def p(project, kind, filename, root=None, create=True):
    """落盘路径：kind="root" 或未知类别都发到项目根。"""
    L = layout(project, root=root, create=create)
    return L.get(kind, L["root"]) / filename


def dirs_of(base):
    """某类别的候选目录：本技能用的中文名优先，其他技能写的英文兼容名其次。"""
    return {key: [base / dname, base / old] for key, dname, old, _d in GROUP_DIRS}


# ---------- 落位判定：单件放项目根，成组进子目录 ----------
_CLASSIFY = [
    (lambda n: n.endswith((".mp4", ".mov", ".webm")) and not n.startswith("p"), "root"),
    (lambda n: n.endswith((".srt", ".ass", ".vtt")),                      "root"),
    (lambda n: n.endswith((".md", ".json")) and not n.startswith("_"),    "root"),
    (lambda n: n.endswith((".wav", ".wav.norm", ".mp3", ".m4a", ".flac")), "music"),
    (lambda n: n.endswith((".png", ".jpg", ".jpeg", ".webp")),            "image"),
    (lambda n: n.endswith((".html", ".htm")) or n == "_concat.txt"
     or (n.startswith("p") and n.endswith(".mp4")),                       "tmp"),
    (lambda n: n.endswith(".log"),                                        "tmp"),
]

_SKIP = {"segments.json", "spec.json"}     # 用户自己维护的规格单，不归类


def classify(name):
    """某个文件该落项目根还是哪个子目录。"""
    for test, kind in _CLASSIFY:
        if test(name):
            return kind
    return "root"


def rel(base, x):
    """相对项目根的路径（posix 分隔符）。"""
    try:
        return pathlib.Path(x).resolve().relative_to(pathlib.Path(base).resolve()).as_posix()
    except Exception:
        return os.path.relpath(str(x), str(base)).replace("\\", "/")


def find(base, name, kind="root"):
    """找文件：项目根优先，再去该类别目录（中文名 → 兼容英文名）。"""
    for cand in [base / name] + [d / name for d in dirs_of(base).get(kind, [])]:
        if cand.is_file():
            return cand
    return base / name


def migrate(project, root=None, dry=False):
    """把放错位置的文件归位（成品回项目根、图进配图/、音频进音频/、中间产物进 _tmp/）。"""
    L = layout(project, root=root)
    base = L["root"]
    moved = []
    spots = [(base, "root")]
    for key, _dname, _old, _desc in GROUP_DIRS:
        for d in dirs_of(base)[key]:
            spots.append((d, key))
    for d, key in spots:
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if not f.is_file() or f.name in _SKIP:
                continue
            want = classify(f.name)
            if want == key:
                continue
            dst = (L.get(want) or base) / f.name
            if dst.exists():
                moved.append((rel(base, f), "%s（已有同名，跳过）" % rel(base, dst)))
                continue
            if not dry:
                shutil.move(str(f), str(dst))
            moved.append((rel(base, f), rel(base, dst)))
    return moved


# ---------- 交付清单 ----------
def _fmt_sec(s):
    s = float(s or 0)
    return "%d:%02d" % (int(s // 60), int(round(s % 60)))


def _wav_seconds(path):
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 0.0


def _kb(path):
    try:
        return round(os.path.getsize(path) / 1024.0, 1)
    except Exception:
        return None


def _num_key(name):
    import re as _re
    m = _re.search(r"(\d+)(?=\.wav$)", name)
    return int(m.group(1)) if m else 0


def _scan(project, root=None):
    """扫描项目实际产物（路径一律相对项目根）。"""
    L = layout(project, root=root, create=False)
    base = L["root"]

    auds = []
    for d in dirs_of(base)["music"] + [base]:
        if d.is_dir():
            auds = sorted(d.glob("%s*.wav" % project), key=lambda x: _num_key(x.name))
            if auds:
                break
    audio_items = [{"index": i, "file": rel(base, x), "duration_sec": round(_wav_seconds(x), 2),
                    "duration": _fmt_sec(_wav_seconds(x)), "size_kb": _kb(x)}
                   for i, x in enumerate(auds, 1)]

    lines = []
    sp = find(base, "口播文案.md")
    if sp.is_file():
        lines = [l.strip() for l in sp.read_text(encoding="utf-8").split("\n") if l.strip()]
    for i, it in enumerate(audio_items):
        it["chars"] = len(lines[i]) if i < len(lines) else 0

    img_items = []
    for d in dirs_of(base)["image"] + [base]:
        if d.is_dir():
            found = sorted(d.glob("S*.png"))
            if found:
                img_items = [{"index": i, "file": rel(base, x), "size_kb": _kb(x)}
                             for i, x in enumerate(found, 1)]
                break

    finals, merged_file, final_file = [], "", ""
    for x in sorted(base.glob("*.mp4")):
        finals.append({"file": rel(base, x), "size_kb": _kb(x)})
        if x.name.startswith("成片_"):
            final_file = rel(base, x)
        elif x.name.startswith("合并_"):
            merged_file = rel(base, x)

    doc_files = []
    for nm in ("口播文案.md", "口播文案_标签.md", "字幕.srt", "字幕.ass"):
        f = find(base, nm)
        if f.is_file():
            doc_files.append({"file": rel(base, f), "size_kb": _kb(f)})

    dirs = {dname: (len(list((base / dname).iterdir())) if (base / dname).is_dir() else 0)
            for _k, dname, _o, _d in GROUP_DIRS}
    total = sum(float(i.get("duration_sec") or 0) for i in audio_items)
    return {"project": project, "project_root": str(base),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": KIND, "dirs": dirs,
            "audio": {"dir": "音频/", "count": len(audio_items), "total_seconds": round(total, 2),
                      "total_duration": _fmt_sec(total), "items": audio_items},
            "image": {"dir": "配图/", "count": len(img_items), "items": img_items},
            "video": {"final_file": final_file, "merged_file": merged_file,
                      "count": len(finals), "items": finals},
            "doc": {"files": doc_files}, "_L": L, "_base": base}


def write_delivery(project, root=None, extra=None, extra_md=None):
    """重建 交付清单.md + delivery.json（人读 + 机器读，同一份事实），落项目根。

    extra 进 delivery.json；extra_md 是追加到清单正文的若干行（如发布信息栏）。
    """
    d = _scan(project, root=root)
    L, base = d.pop("_L"), d.pop("_base")
    if extra:
        d.update(extra)
    jp = base / "delivery.json"
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    def tbl(head, rows):
        if not rows:
            return ["（暂无）"]
        out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
        return out + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]

    lines = ["# 交付清单 —— %s" % project, "",
             "- 生成时间：%s" % d["generated_at"],
             "- 项目目录：`%s`" % str(base),
             "- 存储格式：成品直接放项目根，成组的配图/音频进子目录（由 `sv_layout.py` 强制执行）",
             "", "## ★ 成品（项目根目录，打开项目即可见）", ""]
    rows = []
    for i in d["video"]["items"]:
        nm = i["file"].split("/")[-1]
        _note = ("无字幕母版" if not nm.startswith("成片_")
                 else "★ 发布用（已铺首帧封面）" if "带封面" in nm
                 else "★ 最终交付（已烧字幕）")
        rows.append(["`%s`" % nm, "%s KB" % i["size_kb"], _note])
    rows += [["`%s`" % f["file"].split("/")[-1], "%s KB" % f["size_kb"], "文案 / 字幕"]
             for f in d["doc"]["files"]]
    lines += tbl(["文件", "大小", "说明"], rows)
    lines += ["", "## 配图（`配图/`）", "", "共 %d 张" % d["image"]["count"], ""]
    lines += tbl(["#", "文件", "大小"],
                 [[i["index"], "`%s`" % i["file"], "%s KB" % i["size_kb"]]
                  for i in d["image"]["items"]])
    lines += ["", "## 音频（`音频/`）", "",
              "共 %d 条，合计 %s（%.2f 秒）" % (d["audio"]["count"], d["audio"]["total_duration"],
                                                d["audio"]["total_seconds"]), ""]
    lines += tbl(["#", "文件", "时长", "大小", "字数"],
                 [[i["index"], "`%s`" % i["file"], i["duration"], "%s KB" % i["size_kb"], i["chars"]]
                  for i in d["audio"]["items"]])
    lines += ["", "## 目录一览", "", "```", "%s/" % project]
    for _k, dname, _o, desc in GROUP_DIRS:
        lines.append("├─ %-8s %2d 个文件   # %s" % (dname + "/", d["dirs"].get(dname, 0), desc))
    lines[-1] = lines[-1].replace("├─", "└─", 1)
    lines += ["```", ""]
    if extra_md:
        # 传字符串时按行切（直接 list(str) 会逐字符拆开，实测踩过）
        _md = extra_md.split("\n") if isinstance(extra_md, str) else list(extra_md)
        lines += _md + [""]
    lines += ["---", "",
              "清单由 `sv_layout.write_delivery()` 自动生成，路径相对项目根；",
              "改动产物后执行 `python sv_layout.py <项目名> --delivery` 即可刷新。", ""]
    mp = base / "交付清单.md"
    with open(mp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("[layout] 交付清单：%s" % mp.name)
    print("[layout] 机器读版：%s" % jp.name)
    return {"json": str(jp), "md": str(mp), "data": d}


def export_final(project, to_dir, root=None):
    """导出成片为 `<项目名>_成片.mp4`（多项目放一起也不撞名）。

    🔴 目标目录必须是「独立交付目录」，不得是工作区根或项目目录本身——
    否则导出的副本又散回工作区根，违背「一个项目一个文件夹」的规范。
    """
    d = _scan(project, root=root)
    base = d.pop("_base")
    final = d["video"].get("final_file") or ""
    if not final:
        raise SystemExit("[layout] 还没有成片（成片_*.mp4），先跑完流水线")
    src = base / final
    dst_dir = pathlib.Path(to_dir).resolve()
    root_dir = pathlib.Path(resolve_root(root)).resolve()
    if dst_dir == root_dir or dst_dir == base.resolve():
        raise SystemExit(
            "[layout] --export 目标不得是工作区根或项目目录本身（%s）。\n"
            "          请指定一个独立的交付目录，例如：--export <交付目录>/%s" % (dst_dir, project))
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / ("%s_成片%s" % (project, src.suffix))
    shutil.copy2(str(src), str(dst))
    print("[layout] 已导出：%s" % dst)
    return str(dst)


# ---------- CLI ----------
def main():
    import argparse
    ap = argparse.ArgumentParser(description="项目存储格式（成品放项目根 + 成组分目录）+ 交付清单")
    ap.add_argument("project", help="项目名（产物落 <工作区>/<项目名>/）")
    ap.add_argument("--root", default=None, help="工作区根（默认当前工作区）")
    ap.add_argument("--files", action="store_true", help="连文件一起列")
    ap.add_argument("--migrate", action="store_true", help="把放错位置的文件归位")
    ap.add_argument("--delivery", action="store_true", help="重建 交付清单.md + delivery.json")
    ap.add_argument("--export", metavar="DIR", help="导出成片为 <项目名>_成片.mp4")
    ap.add_argument("--dry", action="store_true", help="只打印将做什么")
    a = ap.parse_args()

    L = layout(a.project, a.root)
    base = L["root"]
    print("项目目录：%s" % base)

    if a.migrate:
        mv = migrate(a.project, a.root, dry=a.dry)
        print("[migrate] %s %d 个文件：" % ("将移动" if a.dry else "已移动", len(mv)))
        for s, t in mv:
            print("  %s -> %s" % (s, t))

    if a.delivery or a.migrate or not (a.export or a.files):
        if not a.dry:
            write_delivery(a.project, a.root)

    if a.export and not a.dry:
        export_final(a.project, a.export, root=a.root)

    if a.files:
        print("\n[项目根 · 成品]")
        for f in sorted(base.iterdir()):
            if f.is_file():
                print("   - %s" % f.name)
        for _k, dname, _o, desc in GROUP_DIRS:
            d = base / dname
            fs = sorted(os.listdir(d)) if d.is_dir() else []
            print("\n[%s · %s] %d 个文件" % (dname, desc, len(fs)))
            for f in fs[:40]:
                print("   - %s" % f)


if __name__ == "__main__":
    main()
