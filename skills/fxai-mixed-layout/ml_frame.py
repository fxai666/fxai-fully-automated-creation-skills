# -*- coding: utf-8 -*-
"""上中下分栏的壳（固定上下带 + 中区素材槽）。

**版式层**里「带壳槽位」这一类的画法：上带标题、下带品牌，中间一条留给素材。
壳是资产——一段写一次、全片复用，每段只换中区那一格。

边界（与 ml_slots / ml_page 一致）：
- 不 import 任何 fxai 技能；画布样式与字段净化由调用方注入（`configure`）。
- 不落盘、不读全局配置、不管录制。

调用方典型用法（幻灯侧）::

    import ml_frame
    ml_frame.configure(canvas_css=slides._canvas_css, sanitize=slides._sanitize_pills)
    ml_frame.frame_mask(fields)          # 带 alpha 的壳遮罩，素材垫在下面
    ml_frame.frame_slide(fields)         # 单图直接出整页（壳+图一张图解决）
"""
import base64
import pathlib

# 🔴 上中下分栏是**竖屏版式**，默认画布 9:16（1080×1920）。
# 这条不是偏好，是版式定义：上下两条带压住画面、中间一条横构图素材带，
# 竖着拿在手里才成立。横屏只作例外（素材本身是横屏片、要整条嵌进来时才借一次）。
FRAME_PORTRAIT = (1080, 1920)

# 中区恒为「全宽 16:9 一条带」：素材按 16:9 出，铺满不裁、不留边。
# 所以中区高度由画布宽度直接定死，不写死比例 —— 换画布尺寸，中区自己跟着对。
FRAME_MEDIA_RATIO = 16.0 / 9.0
# 剩余高度在上下带之间怎么分：上带略高（两行大标题），下带放品牌。
FRAME_SPLIT = 0.527

# 横屏（w > h）是例外分支，沿用之前那一版：中区宽高都 70%，1280×720 → 896×504 也正好 16:9。
# 竖屏比例直接套横屏会把中区压成 1280×248 的扁条，16:9 片源裁掉三成高度，所以单列一套。
FRAME_TOP_H = 0.15
FRAME_MEDIA_H = 0.70
FRAME_SIDE_H = 0.15

_WARNED_LANDSCAPE = [False]

# 上中下分栏的默认壳：本品牌固定包装，规格顶层 `frame` 不写这两项就用这里。
# 显式传 "" 可以留空（dict.get 的默认值只在 key 缺失时生效），换品牌只改这一处。
FRAME_BRAND = {
    "logo": "凤希AI",
    "logo_en": "FENGXI AI",
    "tagline": "自动创作论",
    "slogan": "一个人干一个团队的活",
}

# 🔴 字体只在这里定一次（2026-10-25 用户要求「艺术字风格全片一致」）。
# 参考片的分工是：**所有正文字用同一款方黑**（标题 / 花字 / 栏目名 / slogan），
# **只有品牌 logo 用书法体**。三处各用一套字（标题得意黑 + 下半雅黑）会立刻看出「不是一套东西」。
# Windows 自带 SimHei（方正黑体，横平竖直、字面撑满）与 STXingkai（华文行楷，当书法 logo 用），
# Chromium 按族名直接引用即可，不必 @font-face 内嵌 —— 只有 web 字体才需要内嵌。
# 标题字要用**比黑体更厚**的一款：SimHei 笔画约占字高 13%，参考片那种超粗黑约 19%，
# 差一档就「形似而气不足」。这里内嵌 Noto Sans SC Bold 的 GB2312 子集（1.1MB woff2，
# base64 后 1.5MB，壳只渲一次，代价可接受），缺字自动回退 SimHei。
# 字体文件由 fontTools 子集化产出，运行时不依赖 fontTools。
TITLE_FONT = '"Smiley Sans","Microsoft YaHei",sans-serif'
LOGO_FONT = '"Microsoft YaHei","PingFang SC",sans-serif'

