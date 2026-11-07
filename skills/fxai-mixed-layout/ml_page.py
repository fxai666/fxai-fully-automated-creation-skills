# -*- coding: utf-8 -*-
"""fxai-mixed-layout · 槽内网页（HTML 生成，不含录制）

槽里除了图片、视频，还可以跑**一整页网页**：文字卡、长图滚动、真网页、自定义 HTML。
本模块只负责「把字段变成一页 HTML」，尺寸就是槽的尺寸；录制（Chrome + rec_anim.js）
由调用方做 —— 公共件不绑浏览器。

动效约定（与 fxai-slide-video 的动画库一致）：
CSS 动画默认 `animation-play-state:paused`，录制端开录时统一放行（一行 style 注入即可），
这样第一帧永远是「动画起点」，不会先闪一下终态。
"""
import base64, pathlib

MEDIA_PAGE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:__W__px;background:__BG__;color:#F2F3F5;
  font-family:"Smiley Sans","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased}
body{min-height:__H__px;overflow-x:hidden}
.pg{min-height:__H__px;padding:calc(30px*__S__) calc(38px*__S__);
  display:flex;flex-direction:column;justify-content:center;gap:calc(16px*__S__)}
.pg-k{font-size:calc(21px*__S__);letter-spacing:calc(4px*__S__);color:__ACC__;
  font-weight:700;text-transform:uppercase}
.pg-h{font-size:calc(46px*__S__);line-height:1.22;font-weight:900;color:#fff;letter-spacing:calc(1px*__S__)}
.pg-h em{font-style:normal;color:__ACC__}
.pg-li{display:flex;align-items:flex-start;gap:calc(14px*__S__);
  font-size:calc(30px*__S__);line-height:1.34;color:#E6E8EC}
.pg-li i{flex:0 0 auto;font-style:normal;font-weight:900;color:__ACC__;
  font-size:calc(26px*__S__);line-height:1.5;opacity:.9}
.pg-note{font-size:calc(22px*__S__);color:#8D93A0;letter-spacing:calc(1px*__S__);
  border-left:calc(4px*__S__) solid __ACC__;padding-left:calc(14px*__S__);line-height:1.5}
.pg-img{padding:0;display:block}
.pg-img img{width:100%;display:block}
.pg-cap{font-size:calc(24px*__S__);color:#B9BFCA;text-align:center;
  padding:calc(14px*__S__);background:rgba(0,0,0,.35)}
.pg-raw{padding:0;min-height:__H__px}
@keyframes pgIn{from{opacity:0;transform:translateY(calc(16px*__S__))}
  to{opacity:1;transform:none}}
.an{opacity:0;animation:pgIn .52s cubic-bezier(.2,.8,.2,1) both;animation-play-state:paused}
"""


def data_uri(p):
    """本地图片 → base64 data URI（不依赖路径解析，换目录也不碎）。"""
    if not p:
        return ""
    p = pathlib.Path(str(p))
    if not p.exists() or not p.is_file():
        return ""
    ext = p.suffix.lower().lstrip(".")
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
            "webp": "webp", "gif": "gif"}.get(ext, "png")
    try:
        return "data:image/%s;base64,%s" % (mime, base64.b64encode(p.read_bytes()).decode("ascii"))
    except Exception:
        return ""


def page_html(spec, w=768, h=440):
    """槽内网页：给一页独立 HTML（画布 = 槽尺寸），交给录制端录成素材。

    spec：
      type   "text"（默认，标题+要点逐条浮入）/ "longimg"（长图上下滚动，靠录制端自动滚）
             / "raw"（自定义 HTML 片段，逃生口）
      bg     底色，默认 #0E1014
      accent 强调色，默认 #FFD84D
      text   ：kicker 眉标 / title 大标题（可用 <em> 强调）/ items 要点列表
               / note 底部注解（左侧竖条）
      longimg：src 图片路径，caption 可选图注
      raw    ：html 片段（整段注入，自己控制尺寸与动画）
    """
    spec = dict(spec or {})
    typ = str(spec.get("type") or "text").lower()
    s = max(0.3, float(w) / 768.0)
    css = (MEDIA_PAGE_CSS
           .replace("__W__", str(int(w))).replace("__H__", str(int(h)))
           .replace("__S__", "%.4f" % s)
           .replace("__BG__", spec.get("bg") or "#0E1014")
           .replace("__ACC__", spec.get("accent") or "#FFD84D"))
    if typ in ("longimg", "long", "image"):
        src = data_uri(spec.get("src"))
        cap = '<div class="pg-cap">%s</div>' % spec["caption"] if spec.get("caption") else ""
        inner = '<div class="pg-img"><img src="%s">%s</div>' % (src, cap)
    elif typ == "raw":
        inner = '<div class="pg-raw">%s</div>' % (spec.get("html") or "")
    else:
        inner = text_html(spec)
    return ('<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            '<style>%s</style></head><body>%s</body></html>' % (css, inner))


def text_html(spec):
    """文字卡：眉标 + 大标题 + 要点逐条浮入（多到超出槽高时录制端自动上滚）。"""
    out, k = [], 0

    def d():
        nonlocal k
        k += 1
        return ' style="animation-delay:%.2fs"' % (0.10 * (k - 1))

    if spec.get("kicker"):
        out.append('<div class="pg-k an"%s>%s</div>' % (d(), spec["kicker"]))
    if spec.get("title"):
        out.append('<div class="pg-h an"%s>%s</div>' % (d(), spec["title"]))
    for i, it in enumerate(spec.get("items") or [], 1):
        idx = "" if spec.get("no_index") else "<i>%02d</i>" % i
        out.append('<div class="pg-li an"%s>%s<span>%s</span></div>' % (d(), idx, it))
    if spec.get("note"):
        out.append('<div class="pg-note an"%s>%s</div>' % (d(), spec["note"]))
    return '<div class="pg">%s</div>' % "".join(out)
