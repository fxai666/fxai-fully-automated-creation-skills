# -*- coding: utf-8 -*-
"""三分栏版式（横屏左中右 / 竖屏上中下）+ 背景底。

**版式层**里「三分区」这一类的画法：分区是固定的（文字区 / 媒体槽 / 背景底），
两样东西各自独立可换：

- **怎么分区** = 版式：横屏切左中右，竖屏切上中下，同一份代码按画布比例自动切。
- **底是什么** = `bg`：`static`（纯色渐变）/ `blur`（当段素材放大虚化铺满）/ `blurvideo`（视频虚化）。

所以「竖屏三分栏 + 静态背景」是一个**配方**（版式 + 画布方向 + 背景），不是三个版式。
横屏同一套代码自动变左中右，加虚化背景只改 `bg` 一个字段。

与 `ml_frame` 的分工：

| | 文字 | 媒体 | 谁随段变 |
|---|---|---|---|
| `ml_frame` 上中下分栏 | 上下两条固定带 | 中区一格 | 壳不动，只换中区 |
| `ml_split` 三分栏 | 三分区之一，随旁白逐段换 | 另一区 | 文字与媒体都按段变 |

因为文字每段不同，分栏的遮罩也是**每段一张**（不像上中下分栏的壳全片复用）；
合成端拿到的是 `split_mask(fields)` 出的带 alpha PNG + `split_box()` 给的槽位坐标。

边界（与 ml_slots / ml_page / ml_frame 一致）：
- 不 import 任何 fxai 技能；画布样式与字段净化由调用方注入（`configure`）。
- 不落盘、不读全局配置、不管录制。

调用方典型用法::

    import ml_split
    ml_split.configure(canvas_css=slides._canvas_css, sanitize=slides._sanitize_pills)
    ml_split.split_mask(fields)          # 带 alpha 的半屏遮罩，媒体区透明
    ml_split.split_box(w, h)             # 媒体槽坐标，交给 ml_slots.part(box=...)
"""
import base64
import pathlib

# 分栏的默认品牌落款：换品牌只改这一处，段里不用重复写。
SPLIT_BRAND = {
    "logo": "凤希",
    "tagline": "",
    "slogan": "一个人，就是一支团队",
}

# 横竖屏切换的宽高比门槛（含等比）
LAND_MIN_RATIO = 1.2

# 媒体槽在画布上的比例（与下方 CSS 的百分比必须一致）
LAND_BOX = (0.395, 0.110, 0.555, 0.780)     # 文字在左，媒体在右
LAND_BOX_MIRROR = (0.050, 0.110, 0.555, 0.780)  # 媒体在左，文字在右
PORT_BOX = (0.060, 0.435, 0.880, 0.425)     # 竖屏：文字在上，媒体在下