FRAME_CSS = """
.fr-all{position:fixed;inset:0;background:#000}
.fr-top{position:absolute;left:0;right:0;top:0;height:calc(var(--h)*var(--fr-top));background-color:rgba(11,11,12,.78);
  background-image:radial-gradient(120% 80% at 10% 0%,rgba(124,88,40,.30) 0%,rgba(80,56,24,.10) 34%,transparent 62%),
    repeating-linear-gradient(45deg,rgba(255,255,255,.026) 0 1px,transparent 1px calc(12px*var(--s)));
  display:flex;flex-direction:column;justify-content:center;align-items:center;
  padding:0 calc(44px*var(--s))}
/* 标题：两层叠加 —— 底层画粗黑边，上层画「字芯 + 同色外扩」。
   🔴 一层 CSS 只能给一种描边，而参考片是「厚字芯被一圈黑边包住」：
   字芯先按同色再厚 1.5%，黑边才有 7% 的可见宽度，笔画看着比字体本身粗一档。
   用 em 给描边宽度，字号一变（横屏 --fr-k 缩放）跟着走。 */
.fr-t1,.fr-t2{font-family:%TITLEFONT%;font-weight:700;display:grid;justify-items:center;
  --t-bold:0.052em;--t-edge:0.26em;
  font-size:calc(92px*var(--s)*var(--fr-k));line-height:1.1;text-align:center;
  letter-spacing:calc(2px*var(--s))}
/* 两层用 grid 叠在同一格：absolute 定位会因为行高与基线差出几像素，
   黑边就偏成「重影」，只有 grid 才能保证两层严丝合缝。 */
.fr-t1 .kw-o,.fr-t2 .kw-o{grid-area:1/1;color:#171717;
  -webkit-text-stroke:var(--t-edge) #171717;paint-order:stroke fill}
.fr-t1 .kw-i,.fr-t2 .kw-i{grid-area:1/1;
  -webkit-text-stroke:var(--t-bold) currentColor;paint-order:stroke fill}
.fr-t1 .kw-i{color:#F7F5F2;text-shadow:0 0.05em 0 rgba(0,0,0,.55)}
.fr-t2{margin-top:calc(6px*var(--s))}
.fr-t2 .kw-i{color:#FFD84D;
  text-shadow:0 0.065em 0 #8E1B12,0 0.10em 0.14em rgba(0,0,0,.45)}
.fr-media{position:absolute;left:calc(var(--w)*var(--fr-side));right:calc(var(--w)*var(--fr-side));
  top:calc(var(--h)*var(--fr-top));height:calc(var(--h)*var(--fr-media));
  box-shadow:0 0 0 var(--fr-edge) rgba(217,180,90,.42);
  overflow:hidden;background:#000}
.fr-media img{width:100%;height:100%;object-fit:cover;display:block}
.fr-media.contain img{object-fit:contain;background:#0B0B0C}
.fr-ov{position:absolute;left:calc(36px*var(--s));right:calc(36px*var(--s));bottom:calc(112px*var(--s));
  text-align:center;font-family:%TITLEFONT%;
  font-size:calc(76px*var(--s)*var(--fr-k));font-weight:700;line-height:1.12;color:#fff;
  -webkit-text-stroke:calc(8px*var(--s)) #141414;paint-order:stroke fill;
  text-shadow:0 calc(6px*var(--s)) 0 rgba(0,0,0,.5)}
.fr-ov.mid{top:50%;bottom:auto;transform:translateY(-50%)}
.fr-ov.top{top:calc(26px*var(--s));bottom:auto}
.fr-bot{position:absolute;left:0;right:0;bottom:0;height:calc(var(--h)*var(--fr-bot));background-color:rgba(11,11,12,.78);
  background-image:repeating-linear-gradient(-45deg,rgba(255,255,255,.026) 0 1px,transparent 1px calc(12px*var(--s)));
  display:flex;align-items:center;justify-content:center;gap:calc(22px*var(--s));
  padding:0 calc(40px*var(--s))}
.fr-logo{display:flex;flex-direction:column;align-items:center}
.fr-logo-cn{font-family:%LOGOFONT%;font-size:calc(78px*var(--s)*var(--fr-kb));font-weight:700;letter-spacing:calc(2px*var(--s));
  color:#D9B45A;line-height:1;text-align:center;text-shadow:0 0 calc(24px*var(--s)) rgba(217,180,90,.3)}
.fr-logo-en{font-size:var(--fr-en);letter-spacing:calc(3px*var(--s)*var(--fr-kb));color:#8A7333;
  margin-top:calc(12px*var(--s)*var(--fr-kb));font-weight:600}
.fr-logoimg{height:calc(104px*var(--s)*var(--fr-kb));display:block}
.fr-div{width:1px;height:calc(88px*var(--s)*var(--fr-kb));background:#6B5A2A;opacity:.75}
.fr-tag{writing-mode:var(--fr-tagmode);text-orientation:upright;font-family:%TITLEFONT%;
  font-size:calc(28px*var(--s)*var(--fr-kb));letter-spacing:calc(7px*var(--s));color:#D9B45A;font-weight:700}
.fr-slogan{font-family:%TITLEFONT%;font-size:calc(26px*var(--s)*var(--fr-kb));letter-spacing:calc(2px*var(--s));color:#C9B78A;
  font-weight:700;margin-left:calc(4px*var(--s));max-width:calc(380px*var(--s));white-space:nowrap;
  line-height:1.5;text-align:left}
"""

