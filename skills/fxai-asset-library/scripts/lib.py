#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fxai 素材库管家 —— 应用级素材（能直接剪进片子的通用片段）的配置、入库、检索、维护。

与 fxai-asset-registry 的分工：
  fxai-asset-registry  管「项目台账」—— 这条片子用过什么
  fxai-asset-library   管「素材库」—— 库里有什么、在哪、怎么找到它

库结构：<库根>/_库/<形态>/<主题>_<内容>_<规格>.<ext>
台账：  <库根>/_库/库内清单.json（机读）+ 库内清单.md（渲染）

常用：
  lib.py roots                                  看配置了哪些库
  lib.py add <文件> --theme 仙宫云 --form 录屏 --name 开实例四步实操 --desc "..."
  lib.py find 仙宫云 开实例                       检索
  lib.py info 仙宫云-001 | 开实例                 看详情
  lib.py ls --form 录屏                          按形态列
  lib.py set 仙宫云-001 --tags "仙宫云,开实例"
  lib.py check                                   体检（失联/缺详情）
  lib.py thumbs                                  生成缩略图
  lib.py html                                    出可预览看板
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

# ---------------------------------------------------------------- 常量

LIB_DIRNAME = "_库"
LIB_JSON = "库内清单.json"
LIB_MD = "库内清单.md"
THUMB_DIR = ".thumbs"

FORMS = ("录屏", "演示", "对比", "口播", "实拍", "配乐", "版式", "图", "其它")

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv", ".m4v", ".ts"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
AUD_EXT = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", "_tmp", THUMB_DIR, "_备份", ".workbuddy"}

USER_CONFIG = os.path.join(os.path.expanduser("~"), ".workbuddy", "fxai_asset_library.json")

DEFAULT_CONFIG = {
    "说明": "凤希AI 应用级素材库配置。库[].根 指向素材库目录（其下应有 _库 子目录）。改这里就能换库位置。",
    "默认库": "主素材库",
    "库": [],
    "缩略图宽": 480,
    "项目台账扫描根": "",
}


def _global_defaults() -> tuple:
    """素材库根与项目空间根的唯一来源：fxai-common 全局配置（fxai_global.json）。

    库里不写死盘符；全局配置没填时返回空串，由调用方提示去填。
    """
    common = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "fxai-common"))
    if common not in sys.path:
        sys.path.insert(0, common)
    try:
        import global_config as gc
    except Exception:
        return "", ""
    return (gc.asset_library_root() or ""), (gc.projects_root() or "")


def default_config() -> dict:
    """按全局配置生成默认配置（首次运行落盘用）。"""
    lib_root, proj_root = _global_defaults()
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if lib_root:
        cfg["库"] = [{"名称": "主素材库", "根": lib_root, "形态目录": list(FORMS)}]
    cfg["项目台账扫描根"] = proj_root
    return cfg


# ---------------------------------------------------------------- 索引

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import idx as IDX
except Exception:
    IDX = None


def idx_root(cfg, a) -> str:
    return getattr(a, "src", "") or cfg.get("项目台账扫描根") or _global_defaults()[1]


def idx_build(d, lib, cfg, a=None, refs=True):
    """建/重建索引。失败不影响主流程（会退回内存扫描）。"""
    if not IDX:
        return
    try:
        r = IDX.build(d, lib, refs=refs, refs_root=idx_root(cfg, a), quiet=True)
        print("索引已重建：%d 条素材 / %d 个倒排项 / %d ms" % (r["assets"], r["terms"], r["ms"]))
    except Exception as e:
        print("索引构建失败（不影响台账）：%s" % e)


def idx_touch(d, it):
    if IDX:
        try:
            IDX.upsert_row(d, it)
        except Exception:
            pass


def idx_drop(d, code):
    if IDX:
        try:
            IDX.delete_row(d, code)
        except Exception:
            pass


def idx2item(r):
    """索引行 → 台账字段名，方便和旧打印逻辑共用。"""
    return {
        "编号": r.get("code", ""), "文件": r.get("file", ""), "类型": r.get("kind", ""),
        "形态": r.get("form", ""), "分类": r.get("theme", ""), "规格": r.get("spec", ""),
        "大小": r.get("human", ""), "用途": r.get("desc_", ""),
        "标签": [t for t in (r.get("tags") or "").split(" / ") if t],
        "来源": r.get("src", ""),
    }


# ---------------------------------------------------------------- 小工具

def kind_of(p: str) -> str:
    e = os.path.splitext(p)[1].lower()
    if e in VIDEO_EXT:
        return "视频"
    if e in IMG_EXT:
        return "图"
    if e in AUD_EXT:
        return "音频"
    return "其他"


def human(n: int) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f}{u}" if u != "B" else f"{n}B"
        n /= 1024.0
    return f"{n:.1f}TB"


def jload(p: str, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def jdump(p: str, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def quick_md5(p: str, n: int = 1 << 20) -> str:
    import hashlib
    h = hashlib.md5()
    with open(p, "rb") as f:
        h.update(f.read(n))
    return h.hexdigest()[:10]


def ffprobe(p: str) -> dict:
    """返回 宽高/fps/帧数/时长/编码，失败返回 {}。"""
    out = {}
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-count_packets", "-show_entries",
             "stream=width,height,r_frame_rate,nb_read_packets,codec_name:format=duration",
             "-of", "json", p],
            capture_output=True, text=True, timeout=90,
        )
        d = json.loads(r.stdout or "{}")
        st = (d.get("streams") or [{}])[0]
        fm = d.get("format") or {}
        if st.get("width"):
            out["分辨率"] = f"{st['width']}x{st['height']}"
        fr = st.get("r_frame_rate") or ""
        if "/" in fr:
            a, b = fr.split("/")
            if float(b or 1):
                out["fps"] = round(float(a) / float(b or 1), 2)
        if st.get("nb_read_packets"):
            out["帧数"] = int(st["nb_read_packets"])
        if fm.get("duration"):
            out["时长"] = round(float(fm["duration"]), 2)
        if st.get("codec_name"):
            out["编码"] = st["codec_name"]
    except Exception:
        pass
    return out


