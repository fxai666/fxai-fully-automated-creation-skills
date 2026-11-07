# -*- coding: utf-8 -*-
"""fxai-slide-video · 分段规格（单一事实源，保证画面与字幕严格对应）

本模块把「一段 = 一张配图 + 一句文案」绑成一个结构，渲染时按序号生成
S01_xxx.png 与文案段一一对应，从根上保证画面与字幕是同一段内容：
    segments.json / 列表，每项：
      { "text":     "该段口播/字幕文案（原样作为字幕，长度不限）",
        "template": "版式名：points|list|compare|steps|quote|showcase|code|marketing|outfit|product|stats|chat|pricing|frame",
        "fields":   { ...对应版式参数... },
        "slug":     "可选，文件名后缀，便于辨认" }
"""
import json, sys, re, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import slides as S

# 素材清单归一在公共件里（广告 / 幻灯同一份判据），这里只转发
_ML = pathlib.Path(__file__).resolve().parent.parent / "fxai-mixed-layout"
if _ML.is_dir() and str(_ML) not in sys.path:
    sys.path.insert(0, str(_ML))
import ml_slots as _ml    # noqa: E402
import ml_trio as _mltrio  # noqa: E402  槽里素材方向的判据与 trio_box 同源

# 这些版式走「遮罩 + 素材垫入」：配图只出外壳（媒体区透明），素材由合成端垫。
# frame 的壳全片复用；thirds 的文字随段变，遮罩因此每段一张。
# trio（左中右分栏）与 frame 同族，壳元素一样，只有槽的形状不同。
MASK_TPL = ("frame", "上中下分栏", "上中下分栏静态背景", "上中下", "栏目图文",
            "thirds", "split", "三分栏", "分栏", "左右二分栏", "上下二分栏",
            "trio", "左中右分栏", "左中右", "左中右三分栏")
SPLIT_TPL = ("thirds", "split", "三分栏", "分栏", "左右二分栏", "上下二分栏")
TRIO_TPL = ("trio", "左中右分栏", "左中右", "左中右三分栏")

_TEMPLATES = {
    "points":  lambda f: S.points_slide(f.get("items", []), title=f.get("title", ""),
                                        sub=f.get("sub", "")),
    "list":    lambda f: S.list_slide([(i[0], i[1], i[2]) for i in f.get("items", [])],
                                     f.get("cur", 1), title=f.get("title", ""),
                                     sub=f.get("sub", "")),
    "compare": lambda f: S.compare_slide([(i[0], i[1], i[2], i[3]) for i in f.get("items", [])],
                                        title=f.get("title", ""), sub=f.get("sub", "")),
    "steps":   lambda f: S.steps_slide([(i[0], i[1]) for i in f.get("items", [])],
                                      f.get("cur", 1), title=f.get("title", ""),
                                      sub=f.get("sub", "")),
    "quote":   lambda f: S.quote_slide(f.get("text", ""), f.get("tail", ""),
                                      title=f.get("title", ""), sub=f.get("sub", "")),
    "showcase": lambda f: S.showcase_slide(f.get("image", ""),
                                           f.get("title", ""), f.get("points", []),
                                           f.get("sub", "")),
    "code":    lambda f: S.code_slide(f.get("code", ""), title=f.get("title", ""),
                                     sub=f.get("sub", ""), note=f.get("note", "")),
    "thirds":   lambda f: S.split_slide(f),
    "split":    lambda f: S.split_slide(f),
    "三分栏":   lambda f: S.split_slide(f),
    "分栏":     lambda f: S.split_slide(f),
    "左右二分栏": lambda f: S.split_slide(f),
    "上下二分栏": lambda f: S.split_slide(f),
    "marketing": lambda f: S.marketing_slide(f),
    "outfit":   lambda f: S.outfit_slide(f),
    "product":  lambda f: S.product_slide(f),
    "stats":    lambda f: S.stats_slide(f),
    "chat":     lambda f: S.chat_slide(f),
    "pricing":  lambda f: S.pricing_slide(f),
    "frame":    lambda f: S.frame_slide(f),
    # 「上中下分栏」= 上中下分栏静态背景模板的正式叫法：上带 + 中区展示 + 下带，壳全片固定
    "上中下分栏": lambda f: S.frame_slide(f),
    "上中下分栏静态背景": lambda f: S.frame_slide(f),
    "上中下":     lambda f: S.frame_slide(f),
    "栏目图文":   lambda f: S.frame_slide(f),
    # 「左中右分栏」：同族的竖版版式，中槽满高贴边、宽度可调（media_w），两翼竖排文字
    "trio":       lambda f: S.trio_slide(f),
    "左中右分栏": lambda f: S.trio_slide(f),
    "左中右":     lambda f: S.trio_slide(f),
    "左中右三分栏": lambda f: S.trio_slide(f),
}

