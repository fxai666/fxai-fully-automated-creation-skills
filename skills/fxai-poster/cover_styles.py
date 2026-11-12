# -*- coding: utf-8 -*-
"""封面模板库 —— 视频首帧封面的多种设计样式。

封面段占几帧、音轨垫什么，见 references/05-封面模板库.md「封面段时长」（唯一出处）。

为什么要有这个：封面不是只有「网页排版 + 剧照压底」一种解法。参考片那种
「实拍生图人物 + 金色/白色立体大字压顶」在信息流里的点击率明显更高。
所以做成**样式库**：按主题自动挑，也可以 --style 强制指定。

样式（竖屏 1080×1920，横屏同一套比例自适应，入口见 render()）：

  photo_big   写真大字     生图/剧照人物铺满 + 顶部两行立体大字 + 底部胶囊副标
                           适用：人物出镜、故事、情感、生活、口播人设
  magazine    杂志白卡     底图强虚化 + 中间米白卡片、黑字无描边 + 细金线
                           适用：观点、认知、书单、深度内容（高级感）
  bold_block  撞色色块     底图压暗 + 三行错位色块字（黄/红/白）
                           适用：反差、结论、痛骂式干货、数据爆炸
  split       上下分屏     上半冷暗「旧貌」/ 下半暖亮「新颜」，斜切分割
                           适用：改造、对比、前后、转折
  still       剧照压底     成片截帧 + 底部大面积压暗 + 三行标题贴底
                           适用：短剧 / MV / 数字人（底图必须是成片剧照）

只用两款字：得意黑（标题花字）+ 微软雅黑（正文/副标）。
一切颜色、字号、边距都从 STYLES 里改，不许在 HTML 里散落硬编码。
"""
import json
import pathlib
import sys

SK = pathlib.Path(__file__).resolve().parents[1]   # 技能根（本技能目录的上一级）
sys.path.insert(0, str(SK / "fxai-slide-video"))
import slides                                            # noqa: E402

TITLE = '"Smiley Sans","Microsoft YaHei",sans-serif'
BODY = '"Microsoft YaHei","PingFang SC",sans-serif'


# ---------------------------------------------------------------- 公共零件
def extrude(n, color, step=3, start=5, tail="#2A1303", alpha=.55):
    """向下挤出的立体厚度（每 step px 一层，后段变暗）。"""
    out = []
    for i in range(n):
        c = color if i <= n * 0.55 else tail
        out.append("0 %dpx 0 %s" % (start + i * step, c))
    out.append("0 %dpx 30px rgba(30,10,0,%s)" % (start + n * step + 9, alpha))
    return ", ".join(out)


BASE_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:{{W}}px;height:{{H}}px;overflow:hidden;background:#0A1220}
.wrap{position:relative;width:{{W}}px;height:{{H}}px;overflow:hidden}
.bg{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block}
/* 两行立体大字：描边层 + 填充层 + 可选高光层，grid 同格叠放（absolute 会重影） */
.two{display:grid;justify-items:center;line-height:.94;
     font-family:{{TITLE}};font-weight:700}
.two>span{grid-area:1/1;display:block;white-space:nowrap;
          -webkit-text-stroke:0.02em currentColor;paint-order:stroke fill}