FRAME_CSS = FRAME_CSS.replace("%TITLEFONT%", TITLE_FONT).replace("%LOGOFONT%", LOGO_FONT)

CFG = {"canvas_css": None, "sanitize": None}


def configure(canvas_css=None, sanitize=None):
    """注入调用方的画布样式与字段净化。

    canvas_css(css, w, h) —— 把 __W__/__H__ 换成实际像素并注入缩放变量。幻灯侧传
    自己的版本（带字体 @font-face + 主题 CSS）；不传则用本文件的极简实现。
    sanitize(fields) —— 字段净化（框内文字去装饰），可缺省。
    """
    if canvas_css:
        CFG["canvas_css"] = canvas_css
    if sanitize:
        CFG["sanitize"] = sanitize


def _default_canvas_css(css, w, h):
    """极简画布样式：只做尺寸替换与 --s 缩放变量，不带字体与主题。"""
    w, h = int(w), int(h)
    s = min(w / 768.0, h / 1280.0)
    inject = ":root{--s:%.4f;--w:%dpx;--h:%dpx}\n" % (s, w, h)
    return inject + css.replace("__W__", str(w)).replace("__H__", str(h))


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


def _media_h(w, h):
    """中区像素高：全宽 16:9，**并向偶数对齐**。

    🔴 1080 宽算出来是 607.5，四舍五入成 607 —— 奇数高会让 libx264 直接拒收
    （height not divisible by 2），整条中区片渲染失败。偶数化放在唯一出处这里，
    `frame_ratios` 与 `frame_box` 都从它取，CSS 与像素框才不会各算一套。
    """
    mh = int(round(int(w) / FRAME_MEDIA_RATIO))
    mh = min(mh, int(int(h) * 0.5))           # 极端窄高画布的兜底
    return mh - (mh % 2)


def frame_ratios(w, h):
    """三段的高度占比 (top, media)。**比例只在这里算一次**。

    竖屏：中区 = 全宽 16:9（1080×1920 → 1080×608），剩余高度按 FRAME_SPLIT 分给上下带。
    横屏：例外分支，中区宽高各 70%（1280×720 → 896×504，同样是 16:9）。
    """
    w, h = int(w), int(h)
    if w > h:
        return FRAME_TOP_H, FRAME_MEDIA_H
    mh = _media_h(w, h)
    return round((h - mh) * FRAME_SPLIT / h, 4), round(mh / h, 4)


def frame_box(w, h):
    """中区那一格的像素框 (x, y, w, h)。方向自适应，槽位坐标只在这里算一次。

    竖屏占满全宽、高 = 宽 × 9/16（16:9 素材零裁切）；横屏左右各内缩 15%。
    """
    w, h = int(w), int(h)
    if w > h:
        x = int(round(w * FRAME_SIDE_H))
        return (x, int(round(h * FRAME_TOP_H)), w - 2 * x, int(round(h * FRAME_MEDIA_H)))
    mh = _media_h(w, h)
    return (0, int(round((h - mh) * FRAME_SPLIT)), w, mh)


def _dir_css(w, h):
    """按画布方向注入三段比例与字号系数（壳的竖屏/横屏两套取值只在这里出现一次）。

    竖屏中区恒 16:9 全宽（9:16 画布下 1080×608），横屏例外分支取 70%/70%。
    🔴 下带高度必须单独给 `--fr-bot`（= 1 − 上下两带之外剩下的），
    早先下带直接复用 `--fr-top`，上下各 36% 时两者相加超 100%，
    后画的下带会把中区底部压掉一截（1080×1920 下压掉 71px），中区因此变成 2:1。
    """
    top, media = frame_ratios(w, h)
    bot = round(1.0 - top - media, 4)
    if w > h:
        return (":root{--fr-top:%.4f;--fr-media:%.4f;--fr-bot:%.4f;--fr-side:%.4f;"
                "--fr-k:.82;--fr-kb:1.1;--fr-en:calc(20px*var(--s));"
                "--fr-tagmode:horizontal-tb;--fr-edge:calc(1px*var(--s))}"
                % (top, media, bot, FRAME_SIDE_H))
    return (":root{--fr-top:%.4f;--fr-media:%.4f;--fr-bot:%.4f;--fr-side:0;"
            "--fr-k:1;--fr-kb:1;--fr-en:calc(13px*var(--s));"
            "--fr-tagmode:vertical-rl;--fr-edge:0px}"
            % (top, media, bot))