SPLIT_CSS = """
.sp-all{position:fixed;inset:0;background:#0B0B0C;
  background-image:radial-gradient(120% 90% at 84% 24%,rgba(217,180,90,.14),transparent 62%),
    repeating-linear-gradient(45deg,rgba(255,255,255,.019) 0 1px,transparent 1px calc(14px*var(--sp)))}
.sp-text{position:absolute;display:flex;flex-direction:column;justify-content:center;
  font-family:"Smiley Sans","Microsoft YaHei",sans-serif;color:#fff}
/* 子项不得被 flex 压缩：内容一多，清单格子会被压成细条 */
.sp-text > *{flex:0 0 auto}
.sp-land .sp-text{left:5%;top:0;height:100%;width:31.5%}
.sp-land.side-left .sp-text{left:65%;width:31%}
.sp-port .sp-text{left:6%;top:0;height:43.5%;width:88%}
.sp-kicker{font-size:calc(21px*var(--sp));letter-spacing:calc(4px*var(--sp));color:#D9B45A;
  font-weight:700;margin-bottom:calc(20px*var(--sp))}
.sp-title{font-size:calc(82px*var(--sp));line-height:1.06;font-weight:900;
  letter-spacing:calc(1px*var(--sp));color:#fff;
  -webkit-text-stroke:calc(2px*var(--sp)) #141414;paint-order:stroke fill;
  text-shadow:0 calc(4px*var(--sp)) 0 rgba(0,0,0,.55)}
.sp-title em{font-style:normal;color:#FFD84D}
.sp-lead{font-size:calc(36px*var(--sp));line-height:1.34;color:#E9DDBF;font-weight:600;
  margin-top:calc(18px*var(--sp))}
.sp-pts{margin-top:calc(26px*var(--sp));display:flex;flex-direction:column;gap:calc(13px*var(--sp))}
.sp-pt{position:relative;padding-left:calc(26px*var(--sp));font-size:calc(26px*var(--sp));
  line-height:1.36;color:#B7AD95;font-weight:500}
.sp-pt:before{content:"";position:absolute;left:0;top:calc(14px*var(--sp));
  width:calc(11px*var(--sp));height:calc(2px*var(--sp));background:#D9B45A}
.sp-note{margin-top:calc(26px*var(--sp));font-size:calc(20px*var(--sp));color:#7E7563;
  letter-spacing:calc(1px*var(--sp))}
.sp-steps{display:grid;grid-template-columns:repeat(4,1fr);gap:calc(6px*var(--sp));
  margin-top:calc(28px*var(--sp))}
.sp-st{font-size:calc(17px*var(--sp));padding:calc(5px*var(--sp)) calc(6px*var(--sp));
  border-radius:calc(4px*var(--sp));color:#9A9078;border:1px solid #35312A;
  font-weight:600;line-height:1.25;text-align:center;white-space:nowrap}
.sp-st.on{color:#0B0B0C;background:#FFD84D;border-color:#FFD84D;font-weight:800}
.sp-brand{display:flex;align-items:center;gap:calc(10px*var(--sp));
  margin-top:calc(30px*var(--sp));padding-top:calc(18px*var(--sp));
  border-top:1px solid rgba(217,180,90,.22)}
.sp-logo{font-size:calc(34px*var(--sp));font-weight:900;letter-spacing:calc(3px*var(--sp));
  color:#D9B45A;line-height:1}
.sp-slogan{font-size:calc(19px*var(--sp));color:#8C8270;letter-spacing:calc(1px*var(--sp));
  font-weight:600;line-height:1.3}
.sp-logoimg{height:calc(38px*var(--sp));display:block}
/* 背景底 = 虚化（bg:blur / blurvideo）时，本层只画文字与边框：
   左侧留一层渐隐暗底保证可读，右缘淡出，不盖住媒体槽 */
.sp-veil{position:absolute;left:0;top:0;height:100%;width:42%;
  background:linear-gradient(90deg,rgba(8,8,9,.86) 0%,rgba(8,8,9,.58) 62%,rgba(8,8,9,0) 100%)}
.sp-port .sp-veil{width:100%;height:47%;
  background:linear-gradient(180deg,rgba(8,8,9,.86) 0%,rgba(8,8,9,.56) 60%,rgba(8,8,9,0) 100%)}
/* 整页模式下自己虚化：当段素材放大 + 模糊 + 压暗铺满 */
.sp-blurbg{position:absolute;inset:0;background-size:cover;background-position:center;
  filter:blur(calc(30px*var(--sp))) brightness(.52) saturate(.92);
  transform:scale(1.14)}
.sp-blurbg.blurvideo{filter:blur(calc(22px*var(--sp))) brightness(.48) saturate(.9)}

.sp-media{position:absolute;overflow:hidden;background:#000;
  border-radius:calc(10px*var(--sp));
  box-shadow:0 0 0 1px rgba(217,180,90,.30),0 calc(16px*var(--sp)) calc(44px*var(--sp)) rgba(0,0,0,.62)}
.sp-land .sp-media{left:39.5%;top:11%;width:55.5%;height:78%}
.sp-land.side-left .sp-media{left:5%;width:55.5%}
.sp-port .sp-media{left:6%;top:43.5%;width:88%;height:42.5%}
.sp-media img{width:100%;height:100%;object-fit:cover;display:block}
.sp-media.contain img{object-fit:contain;
  background:radial-gradient(80% 80% at 50% 45%,#17181B,#0A0A0B 78%)}
.sp-full .sp-media{inset:0;width:100%;height:100%;border-radius:0;box-shadow:none}
/* 全屏压字的文案通常更长，标题降一档，长句才不会挤成三行 */
.sp-full .sp-title{font-size:calc(70px*var(--sp))}
/* 全屏压字：底色渐变铺满整幅（越往右越透），文字只占左侧内容区 */
.sp-full .sp-text{left:0;top:0;height:100%;width:100%;padding-left:5%;padding-right:47%;
  background:linear-gradient(90deg,rgba(8,8,9,.96) 0%,rgba(8,8,9,.94) 32%,
    rgba(8,8,9,.80) 50%,rgba(8,8,9,.34) 68%,rgba(8,8,9,.06) 86%,rgba(8,8,9,0) 100%)}
.sp-port.sp-full .sp-text{height:100%;padding-right:6%;padding-top:8%;
  justify-content:flex-start;
  background:linear-gradient(180deg,rgba(8,8,9,.95) 0%,rgba(8,8,9,.88) 40%,rgba(8,8,9,0) 70%)}
"""