def spec_tokens(info: dict, kind: str) -> tuple[str, str]:
    """返回 (文件名用 token, 显示用规格)。"""
    if kind == "视频":
        tok = []
        disp = []
        if info.get("分辨率"):
            tok.append(info["分辨率"])
            disp.append(info["分辨率"])
        if info.get("fps"):
            disp.append(f"{info['fps']:g}fps")
        if info.get("帧数"):
            tok.append(f"{info['帧数']}f")
            disp.append(f"{info['帧数']}帧")
        if info.get("时长"):
            disp.append(f"{info['时长']}s")
        return ("_".join(tok), " · ".join(disp))
    if kind == "图":
        wh = info.get("分辨率")
        return (wh or "", wh or "")
    if kind == "音频":
        t = info.get("时长")
        return (f"{t}s" if t else "", f"{t}s" if t else "")
    return ("", "")


def audio_dur(p: str):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", p], capture_output=True, text=True, timeout=60)
        return round(float((r.stdout or "").strip() or 0), 2)
    except Exception:
        return 0


# ---------------------------------------------------------------- 配置

def load_config() -> dict:
    if os.path.exists(USER_CONFIG):
        cfg = jload(USER_CONFIG, {})
        if cfg.get("库"):
            return cfg
    # 首次：按全局配置落一份用户配置，方便直接改
    cfg = default_config()
    jdump(USER_CONFIG, cfg)
    return cfg


def pick_lib(cfg: dict, want: str = "") -> dict:
    libs = cfg.get("库") or []
    if not libs:
        print("配置里没有任何库，先跑 lib.py roots 生成配置后再改。")
        sys.exit(2)
    if want:
        for l in libs:
            if want in (l.get("名称"), l.get("根")):
                return l
        print(f"没找到库「{want}」，现有：{' / '.join(l.get('名称', '?') for l in libs)}")
        sys.exit(2)
    env = os.environ.get("FXAI_ASSET_LIB", "").strip()
    if env:
        return {"名称": "env", "根": env, "形态目录": list(FORMS)}
    d = cfg.get("默认库", "")
    for l in libs:
        if l.get("名称") == d:
            return l
    return libs[0]


def lib_paths(cfg: dict, a) -> tuple[str, str]:
    """返回 (库根, _库 目录)。允许 --lib 直接覆盖。"""
    if getattr(a, "lib", ""):
        root = os.path.abspath(a.lib)
    else:
        root = os.path.abspath(pick_lib(cfg, getattr(a, "use", "")).get("根", ""))
    return root, os.path.join(root, LIB_DIRNAME)


def ensure_lib(root: str) -> str:
    d = os.path.join(root, LIB_DIRNAME)
    for s in FORMS:
        os.makedirs(os.path.join(d, s), exist_ok=True)
    os.makedirs(os.path.join(d, THUMB_DIR), exist_ok=True)
    return d


def lib_load(d: str) -> dict:
    p = os.path.join(d, LIB_JSON)
    obj = jload(p, {})
    if not obj:
        obj = {"更新时间": now(), "素材": []}
    obj.setdefault("素材", [])
    return obj


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def next_id(lib: dict, theme: str) -> str:
    used = set()
    for it in lib["素材"]:
        n = it.get("编号", "")
        if n.startswith(theme + "-"):
            try:
                used.add(int(n.split("-")[-1]))
            except Exception:
                pass
    i = 1
    while i in used:
        i += 1
    return f"{theme}-{i:03d}"


def find_entry(lib: dict, key: str):
    key = (key or "").strip()
    if not key:
        return None
    for it in lib["素材"]:
        if it.get("编号") == key:
            return it
    low = key.lower()
    hits = [it for it in lib["素材"]
            if low in (it.get("文件", "") + it.get("原名", "") + it.get("用途", "")).lower()]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        print(f"「{key}」匹配到 {len(hits)} 条，用编号指定：")
        for it in hits:
            print(f"  {it.get('编号')} | {it.get('文件')}")
        return None
    return None


# ---------------------------------------------------------------- 渲染