# 中区能接受的外部素材类型；视频段只出「壳遮罩」，素材由合成端垫进去
_VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv")


def is_video_media(p):
    return str(p or "").lower().endswith(_VIDEO_EXT)


def _media_items(fields, base_dir=None, seg_fit=None):
    """把一段的媒体素材归一成 [{"path","kind","fit",...}]（转发公共件）。

    写法与判据只有一处：`fxai-mixed-layout/references/01-素材槽规格.md`。
    seg_fit 是版式给的默认填充档（左中右分栏默认 "height"：满高居中、上下零边距），
    段里显式写 media_fit 或逐条写 fit 时以段里的为准。
    """
    return _ml.normalize_items(fields.get("media"), base_dir,
                               fields.get("media_fit") or seg_fit)


# ---------- 版式自适应：按素材方向选布局 ----------
AUTO_TPL = ("auto", "自动", "自适应")


def _first_media_path(fields):
    """段里第一个素材的路径（单张 / list 第一项 / dict 取 path|src）。"""
    m = fields.get("media")
    if isinstance(m, (list, tuple)):
        m = m[0] if m else None
    if isinstance(m, dict):
        m = m.get("path") or m.get("src")
    return str(m) if m else None


def auto_layout(fields):
    """按**第一个素材的构图方向**选版式。

        竖构图素材 → `trio`（左中右：中槽满高竖条，左右两栏放文字）
        横构图素材 → `frame`（上中下：中区全宽 16:9 横带，上下两条带放文字）

    读不到素材（没给 / 文件不存在）时按 `frame` 兜底 —— 横素材更常见，
    而且 frame 的中区是 16:9，任何横素材进去都不会出边距。
    想要「横素材 + 左右文字」就显式写 `template: "trio"`，auto 不承担这个组合。
    """
    p = _first_media_path(fields)
    if not p:
        return "frame"
    w, h = _ml.wh(p)
    if not w or not h:
        return "frame"
    return "trio" if h > w else "frame"


def _abs_media(p, base_dir):
    """素材相对路径按 segments.json 所在目录解析（绝对路径原样返回）。"""
    if not p:
        return p
    p = pathlib.Path(str(p))
    if p.is_absolute():
        return str(p)
    cand = pathlib.Path(base_dir) / p
    return str(cand if cand.exists() else p)


def _abs_media_any(m, base_dir):
    """media 可以是单张也可以是 list（段内轮播），统一按规格目录解析相对路径。

    除了顶层 src，中区网页槽的 page.src（长图滚动）也一并解析。
    """
    if isinstance(m, (list, tuple)):
        out = []
        for it in m:
            if isinstance(it, dict):
                d = dict(it)
                src = d.get("src") or d.get("path") or d.get("media")
                if src:
                    d["src"] = _abs_media(src, base_dir)
                    d.pop("path", None)
                    d.pop("media", None)
                if isinstance(d.get("page"), dict) and d["page"].get("src"):
                    d["page"] = dict(d["page"])
                    d["page"]["src"] = _abs_media(d["page"]["src"], base_dir)
                out.append(d)
            else:
                out.append(_abs_media(it, base_dir))
        return out
    return _abs_media(m, base_dir)


