# -*- coding: utf-8 -*-
"""左中右分栏（`trio`）的壳：中间一条素材槽 + 左右两栏文字。

和 `ml_frame`（上中下分栏）是**同一族**版式 —— 壳元素一样（两行标题 / 品牌落款 /
中区花字），差别只在**槽的形状与位置**，而且槽形**按画布方向自己变**：

    画布竖屏（h > w）：槽 = 满高竖条，**上下贴边零边距**，左右让出两栏
                       → 竖屏素材用这套，视频上下百分之百显示
    画布横屏（w > h）：槽 = **16:9 横带、水平垂直居中**，左右让出两栏
                       → 横屏素材用这套，上下也留出空间（可放花字/留白）

🔴 竖屏那条的定义就是「视频上下百分之百显示」：中槽高度恒等于画布高度，
左右宽度由 `media_w` 调（0.30~0.92），素材按**高度对齐**填进槽里（`fit="height"`），
水平居中；比槽宽的左右对称裁掉，比槽窄的补底色。所以槽宽怎么调，上下都不会出边距。

🔴 横屏那条反过来：槽恒为 16:9（横屏素材的原生比例），素材按 `fit="cover"` 铺满 ——
槽本身已经是 16:9，16:9 素材进去零裁切，4:3 素材左右各裁一点。

边界与 ml_frame 一致：不 import 任何 fxai 技能，画布样式与字段净化由调用方 `configure()` 注入。

调用方典型用法（幻灯侧）::

    import ml_trio
    ml_trio.configure(canvas_css=slides._canvas_css, sanitize=slides._sanitize_pills)
    ml_trio.trio_box(w, h, media_w)      # 中槽像素框（竖屏 (x,0,mw,h) / 横屏 (x,y,mw,mh)）
    ml_trio.trio_mask(fields)            # 带 alpha 的壳遮罩，素材垫在下面
    ml_trio.trio_slide(fields)           # 单图直接出整页
"""
import pathlib

# 默认画布（只在调用方一个尺寸都不给时兜底）
TRIO_PORTRAIT = (1080, 1920)

# 中槽宽占画布宽的比例。默认值按「画布方向 × 槽里素材方向」四档（见 default_media_w）：
# 竖画布 + 竖素材 0.62：1080 画布下中槽 670、两翼各 205；768 画布下中槽 476、两翼各 146
#   —— 竖排标题两列刚好放得下。
# 横画布 + 横素材 0.70：与上中下分栏的横屏中区（宽高各 70%）取同一个数，两族版式观感统一；
#   1366×768 下中槽 956×538（正好 16:9）、两翼各 205。
# 横画布 + 竖素材：不走这两个常量，按 `画布高×9/16 ÷ 画布宽` 算（1366×768 → 0.3163 → 槽 432×768）
#   —— 槽宽正好等于 9:16 素材在 768 高下的原生宽度，零露边零裁切，两翼各 466 全给文字。
TRIO_MEDIA_W = 0.62
TRIO_MEDIA_W_LAND = 0.70
TRIO_MIN_MEDIA_W = 0.30
TRIO_MAX_MEDIA_W = 0.92

# 两翼内容全部竖排（窄栏横排会把中文挤成一列单字，竖排反而更像落款）。
# 字号不写死，一律按「栏宽 × 系数」算 —— 槽宽一调，字号自己跟着缩，不用重调参数。
# 🔴 上下都要 clamp：槽调宽（栏变窄）时不封顶就会两列标题粘在一起；
# 槽调窄（栏变宽）时不封底会撑破画布。
# 🔴 上限一律按**画布高度**算（`h*0.13` 等），勿写 `96px*var(--s)` —— `--s = min(w/768, h/1280)`
#    在横屏画布上只有 0.6，横屏放竖素材时栏宽 466、理想字号 158px 会被 57.6px 的上限压死，
#    左右两栏字小得像脚注。按高度算：横屏 768×0.13 = 100px、竖屏 1280×0.13 = 166px，
#    竖屏本来就靠栏宽定（146×0.34 = 50px）碰不到上限，行为不变。
_TITLE_K = 0.34      # 标题字号 / 栏宽（两列标题 + 间距刚好塞满一栏）
_LOGO_K = 0.30       # 品牌 logo 字号 / 栏宽
_TAG_K = 0.135       # 栏目名
_SLOGAN_K = 0.115    # 口号