def render_md(d: str, lib: dict):
    rows = sorted(lib["素材"], key=lambda x: (x.get("形态", ""), x.get("编号", "")))
    lines = [f"# 库内清单", ""]
    lines.append(f"- 更新时间：{lib.get('更新时间', '')}")
    lines.append(f"- 素材总数：{len(rows)}")
    lines.append("")
    lines.append("> 机读文件是同目录 `库内清单.json`。本文件由 `lib.py md` 渲染，结构别手改；")
    lines.append("> 改用途/标签/主题用 `lib.py set <编号> --tags ... --desc ...`。")
    lines.append("")
    by_form = {}
    for it in rows:
        by_form.setdefault(it.get("形态") or "其它", []).append(it)
    for form in FORMS:
        g = by_form.get(form)
        if not g:
            continue
        lines.append(f"## {form}（{len(g)}）")
        lines.append("")
        lines.append("| 编号 | 主题 | 文件 | 规格 | 大小 | 用途 | 标签 |")
        lines.append("|---|---|---|---|---|---|---|")
        for it in g:
            use = (it.get("用途") or "").replace("|", "／")
            if len(use) > 90:
                use = use[:90] + "…"
            lines.append(
                f"| {it.get('编号', '')} | {it.get('分类', '')} | `{it.get('文件', '')}` "
                f"| {it.get('规格', '')} | {it.get('大小', '')} | {use} "
                f"| {' / '.join(it.get('标签') or [])} |")
        lines.append("")
    with open(os.path.join(d, LIB_MD), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------- 命令：roots

def cmd_roots(a):
    cfg = load_config()
    print(f"配置文件：{USER_CONFIG}")
    print("")
    for l in cfg.get("库") or []:
        root = os.path.abspath(l.get("根", ""))
        ok = os.path.isdir(os.path.join(root, LIB_DIRNAME))
        mark = "✅" if ok else "❌ 无 _库"
        mark2 = "  ← 默认" if l.get("名称") == cfg.get("默认库") else ""
        cnt = 0
        if ok:
            cnt = len(lib_load(os.path.join(root, LIB_DIRNAME))["素材"])
        print(f"{l.get('名称', '?'):<10} {root}")
        print(f"{'':<10} {mark}  素材 {cnt} 条{mark2}")
    env = os.environ.get("FXAI_ASSET_LIB", "")
    if env:
        print(f"\n环境变量 FXAI_ASSET_LIB 生效：{env}")


# ---------------------------------------------------------------- 命令：add

def cmd_add(a):
    cfg = load_config()
    root, d = lib_paths(cfg, a)
    ensure_lib(root)
    lib = lib_load(d)

    src = os.path.abspath(a.file)
    if not os.path.isfile(src):
        print(f"文件不存在：{src}")
        sys.exit(2)
    kind = kind_of(src)
    if kind == "其他":
        print(f"不认识的素材类型：{src}")
        sys.exit(2)

    in_lib = os.path.normcase(d) in os.path.normcase(os.path.dirname(src))
    theme = (a.theme or "").strip()
    form = (a.form or ("图" if kind == "图" else "其它")).strip()
    if form not in FORMS:
        print(f"--form 只能是 {'/'.join(FORMS)}")
        sys.exit(2)

    info = ffprobe(src) if kind == "视频" else {}
    if kind == "图":
        try:
            from PIL import Image
            with Image.open(src) as im:
                info["分辨率"] = f"{im.size[0]}x{im.size[1]}"
        except Exception:
            pass
    if kind == "音频":
        t = audio_dur(src)
        if t:
            info["时长"] = t
    tok, spec = spec_tokens(info, kind)

    if in_lib:
        rel = os.path.relpath(src, d).replace("\\", "/")
        print(f"文件已在库内：{rel}")
    else:
        if not theme:
            print("入库必须带 --theme（决定分册与编号前缀），如 --theme 仙宫云")
            sys.exit(2)
        base = (a.name or os.path.splitext(os.path.basename(src))[0]).strip()
        base = re.sub(r'[\\/:*?"<>|]', "_", base)
        stem = f"{theme}_{base}" + (f"_{tok}" if tok else "")
        rel = f"{form}/{stem}{os.path.splitext(src)[1].lower()}"
        dst = os.path.join(d, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
            print(f"已存在同名同大小，跳过复制：{rel}")
        else:
            if a.move:
                shutil.move(src, dst)
            else:
                shutil.copy2(src, dst)
            print(f"{'已移入' if a.move else '已入库'}：{rel}")

    full = os.path.join(d, rel)
    hit = None
    for it in lib["素材"]:
        if it.get("文件") == rel:
            hit = it
            break
    if hit is None:
        hit = {"编号": next_id(lib, a.theme or "未分类"), "文件": rel}
        lib["素材"].append(hit)

    size = os.path.getsize(full) if os.path.exists(full) else 0
    hit.update({
        "原名": os.path.basename(src),
        "类型": kind,
        "形态": form,
        "分类": theme or hit.get("分类", "未分类"),
        "规格": spec or hit.get("规格", ""),
        "大小": human(size),
        "大小字节": size,
        "用途": a.desc or hit.get("用途", ""),
        "标签": [t for t in re.split(r"[,，\s]+", a.tags) if t] or hit.get("标签", []),
        "来源": a.from_ or hit.get("来源", ""),
        "md5_1m": quick_md5(full) if os.path.exists(full) else "",
        "入库": now(),
    })
    if not hit.get("标签") and hit.get("分类"):
        hit["标签"] = [hit["分类"]]
    lib["更新时间"] = now()
    jdump(os.path.join(d, LIB_JSON), lib)
    render_md(d, lib)
    idx_touch(d, hit)

    if not a.desc:
        print("⚠️ 没给 --desc，用途为空，以后检索不到它。补：lib.py set %s --desc \"...\"" % hit["编号"])
    if a.thumb:
        make_thumb(d, hit, cfg.get("缩略图宽", 480), force=True)
        jdump(os.path.join(d, LIB_JSON), lib)
    print(f"{hit['编号']} | {rel} | {hit.get('规格', '')}")


# ---------------------------------------------------------------- 命令：ls

def cmd_ls(a):
    cfg = load_config()
    root, d = lib_paths(cfg, a)
    lib = lib_load(d)
    rows = lib["素材"]
    if a.form:
        rows = [x for x in rows if x.get("形态") == a.form]
    if a.theme:
        rows = [x for x in rows if x.get("分类") == a.theme]
    if a.tag:
        rows = [x for x in rows if a.tag in (x.get("标签") or [])]
    if a.sort == "大小":
        rows = sorted(rows, key=lambda x: x.get("大小字节", 0), reverse=True)
    else:
        rows = sorted(rows, key=lambda x: (x.get("形态", ""), x.get("编号", "")))

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        print("没有符合条件的素材。")
        return
    print(f"共 {len(rows)} 条（库：{root}）\n")
    cur = None
    for it in rows:
        if it.get("形态") != cur:
            cur = it.get("形态")
            print(f"[{cur}]")
        use = (it.get("用途") or "** 未标注 **").replace("\n", " ")
        if len(use) > 52:
            use = use[:52] + "…"
        exists = os.path.exists(os.path.join(d, it.get("文件", "")))
        flag = "" if exists else "  ⚠️失联"
        print(f"  {it.get('编号', ''):<12} {it.get('文件', '').split('/')[-1][:46]:<46} "
              f"{it.get('规格', ''):<28} {use}{flag}")


# ---------------------------------------------------------------- 命令：find

STOP = {"的", "了", "和", "与", "或", "一个", "一段", "素材", "片段", "视频", "内容"}


def score(it: dict, terms: list[str]) -> int:
    s = 0
    name = (it.get("文件", "") + " " + it.get("原名", "")).lower()
    theme = (it.get("分类", "") or "").lower()
    form = (it.get("形态", "") or "").lower()
    desc = (it.get("用途", "") or "").lower()
    src = (it.get("来源", "") or "").lower()
    tags = [t.lower() for t in (it.get("标签") or [])]
    for t in terms:
        if t in name:
            s += 8
        if t in theme:
            s += 6
        if t in tags:
            s += 6
        if t in form:
            s += 4
        if t in desc:
            s += 3
        if t in src:
            s += 1
        if any(t in tg for tg in tags):
            s += 2
    return s


def cmd_find(a):
    cfg = load_config()
    root, d = lib_paths(cfg, a)
    # 索引新鲜时不解析整份台账（5000 条约 3MB，解析一次 ~80ms）
    py_lib = None if (IDX and not a.scan and IDX.fresh(d)) else lib_load(d)
    words = [t for t in re.split(r"[,，\s]+", " ".join(a.words)) if t and t not in STOP]
    if not words:
        print("给几个关键词，如：lib.py find 仙宫云 开实例")
        sys.exit(2)

    hits = []
    used_index = False
    if IDX and not a.scan:
        try:
            if py_lib is not None:
                idx_build(d, py_lib, cfg, a)     # 索引过期：读一次台账、重建
            rows = IDX.search(d, words, form=a.form, theme=a.theme, tag=a.tag,
                              limit=a.limit, lib=None, refs_root=idx_root(cfg, a))
            hits = [(sc, idx2item(r)) for sc, r in rows]
            used_index = True
        except Exception as e:
            print("（索引不可用，退回内存扫描：%s）" % e)

    if not used_index:
        if py_lib is None:
            py_lib = lib_load(d)
        terms = [w.lower() for w in words]
        cand = []
        for it in py_lib["素材"]:
            if a.form and it.get("形态") != a.form:
                continue
            if a.theme and it.get("分类") != a.theme:
                continue
            if a.tag and a.tag not in (it.get("标签") or []):
                continue
            sc = score(it, terms)
            if sc > 0:
                cand.append((sc, it))
        cand.sort(key=lambda x: (-x[0], x[1].get("编号", "")))
        hits = cand[: a.limit] if a.limit else cand

    if a.json:
        print(json.dumps([dict(it, 匹配分=s) for s, it in hits], ensure_ascii=False, indent=2))
        return
    if not hits:
        print("没找到「%s」。" % " ".join(words))
        print("换更短的词，或先 lib.py ls 看全库。")
        return
    print("命中 %d 条%s\n" % (len(hits), "（索引检索）" if used_index else "（内存扫描）"))
    for sc, it in hits:
        exists = os.path.exists(os.path.join(d, it.get("文件", "")))
        print("{:<12} {:<8} {:<6} {:<28} 分{}{}".format(
            it.get("编号", ""), it.get("分类", ""), it.get("形态", ""),
            it.get("规格", ""), sc, "" if exists else "  ⚠️文件失联"))
        print("  {:<12} {}".format("", it.get("文件", "")))
        use = (it.get("用途") or "").replace("\n", " ")
        if use:
            print("  {:<12} {}".format("", use[:110]))
        if it.get("标签"):
            print("  {:<12} 标签：{}".format("", " / ".join(it["标签"])))
        print("")


# ---------------------------------------------------------------- 命令：info

def project_refs(cfg: dict, a, code: str) -> list[str]:
    """扫各项目 素材登记.json，找出引用了该库内编号的项目。"""
    src_root = a.src or cfg.get("项目台账扫描根") or _global_defaults()[1]
    out = []
    if not os.path.isdir(src_root):
        return out
    for dp, dns, fns in os.walk(src_root):
        dns[:] = [x for x in dns if x not in SKIP_DIRS and not x.startswith(".")]
        if "素材登记.json" in fns:
            lg = jload(os.path.join(dp, "素材登记.json"), {})
            for it in lg.get("素材", []):
                if it.get("库内") == code:
                    out.append(f"{os.path.relpath(dp, src_root).replace(os.sep, '/')} → {it.get('文件', '')}")
                    break
        if dp.count(os.sep) - src_root.count(os.sep) > 4:
            dns[:] = []
    return out


def cmd_info(a):
    cfg = load_config()
    root, d = lib_paths(cfg, a)
    lib = lib_load(d)
    it = find_entry(lib, a.key)
    if not it:
        print(f"没找到「{a.key}」")
        sys.exit(2)
    full = os.path.join(d, it.get("文件", ""))
    ok = os.path.exists(full)
    print(f"编号    {it.get('编号')}")
    print(f"文件    {it.get('文件')}")
    print(f"绝对    {full.replace(os.sep, '/')}")
    print(f"形态    {it.get('形态', '')}    主题  {it.get('分类', '')}    类型  {it.get('类型', '')}")
    print(f"规格    {it.get('规格', '')}")
    print(f"大小    {it.get('大小', '')}    入库  {it.get('入库', '')}")
    print(f"状态    {'✅ 在库' if ok else '❌ 文件失联'}")
    if ok:
        real = os.path.getsize(full)
        if it.get("大小字节") and real != it["大小字节"]:
            print(f"        ⚠️ 文件大小已变：登记 {human(it['大小字节'])} → 实际 {human(real)}，跑 lib.py check")
    print(f"标签    {' / '.join(it.get('标签') or []) or '（空）'}")
    print(f"来源    {it.get('来源', '') or '（空）'}")
    print(f"用途    {it.get('用途', '') or '（空，检索不到它，补一下）'}")
    th = os.path.join(d, THUMB_DIR, it.get("编号", "") + ".jpg")
    print(f"缩略图  {th.replace(os.sep, '/') if os.path.exists(th) else '（未生成，跑 lib.py thumbs）'}")
    refs = []
    if IDX and not getattr(a, "scan", False):
        try:
            refs = IDX.refs_of(d, it.get("编号", ""))
        except Exception:
            refs = []
    if not refs:
        refs = project_refs(cfg, a, it.get("编号", ""))
    print(f"被引用  {len(refs)} 个项目")
    for r in refs[:10]:
        print(f"        {r}")


# ---------------------------------------------------------------- 命令：set

def cmd_set(a):
    cfg = load_config()
    root, d = lib_paths(cfg, a)
    lib = lib_load(d)
    it = find_entry(lib, a.key)
    if not it:
        print(f"没找到「{a.key}」")
        sys.exit(2)

    if a.theme:
        it["分类"] = a.theme
    if a.form:
        if a.form not in FORMS:
            print(f"--form 只能是 {'/'.join(FORMS)}")
            sys.exit(2)
        it["形态"] = a.form
    if a.desc is not None:
        it["用途"] = a.desc
    if a.tags is not None:
        it["标签"] = [t for t in re.split(r"[,，\s]+", a.tags) if t]
    if a.from_ is not None:
        it["来源"] = a.from_
    if a.add_tags:
        cur = it.get("标签") or []
        for t in re.split(r"[,，\s]+", a.add_tags):
            if t and t not in cur:
                cur.append(t)
        it["标签"] = cur

    # 主题/形态变了 → 文件跟着搬（可选）
    if a.move_file:
        old = os.path.join(d, it["文件"])
        parts = it["文件"].split("/")
        newrel = f"{it['形态']}/{parts[-1]}"
        if newrel != it["文件"]:
            newfull = os.path.join(d, newrel)
            os.makedirs(os.path.dirname(newfull), exist_ok=True)
            if os.path.exists(old):
                shutil.move(old, newfull)
            it["文件"] = newrel
            print(f"文件已移到 {newrel}")

    lib["更新时间"] = now()
    jdump(os.path.join(d, LIB_JSON), lib)
    render_md(d, lib)
    idx_touch(d, it)
    print(f"{it['编号']} 已更新")


# ---------------------------------------------------------------- 命令：rm

def cmd_rm(a):
    cfg = load_config()
    root, d = lib_paths(cfg, a)
    lib = lib_load(d)
    it = find_entry(lib, a.key)
    if not it:
        print(f"没找到「{a.key}」")
        sys.exit(2)
    if not a.yes:
        print(f"将从台账移除：{it['编号']} | {it['文件']}")
        print("（文件默认保留在磁盘。要连文件一起删加 --file 和 --yes）")
        print("确认加 --yes")
        sys.exit(2)
    lib["素材"] = [x for x in lib["素材"] if x is not it]
    lib["更新时间"] = now()
    jdump(os.path.join(d, LIB_JSON), lib)
    render_md(d, lib)
    idx_drop(d, it["编号"])
    print(f"已从台账移除 {it['编号']}")
    if a.file:
        p = os.path.join(d, it["文件"])
        if os.path.exists(p):
            os.remove(p)
            print(f"文件已删除：{it['文件']}")


# ---------------------------------------------------------------- 命令：check

def cmd_check(a):
    cfg = load_config()
    root, d = lib_paths(cfg, a)
    lib = lib_load(d)
    rows = lib["素材"]
    gone, changed, no_desc, no_tags, no_thumb = [], [], [], [], []
    seen, dups = {}, []
    for it in rows:
        p = os.path.join(d, it.get("文件", ""))
        if not os.path.exists(p):
            gone.append(it)
            continue
        real = os.path.getsize(p)
        if it.get("大小字节") and real != it["大小字节"]:
            changed.append((it, real))
        if not (it.get("用途") or "").strip():
            no_desc.append(it)
        if not (it.get("标签") or []):
            no_tags.append(it)
        th = os.path.join(d, THUMB_DIR, it.get("编号", "") + ".jpg")
        if not os.path.exists(th):
            no_thumb.append(it)
        h = it.get("md5_1m")
        if h:
            seen.setdefault(h, []).append(it)
    dups = [v for v in seen.values() if len(v) > 1]

    print(f"库：{root}")
    print(f"台账 {len(rows)} 条")
    print(f"  失联      {len(gone)}")
    print(f"  大小变化  {len(changed)}")
    print(f"  缺用途    {len(no_desc)}")
    print(f"  缺标签    {len(no_tags)}")
    print(f"  缺缩略图  {len(no_thumb)}")
    print(f"  疑似重复  {len(dups)}")
    for it in gone:
        print(f"    ❌ {it.get('编号')} | {it.get('文件')}")
    for it, real in changed:
        print(f"    ⚠️ {it.get('编号')} | 登记 {it.get('大小')} → 实际 {human(real)}")
    for it in no_desc:
        print(f"    · {it.get('编号')} 缺用途：lib.py set {it.get('编号')} --desc \"...\"")
    for v in dups:
        print(f"    ≡ {' ≡ '.join(x.get('编号', '') for x in v)}（首1MB相同）")
    if not gone and not changed and not no_desc:
        print("  ✅ 台账健康")
    if IDX:
        st = IDX.stats(d)
        if st:
            print("\n索引    %s  %d 条 / %d 倒排项 / %d 词 / %s / 建于 %s" % (
                "✅ 新鲜" if st["新鲜"] else "⚠️ 过期，跑 lib.py reindex",
                st["素材"], st["倒排项"], st["去重词"], human(st["大小"]), st["构建"]))
        else:
            print("\n索引    未建（跑 lib.py reindex）")


# ---------------------------------------------------------------- 命令：thumbs

def make_thumb(d: str, it: dict, width: int, force=False) -> bool:
    code = it.get("编号", "")
    th = os.path.join(d, THUMB_DIR, code + ".jpg")
    if os.path.exists(th) and not force:
        return True
    src = os.path.join(d, it.get("文件", ""))
    if not os.path.exists(src):
        return False
    os.makedirs(os.path.dirname(th), exist_ok=True)
    kind = it.get("类型") or kind_of(src)
    try:
        if kind == "视频":
            subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", "0.4", "-i", src, "-frames:v", "1",
                 "-vf", f"scale={width}:-2", "-q:v", "3", "-y", th],
                capture_output=True, timeout=90, check=True)
        elif kind == "图":
            from PIL import Image
            with Image.open(src) as im:
                im = im.convert("RGB")
                w, h = im.size
                im = im.resize((width, max(1, int(h * width / w))), Image.LANCZOS)
                im.save(th, "JPEG", quality=88)
        else:
            return False
    except Exception:
        return False
    return os.path.exists(th)