def frame_page(inner, w=None, h=None, transparent=False):
    """上中下分栏专用页面壳：纯黑底、无内边距（三段式自己定位）。

    不给画布尺寸就按规范画布 9:16（1080×1920）出。
    """
    w = int(w or FRAME_PORTRAIT[0])
    h = int(h or FRAME_PORTRAIT[1])
    _note_landscape(w, h)
    base = ("*{box-sizing:border-box;margin:0;padding:0}"
            "html,body{width:__W__px;height:__H__px;overflow:hidden;background:#000}")
    if transparent:
        base += "html,body{background:transparent!important}"
    css = (CFG["canvas_css"] or _default_canvas_css)(
        base + _dir_css(w, h) + FRAME_CSS, w, h)

    return ('<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            '<style>%s</style></head><body>%s</body></html>' % (css, inner))


def _title_html(cls, text):
    """一行标题 = 黑边层 + 字芯层（两层同一句，叠出「厚字 + 细黑边」，见 FRAME_CSS 注释）。"""
    return ('<div class="%s"><span class="kw-o">%s</span>'
            '<span class="kw-i">%s</span></div>' % (cls, text, text))


def _frame_top(fields):
    """顶部标题带：t1 白字 / t2 黄字，两行紧排。"""
    out = ""
    if fields.get("title1"):
        out += _title_html("fr-t1", fields["title1"])
    if fields.get("title2"):
        out += _title_html("fr-t2", fields["title2"])
    return ('<div class="fr-top">%s</div>' % out) if out else ""


def _frame_bottom(fields):
    """底部品牌带：logo（文字或图片）+ 竖排栏目名 + 一句 slogan。

    logo / logo_en / tagline / slogan 缺省时取 `FRAME_BRAND`（本品牌固定壳），
    显式传 "" 可留空。换品牌 / 换口号只改 FRAME_BRAND 一处，不必每集重填。
    顶部 title1/title2 是选题标题，随片变，没有默认值。
    """
    logo = ""
    if fields.get("logo_img"):
        src = _media_src(fields["logo_img"])
        if src:
            logo = '<img class="fr-logoimg" src="%s">' % src
    cn = fields.get("logo", FRAME_BRAND["logo"])
    en = fields.get("logo_en", FRAME_BRAND["logo_en"])
    if not logo and cn:
        en_html = ('<div class="fr-logo-en">%s</div>' % en) if en else ""
        # 品牌名横排一行（规范：书法 logo 在左），下方拉丁小字；
        # 下栏宽度靠字号档控制（logo 90px + slogan nowrap 保证一行）。
        logo = '<div class="fr-logo"><div class="fr-logo-cn">%s</div>%s</div>' % (cn, en_html)
    parts = [logo] if logo else []
    tag = fields.get("tagline", FRAME_BRAND["tagline"])
    sl = fields.get("slogan", FRAME_BRAND["slogan"])
    if tag:
        if parts:
            parts.append('<div class="fr-div"></div>')
        parts.append('<div class="fr-tag">%s</div>' % tag)
    if sl:
        parts.append('<div class="fr-slogan">%s</div>' % sl)
    return ('<div class="fr-bot">%s</div>' % "".join(parts)) if parts else ""


def _frame_overlay(fields):
    pos = str(fields.get("overlay_pos") or "bottom").lower()
    cls = {"mid": " mid", "center": " mid", "中": " mid",
           "top": " top", "上": " top"}.get(pos, "")
    return '<div class="fr-ov%s">%s</div>' % (cls, fields["overlay"])


def _is_page_slot(fields):
    """这一段的中区是不是「网页槽」（文字卡 / 长图滚动 / 真网页）。

    网页里本来就是字，再压一层花字必然打架 —— 所以网页段默认不出花字，
    真要压就显式写 "overlay_show": true，并且自己挑 overlay_pos。
    """
    m = fields.get("media")
    items = m if isinstance(m, (list, tuple)) else ([m] if m else [])
    for it in items:
        if isinstance(it, dict) and (it.get("page") or it.get("url") or it.get("scroll")):
            return True
    return False


def _frame_ov_html(fields):
    """花字：有文案、且（不是网页槽 或 显式要求）才画。"""
    if not fields.get("overlay"):
        return ""
    if _is_page_slot(fields) and not fields.get("overlay_show"):
        return ""
    return _frame_overlay(fields)


def _clean(fields):
    fn = CFG["sanitize"]
    if fn:
        fn(fields)
    return fields


def _note_landscape(w, h):
    """横屏是例外：上中下分栏的规范画布是 9:16，横屏只在「素材本身是横屏」时借用。

    不拦，只提醒一次 —— 每渲染一段刷一遍会淹掉真日志。
    """
    if int(w) > int(h) and not _WARNED_LANDSCAPE[0]:
        _WARNED_LANDSCAPE[0] = True
        print("  [上中下分栏] 横屏画布 %d×%d：本版式规范为竖屏 9:16（%d×%d），"
              "横屏仅当素材本身是横屏片、要整条嵌进中区时才用。"
              % (w, h, FRAME_PORTRAIT[0], FRAME_PORTRAIT[1]), flush=True)


def frame_slide(fields, w=None, h=None):
    """版式「上中下分栏」：固定节目壳 + 中区换素材。

    竖屏 9:16 是这个版式的规范用法；横屏是例外分支（见 `_note_landscape`）。
    上下两条带全片复用（栏目包装），中线按 3~5 秒换一张素材。

    fields:
      title1 / title2   顶部两行大字（白 / 黄，粗描边）。全片每段都传同样的值
      logo              底部主 logo 文字（金色）；给了 logo_img 则优先用图片
      logo_en           logo 下方拉丁小字（可选）
      logo_img          logo 图片路径（想用书法字/正式 logo 时传这个）
      tagline           竖排栏目名（缺省取 FRAME_BRAND「自动创作论」）
      slogan            底部右侧一句（缺省取 FRAME_BRAND「一个人干一个团队的活」）
      media             中区素材：单张路径，或一叠 list（段内轮播）。实际渲染走 frame_mask，
                        素材由素材槽垫进来（图片/视频/多图转场都在那里做）；
                        list 里也可以放 {"page": {...}} 网页槽（文字卡/长图滚动/真网页）
      media_fit         "cover"（默认）/ "blur"（竖版海报：原图居中+虚化铺底）/ "contain"（两侧黑边）
      overlay           中区花字（如 "满眼都是情侣"）。中区是网页槽时默认不画（页面里本来
                        就是字），要压请显式 overlay_show: true
      overlay_pos       bottom（默认）/ mid / top
    壳参数（title/logo/tagline/slogan）建议写在规格顶层 `frame` 里，
    管线会自动合并进每段 —— 一段都不用重复写。
    """
    _clean(fields)
    top = _frame_top(fields)
    _m = fields.get("media")
    if isinstance(_m, (list, tuple)):
        _m = ""                      # 一叠素材只走遮罩路线，页面里不嵌图
    is_video = str(_m or "").lower().endswith((".mp4", ".mov", ".m4v", ".webm"))
    fit = " contain" if str(fields.get("media_fit") or "").lower() in ("contain", "完整", "不裁切") else ""
    img = "" if is_video else _media_src(_m)
    ov = _frame_ov_html(fields)
    media = ('<div class="fr-media%s">%s%s</div>'
             % (fit, ('<img src="%s">' % img) if img else "", ov))
    return frame_page('<div class="fr-all">%s%s%s</div>' % (top, media, _frame_bottom(fields)),
                      w=w, h=h)


def frame_mask(fields, w=None, h=None):
    """上中下分栏的「壳遮罩」：上下带 + 花字照画，媒体区留透明。

    视频段 / 推近段用它出带 alpha 的 PNG，合成时垫在素材视频上面 ——
    遮罩不动、素材动，就得到「镜头在框里放映」的效果。
    """
    _clean(fields)
    ov = _frame_ov_html(fields)
    media = '<div class="fr-media" style="background:transparent">%s</div>' % ov
    return frame_page('<div class="fr-all" style="background:transparent">%s%s%s</div>'
                      % (_frame_top(fields), media, _frame_bottom(fields)),
                      w=w, h=h, transparent=True)