# 复用上中下分栏的壳文案与工具（同族版式，标题/品牌/花字只该有一套写法）
try:
    from ml_frame import (_title_html, _is_page_slot, _media_src, _clean,
                          TITLE_FONT, LOGO_FONT, FRAME_BRAND)
except Exception:                                   # 单独拷出来跑时给个明确错误
    raise ImportError("ml_trio 依赖同目录的 ml_frame.py（fxai-mixed-layout 公共件）")


# ---------- 素材方向探测 ----------
# 同一个文件在一段渲染里会被问好几次（页面 / 遮罩 / 合成端），探一次就记住。
_WH_CACHE = {}


def media_wh(p):
    """素材宽高 `(w, h)`，读不到 `(0, 0)`。探针在 ml_slots，判据只有那一处。"""
    p = str(p or "")
    if not p:
        return (0, 0)
    if p not in _WH_CACHE:
        try:
            from ml_slots import wh as _wh
            _WH_CACHE[p] = _wh(p)
        except Exception:
            _WH_CACHE[p] = (0, 0)
    return _WH_CACHE[p]


def _first_media_path(fields):
    """段里第一个素材的路径（单张 / list 第一项 / dict 取 path|src）。"""
    m = fields.get("media")
    if isinstance(m, (list, tuple)):
        m = m[0] if m else None
    if isinstance(m, dict):
        m = m.get("path") or m.get("src")
    return str(m) if m else None


def portrait_media_of(fields):
    """槽里素材是不是**竖构图**：True 竖 / False 横 / None 读不到。

    槽形只认这个判断（不是画布方向）—— 横屏画布里放竖屏视频，槽仍是满高竖条。
    显式字段 `media_portrait` 优先（布尔，或写 "竖"/"横"/"portrait"/"landscape"）。
    """
    v = fields.get("media_portrait")
    if v is not None:
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("竖", "竖屏", "portrait", "vertical", "tall", "1", "true", "yes"):
            return True
        if s in ("横", "横屏", "landscape", "horizontal", "wide", "0", "false", "no"):
            return False
    p = _first_media_path(fields)
    if not p:
        return None
    w, h = media_wh(p)
    if not w or not h:
        return None
    return h > w