/* 金色 */
.gold_o{color:#4A2405;-webkit-text-stroke:0.132em #4A2405;text-shadow:{{EX1}}}
.gold_g{background:linear-gradient(180deg,#FFFDF0 0%,#FFEFAE 18%,#FFD75F 42%,#F0A81A 70%,#D8860A 100%);
        -webkit-background-clip:text;background-clip:text;color:transparent}
.gold_h{background:linear-gradient(180deg,rgba(255,255,255,.88) 0%,rgba(255,255,255,.34) 24%,rgba(255,255,255,0) 44%);
        -webkit-background-clip:text;background-clip:text;color:transparent}
/* 白色（配蓝光晕） */
.white_o{color:#0B1F49;-webkit-text-stroke:0.122em #0B1F49;
         text-shadow:0 0 46px rgba(150,205,255,.95),0 0 92px rgba(120,180,255,.5),{{EX2}}}
.white_g{background:linear-gradient(180deg,#FFFFFF 0%,#FFFFFF 64%,#DFF0FF 100%);
         -webkit-background-clip:text;background-clip:text;color:transparent}
/* 纯黑（杂志卡内，靠同色外扩加厚，不加描边） */
.ink{color:#12161C;-webkit-text-stroke:0.02em currentColor}
.ink_g{color:#12161C}
"""


def page(body_html, extra_css, w, h):
    css = (BASE_CSS.replace("{{W}}", str(w)).replace("{{H}}", str(h))
           .replace("{{TITLE}}", TITLE)
           .replace("{{EX1}}", extrude(11, "#4E2707", 3, 5))
           .replace("{{EX2}}", extrude(8, "#0C2049", 3, 4, "#08152F", .5))
           .replace("{{BODY}}", BODY))
    return ("<!doctype html><html><head><meta charset='utf-8'><style>"
            + css + extra_css + "</style></head><body><div class='wrap'>"
            + body_html + "</div></body></html>")


# ---------------------------------------------------------------- 1 写真大字
CSS_PHOTO_BIG = """
.veil{position:absolute;inset:0;
  background:radial-gradient({{rw}}px {{rh}}px at 50% -6%, rgba(255,244,214,.30), transparent 68%),
             linear-gradient(180deg, rgba(6,10,20,.42) 0%, rgba(6,10,20,0) 22%,
                             rgba(6,10,20,0) 60%, rgba(6,10,20,.58) 100%)}
.title{position:absolute;left:0;right:0;top:{{top}}px;text-align:center}
.l1{font-size:{{s1}}px;letter-spacing:{{ls1}}em}
.l2{font-size:{{s2}}px;margin-top:{{gap}}px;letter-spacing:{{ls2}}em}
.sub{position:absolute;left:0;right:0;top:{{subtop}}px;text-align:center;font-family:{{BODY}}}
.sub i{font-style:normal;display:inline-block;font-size:{{ss}}px;color:#fff;letter-spacing:2px;
  background:rgba(8,14,26,.44);border:2px solid rgba(255,255,255,.44);border-radius:999px;
  padding:12px 34px;text-shadow:0 2px 10px rgba(0,0,0,.55)}
.sub em{font-style:normal;color:#FFD24D;font-weight:700}
.star{position:absolute;color:#FFF3C0;text-shadow:0 0 22px rgba(255,214,110,.95)}
"""


def _photo_big(bg, t1, t2, sub, w, h, s1=282, s2=196, top=110, gap=22,
               ls1=0.0, ls2=-0.01, ss=40, subtop=None):
    if subtop is None:
        subtop = top + int(s1 * .96) + gap + int(s2 * .96) + 46
    css = (CSS_PHOTO_BIG.replace("{{rw}}", str(int(w * 1.02)))
           .replace("{{rh}}", str(int(h * .32)))
           .replace("{{top}}", str(top)).replace("{{s1}}", str(s1))
           .replace("{{s2}}", str(s2)).replace("{{gap}}", str(gap))
           .replace("{{ls1}}", str(ls1)).replace("{{ls2}}", str(ls2))
           .replace("{{ss}}", str(ss)).replace("{{subtop}}", str(subtop))
           .replace("{{BODY}}", BODY))
    for k, v in _side_vars(side).items():
        css = css.replace("{{%s}}" % k, str(v))
    body = """
<img class="bg" src="{{BG}}"><div class="veil"></div>
<div class="star" style="left:{{X1}}px;top:{{Y1}}px;font-size:52px;opacity:.95">✦</div>
<div class="star" style="right:86px;top:{{Y2}}px;font-size:36px;opacity:.85">✦</div>
<div class="star" style="right:152px;top:{{Y3}}px;font-size:26px;opacity:.8">✦</div>
<div class="title">
  <div class="two l1"><span class="gold_o">{{T1}}</span><span class="gold_g">{{T1}}</span>
    <span class="gold_h">{{T1}}</span></div>
  <div class="two l2"><span class="white_o">{{T2}}</span><span class="white_g">{{T2}}</span></div>
</div>
<div class="sub"><i>{{SUB}}</i></div>
"""
    body = (body.replace("{{BG}}", _uri(bg)).replace("{{T1}}", t1).replace("{{T2}}", t2)
            .replace("{{SUB}}", sub).replace("{{X1}}", str(int(w * .072)))
            .replace("{{Y1}}", str(subtop + 40)).replace("{{Y2}}", str(subtop - 40))
            .replace("{{Y3}}", str(top + 200)))
    return page(body, css, w, h)


# ---------------------------------------------------------------- 2 杂志白卡
CSS_MAG = """
.bg{filter:blur(5px) saturate(.88) brightness(1.05);transform:scale(1.06)}
.veil{position:absolute;inset:0;
  background:linear-gradient(180deg,rgba(255,255,255,.30),rgba(12,18,28,.30))}
.card{position:absolute;left:{{cx}}px;right:{{cx}}px;{{vpos}};
  background:#FBF7EE;border-radius:8px;padding:{{pad}}px {{padx}}px;
  box-shadow:0 40px 90px rgba(0,0,0,.45),0 2px 0 rgba(255,255,255,.6) inset}
.corner{position:absolute;right:{{padx}}px;top:0;width:0;height:0;
  border-left:34px solid transparent;border-top:34px solid #C9AE73}
.kicker{font-family:{{BODY}};font-size:{{ks}}px;letter-spacing:5px;color:#8A7C5E;
  display:flex;justify-content:space-between;align-items:center}
.kicker b{color:#B8862B;font-weight:700}
.rule{height:2px;background:linear-gradient(90deg,#C9AE73,#F2E2BE,transparent);margin:{{rm}}px 0 {{rm}}px}
.h{font-family:{{TITLE}};color:#12161C;font-size:{{hs}}px;line-height:1.06;
  letter-spacing:1px;-webkit-text-stroke:0.018em #12161C;paint-order:stroke fill}
.h em{font-style:normal;color:#C0392B}
.hr{width:96px;height:6px;background:#B8862B;margin:{{rm}}px 0}
.sub{font-family:{{BODY}};font-size:{{bs}}px;line-height:1.62;color:#3A4048;letter-spacing:1px}
.sub b{color:#12161C}
.foot{font-family:{{BODY}};font-size:{{fs}}px;color:#9A8F79;letter-spacing:4px;margin-top:{{rm}}px}
"""


def _magazine(bg, t1, t2, sub, w, h, kicker_l="凤希 AI · 自动创作论", kicker_r="EP.01",
              hs=132, bs=38, ks=26, fs=24, cx=76, pad=64, padx=60,
              pos="bottom", cb=64, foot=None):
    """foot 显式传 "" 即裸出（媒体封面不带品牌署名，见 02 册硬规则）；缺省保留品牌行。"""
    foot = "FENGXI · 从写稿到成片全自动" if foot is None else foot
    vpos = ("bottom:%dpx" % cb) if pos == "bottom" else "top:50%;transform:translateY(-50%)"
    css = (CSS_MAG.replace("{{cx}}", str(cx)).replace("{{padx}}", str(padx))
           .replace("{{pad}}", str(pad)).replace("{{ks}}", str(ks))
           .replace("{{hs}}", str(hs)).replace("{{bs}}", str(bs))
           .replace("{{fs}}", str(fs)).replace("{{rm}}", "26")
           .replace("{{vpos}}", vpos)
           .replace("{{BODY}}", BODY).replace("{{TITLE}}", TITLE))
    body = """
<img class="bg" src="{{BG}}"><div class="veil"></div>
<div class="card">
  <div class="corner"></div>
  <div class="kicker"><b>{{KL}}</b><span>{{KR}}</span></div>
  <div class="rule"></div>
  <div class="h">{{T1}}<br>{{T2}}</div>
  <div class="hr"></div>
  <div class="sub">{{SUB}}</div>
  <div class="foot">{{FOOT}}</div>
</div>
"""
    body = (body.replace("{{BG}}", _uri(bg)).replace("{{KL}}", kicker_l)
            .replace("{{KR}}", kicker_r).replace("{{T1}}", t1).replace("{{T2}}", t2)
            .replace("{{SUB}}", sub).replace("{{FOOT}}", foot))
    return page(body, css, w, h)


# ---------------------------------------------------------------- 3 撞色色块
CSS_BLOCK = """
.bg{filter:brightness(.50) saturate(.95) blur(1.2px)}
.veil{position:absolute;inset:0;
  background:linear-gradient(160deg,rgba(10,16,30,.60),rgba(10,16,30,.26) 55%,rgba(10,16,30,.82))}
.stack{position:absolute;left:0;right:0;top:43%;transform:translateY(-50%) rotate(-2.4deg);
  text-align:center}
.row{font-family:{{TITLE}};font-size:{{fs}}px;line-height:1;padding:{{pv}}px {{ph}}px;
  margin:0 auto -{{ov}}px;display:block;width:fit-content;letter-spacing:2px;
  box-shadow:0 16px 40px rgba(0,0,0,.45)}
.r1{background:#FFD400;color:#141414;-webkit-text-stroke:0.02em #141414;transform:translateX(-16px)}
.r2{background:#E2331F;color:#FFFFFF;-webkit-text-stroke:0.02em #FFF;transform:translateX(22px)}
.r3{background:#FFFFFF;color:#141414;-webkit-text-stroke:0.02em #141414;transform:translateX(-8px)}
.kick{position:absolute;left:0;right:0;top:{{ktop}}px;text-align:center;font-family:{{BODY}};
  font-size:28px;letter-spacing:8px;color:#FFD400}
.foot{position:absolute;left:0;right:0;bottom:110px;text-align:center;font-family:{{BODY}};
  font-size:32px;color:rgba(255,255,255,.86);letter-spacing:3px}
"""


def _bold_block(bg, t1, t2, t3, sub, w, h, fs=None, lx=64, kick=None):
    """kick 显式传 "" 即裸出（媒体封面不带品牌署名，见 02 册硬规则）；缺省保留默认行。"""
    kick = "一个人 · 一条片" if kick is None else kick
    ph_r, pv_r, ov_r = .20, .17, .10
    n = max(len(t1), len(t2), len(t3))
    fit = (w - 2 * lx) / (n + 2 * ph_r)
    fs = int(min(fs or fit, fit))
    m = int(fs * 1.28)
    ktop = int(h * .43 - m * 1.7)
    css = (CSS_BLOCK.replace("{{lx}}", str(lx))
           .replace("{{fs}}", str(fs)).replace("{{pv}}", str(int(fs * pv_r)))
           .replace("{{ph}}", str(int(fs * ph_r))).replace("{{ov}}", str(int(fs * ov_r)))
           .replace("{{ktop}}", str(ktop)).replace("{{BODY}}", BODY)
           .replace("{{TITLE}}", TITLE))
    body = """
<img class="bg" src="{{BG}}"><div class="veil"></div>
<div class="kick">{{KICK}}</div>
<div class="stack">
  <span class="row r1">{{T1}}</span>
  <span class="row r2">{{T2}}</span>
  <span class="row r3">{{T3}}</span>
</div>
<div class="foot">{{SUB}}</div>
"""
    body = (body.replace("{{BG}}", _uri(bg)).replace("{{T1}}", t1).replace("{{T2}}", t2)
            .replace("{{T3}}", t3).replace("{{SUB}}", sub)
            .replace("{{KICK}}", kick))
    return page(body, css, w, h)


# ---------------------------------------------------------------- 4 上下分屏
CSS_SPLIT = """
.top,.bot{position:absolute;left:0;right:0;overflow:hidden}
.top{top:0;height:{{sh}}px;clip-path:polygon(0 0,100% 0,100% 92%,0 100%)}
.bot{top:{{sh}}px;bottom:0;clip-path:polygon(0 8%,100% 0,100% 100%,0 100%)}
.top .bg,.bot .bg{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.top .bg{filter:grayscale(.72) brightness(.62) contrast(1.05)}
.bot .bg{filter:saturate(1.12) brightness(.92)}
.top .w,.bot .w{position:absolute;inset:0}
.top .w{background:linear-gradient(180deg,rgba(18,26,40,.78),rgba(18,26,40,.5))}
.bot .w{background:linear-gradient(180deg,rgba(255,168,60,.22),rgba(10,14,22,.62))}
.line{position:absolute;left:-4%;right:-4%;top:{{sh}}px;height:10px;
  background:linear-gradient(90deg,#FFD400,#FF8A00,#FFD400);transform:rotate(-1.9deg);
  box-shadow:0 0 40px rgba(255,180,40,.85)}
.tag{position:absolute;font-family:{{BODY}};font-size:34px;letter-spacing:8px;
  padding:10px 28px;border-radius:6px}
.t1{left:{{lx}}px;top:{{ty1}}px;color:#DCE6F5;border:2px solid rgba(220,230,245,.5)}
.t2{left:{{lx}}px;top:{{ty2}}px;color:#141414;background:#FFD400;font-weight:700}
.txt{position:absolute;left:{{lx}}px;right:{{lx}}px;font-family:{{TITLE}};line-height:1.02;
  letter-spacing:2px}
.a1{top:{{a1}}px;font-size:{{s1}}px;color:#EAEFF8;-webkit-text-stroke:0.09em #0B1424;
  text-shadow:0 8px 0 rgba(11,20,36,.85),0 20px 40px rgba(0,0,0,.6);paint-order:stroke fill}
.a2{top:{{a2}}px;font-size:{{s2}}px;color:#FFFFFF;-webkit-text-stroke:0.10em #141414;
  text-shadow:0 8px 0 rgba(20,20,20,.9),0 18px 36px rgba(0,0,0,.55);paint-order:stroke fill}
.a2 em{font-style:normal;color:#FFD400}
"""


def _ew(text):
    """粗略视觉宽度（以字号为 1 单位）：全角 1.0，空格/半角 0.45。"""
    return sum(0.45 if ord(c) < 128 else 1.0 for c in text)


def _split(bg, a1, a2, w, h, sh_ratio=.47, s1=112, s2=124, lx=64, bg2=None):
    sh = int(h * sh_ratio)
    s1 = int(min(s1, (w - 2 * lx) / (_ew(a1) + 0.12)))
    s2 = int(min(s2, (w - 2 * lx) / (_ew(a2) + 0.12)))
    css = (CSS_SPLIT.replace("{{sh}}", str(sh)).replace("{{lx}}", str(lx))
           .replace("{{s1}}", str(s1)).replace("{{s2}}", str(s2))
           .replace("{{ty1}}", "112").replace("{{ty2}}", "122")
           .replace("{{a1}}", "218").replace("{{a2}}", "230")
           .replace("{{BODY}}", BODY).replace("{{TITLE}}", TITLE))
    body = """
<div class="top"><img class="bg" src="{{BG}}"><div class="w"></div>
  <div class="tag t1">以 前</div><div class="txt a1">{{A1}}</div></div>
<div class="bot"><img class="bg" src="{{BG2}}"><div class="w"></div>
  <div class="tag t2">现 在</div><div class="txt a2">{{A2}}</div></div>
<div class="line"></div>
"""
    return page(body.replace("{{BG}}", _uri(bg)).replace("{{BG2}}", _uri(bg2 or bg))
                .replace("{{A1}}", a1).replace("{{A2}}", a2), css, w, h)


# ---------------------------------------------------------------- 5 人物特写
CSS_CLOSEUP = """
.bg{transform:scale(1.26);object-position:50% 24%}
.veil{position:absolute;inset:0;
  background:linear-gradient(180deg,rgba(6,10,20,.30) 0%,rgba(6,10,20,0) 26%,
             rgba(6,10,20,.30) 52%,rgba(6,10,20,.90) 88%)}
.cap{position:absolute;left:0;right:0;top:{{cptop}}px;text-align:center;font-family:{{BODY}}}
.cap i{font-style:normal;font-size:{{cs}}px;letter-spacing:5px;color:#fff;
  border:2px solid rgba(255,255,255,.55);border-radius:999px;padding:10px 28px;
  background:rgba(6,12,22,.42)}
.title{position:absolute;left:0;right:0;bottom:{{btm}}px;text-align:center}
.l1{font-size:{{s1}}px;letter-spacing:{{ls1}}em}
.l2{font-size:{{s2}}px;margin-top:{{gap}}px;letter-spacing:{{ls2}}em}
"""


def _closeup(bg, t1, t2, cap, w, h, s1=None, s2=None, btm=176, gap=14,
             ls1=0.0, ls2=.01, cs=32):
    s1 = int(min(s1 or 214, (w - 96) / (_ew(t1) + .3)))
    s2 = int(min(s2 or 214, (w - 96) / (_ew(t2) + .3)))
    css = (CSS_CLOSEUP.replace("{{s1}}", str(s1)).replace("{{s2}}", str(s2))
           .replace("{{gap}}", str(gap)).replace("{{ls1}}", str(ls1))
           .replace("{{ls2}}", str(ls2)).replace("{{cs}}", str(cs))
           .replace("{{cptop}}", str(int(h * .10))).replace("{{btm}}", str(btm))
           .replace("{{BODY}}", BODY).replace("{{TITLE}}", TITLE))
    body = """
<img class="bg" src="{{BG}}"><div class="veil"></div>
<div class="cap"><i>{{CAP}}</i></div>
<div class="title">
  <div class="two l1"><span class="gold_o">{{T1}}</span><span class="gold_g">{{T1}}</span>
    <span class="gold_h">{{T1}}</span></div>
  <div class="two l2"><span class="white_o">{{T2}}</span><span class="white_g">{{T2}}</span></div>
</div>
"""
    return page(body.replace("{{BG}}", _uri(bg)).replace("{{CAP}}", cap)
                .replace("{{T1}}", t1).replace("{{T2}}", t2), css, w, h)


# ---------------------------------------------------------------- 6 清单三条
CSS_LIST = """
.bg{filter:brightness(.42) saturate(.9) blur(2px)}
.veil{position:absolute;inset:0;
  background:linear-gradient(180deg,rgba(8,12,22,.78) 0%,rgba(8,12,22,.50) 45%,rgba(8,12,22,.88) 100%)}
.hd{position:absolute;left:{{lx}}px;right:{{lx}}px;top:{{hdt}}px}
.kick{font-family:{{BODY}};font-size:28px;letter-spacing:6px;color:#FFD400;margin-bottom:22px}
.h{font-family:{{TITLE}};font-size:{{hs}}px;line-height:1.08;color:#FFFFFF;letter-spacing:2px;
  -webkit-text-stroke:0.028em #0A0F1A;paint-order:stroke fill;
  text-shadow:0 10px 30px rgba(0,0,0,.55)}
.h em{font-style:normal;color:#FFD400}
.items{position:absolute;left:{{lx}}px;right:{{lx}}px;top:{{it}}px}
.it{display:flex;align-items:center;gap:26px;padding:26px 30px;margin-bottom:24px;
  background:rgba(255,255,255,.075);border-left:8px solid #FFD400;border-radius:4px;
  box-shadow:0 10px 30px rgba(0,0,0,.3)}
.n{font-family:{{TITLE}};font-size:{{ns}}px;color:#141414;background:#FFD400;
  width:{{ns2}}px;height:{{ns2}}px;line-height:{{ns2}}px;text-align:center;border-radius:6px;
  flex:none}
.tx{font-family:{{BODY}};font-size:{{bs}}px;line-height:1.4;color:#F2F5FA;letter-spacing:1px}
.tx b{color:#FFD400}
.foot{position:absolute;left:0;right:0;bottom:96px;text-align:center;font-family:{{BODY}};
  font-size:30px;letter-spacing:4px;color:rgba(255,255,255,.62)}
"""


def _list(bg, head, items, foot, w, h, kick="一个人 · 一条片", hs=104, bs=36,
          ns=52, lx=64, hdt=150, it=None):
    n = len(items)
    it = it or int(hdt + hs * 1.15 + 92)
    css = (CSS_LIST.replace("{{lx}}", str(lx)).replace("{{hdt}}", str(hdt))
           .replace("{{hs}}", str(hs)).replace("{{it}}", str(it))
           .replace("{{ns}}", str(ns)).replace("{{ns2}}", str(int(ns * 1.08)))
           .replace("{{bs}}", str(bs)).replace("{{BODY}}", BODY)
           .replace("{{TITLE}}", TITLE))
    rows = "".join(
        '<div class="it"><span class="n">%d</span><span class="tx">%s</span></div>' % (i + 1, t)
        for i, t in enumerate(items))
    body = """
<img class="bg" src="{{BG}}"><div class="veil"></div>
<div class="hd"><div class="kick">{{KICK}}</div><div class="h">{{HEAD}}</div></div>
<div class="items">{{ROWS}}</div>
<div class="foot">{{FOOT}}</div>
"""
    body = (body.replace("{{BG}}", _uri(bg)).replace("{{KICK}}", kick)
            .replace("{{HEAD}}", head).replace("{{ROWS}}", rows)
            .replace("{{FOOT}}", foot))
    return page(body, css, w, h)


# ---------------------------------------------------------------- 注册表
def _uri(p):
    return pathlib.Path(p).resolve().as_uri()


# ---------------------------------------------------------------- 8 竖排贴左
# 为什么有这一款：照片类封面（photo_big）把大字横排压在顶部，人物一旦居中构图，
# 字就落在额头到下巴那一段，等于给脸打了个码。竖排只占左侧一条窄带，人物完整露出，
# 而且从 1080×1920 到 900×383 各种比例都不会把标题裁断。
CSS_VERT_TITLE = """
.bg{object-position:50% 0%}   /* 顶部对齐：竖图转 3:4 / 1:1 时先保头顶 */
.veilL{position:absolute;{{VEILPOS}};top:0;bottom:0;width:{{vlw}}%;
  background:linear-gradient({{GRAD}},rgba(3,6,12,.93) 0%,rgba(3,6,12,.89) 21%,
             rgba(3,6,12,.50) 44%,rgba(3,6,12,.14) 68%,rgba(3,6,12,0) 86%)}
.veilT{position:absolute;left:0;right:0;top:0;height:15%;
  background:linear-gradient(180deg,rgba(3,6,12,.66),rgba(3,6,12,0))}
.veilB{position:absolute;left:0;right:0;bottom:0;height:26%;
  background:linear-gradient(0deg,rgba(3,6,12,.95) 22%,rgba(3,6,12,0))}
.kick{position:absolute;{{ANCHOR}}:{{lx}}px;top:{{kicktop}}px;font-family:{{BODY}};
  font-size:{{ks}}px;letter-spacing:.42em;color:{{GOLD}};font-weight:600;
  text-shadow:0 2px 10px rgba(0,0,0,.75)}
.kick i{display:inline-block;width:{{bw}}px;height:1px;background:{{GOLD}};
  vertical-align:middle;margin-right:{{IMR}};margin-left:{{IML}};opacity:.85}
.en{position:absolute;{{ANCHOR}}:{{lx}}px;top:{{entop}}px;font-family:{{BODY}};
  font-size:{{es}}px;letter-spacing:.52em;color:#fff;opacity:.82;font-weight:300;
  text-shadow:0 2px 12px rgba(0,0,0,.8)}
.vt{position:absolute;{{ANCHOR}}:{{lx}}px;top:{{ttop}}px;writing-mode:vertical-rl;
  text-orientation:upright;font-family:{{BODY}};font-weight:800;
  font-size:{{ts}}px;letter-spacing:.10em;color:{{PAPER}};line-height:1;
  text-shadow:0 6px 26px rgba(0,0,0,.75),0 2px 5px rgba(0,0,0,.85),0 0 60px rgba(216,195,154,.16)}
.vt::before{content:"";display:block;width:100%;height:{{bw2}}px;
  background:linear-gradient(90deg,transparent,{{GOLD}});margin-bottom:{{gp}}px;border-radius:2px}
.sub{position:absolute;left:0;right:0;bottom:{{sbb}}px;text-align:center;
  font-family:{{BODY}};font-size:{{ss}}px;letter-spacing:.34em;color:#fff;opacity:.93;
  font-weight:500;text-shadow:0 2px 14px rgba(0,0,0,.9)}
.foot{position:absolute;left:0;right:0;bottom:{{fbb}}px;text-align:center;
  font-family:{{BODY}};font-size:{{fs}}px;letter-spacing:.26em;color:{{GOLD}};opacity:.88;
  text-shadow:0 2px 10px rgba(0,0,0,.85)}
"""


CSS_VERT_LAND = """
.bgblur{position:absolute;inset:-32%;background-image:url("{{SRC}}");
  background-position:50% 8%;background-size:cover;
  filter:blur({{BLUR}}px) brightness(.52) saturate(.92)}
.hero{position:absolute;left:{{HEROX}};top:50%;height:122%;transform:translate(-50%,-50%);
  -webkit-mask-image:linear-gradient(90deg,transparent 0,#000 7%,#000 93%,transparent 100%);
  mask-image:linear-gradient(90deg,transparent 0,#000 7%,#000 93%,transparent 100%)}
.hero img{height:100%;width:auto;display:block}
.tint{position:absolute;inset:0;background:
  linear-gradient({{GRAD}},rgba(3,6,12,.90) 0%,rgba(3,6,12,.80) 30%,
                  rgba(3,6,12,.30) 48%,rgba(3,6,12,.16) 64%,
                  rgba(3,6,12,.55) 82%,rgba(3,6,12,.90) 100%)}
.veilT{position:absolute;left:0;right:0;top:0;height:15%;
  background:linear-gradient(180deg,rgba(3,6,12,.66),rgba(3,6,12,0))}
.veilB{position:absolute;left:0;right:0;bottom:0;height:26%;
  background:linear-gradient(0deg,rgba(3,6,12,.95) 22%,rgba(3,6,12,0))}
.kick{position:absolute;left:{{lx}}px;top:{{kicktop}}px;font-family:{{BODY}};
  font-size:{{ks}}px;letter-spacing:.42em;color:{{GOLD}};font-weight:600;
  text-shadow:0 2px 10px rgba(0,0,0,.75)}
.kick i{display:inline-block;width:{{bw}}px;height:1px;background:{{GOLD}};
  vertical-align:middle;margin-right:{{IMR}};margin-left:{{IML}};opacity:.85}
.en{position:absolute;{{ANCHOR}}:{{lx}}px;top:{{entop}}px;font-family:{{BODY}};
  font-size:{{es}}px;letter-spacing:.52em;color:#fff;opacity:.82;font-weight:300;
  text-shadow:0 2px 12px rgba(0,0,0,.8)}
.vt{position:absolute;{{ANCHOR}}:{{lx}}px;top:{{ttop}}px;writing-mode:vertical-rl;
  text-orientation:upright;font-family:{{BODY}};font-weight:800;
  font-size:{{ts}}px;letter-spacing:.10em;color:{{PAPER}};line-height:1;
  text-shadow:0 6px 26px rgba(0,0,0,.75),0 2px 5px rgba(0,0,0,.85)}
.vt::before{content:"";display:block;width:100%;height:{{bw2}}px;
  background:linear-gradient(90deg,transparent,{{GOLD}});margin-bottom:{{gp}}px;border-radius:2px}
.sub{position:absolute;left:0;right:0;bottom:{{sbb}}px;text-align:center;
  font-family:{{BODY}};font-size:{{ss}}px;letter-spacing:.34em;color:#fff;opacity:.93;
  font-weight:500;text-shadow:0 2px 14px rgba(0,0,0,.9)}
.foot{position:absolute;left:0;right:0;bottom:{{fbb}}px;text-align:center;
  font-family:{{BODY}};font-size:{{fs}}px;letter-spacing:.26em;color:{{GOLD}};opacity:.88;
  text-shadow:0 2px 10px rgba(0,0,0,.85)}
"""


def _side_vars(side):
    """文字贴哪侧 → 定位锚点 / 遮罩方向 / 主体位置 / 装饰线边距。"""
    right = side == "right"
    return dict(ANCHOR="right" if right else "left",
                VEILPOS="right:0" if right else "left:0",
                GRAD="270deg" if right else "90deg",
                HEROX="38%" if right else "62%",
                IMR="0" if right else ".62em", IML=".62em" if right else "0")


def _kick_html(kick, side):
    if side == "right":
        return "<div class='kick'>%s<i></i></div>" % kick
    return "<div class='kick'><i></i>%s</div>" % kick


def _vert_title_land(bg, t1, sub="", w=1920, h=1080, en="", kick="", brand="",
                     gold="#d8c39a", paper="#f7f2e9", side="left"):
    """横版：模糊底只取上部天空（防止人像重影）+ 主体放大出血靠右，文字全走左侧空区。"""
    f = h / 1080.0
    compact = h < 520
    vals = dict(SRC=_uri(bg), BLUR=round(62 * f), BODY=BODY,
                GOLD=gold, PAPER=paper, **_side_vars(side),
                lx=round(w * 0.050), kicktop=round(h * 0.075),
                entop=round(h * 0.140), ttop=round(h * 0.205),
                ks=max(10, round(19 * f)), es=max(11, round(25 * f)),
                ts=max(28, round(132 * f)), ss=max(12, round(28 * f)),
                fs=max(10, round(20 * f)), bw=max(18, round(64 * f)),
                bw2=max(2, round(6 * f)), gp=max(6, round(26 * f)),
                sbb=round(h * 0.062), fbb=round(h * 0.030))
    css = CSS_VERT_LAND
    for k, v in vals.items():
        css = css.replace("{{%s}}" % k, str(v))
    body = ['<div class="bgblur"></div>',
            '<div class="hero"><img src="%s"></div>' % _uri(bg),
            '<div class="tint"></div><div class="veilT"></div><div class="veilB"></div>']
    if kick:
        body.append(_kick_html(kick, side))
    if en and not compact:
        body.append('<div class="en">%s</div>' % en)
    body.append('<div class="vt">%s</div>' % t1)
    if sub and not compact:
        body.append('<div class="sub">%s</div>' % sub)
    if brand and not compact:
        body.append('<div class="foot">%s</div>' % brand)
    return page("".join(body), css, w, h)


def _vert_title(bg, t1, sub="", w=1080, h=1920, en="", kick="", brand="",
                gold="#d8c39a", paper="#f7f2e9", vlw=64, side="left"):
    """竖排贴左。所有字号按 h 派生，所以同一套参数在 9:16 / 3:4 / 1:1 / 横幅上都成立。"""
    if w > h:
        return _vert_title_land(bg, t1, sub=sub, w=w, h=h, en=en, kick=kick,
                                brand=brand, gold=gold, paper=paper, side=side)
    f = h / 1920.0
    compact = h < 520          # 公众号首图那种小图：多一行就糊，只留 kick + 标题
    css = (CSS_VERT_TITLE
           .replace("{{vlw}}", str(vlw))
           .replace("{{lx}}", str(round(w * 0.050)))
           .replace("{{kicktop}}", str(round(h * 0.055)))
           .replace("{{entop}}", str(round(h * 0.092)))
           .replace("{{ttop}}", str(round(h * 0.136)))
           .replace("{{ks}}", str(max(10, round(19 * f))))
           .replace("{{es}}", str(max(11, round(25 * f))))
           .replace("{{ts}}", str(max(28, round(124 * f))))
           .replace("{{ss}}", str(max(12, round(28 * f))))
           .replace("{{fs}}", str(max(10, round(20 * f))))
           .replace("{{bw}}", str(max(18, round(64 * f))))
           .replace("{{bw2}}", str(max(2, round(6 * f))))
           .replace("{{gp}}", str(max(6, round(26 * f))))
           .replace("{{sbb}}", str(round(h * 0.062)))
           .replace("{{fbb}}", str(round(h * 0.030)))
           .replace("{{GOLD}}", gold).replace("{{PAPER}}", paper)
           .replace("{{BODY}}", BODY))
    for k, v in _side_vars(side).items():
        css = css.replace("{{%s}}" % k, str(v))
    body = ['<img class="bg" src="%s">' % _uri(bg),
            '<div class="veilT"></div><div class="veilL"></div><div class="veilB"></div>']
    if kick:
        body.append(_kick_html(kick, side))
    if en and not compact:
        body.append('<div class="en">%s</div>' % en)
    body.append('<div class="vt">%s</div>' % t1)
    if sub and not compact:
        body.append('<div class="sub">%s</div>' % sub)
    if brand and not compact:
        body.append('<div class="foot">%s</div>' % brand)
    return page("".join(body), css, w, h)


STYLES = {
    "photo_big": dict(cn="写真大字", fn=_photo_big, need_bg=True,
                      use="人物出镜 / 故事 / 情感 / 人设口播",
                      keys=("人", "故事", "我", "个人", "经历", "生活", "情感", "真实")),
    "vert_title": dict(cn="竖排贴左", fn=_vert_title, need_bg=True,
                       use="MV / 歌曲 / 情绪向（标题竖排贴左，绝不压脸）",
                       keys=("MV", "歌", "曲", "音乐", "海边", "治愈", "情绪", "来信")),
    "magazine": dict(cn="杂志白卡", fn=_magazine, need_bg=True,
                     use="观点 / 认知 / 书单 / 深度（高级感）",
                     keys=("观点", "认知", "方法论", "为什么", "本质", "思考", "书")),
    "bold_block": dict(cn="撞色色块", fn=_bold_block, need_bg=True,
                       use="反差 / 结论 / 硬核干货 / 数据",
                       keys=("钱", "效率", "翻倍", "错", "坑", "真相", "对比", "多少")),
    "split": dict(cn="上下分屏", fn=_split, need_bg=True,
                  use="改造 / 前后对比 / 转折",
                  keys=("以前", "现在", "改造", "前后", "从", "变成", "升级")),  # std:allow
    "closeup": dict(cn="人物特写", fn=_closeup, need_bg=True,
                    use="强人设 / 口播 / 情绪（脸占满画面）",
                    keys=("我", "你们", "人设", "情绪", "心里", "为什么我")),
    "list": dict(cn="清单三条", fn=_list, need_bg=True,
                 use="干货 / 教程 / 步骤 / 盘点",
                 keys=("三", "步", "清单", "方法", "流程", "怎么", "教程", "盘点")),
    "still": dict(cn="剧照压底", fn=None, need_bg=True,
                  use="短剧 / MV / 数字人（成片剧照当底）", keys=("短剧", "剧", "MV", "集")),
}


def auto_style(text):
    """按主题词打分选样式，返回 (key, 分数表)。打不出分就回 photo_big。"""
    score = {k: 0 for k in STYLES}
    for k, v in STYLES.items():
        for kw in v["keys"]:
            if kw in text:
                score[k] += 1
    best = max(score, key=lambda k: score[k])
    if score[best] == 0:
        best = "photo_big"
    return best, score


def render(style, out_dir, name, bg=None, w=1080, h=1920, **kw):
    st = STYLES[style]
    if st["fn"] is None:
        raise SystemExit("样式 %s 的底图必须是成片剧照，走 poster.py --mode hybrid" % style)
    if not bg:
        raise SystemExit("样式 %s 需要 --bg 底图" % style)
    html = st["fn"](bg, w=w, h=h, **kw)
    html = html.replace("</head>", "<style>%s</style></head>" % slides._font_face_css(), 1)
    return slides.render(name, html, pathlib.Path(out_dir), w=w, h=h)


# ---------------------------------------------------------------- 尺寸矩阵
_SIZES_JSON = pathlib.Path(__file__).resolve().parent / "cover_sizes.json"


def load_sizes():
    """读 cover_sizes.json（尺寸唯一真源）。缺文件时退回最小集的两个尺寸。"""
    if _SIZES_JSON.is_file():
        return json.loads(_SIZES_JSON.read_text(encoding="utf-8"))
    return {"sizes": {"vert_9x16": {"w": 1080, "h": 1920},
                      "horiz_16x9": {"w": 1920, "h": 1080}}, "policy": {}}


def render_matrix(style, out_dir, name, bg=None, keys=None, **kw):
    """一次出全套尺寸。

    keys 省略时按 cover_sizes.json 的 policy.minimal_set（默认只要两张，
    能统一就统一）；要额外尺寸就显式传 keys。返回 {key: png 路径}。
    """
    reg = load_sizes()
    sizes = reg.get("sizes", {})
    if not keys:
        keys = reg.get("policy", {}).get("minimal_set") or ["vert_9x16", "horiz_16x9"]
    out = {}
    for k in keys:
        if k not in sizes:
            raise SystemExit("未知尺寸 key：%s（可选 %s）" % (k, ", ".join(sizes)))
        w, h = int(sizes[k]["w"]), int(sizes[k]["h"])
        out[k] = render(style, out_dir, "%s_%dx%d" % (name, w, h), bg=bg, w=w, h=h, **kw)
    return out