# 背景底三档：static 纯色渐变 / blur 当段素材虚化铺满 / blurvideo 视频虚化铺满。
# blur 与 blurvideo 的层序在合成端（ml_slots.part 的 bg_slot），遮罩这时只留文字与边框。
BG_MODES = ("static", "blur", "blurvideo")


def bg_of(fields):
    """取背景底档位：static（缺省）/ blur / blurvideo，容忍中文写法。"""
    v = str(fields.get("bg") or "static").strip().lower()
    if v in ("blur", "虚化", "虚化图片", "模糊", "模糊铺底"):
        return "blur"
    if v in ("blurvideo", "虚化视频", "视频虚化", "video"):
        return "blurvideo"
    return "static"


CFG = {"canvas_css": None, "sanitize": None}


def configure(canvas_css=None, sanitize=None):
    """注入调用方的画布样式与字段净化（与 ml_frame 同签名，便于一处配置两处用）。"""
    if canvas_css:
        CFG["canvas_css"] = canvas_css
    if sanitize:
        CFG["sanitize"] = sanitize


def _default_canvas_css(css, w, h):
    w, h = int(w), int(h)
    s = min(w / 768.0, h / 1280.0)
    return (":root{--s:%.4f;--w:%dpx;--h:%dpx}\n" % (s, w, h)
            + css.replace("__W__", str(w)).replace("__H__", str(h)))


def _media_src(p):
    """本地素材 → base64 data URI（不依赖路径解析，换目录也不碎）。"""
    if not p:
        return ""
    p = pathlib.Path(str(p))
    if not p.exists() or not p.is_file():
        return ""
    ext = p.suffix.lower().lstrip(".")
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp", "gif": "gif"}.get(ext, "png")
    try:
        return "data:image/%s;base64,%s" % (mime, base64.b64encode(p.read_bytes()).decode("ascii"))
    except Exception:
        return ""


def is_land(w, h):
    return (float(w) / float(h)) >= LAND_MIN_RATIO


def _side(fields):
    v = str(fields.get("media_side") or "right").strip().lower()
    return "left" if v in ("left", "左", "图左", "左图") else "right"


def _full(fields):
    return bool(fields.get("media_full") or fields.get("全屏"))