def cmd_thumbs(a):
    cfg = load_config()
    root, d = lib_paths(cfg, a)
    lib = lib_load(d)
    w = cfg.get("缩略图宽", 480)
    n = 0
    for it in lib["素材"]:
        if a.code and it.get("编号") != a.code:
            continue
        if make_thumb(d, it, w, force=a.force):
            n += 1
    print(f"缩略图就绪 {n} 张 → {(os.path.join(d, THUMB_DIR)).replace(os.sep, '/')}")


# ---------------------------------------------------------------- 命令：html

def cmd_html(a):
    cfg = load_config()
    root, d = lib_paths(cfg, a)
    lib = lib_load(d)
    rows = sorted(lib["素材"], key=lambda x: (x.get("形态", ""), x.get("分类", ""), x.get("编号", "")))

    data = []
    for it in rows:
        code = it.get("编号", "")
        data.append({
            "c": code,
            "t": it.get("分类", ""),
            "f": it.get("形态", ""),
            "k": it.get("类型", ""),
            "s": it.get("规格", ""),
            "z": it.get("大小", ""),
            "d": it.get("用途", ""),
            "g": it.get("标签") or [],
            "p": it.get("文件", ""),
            "h": 1 if os.path.exists(os.path.join(d, THUMB_DIR, code + ".jpg")) else 0,
        })
    forms = sorted({x["f"] for x in data if x["f"]})
    themes = sorted({x["t"] for x in data if x["t"]})
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>素材库看板 · __N__ 条</title>
<style>
:root{--bg:#0e1116;--pn:#151a22;--pn2:#1b222c;--bd:#252d3a;--tx:#e6ebf2;--mu:#8b97a8;--ac:#4ea1ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
 font:14px/1.6 "Microsoft YaHei","PingFang SC",system-ui,sans-serif}