def load_specs(path):
    """读分段规格，并把顶层 `frame` / `trio`（节目壳）合并进每一段。

    上中下分栏 / 左中右分栏的壳（title1/title2/logo/tagline/slogan）全片一致 ——
    写一次在顶层，每段只写自己的 media / overlay。段内同名字段优先（允许某段临时改壳）。
    两个键同族同字段：只写 `frame` 就上下分栏用、只写 `trio` 就左中右用、都写则以 `trio` 为准。
    segments.json 顶层还可给 theme，见 spec_theme。
    """
    src = pathlib.Path(path)
    d = json.loads(src.read_text(encoding="utf-8"))
    specs = d if isinstance(d, list) else d.get("segments", [])
    shell = {}
    if isinstance(d, dict):
        shell = dict(d.get("frame") or {})
        shell.update(d.get("trio") or {})      # 同族壳，字段同名；两处都写时以 trio 为准
    base_dir = src.parent
    for sp in specs:
        f = sp.get("fields") or {}
        if shell:
            merged = dict(shell)
            merged.update(f)
            f = merged
            sp["fields"] = f
        if f.get("media"):
            f["media"] = _abs_media_any(f["media"], base_dir)
        if f.get("logo_img"):
            f["logo_img"] = _abs_media(f["logo_img"], base_dir)
    return specs


def spec_theme(path):
    """取 segments.json 顶层的 theme（样式名或 .css 路径）；没有返回 None。"""
    try:
        d = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        return (d.get("theme") if isinstance(d, dict) else None) or None
    except Exception:
        return None


def texts_of(specs):
    """抽出各段文案，作为字幕与口播的单一来源。"""
    return [str(s.get("text", "")).strip() for s in specs]