def split_box(w, h, side=None, full=False):
    """媒体槽在画布上的 (x, y, w, h)。与 CSS 里的百分比一一对应。

    合成端拿到它直接交给 `ml_slots.part(box=...)`：遮罩不动、素材在框里动。
    full=True（媒体铺满整屏、文字渐隐压在画面上）时返回整幅。
    """
    w, h = int(w), int(h)
    if full:
        return (0, 0, w, h)
    r = LAND_BOX_MIRROR if str(side or "right").lower() == "left" else LAND_BOX
    if not is_land(w, h):
        r = PORT_BOX
    return (int(round(w * r[0])), int(round(h * r[1])),
            int(round(w * r[2])), int(round(h * r[3])))


def split_page(inner, w=None, h=None, transparent=False):
    """分栏专用页面壳：--sp 按面积基准缩放。

    取宽度比与高度比的几何平均：1280×720 = 1.0，768×1280 ≈ 1.03。
    只按 min 会让竖屏缩到 0.6（字号在 1280 高的画面里太小），只按宽度对横屏不友好。
    """
    w = int(w or 1280)
    h = int(h or 720)
    base = ("*{box-sizing:border-box;margin:0;padding:0}"
            "html,body{width:__W__px;height:__H__px;overflow:hidden;background:#000}")
    if transparent:
        base += "html,body{background:transparent!important}"
    css = (CFG["canvas_css"] or _default_canvas_css)(base + SPLIT_CSS, w, h)
    sp = ((w / 1280.0) * (h / 720.0)) ** 0.5
    css += ":root{--sp:%.4f}" % sp
    return ('<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            '<style>%s</style></head><body>%s</body></html>' % (css, inner))


def _cls(w, h, fields):
    c = ["sp-all", "sp-land" if is_land(w, h) else "sp-port"]
    if _side(fields) == "left":
        c.append("side-left")
    if _full(fields):
        c.append("sp-full")
    return " ".join(c)


def _brand(fields):
    logo = ""
    if fields.get("logo_img"):
        src = _media_src(fields["logo_img"])
        if src:
            logo = '<img class="sp-logoimg" src="%s">' % src
    cn = fields.get("logo", SPLIT_BRAND["logo"])
    if not logo and cn:
        logo = '<div class="sp-logo">%s</div>' % cn
    sl = fields.get("slogan", SPLIT_BRAND["slogan"])
    tag = fields.get("tagline", SPLIT_BRAND["tagline"])
    txt = " · ".join(x for x in (tag, sl) if x)
    if not logo and not txt:
        return ""
    parts = [logo] if logo else []
    if txt:
        parts.append('<div class="sp-slogan">%s</div>' % txt)
    return '<div class="sp-brand">%s</div>' % "".join(parts)


def _steps(fields):
    """岗位 / 环节清单：一串小标签，当前那条高亮（分栏的节奏感靠它）。"""
    items = fields.get("steps")
    if not items:
        return ""
    cur = fields.get("cur")
    if cur is None:
        cur = fields.get("step_cur")
    try:
        cur = int(cur) if cur is not None else None
    except (TypeError, ValueError):
        cur = None
    out = []
    for k, s in enumerate(items, start=1):
        on = " on" if cur is not None and k == cur else ""
        out.append('<div class="sp-st%s">%s</div>' % (on, s))
    return '<div class="sp-steps">%s</div>' % "".join(out)


def _text(fields):
    """左栏 / 上栏内容：栏目号 → 主标题 → 一句要点 → 要点清单 → 角注 → 环节清单 → 品牌。"""
    h = []
    if fields.get("kicker"):
        h.append('<div class="sp-kicker">%s</div>' % fields["kicker"])
    if fields.get("title"):
        h.append('<div class="sp-title">%s</div>' % fields["title"])
    if fields.get("lead"):
        h.append('<div class="sp-lead">%s</div>' % fields["lead"])
    pts = fields.get("points") or []
    if pts:
        h.append('<div class="sp-pts">%s</div>'
                 % "".join('<div class="sp-pt">%s</div>' % p for p in pts))
    if fields.get("note"):
        h.append('<div class="sp-note">%s</div>' % fields["note"])
    h.append(_steps(fields))
    h.append(_brand(fields))
    return '<div class="sp-text">%s</div>' % "".join(h)


def _clean(fields):
    fn = CFG["sanitize"]
    if fn:
        fn(fields)
    return fields


def _single_media(fields):
    """整页直贴时能嵌进 HTML 的那一张（一叠素材 / 视频都只能走遮罩路线）。"""
    m = fields.get("media")
    if isinstance(m, (list, tuple)):
        return ""
    if str(m or "").lower().endswith((".mp4", ".mov", ".m4v", ".webm", ".mkv")):
        return ""
    return m or ""


def split_slide(fields, w=1280, h=720):
    """版式「三分栏」：横屏左中右 / 竖屏上中下（单图一张 HTML 解决）。

    fields:
      kicker     左上栏目号（如「岗位 03 / 08」），金色小字
      title      主标题（岗位名 / 结论），可用 <em> 标黄
      lead       主标题下一句（大号、米白）
      points     要点清单（list，3 条以内最好看）
      note       角注（小字灰）
      steps      环节 / 岗位清单（list），配 cur 高亮当前那条
      cur        当前序号（从 1 起，对应 steps）
      media      媒体：单张路径；一叠 list 走遮罩路线做段内轮播；
                 {"page": {...}} 是网页槽（文字卡 / 长图滚动 / 真网页）
      media_fit  "cover"（默认）/ "blur"（竖版海报虚化铺底）/ "contain"（完整不裁切）
      media_side "right"（默认，左文右图）/ "left"（左图右文）
      bg         背景底：static（默认，纯色渐变）/ blur（当段素材虚化铺满）/
                 blurvideo（视频虚化铺满，整页模式与 blur 同画法）
      media_full true 时媒体铺满整屏、文字渐隐压在上面（另一种版式：全屏压字）
      logo / slogan / tagline   品牌落款，缺省取 SPLIT_BRAND
    """
    _clean(fields)
    fit = " contain" if str(fields.get("media_fit") or "").lower() in ("contain", "完整", "不裁切") else ""
    img = _media_src(_single_media(fields))
    media = '<div class="sp-media%s">%s</div>' % (fit, ('<img src="%s">' % img) if img else "")
    blur = ""
    if bg_of(fields) != "static" and img:
        # 整页模式没有合成端，虚化底就在页面里做（视频素材走遮罩 + part 的 bg_slot）
        blur = '<div class="sp-blurbg%s" style="background-image:url(%s)"></div>' % (
            " blurvideo" if bg_of(fields) == "blurvideo" else "", img)
    return split_page('<div class="%s">%s%s%s</div>'
                      % (_cls(w, h, fields), blur, media, _text(fields)), w, h)


def split_mask(fields, w=1280, h=720):
    """三分栏的「遮罩」：文字照画，媒体区留透明。

    视频 / 轮播 / 网页段用它出带 alpha 的 PNG，合成时垫在素材上面 ——
    遮罩不动、素材在框里动。坐标用 `split_box()` 取，两边不可各算一遍。
    """
    _clean(fields)
    # 素材垫在遮罩下面，做不出圆角裁切；这里退成直角 + 一圈细金边，照样能界定媒体框
    media = ('<div class="sp-media" style="background:transparent;box-shadow:none;'
             'border:1px solid rgba(217,180,90,.34);border-radius:0"></div>')
    veil = '<div class="sp-veil"></div>' if bg_of(fields) != "static" else ""
    return split_page('<div class="%s" style="background:transparent">%s%s%s</div>'
                      % (_cls(w, h, fields), veil, media, _text(fields)), w, h, transparent=True)