header{position:sticky;top:0;z-index:9;background:rgba(14,17,22,.94);
 backdrop-filter:blur(8px);border-bottom:1px solid var(--bd);padding:14px 22px}
h1{margin:0 0 10px;font-size:17px;font-weight:600}
h1 em{font-style:normal;color:var(--mu);font-weight:400;margin-left:10px;font-size:13px}
.bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input[type=search]{flex:1;min-width:220px;max-width:430px;background:var(--pn2);
 border:1px solid var(--bd);color:var(--tx);border-radius:8px;padding:8px 12px;font-size:14px;outline:none}
input[type=search]:focus{border-color:var(--ac)}
.chip{cursor:pointer;border:1px solid var(--bd);background:var(--pn);color:var(--mu);
 border-radius:999px;padding:4px 12px;font-size:12.5px;user-select:none}
.chip.on{border-color:var(--ac);color:#fff;background:#17324f}
.stat{margin-left:auto;color:var(--mu);font-size:12.5px;font-variant-numeric:tabular-nums}
main{padding:18px 22px 20px;display:grid;gap:14px;
 grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}
.c{background:var(--pn);border:1px solid var(--bd);border-radius:12px;overflow:hidden;
 display:flex;flex-direction:column;transition:.15s}
.c:hover{border-color:#3a465a;transform:translateY(-2px)}
.th{aspect-ratio:16/10;background:#0a0d12;display:flex;align-items:center;justify-content:center;
 cursor:pointer;overflow:hidden}
.th img{width:100%;height:100%;object-fit:cover;display:block}
.ph{color:#3d4a5c;font-size:30px;display:flex;flex-direction:column;align-items:center;gap:2px}
.ph em{font-style:normal;font-size:11px;color:#4a5768}
.b{padding:10px 12px 12px;display:flex;flex-direction:column;gap:5px;flex:1}
.r1{display:flex;gap:7px;align-items:baseline;font-size:12px}
.r1 b{color:var(--ac);font-weight:600}
.r1 u{text-decoration:none;color:#cfd8e6}
.r1 s{text-decoration:none;color:var(--mu);border:1px solid var(--bd);border-radius:4px;padding:0 5px;font-size:11px}
.sp{font-size:11.5px;color:var(--mu);font-variant-numeric:tabular-nums}
.b p{margin:2px 0 0;font-size:12.5px;color:#b6c1d0;display:-webkit-box;
 -webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.tg{display:flex;gap:5px;flex-wrap:wrap;margin-top:auto;padding-top:6px}
.tg i{font-style:normal;font-size:11px;color:#7fa8d6;background:#16222f;
 border:1px solid #22303f;border-radius:4px;padding:0 6px}
footer{padding:0 22px 60px;text-align:center}
#more{background:var(--pn2);border:1px solid var(--bd);color:var(--tx);border-radius:9px;
 padding:10px 26px;font-size:13.5px;cursor:pointer}
#more:hover{border-color:var(--ac)}
#m{position:fixed;inset:0;background:rgba(4,6,10,.93);display:none;z-index:99;
 align-items:center;justify-content:center;padding:24px;flex-direction:column;gap:12px}
#m.on{display:flex}
#m video,#m img{max-width:92vw;max-height:82vh;border-radius:10px;background:#000}
#m .pth{font-size:12px;color:var(--mu);word-break:break-all;text-align:center;max-width:90vw}
#m .x{position:absolute;top:18px;right:24px;font-size:26px;color:#8b97a8;cursor:pointer}
.empty{color:var(--mu);padding:40px;text-align:center;grid-column:1/-1}
</style></head><body>
<header>
  <h1>素材库看板 <em>__N__ 条 · 更新 __UPD__</em></h1>
  <div class="bar">
    <input id="q" type="search" placeholder="搜编号 / 主题 / 标签 / 用途 / 规格，空格分隔多个词">
    <span class="chip on" data-f="">全部形态</span>
__FORMCHIPS__
    <span class="chip on" data-t="">全部主题</span>
__THEMECHIPS__
    <span class="stat" id="stat"></span>
  </div>
</header>
<main id="g"></main>
<footer><button id="more">加载更多</button></footer>
<div id="m"><span class="x">✕</span><div id="mv"></div><div class="pth"></div></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const ALL = JSON.parse(document.getElementById('data').textContent);
const PAGE = 48;
const g = document.getElementById('g'), more = document.getElementById('more');
const stat = document.getElementById('stat'), q = document.getElementById('q');
let F = '', T = '', view = ALL, shown = 0;

function esc(s){return String(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function card(o){
  const th = o.h ? `<img src="_库/.thumbs/${encodeURIComponent(o.c)}.jpg" loading="lazy" alt="">`
    : `<div class="ph"><span>${o.k==='视频'?'▶':o.k==='图'?'🖼':'♪'}</span><em>${esc(o.k)}</em></div>`;
  const tags = (o.g||[]).map(t=>`<i>${esc(t)}</i>`).join('');
  return `<article class="c" data-code="${esc(o.c)}">
    <div class="th" data-p="${esc(o.p)}" data-k="${esc(o.k)}">${th}</div>
    <div class="b">
      <div class="r1"><b>${esc(o.c)}</b><u>${esc(o.t)}</u><s>${esc(o.f)}</s></div>
      <div class="sp">${esc(o.s)} · ${esc(o.z)}</div>
      <p>${esc(o.d)||'未标注用途'}</p>
      <div class="tg">${tags}</div>
    </div></article>`;
}
function render(reset){
  if(reset){g.innerHTML='';shown=0;}
  const slice = view.slice(shown, shown+PAGE);
  g.insertAdjacentHTML('beforeend', slice.map(card).join(''));
  shown += slice.length;
  more.style.display = shown < view.length ? '' : 'none';
  more.textContent = `加载更多（已显示 ${shown} / ${view.length}）`;
  stat.textContent = `共 ${view.length} 条` + (view.length!==ALL.length?` / 全库 ${ALL.length}`:'');
  if(!view.length) g.innerHTML = '<div class="empty">没有匹配的素材</div>';
}
function apply(){
  const terms = q.value.trim().toLowerCase().split(/\\s+/).filter(Boolean);
  view = ALL.filter(o=>{
    if(F && o.f!==F) return false;
    if(T && o.t!==T) return false;
    if(!terms.length) return true;
    const hay = (o.c+' '+o.t+' '+o.f+' '+o.d+' '+o.s+' '+(o.g||[]).join(' ')).toLowerCase();
    return terms.every(t=>hay.includes(t));
  });
  render(true);
}
q.addEventListener('input', apply);
more.addEventListener('click', ()=>render(false));
document.querySelectorAll('.chip').forEach(ch=>ch.addEventListener('click', ()=>{
  if(ch.dataset.f!==undefined){
    F = (F===ch.dataset.f?'':ch.dataset.f);
    document.querySelectorAll('.chip[data-f]').forEach(x=>x.classList.toggle('on', x.dataset.f===F));
  } else {
    T = (T===ch.dataset.t?'':ch.dataset.t);
    document.querySelectorAll('.chip[data-t]').forEach(x=>x.classList.toggle('on', x.dataset.t===T));
  }
  apply();
}));
const m = document.getElementById('m'), mv = document.getElementById('mv'), pth = m.querySelector('.pth');
g.addEventListener('click', e=>{
  const th = e.target.closest('.th'); if(!th) return;
  const p = '_库/' + th.dataset.p, k = th.dataset.k, enc = encodeURI(p);
  mv.innerHTML = k==='视频' ? `<video src="${enc}" controls autoplay loop></video>`
    : k==='图' ? `<img src="${enc}">` : '';
  pth.textContent = p;
  m.classList.add('on');
});
m.addEventListener('click', e=>{
  if(e.target===m || e.target.className==='x'){ m.classList.remove('on'); mv.innerHTML=''; }
});
document.addEventListener('keydown', e=>{
  if(e.key==='Escape'){ m.classList.remove('on'); mv.innerHTML=''; }
});
apply();
</script></body></html>"""

    html = (html.replace("__N__", str(len(data)))
            .replace("__UPD__", lib.get("更新时间", ""))
            .replace("__FORMCHIPS__", "".join(
                f'<span class="chip" data-f="{f}">{f}</span>' for f in forms))
            .replace("__THEMECHIPS__", "".join(
                f'<span class="chip" data-t="{t}">{t}</span>' for t in themes))
            .replace("__DATA__", payload))

    out = os.path.join(root, a.out or "素材库看板.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"看板已生成：{out.replace(os.sep, '/')}（{len(rows)} 条，分页 {48}/页）")


# ---------------------------------------------------------------- 命令：reindex

def cmd_reindex(a):
    cfg = load_config()
    root, d = lib_paths(cfg, a)
    lib = lib_load(d)
    if not IDX:
        print("索引模块 idx.py 不在，检查 scripts/ 目录。")
        sys.exit(2)
    idx_build(d, lib, cfg, a, refs=not a.no_refs)


# ---------------------------------------------------------------- 命令：stats

def cmd_stats(a):
    cfg = load_config()
    root, d = lib_paths(cfg, a)
    lib = lib_load(d)
    rows = lib.get("素材", [])
    total = sum(int(x.get("大小字节") or 0) for x in rows)
    print("库        %s" % root)
    print("台账      %d 条 / %s" % (len(rows), human(total)))
    by_form = {}
    for x in rows:
        f = x.get("形态") or "其它"
        by_form[f] = by_form.get(f, 0) + 1
    if by_form:
        print("形态      " + "  ".join(
            "%s %d" % (k, v) for k, v in sorted(by_form.items(), key=lambda kv: -kv[1])))
    if not IDX:
        print("索引      模块缺失")
        return
    st = IDX.stats(d)
    if not st:
        print("索引      未建（跑 lib.py reindex）")
        return
    print("索引      %s" % ("✅ 新鲜" if st["新鲜"] else "⚠️ 过期，跑 lib.py reindex"))
    print("  素材    %d 条" % st["素材"])
    print("  倒排    %d 项 / %d 个不同词" % (st["倒排项"], st["去重词"]))
    print("  引用    %d 条（扫自 %s）" % (st["引用"], st["引用扫描根"] or "未设"))
    print("  库文件  %s" % st["数据库"])
    print("  体积    %s" % human(st["大小"]))
    print("  构建    %s（%s）" % (st["构建"], st["耗时"]))



# ---------------------------------------------------------------- 命令：md

def cmd_md(a):
    cfg = load_config()
    root, d = lib_paths(cfg, a)
    lib = lib_load(d)
    render_md(d, lib)
    print(f"已渲染 {(os.path.join(d, LIB_MD)).replace(os.sep, '/')}")


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(prog="lib.py", description="凤希AI 素材库管家")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def base(p, need_dir=True):
        p.add_argument("--lib", default="", help="临时指定库根（默认取配置文件）")
        p.add_argument("--use", default="", help="用配置里哪个库（名称）")

    p = sub.add_parser("roots", help="看配置了哪些库、各有多少条")
    base(p)

    p = sub.add_parser("add", help="入库：复制+规范改名+登记")
    p.add_argument("file")
    p.add_argument("--theme", default="", help="主题=分册名，如 仙宫云（决定编号前缀）")
    p.add_argument("--form", default="", help=f"形态：{'/'.join(FORMS)}")
    p.add_argument("--name", default="", help="内容名，如 镜像广场开实例四步实操")
    p.add_argument("--desc", default="", help="用途：画面内容+关键识别信息")
    p.add_argument("--tags", default="", help="标签，逗号分隔，首词=分册")
    p.add_argument("--from", dest="from_", default="", help="来源说明")
    p.add_argument("--move", action="store_true", help="移动而不是复制（源文件即被移走）")
    p.add_argument("--no-thumb", dest="thumb", action="store_false", help="不生成缩略图")
    base(p)

    p = sub.add_parser("ls", help="列库内素材")
    p.add_argument("--form", default="")
    p.add_argument("--theme", default="")
    p.add_argument("--tag", default="")
    p.add_argument("--sort", default="编号", choices=("编号", "大小"))
    p.add_argument("--json", action="store_true")
    base(p)

    p = sub.add_parser("find", help="关键词检索（走索引）")
    p.add_argument("words", nargs="+")
    p.add_argument("--scan", action="store_true", help="不走索引，用内存扫描（排查用）")
    p.add_argument("--form", default="")
    p.add_argument("--theme", default="")
    p.add_argument("--tag", default="")
    p.add_argument("--limit", type=int, default=30, help="最多返回几条，0=全部（默认 30）")
    p.add_argument("--json", action="store_true")
    base(p)

    p = sub.add_parser("info", help="看单条详情")
    p.add_argument("key", help="编号或文件名片段")
    p.add_argument("--src", default="", help="扫哪些项目台账找引用（默认取配置）")
    p.add_argument("--scan", action="store_true", help="不用索引里的引用表，现扫项目台账")
    base(p)

    p = sub.add_parser("set", help="改用途/标签/主题/形态")
    p.add_argument("key")
    p.add_argument("--desc", default=None)
    p.add_argument("--tags", default=None)
    p.add_argument("--add-tags", dest="add_tags", default="")
    p.add_argument("--theme", default="")
    p.add_argument("--form", default="")
    p.add_argument("--from", dest="from_", default=None)
    p.add_argument("--move-file", action="store_true", help="形态变了把文件挪到对应目录")
    base(p)

    p = sub.add_parser("rm", help="从台账移除（默认保留文件）")
    p.add_argument("key")
    p.add_argument("--file", action="store_true")
    p.add_argument("--yes", action="store_true")
    base(p)

    p = sub.add_parser("check", help="体检：失联/大小变/缺详情/缺缩略图/重复")
    base(p)

    p = sub.add_parser("thumbs", help="生成缩略图")
    p.add_argument("--force", action="store_true")
    p.add_argument("--code", default="")
    base(p)

    p = sub.add_parser("html", help="生成可预览看板")
    p.add_argument("--out", default="素材库看板.html")
    base(p)

    p = sub.add_parser("reindex", help="重建检索索引（素材多了定期跑）")
    p.add_argument("--no-refs", dest="no_refs", action="store_true", help="不重扫项目引用")
    base(p)

    p = sub.add_parser("stats", help="看库与索引规模")
    base(p)

    p = sub.add_parser("md", help="重渲染 库内清单.md")
    base(p)

    a = ap.parse_args()
    fn = globals().get("cmd_" + a.cmd)
    if not fn:
        ap.print_help()
        sys.exit(2)
    fn(a)


if __name__ == "__main__":
    main()