def render_specs(specs, out_dir, tmp_dir=None, anim=False, anim_entrance=1.2, anim_fps=20,
                 record=False, rec_fps=30, rec_wait=150, w=None, h=None,
                 scroll="auto", audio_durs=None, theme=None):
    """渲染各段配图。

    record=True 时动画走「CDP 实时录制」（rec_fps 帧率）而非「逐帧定格截图」。
    w/h     ：画布尺寸（不传用默认 768×1280）；**必须在构建页面之前设定**，页面按此宽高出图。
    scroll  ：录制时内容超出视口是否自动上滚（"auto" 默认 / "wheel" / "none"）。
    audio_durs：可选，每段音频时长（秒）列表；给了就让滚动节奏跟着「说话进度」走。
    theme   ：样式名（themes/<名>.css）或自定义 .css 路径；不传用默认「深夜蓝金」。
    """
    if theme:
        S.set_theme(path=theme) if str(theme).lower().endswith(".css") else S.set_theme(theme)
    if w or h:
        S.set_canvas(w, h)                     # 页面按实际视频宽高出图（宽度锁死）
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cw, ch = S.CANVAS["w"], S.CANVAS["h"]
    mapping = {}
    videos = {}
    _split_used = False
    _trio_used = False
    for i, sp in enumerate(specs, 1):
        tpl = sp.get("template", "points")
        fields = sp.get("fields", {}) or {}
        # 版式自适应：template 写 auto 时按第一个素材的方向挑布局（竖→trio / 横→frame）
        if tpl in AUTO_TPL:
            tpl = auto_layout(fields)
            print("[auto] 段%d 按素材方向 → %s（%s构图）"
                  % (i, tpl, "竖" if tpl == "trio" else "横"), flush=True)
        if tpl not in _TEMPLATES:
            raise ValueError("未知版式 %r @段%d（可选：%s）" % (tpl, i, "/".join(_TEMPLATES)))
        _raw = sp.get("slug") or fields.get("title") or "slide"
        slug = re.sub(r"[^\w一-鿿]+", "", str(_raw))[:8]
        name = "S%02d_%s" % (i, slug)
        # 上中下分栏：中区素材交给合成端垫（遮罩不动、素材动），图片视频同一条路。
        # 配图只画壳（媒体区留透明），素材清单记进 frame_media.json 供 sv_media 取。
        # media 给一叠时，合成端把它做成段内轮播（推移/上下切换），这就是「幻灯片」效果。
        # 左中右分栏的默认填充档跟**槽里素材的方向**走（不是画布方向）：
        # 竖素材 → height（满高、上下零边距）；横素材 → cover（16:9 槽铺满）。
        # 素材读不到时才退回按画布方向判。
        _trio_portrait = _mltrio.portrait_media_of(fields) if tpl in TRIO_TPL else None
        if _trio_portrait is None:
            _trio_fit = "cover" if cw > ch else "height"
        else:
            _trio_fit = "height" if _trio_portrait else "cover"
        items = (_media_items(fields, seg_fit=(_trio_fit if tpl in TRIO_TPL else None))
                 if tpl in MASK_TPL else [])
        if items:
            _split_used = _split_used or (tpl in SPLIT_TPL)
            _trio_used = _trio_used or (tpl in TRIO_TPL)
            if tpl in TRIO_TPL:
                html = S.trio_mask(fields)
            elif tpl in SPLIT_TPL:
                html = S.split_mask(fields)
            else:
                html = S.frame_mask(fields)
            mapping[name] = S.render(name, html, out_dir, tmp_dir, w=cw, h=ch, transparent=True)
            rec = {"items": items,
                   "path": items[0]["path"], "kind": items[0]["kind"], "fit": items[0]["fit"]}
            # 版式记进登记表：合成端按它选槽位（frame_box / trio_box），别在合成端猜
            rec["layout"] = "trio" if tpl in TRIO_TPL else (
                "thirds" if tpl in SPLIT_TPL else "frame")
            if tpl in TRIO_TPL:
                rec["media_w"] = fields.get("media_w")      # 中槽宽占比（0.30~0.92）
                # 槽里素材方向：True 满高竖条 / False 16:9 横带 / None 合成端按画布兜底。
                # 合成端要靠它选槽位 —— 横屏画布里放竖屏视频也是满高竖条，不得按画布猜。
                rec["portrait_media"] = _trio_portrait
            if len(items) > 1:
                rec["trans"] = fields.get("trans") or "push_left"
                rec["trans_dur"] = fields.get("trans_dur")
            videos[name] = rec
            continue
        html = _TEMPLATES[tpl](fields)
        mapping[name] = S.render(name, html, out_dir, tmp_dir, w=cw, h=ch)
        # 每段动画：False=静态；True/缺省=默认「分层弹入」；字符串=从动画库点名
        av = sp.get("anim", True)
        if anim and av is not False and av != "none":
            aname = S.DEFAULT_ANIM if av is True else str(av)
            if aname not in S.ANIMS:
                raise ValueError("未知动画 %r @段%d（可选：%s）" % (aname, i, "/".join(S.ANIMS)))
            adur = None
            if audio_durs and i - 1 < len(audio_durs):
                adur = audio_durs[i - 1]
            if record:
                S.record_anim(name, html, out_dir, entrance=anim_entrance, fps=rec_fps,
                              tmp_dir=tmp_dir, anim_name=aname, wait_ms=rec_wait,
                              w=cw, h=ch, scroll=scroll, audio_dur=adur)
            else:
                S.render_anim(name, html, out_dir, entrance=anim_entrance, fps=anim_fps,
                              tmp_dir=tmp_dir, anim_name=aname, w=cw, h=ch)
    if videos:
        _vd = out_dir.parent / "_tmp"
        _vd.mkdir(parents=True, exist_ok=True)
        (_vd / "frame_media.json").write_text(
            json.dumps(videos, ensure_ascii=False, indent=2), encoding="utf-8")
        _all = [it for v in videos.values() for it in (v.get("items") or [])]
        # 一段片子里混了多种版式时别只报其中一种，直接说「混合版式」，免得看日志的人误判
        _lays = {v.get("layout") for v in videos.values()}
        _name = ("混合版式" if len(_lays) > 1 else
                 ("上下二分栏" if _split_used else ("左中右分栏" if _trio_used else "上中下分栏")))
        print("[%s] 中区素材登记 %d 段 / %d 份（图片 %d / 视频 %d / 网页 %d，多图段 %d）：%s"
              % (_name,
                 len(videos), len(_all),
                 sum(1 for it in _all if it.get("kind") == "image"),
                 sum(1 for it in _all if it.get("kind") == "video"),
                 sum(1 for it in _all if it.get("kind") == "page"),
                 sum(1 for v in videos.values() if len(v.get("items") or []) > 1),
                 "、".join(videos)), flush=True)
    return mapping


def verify(specs, image_dir):
    """配图数量、命名必须与分段一一对应，否则画面与字幕无法对齐。"""
    imgs = sorted(pathlib.Path(image_dir).glob("S*.png"))
    if len(imgs) != len(specs):
        raise SystemExit("配图 %d 张 ≠ 分段 %d 段：画面与字幕无法一一对应，先补齐配图"
                         % (len(imgs), len(specs)))
    return True