TRIO_CSS = """
.tr-all{position:fixed;inset:0;background:rgba(8,10,14,.42)}
/* 中槽：位置/尺寸全部由 --tr-side / --tr-mw / --tr-mh / --tr-my 四个变量决定（trio_box 算出）。
   竖素材：--tr-my=0、--tr-mh=1 → top:0 + height:100%，上下贴边零边距（画布横竖都一样）。
   横素材：--tr-my≈0.15、--tr-mh≈0.70 → 16:9 横带水平垂直居中，上下留出空间。 */
.tr-media{position:absolute;left:calc(var(--w)*var(--tr-side));
  top:calc(var(--h)*var(--tr-my));
  width:calc(var(--w)*var(--tr-mw));height:calc(var(--h)*var(--tr-mh));overflow:hidden;background:#000;
  border-left:calc(1px*var(--s)) solid rgba(217,180,90,.30);
  border-right:calc(1px*var(--s)) solid rgba(217,180,90,.30)}
/* 竖素材槽（满高）：素材按高度撑满、宽度按原比例，左右对称溢出裁掉（fit=height 的 CSS 对应）。
   素材比槽窄时两侧露底色 —— 那是 media_w 调超过素材原生宽度了，不是版式问题。 */
.tr-media img,.tr-media video{position:absolute;left:50%;top:0;height:100%;width:auto;
  transform:translateX(-50%);display:block;border:0}
/* 横素材槽（16:9）：素材铺满整槽。槽本身就是横素材的原生比例，所以等于零裁切。 */
.tr-media.cover img,.tr-media.cover video{left:0;width:100%;height:100%;transform:none;
  object-fit:cover}
/* 左栏：两行标题竖排。row-reverse 让 title1 在右列、title2 在左列（竖排从右往左读）。 */
.tr-left{position:absolute;left:0;top:0;width:calc(var(--w)*var(--tr-side));height:var(--h);
  display:flex;flex-direction:row-reverse;justify-content:center;align-items:center;
  gap:clamp(calc(6px*var(--s)),calc(var(--w)*var(--tr-side)*0.08),calc(20px*var(--s)));
  padding:calc(56px*var(--s)) calc(var(--w)*var(--tr-side)*0.05);overflow:hidden}
.tr-t1,.tr-t2{font-family:%TITLEFONT%;font-weight:700;
  writing-mode:vertical-rl;text-orientation:upright;
  display:grid;justify-items:center;
  --t-bold:0.05em;--t-edge:0.24em;
  font-size:clamp(calc(22px*var(--s)),calc(var(--w)*var(--tr-side)*%TITLEK%),calc(var(--h)*0.10));
  line-height:1.06;letter-spacing:calc(var(--w)*var(--tr-side)*0.02);text-align:center}
/* 两层叠字（黑边层 + 字芯层）与上中下分栏同法：grid 同一格，保证严丝合缝 */
.tr-t1 .kw-o,.tr-t2 .kw-o{grid-area:1/1;color:#171717;
  -webkit-text-stroke:var(--t-edge) #171717;paint-order:stroke fill}
.tr-t1 .kw-i,.tr-t2 .kw-i{grid-area:1/1;
  -webkit-text-stroke:var(--t-bold) currentColor;paint-order:stroke fill}
.tr-t1 .kw-i{color:#F7F5F2;text-shadow:0 0.05em 0 rgba(0,0,0,.55)}
.tr-t2 .kw-i{color:#FFD84D;
  text-shadow:0 0.065em 0 #8E1B12,0 0.10em 0.14em rgba(0,0,0,.45)}
/* 右栏：品牌落款竖排，从右到左 = logo / 栏目名 / 口号 */
.tr-right{position:absolute;right:0;top:0;width:calc(var(--w)*var(--tr-side));height:var(--h);
  display:flex;flex-direction:row-reverse;justify-content:center;align-items:center;
  gap:clamp(calc(5px*var(--s)),calc(var(--w)*var(--tr-side)*0.07),calc(18px*var(--s)));
  padding:calc(56px*var(--s)) calc(var(--w)*var(--tr-side)*0.04);overflow:hidden}
.tr-logo-cn{writing-mode:vertical-rl;text-orientation:upright;
  font-family:%LOGOFONT%;font-weight:700;color:#D9B45A;line-height:1;
  font-size:clamp(calc(18px*var(--s)),calc(var(--w)*var(--tr-side)*%LOGOK%),calc(var(--h)*0.075));
  letter-spacing:calc(var(--w)*var(--tr-side)*0.02);
  text-shadow:0 0 calc(24px*var(--s)) rgba(217,180,90,.3)}
.tr-logoimg{height:calc(var(--w)*var(--tr-side)*0.30);display:block}
.tr-tag{writing-mode:vertical-rl;text-orientation:upright;font-family:%TITLEFONT%;
  font-weight:700;color:#D9B45A;
  font-size:clamp(calc(11px*var(--s)),calc(var(--w)*var(--tr-side)*%TAGK%),calc(var(--h)*0.034));
  letter-spacing:calc(var(--w)*var(--tr-side)*0.05)}
.tr-slogan{writing-mode:vertical-rl;text-orientation:upright;font-family:%TITLEFONT%;
  font-weight:700;color:#C9B78A;
  font-size:clamp(calc(9px*var(--s)),calc(var(--w)*var(--tr-side)*%SLOGANK%),calc(var(--h)*0.028));
  letter-spacing:calc(var(--w)*var(--tr-side)*0.03);line-height:1.5}
/* 中区花字：相对中槽定位（不是相对整页），槽宽一调字跟着居中。
   🔴 bottom / font-size 都用 Python 算好的 px 变量（--tr-ovb / --tr-ovs），
   不在 CSS 里写嵌套 calc+max/min —— 实测 Chrome 对 `calc(… + max(calc(…),calc(…)))`
   会整条丢弃，花字掉回默认位置跟字幕叠在一起。 */
.tr-ov{position:absolute;left:calc(22px*var(--s));right:calc(22px*var(--s));
  bottom:var(--tr-ovb);
  text-align:center;font-family:%TITLEFONT%;
  font-size:var(--tr-ovs);
  font-weight:700;line-height:1.14;color:#fff;
  -webkit-text-stroke:0.11em #141414;paint-order:stroke fill;
  text-shadow:0 0.09em 0 rgba(0,0,0,.5)}
.tr-ov.mid{top:50%;bottom:auto;transform:translateY(-50%)}
.tr-ov.top{top:var(--tr-ovt);bottom:auto}
"""

TRIO_CSS = (TRIO_CSS
            .replace("%TITLEFONT%", TITLE_FONT)
            .replace("%LOGOFONT%", LOGO_FONT)
            .replace("%TITLEK%", "%.4f" % _TITLE_K)
            .replace("%LOGOK%", "%.4f" % _LOGO_K)
            .replace("%TAGK%", "%.4f" % _TAG_K)
            .replace("%SLOGANK%", "%.4f" % _SLOGAN_K))

CFG = {"canvas_css": None, "sanitize": None}


def configure(canvas_css=None, sanitize=None):
    """注入调用方的画布样式与字段净化（与 ml_frame 同签名）。"""
    if canvas_css:
        CFG["canvas_css"] = canvas_css
    if sanitize:
        CFG["sanitize"] = sanitize


def _default_canvas_css(css, w, h):
    """极简画布样式：只做尺寸替换与 --s 缩放变量。"""
    w, h = int(w), int(h)
    s = min(w / 768.0, h / 1280.0)
    inject = ":root{--s:%.4f;--w:%dpx;--h:%dpx}\n" % (s, w, h)
    return inject + css.replace("__W__", str(w)).replace("__H__", str(h))


def media_w_of(v):
    """把 media_w 归一成 0~1 的浮点（认 `0.62` / `"62%"` / `62`）。

    给不出数（None / 空 / 乱写）返回 None —— 由 `default_media_w()` 按方向补默认值，
    因为默认值跟「画布方向 × 素材方向」两个维度都有关，须在这里按维度分别给出。
    """
    try:
        x = float(str(v).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    if x > 1.0:
        x = x / 100.0
    return max(TRIO_MIN_MEDIA_W, min(TRIO_MAX_MEDIA_W, x))


def default_media_w(w, h, portrait_media=None):
    """没给 `media_w` 时的默认槽宽占比，按「画布方向 × 槽里素材方向」定。

    | 画布 | 槽里素材 | 槽形 | 默认槽宽 |
    |---|---|---|---|
    | 竖屏 | 竖 | 满高竖条 | 0.62（两翼各 19%，竖排两列标题放得下）|
    | 竖屏 | 横 | 16:9 横带 | 0.62（居中，上下留白）|
    | 横屏 | 横 | 16:9 横带 | 0.70（与上中下分栏横屏中区同宽）|
    | **横屏** | **竖** | **满高竖条** | **按素材原生宽算** `h×9/16` |

    最后那行是关键：横屏画布里放竖屏视频，槽要**满高**（上下百分之百显示），
    宽度让给素材原生比例（1366×768 下 = 432 宽、占 31.6%），剩下左右各 467 全给文字。
    """
    w, h = int(w), int(h)
    land = w > h
    if portrait_media is None:
        portrait_media = not land          # 没信息时按画布方向兜底
    if portrait_media and land:
        return max(TRIO_MIN_MEDIA_W, min(TRIO_MAX_MEDIA_W, (h * 9 / 16.0) / w))
    return TRIO_MEDIA_W_LAND if land else TRIO_MEDIA_W


def is_landscape(w, h):
    """画布是否横屏（宽 > 高）。"""
    return int(w) > int(h)


def trio_box(w, h, media_w=None, portrait_media=None):
    """中槽像素框。**槽位坐标只在这里算一次。**

    `portrait_media` 是**槽里素材**的方向（不是画布方向），它决定槽形：

        True（竖素材）  → `(x, 0, mw, h)`：满高竖条，y 恒 0、h 恒等于画布高（「上下零边距」）
        False（横素材） → `(x, y, mw, mh)`：**恒 16:9 横带**，水平垂直都居中
        None            → 按画布方向兜底（竖屏当竖素材、横屏当横素材），即老行为

    🔴 **槽形跟素材走，不是跟画布走**：横屏画布里放竖屏视频，出来的仍是满高竖条 ——
    否则竖素材被塞进 16:9 横带会被 cover 裁掉上下大半。

    宽高都向偶数对齐：奇数会让 libx264 直接拒收（not divisible by 2）。
    """
    w, h = int(w), int(h)
    w -= w % 2
    h -= h % 2
    land = w > h
    if portrait_media is None:
        portrait_media = not land
    mwr = media_w_of(media_w)
    if mwr is None:
        mwr = default_media_w(w, h, portrait_media)
    mw = int(round(w * mwr))
    mw -= mw % 2
    mw = max(2, min(mw, w))
    x = (w - mw) // 2
    x -= x % 2
    if portrait_media:
        return (x, 0, mw, h)              # 满高竖条（竖屏画布、横屏画布放竖素材，都是这条）
    # 16:9 横带：按宽定高；槽宽过大使按宽定的高超出画布时，反过来按高度收宽度（比例不破）
    mh = int(round(mw * 9 / 16.0))
    mh -= mh % 2
    if mh >= h:
        mh = h - 2
        mw = int(round(mh * 16 / 9.0))
        mw -= mw % 2
        mw = max(2, min(mw, w))
        x = (w - mw) // 2
        x -= x % 2
    y = (h - mh) // 2
    y -= y % 2
    return (x, y, mw, mh)


def trio_ratios(w, h, media_w=None, portrait_media=None):
    """`(side, mw, mh, my)` 四个 CSS 变量值，与 trio_box 同源（不另算一遍）。"""
    w, h = int(w), int(h)
    bx, by, bw, bh = trio_box(w, h, media_w, portrait_media)
    return (round((w - bw) / 2.0 / w, 4), round(bw / float(w), 4),
            round(bh / float(h), 4), round(by / float(h), 4))


def _dir_css(w, h, media_w=None, portrait_media=None):
    """把几何量一次性算成 CSS 变量（Python 算好，CSS 里只 var 取值）。

    除了两翼/中槽的四个比例，还预先算两个花字量：

    `--tr-ovb` 花字距画布底 = **中槽下边距** + max(96px×s, 画布高×13.5%)
        —— 只按 `96px×s` 算的话，满高槽上它正好和字幕的 sub_bottom 落在同一行，
           花字会被字幕压住（竖屏 96 vs 96、横屏 57.6 vs 58）。
    `--tr-ovt` 花字距画布顶 = **中槽上边距** + max(48px×s, 画布高×6%)
        —— overlay_pos=top 时用：标题卡放视频上部、避开底部字幕区。
    `--tr-ovs` 花字字号 = min(槽宽×13.5%, 槽高×14%)
        —— 只按槽宽算，横屏 16:9 槽会给出 129px，把整条槽压满。

    🔴 这两个值**必须在这里算**，CSS 里勿写 `calc(… + max(calc(…),calc(…)))`：
    实测 Chrome 会整条丢弃，花字掉回默认位置，和字幕叠在一起。
    """
    w, h = int(w), int(h)
    side, mw, mh, my = trio_ratios(w, h, media_w, portrait_media)
    s = min(w / 768.0, h / 1280.0)
    pad_top = h * my                               # 中槽上边距
    pad_bottom = h * (1 - my - mh)                    # 中槽下边距
    ovb = int(round(pad_bottom + max(96 * s, h * 0.135)))
    ovt = int(round(pad_top + max(48 * s, h * 0.06)))
    ovs = int(round(min(w * mw * 0.11, h * mh * 0.115)))
    return (":root{--tr-side:%.4f;--tr-mw:%.4f;--tr-mh:%.4f;--tr-my:%.4f;"
            "--tr-ovb:%dpx;--tr-ovt:%dpx;--tr-ovs:%dpx}" % (side, mw, mh, my, ovb, ovt, ovs))


def trio_page(inner, w=None, h=None, transparent=False, media_w=None, portrait_media=None):
    """左中右分栏专用页面壳：纯黑底、无内边距（三段自己定位）。"""
    w = int(w or TRIO_PORTRAIT[0])
    h = int(h or TRIO_PORTRAIT[1])
    base = ("*{box-sizing:border-box;margin:0;padding:0}"
            "html,body{width:__W__px;height:__H__px;overflow:hidden;background:#000}")
    if transparent:
        base += "html,body{background:transparent!important}"
    css = (CFG["canvas_css"] or _default_canvas_css)(
        base + _dir_css(w, h, media_w, portrait_media) + TRIO_CSS, w, h)
    return ('<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            '<style>%s</style></head><body>%s</body></html>' % (css, inner))


def _trio_left(fields):
    """左栏：title1（白）/ title2（黄），竖排两列。"""
    out = ""
    if fields.get("title1"):
        out += _title_html("tr-t1", fields["title1"])
    if fields.get("title2"):
        out += _title_html("tr-t2", fields["title2"])
    return ('<div class="tr-left">%s</div>' % out) if out else ""


def _trio_right(fields):
    """右栏：品牌落款（竖排）。logo / logo_en / tagline / slogan 缺省取 FRAME_BRAND。"""
    logo = ""
    if fields.get("logo_img"):
        src = _media_src(fields["logo_img"])
        if src:
            logo = '<img class="tr-logoimg" src="%s">' % src
    cn = fields.get("logo", FRAME_BRAND["logo"])
    if not logo and cn:
        logo = '<div class="tr-logo-cn">%s</div>' % cn
    parts = [logo] if logo else []
    tag = fields.get("tagline", FRAME_BRAND["tagline"])
    sl = fields.get("slogan", FRAME_BRAND["slogan"])
    if tag:
        parts.append('<div class="tr-tag">%s</div>' % tag)
    if sl:
        parts.append('<div class="tr-slogan">%s</div>' % sl)
    return ('<div class="tr-right">%s</div>' % "".join(parts)) if parts else ""


def _ov_html(fields):
    """中区花字：有文案、且（不是网页槽 或 显式要求）才画。"""
    if not fields.get("overlay"):
        return ""
    if _is_page_slot(fields) and not fields.get("overlay_show"):
        return ""
    pos = str(fields.get("overlay_pos") or "bottom").lower()
    cls = {"mid": " mid", "center": " mid", "中": " mid",
           "top": " top", "上": " top"}.get(pos, "")
    return '<div class="tr-ov%s">%s</div>' % (cls, fields["overlay"])


def trio_slide(fields, w=None, h=None, media_w=None, portrait_media=None):
    """版式「左中右分栏」：中槽放素材、左右两栏放文字。槽形随**素材方向**变。

    fields（与上中下分栏同一套壳字段）:
      title1 / title2   左栏两行竖排大字（白 / 黄，粗描边）
      logo              右栏主 logo 文字（金色）；给了 logo_img 则优先用图片
      logo_en           兼容保留（竖排窄栏里不放拉丁小字，横排版式才用）
      logo_img          logo 图片路径
      tagline           右栏栏目名（缺省取 FRAME_BRAND「自动创作论」）
      slogan            右栏一句（缺省取 FRAME_BRAND「一个人干一个团队的活」）
      media             中槽素材：单张路径，或一叠 list（段内轮播）。实际渲染走
                        trio_mask，素材由素材槽垫进来（图片/视频/多图转场都在那里做）
      media_portrait    槽里素材方向：True 竖 / False 横 / 不写则读素材本身。
                        **竖素材 → 满高竖条（上下百分之百显示）；横素材 → 16:9 横带居中**
      media_w           中槽宽占比 0.30~0.92（认 0.62 / "62%" / 62）。不写按方向取默认：
                        竖屏画布+竖素材 0.62 / 横屏画布+横素材 0.70 /
                        **横屏画布+竖素材 按素材原生宽（h×9/16）** / 竖屏画布+横素材 0.62
      media_fit         "height"（竖素材默认：满高居中、上下零边距）/ "cover"（横素材默认：铺满）
                        / "blur" / "contain"
      overlay / overlay_pos   中槽花字与位置（bottom 默认 / mid / top）
    """
    _clean(fields)
    mw = media_w if media_w is not None else fields.get("media_w")
    _m = fields.get("media")
    if isinstance(_m, (list, tuple)):
        _m = ""                       # 一叠素材只走遮罩路线，页面里不嵌图
    is_video = str(_m or "").lower().endswith((".mp4", ".mov", ".m4v", ".webm"))
    _w = int(w or TRIO_PORTRAIT[0])
    _h = int(h or TRIO_PORTRAIT[1])
    if portrait_media is None:
        portrait_media = portrait_media_of(fields)
    if portrait_media is None:
        portrait_media = not is_landscape(_w, _h)      # 读不到素材就按画布方向兜底
    # 嵌图方式按槽形定：竖素材槽是满高竖条 → 按高对齐；横素材槽是 16:9 横带 → 铺满。
    # 显式 media_fit 优先，写什么都不覆盖它。
    fit = str(fields.get("media_fit") or "").lower()
    if fit in ("cover", "撑满", "裁切"):
        cls = " cover"
    elif fit in ("height", "满高", "上下贴边", "按高"):
        cls = ""
    else:
        cls = "" if portrait_media else " cover"
    img = "" if is_video else _media_src(_m)
    media = ('<div class="tr-media%s">%s%s</div>'
             % (cls, ('<img src="%s">' % img) if img else "", _ov_html(fields)))
    return trio_page('<div class="tr-all">%s%s%s</div>'
                     % (_trio_left(fields), media, _trio_right(fields)),
                     w=w, h=h, media_w=mw, portrait_media=portrait_media)


def trio_mask(fields, w=None, h=None, media_w=None, portrait_media=None):
    """左中右分栏的「壳遮罩」：两翼 + 花字照画，中槽留透明。

    视频段 / 轮播段 / 推近段用它出带 alpha 的 PNG，合成时垫在素材下面 ——
    壳不动、素材动，中槽里就是「满高放映」的效果。
    """
    _clean(fields)
    mw = media_w if media_w is not None else fields.get("media_w")
    _w = int(w or TRIO_PORTRAIT[0])
    _h = int(h or TRIO_PORTRAIT[1])
    if portrait_media is None:
        portrait_media = portrait_media_of(fields)
    if portrait_media is None:
        portrait_media = not is_landscape(_w, _h)
    media = '<div class="tr-media" style="background:transparent">%s</div>' % _ov_html(fields)
    return trio_page('<div class="tr-all" style="background:transparent">%s%s%s</div>'
                     % (_trio_left(fields), media, _trio_right(fields)),
                     w=w, h=h, transparent=True, media_w=mw, portrait_media=portrait_media)
