# -*- coding: utf-8 -*-
"""fxai-mixed-layout · 转场库（CSS3 / 30 种）

ffmpeg 的 xfade 只有二维擦除/位移，做不出 3D 翻页、立方体、圆形收拢这些。
这里把「网页能做的转场」收成一份登记表，渲染方式是把整条轮播写成一个 HTML，
让 CSS 动画**按真实时间播放**，用录制端（CDP screencast）抓屏 —— 48 秒的片子
约 1 分钟出片，比逐帧定格截图（每帧 0.9 秒）快一个量级。

🔴 本模块零业务依赖：录制端由调用方 `configure(record_page=...)` 注入。
   方向只能是「技能 → 本模块」。

用法（技能侧）：
    import ml_trans, slides
    ml_trans.configure(record_page=lambda html, name, outdir, w, h, sec:
                       slides.record_anim(name, html, outdir, entrance=sec,
                                          fps=25, w=w, h=h, scroll="none", wait_ms=500))
    ml_trans.reel(items, out_mp4, total=48.9, w=1080, h=608, fps=25,
                  trans=["push_left", "cube_up", ...], trans_dur=0.5)

items 每项：{"path": 图片路径, "zoom": 0.05(可选，推近幅度), "dur": 秒(可选)}
"""

import json
import pathlib
import subprocess

CFG = {
    "shot": None,            # 注入：shot(html, name, outdir, w, h) -> png 路径（一帧一张）
    "ffmpeg": "ffmpeg",
    "ffprobe": "",
    "fps": 25,
    "bg": "#0B0B0C",
}


def configure(shot=None, ffmpeg=None, ffprobe=None, fps=None, bg=None):
    """注入截帧端与编码环境。shot = 技能侧的 slides.render（一个 html → 一张 png）。"""
    if shot:
        CFG["shot"] = shot
    if ffmpeg:
        CFG["ffmpeg"] = str(ffmpeg)
    if ffprobe is not None:
        CFG["ffprobe"] = str(ffprobe)
    if fps:
        CFG["fps"] = int(fps)
    if bg:
        CFG["bg"] = str(bg)
    return dict(CFG)


# ---------------------------------------------------------------- 转场登记表
# 每条：k=代码名 cn=中文名 in/out=keyframes 主体（None = 这一侧不动）
#      oi/oo=进入/退出元素的 transform-origin，ease=缓动，under=True 表示
#      「新图在下面，旧图演完露出新图」（翻书、圆形收拢这类）
D = "cubic-bezier(.66,0,.34,1)"

TRANS = [
    # ---- 位移推拉 ----
    dict(k="push_left", cn="左推",
         in_="from{transform:translateX(100%)}to{transform:translateX(0)}",
         out="from{transform:translateX(0)}to{transform:translateX(-100%)}"),
    dict(k="push_right", cn="右推",
         in_="from{transform:translateX(-100%)}to{transform:translateX(0)}",
         out="from{transform:translateX(0)}to{transform:translateX(100%)}"),
    dict(k="push_up", cn="上推",
         in_="from{transform:translateY(100%)}to{transform:translateY(0)}",
         out="from{transform:translateY(0)}to{transform:translateY(-100%)}"),
    dict(k="push_down", cn="下推",
         in_="from{transform:translateY(-100%)}to{transform:translateY(0)}",
         out="from{transform:translateY(0)}to{transform:translateY(100%)}"),
    dict(k="cover_left", cn="左盖",
         in_="from{transform:translateX(-100%)}to{transform:translateX(0)}", out=None),
    dict(k="cover_up", cn="上盖",
         in_="from{transform:translateY(-100%)}to{transform:translateY(0)}", out=None),

    # ---- 3D ----
    dict(k="cube_up", cn="立方上翻", oi="50% 0", oo="50% 100%",
         in_="from{transform:translateY(-100%) rotateX(90deg);opacity:.35}"
             "to{transform:none;opacity:1}",
         out="from{transform:none;opacity:1}"
             "to{transform:translateY(100%) rotateX(-90deg);opacity:.35}"),
    dict(k="cube_left", cn="立方左翻", oi="0 50%", oo="100% 50%",
         in_="from{transform:translateX(100%) rotateY(-90deg);opacity:.35}"
             "to{transform:none;opacity:1}",
         out="from{transform:none;opacity:1}"
             "to{transform:translateX(-100%) rotateY(90deg);opacity:.35}"),
    dict(k="flip_x", cn="上下翻牌",
         in_="from{transform:rotateX(-90deg);opacity:.3}to{transform:none;opacity:1}",
         out="from{transform:none;opacity:1}to{transform:rotateX(90deg);opacity:.3}"),
    dict(k="flip_y", cn="左右翻牌",
         in_="from{transform:rotateY(90deg);opacity:.3}to{transform:none;opacity:1}",
         out="from{transform:none;opacity:1}to{transform:rotateY(-90deg);opacity:.3}"),
    dict(k="page_turn", cn="翻书", oo="0 50%", under=True, in_=None,
         out="from{transform:none;filter:brightness(1)}"
             "to{transform:rotateY(-105deg);filter:brightness(.6)}"),
    dict(k="card_drop", cn="卡片落", oi="50% 0", out=None,
         ease="cubic-bezier(.2,1.25,.4,1)",
         in_="from{transform:translateY(-118%) rotateX(-52deg) scale(.94);opacity:.4}"
             "to{transform:none;opacity:1}"),
    dict(k="swing_in", cn="侧摆",
         in_="from{transform:translateX(-70%) rotate(-10deg) scale(.92);opacity:0}"
             "to{transform:none;opacity:1}",
         out="from{transform:none;opacity:1}"
             "to{transform:translateX(70%) rotate(9deg) scale(.92);opacity:0}"),

    # ---- 形状遮罩 ----
    dict(k="iris_open", cn="圆形开", out=None,
         in_="from{clip-path:circle(0% at 50% 50%)}to{clip-path:circle(78% at 50% 50%)}"),
    dict(k="iris_close", cn="圆形收", under=True, in_=None,
         out="from{clip-path:circle(78% at 50% 50%)}to{clip-path:circle(0% at 50% 50%)}"),
    dict(k="diag_wipe", cn="对角擦", out=None,
         in_="from{clip-path:polygon(0 0,0 0,0 0)}to{clip-path:polygon(0 0,155% 0,0 155%)}"),
    dict(k="split_h", cn="中缝横开", out=None,
         in_="from{clip-path:polygon(50% 0,50% 0,50% 100%,50% 100%)}"
             "to{clip-path:polygon(0 0,100% 0,100% 100%,0 100%)}"),
    dict(k="split_v", cn="中缝竖开", out=None,
         in_="from{clip-path:polygon(0 50%,100% 50%,100% 50%,0 50%)}"
             "to{clip-path:polygon(0 0,100% 0,100% 100%,0 100%)}"),
    dict(k="window_open", cn="小窗展开", out=None,
         in_="from{clip-path:inset(30% 34% 30% 34%);opacity:.6}"
             "to{clip-path:inset(0 0 0 0);opacity:1}"),

    # ---- 模糊 / 缩放 ----
    dict(k="focus_pull", cn="焦点拉",
         in_="from{filter:blur(16px) brightness(1.12);transform:scale(1.12);opacity:.55}"
             "to{filter:blur(0) brightness(1);transform:none;opacity:1}",
         out="from{filter:blur(0)}to{filter:blur(16px)}"),
    dict(k="fade", cn="叠化",
         in_="from{opacity:0}to{opacity:1}", out="from{opacity:1}to{opacity:0}"),
    dict(k="fade_white", cn="白闪",
         in_="from{opacity:0;filter:brightness(2.2)}to{opacity:1;filter:brightness(1)}",
         out="from{opacity:1;filter:brightness(1)}to{opacity:0;filter:brightness(2.6)}"),
    dict(k="fade_black", cn="黑场", out="from{opacity:1}to{opacity:0;filter:brightness(.12)}",
         in_="from{opacity:0}to{opacity:1}"),
    dict(k="zoom_blur", cn="模糊推进",
         in_="from{transform:scale(1.42);filter:blur(16px);opacity:0}"
             "to{transform:none;filter:blur(0);opacity:1}",
         out="from{transform:none;filter:blur(0);opacity:1}"
             "to{transform:scale(.84);filter:blur(12px);opacity:0}"),
    dict(k="whip_blur", cn="甩镜",
         in_="from{transform:translateX(38%) skewX(-8deg);filter:blur(20px);opacity:.3}"
             "to{transform:none;filter:blur(0);opacity:1}",
         out="from{transform:none;filter:blur(0)}"
             "to{transform:translateX(-38%) skewX(8deg);filter:blur(20px);opacity:.3}"),
    dict(k="stretch_x", cn="横拉", out=None,
         in_="from{transform:scaleX(.1) translateX(-45%)}to{transform:none}"),
    dict(k="rotate_in", cn="旋转入",
         in_="from{transform:rotate(-18deg) scale(1.35);opacity:0}"
             "to{transform:none;opacity:1}",
         out="from{transform:none;opacity:1}"
             "to{transform:rotate(14deg) scale(.8);opacity:0}"),

    # ---- 特殊 ----
    dict(k="glitch", cn="故障", out=None, ease="steps(1,end)",
         in_="0%{clip-path:inset(0 0 100% 0);transform:translateX(-8%);filter:contrast(1.5)}"
             "18%{clip-path:inset(0 0 44% 0);transform:translateX(5%)}"
             "36%{clip-path:inset(10% 0 20% 0);transform:translateX(-4%)}"
             "58%{clip-path:inset(0 0 0 0);transform:translateX(3%);filter:contrast(1.2)}"
             "76%{transform:translateX(-1.5%)}"
             "100%{clip-path:inset(0 0 0 0);transform:none;filter:none}"),
    dict(k="flash_zoom", cn="闪推", out=None,
         in_="from{transform:scale(1.6);opacity:0;filter:brightness(2.2)}"
             "to{transform:none;opacity:1;filter:brightness(1)}"),
    dict(k="zoom_out_reveal", cn="拉远",
         in_="from{transform:scale(.7);opacity:0}to{transform:none;opacity:1}",
         out="from{transform:none;opacity:1}to{transform:scale(1.5);opacity:0}"),
]

REG = {t["k"]: t for t in TRANS}
CN = {t["cn"]: t["k"] for t in TRANS}

# 旧名字（ffmpeg xfade 那套）映射到最接近的 CSS 转场，老规格不用改就能跑
ALIAS = {
    "slideleft": "push_left", "smoothleft": "push_left", "slide_right": "push_right",
    "slideright": "push_right", "smoothright": "push_right",
    "slideup": "push_up", "smoothup": "push_up", "slide_up": "push_up",
    "slidedown": "push_down", "smoothdown": "push_down",
    "wipeleft": "cover_left", "wipe_up": "cover_up", "wipeup": "cover_up",
    "circlecrop": "iris_open", "circleopen": "iris_open", "circleclose": "iris_close",
    "fade": "fade", "dissolve": "fade", "distance": "zoom_blur", "smooth": "fade",
    "左移": "push_left", "左滑": "push_left", "推移": "push_left", "左右推移": "push_left",
    "上移": "push_up", "上滑": "push_up", "上下切换": "push_up",
    "叠化": "fade", "淡入淡出": "fade", "圆展": "iris_open", "圆形": "iris_open",
    "擦除": "cover_left", "对角": "diag_wipe", "翻书": "page_turn", "翻牌": "flip_y",
    "立方体": "cube_up", "推进": "zoom_blur", "模糊": "focus_pull", "白闪": "fade_white",
}

# 「自动」轮换顺序：手写一版就够，别每条片子都同一套。按这个顺序取用的转场
# 有远近、有方向、有快慢的区别，不会连着两条都是左右滑。
AUTO_ORDER = ["fade", "push_left", "cube_up", "iris_open", "zoom_blur", "flip_y",
              "cover_up", "diag_wipe", "fade_white", "card_drop", "split_h",
              "whip_blur", "push_up", "page_turn", "focus_pull", "split_v",
              "swing_in", "cover_left", "zoom_out_reveal", "push_right",
              "window_open", "glitch", "stretch_x", "iris_close", "flash_zoom",
              "flip_x", "push_down", "rotate_in", "fade_black", "cube_left"]


def names():
    """所有可用转场：[{k, cn}]，按登记顺序。"""
    return [{"k": t["k"], "cn": t["cn"]} for t in TRANS]


def resolve(name):
    """任意写法（代码名 / 中文名 / 旧 ffmpeg 名）→ 登记表里的代码名。"""
    n = str(name or "").strip()
    if n in REG:
        return n
    if n in CN:
        return CN[n]
    if n in ALIAS:
        return ALIAS[n]
    low = n.lower()
    if low in REG:
        return low
    if low in ALIAS:
        return ALIAS[low]
    raise KeyError("未知转场 %r（可用：%s）" % (name, ", ".join(t["k"] for t in TRANS)))


def auto_list(n, start=0):
    """要 n 个转场时，从 AUTO_ORDER 里顺次取，保证相邻两个不同。"""
    out, i = [], start
    for k in range(n):
        out.append(AUTO_ORDER[i % len(AUTO_ORDER)])
        i += 1
    # 相邻重复就往后挪一格
    for k in range(1, len(out)):
        if out[k] == out[k - 1] and len(AUTO_ORDER) > 1:
            out[k] = AUTO_ORDER[(AUTO_ORDER.index(out[k]) + 1) % len(AUTO_ORDER)]
    return out


# ---------------------------------------------------------------- HTML 生成
def _kf(name, body):
    return "@keyframes %s{%s}" % (name, body)


def reel_html(items, w, h, total, trans, trans_dur=0.5, fps=25, bg=None):
    """生成整条轮播的 HTML（CSS 动画按真实时间播放，不做定格）。

    items 每项 {"path": ..., "zoom": 0.05, "dur": 秒}；dur 缺省时按 total 均分。
    trans 标量或 list（第 k 个管「第 k→k+1 镜」）。
    """
    n = len(items)
    if n == 0:
        raise ValueError("reel_html: items 为空")
    td = float(trans_dur or 0.5)
    durs = [it.get("dur") for it in items]
    if any(d is None for d in durs):
        durs = [max(0.4, float(total) / n)] * n
    durs = [float(d) for d in durs]

    if isinstance(trans, (list, tuple)):
        seq = [resolve(t) for t in trans]
        if len(seq) < n - 1:
            seq += auto_list(n - 1 - len(seq), len(seq))
    else:
        k = resolve(trans) if trans not in (None, "", "auto", "mix") else None
        seq = [k] * (n - 1) if k else auto_list(n - 1)

    # 各镜起始时刻与可见窗口
    starts, acc = [], 0.0
    for i, d in enumerate(durs):
        starts.append(acc)
        acc += d
    total = float(total) or acc

    # 每条边界用哪种转场，决定新图压在上面还是躺在下面
    under = [REG[seq[i]].get("under", False) if i < len(seq) else False for i in range(n)]
    zidx, cur = [], 1
    for i in range(n):
        if i > 0 and under[i - 1]:
            zidx.append(max(1, zidx[i - 1] - 1))     # 新图垫在旧图下面
        else:
            zidx.append(cur)
            cur += 1

    css, divs = [], []
    for i, it in enumerate(items):
        t_in = starts[i]
        win = (starts[i + 1] + td if i + 1 < n else total) - t_in
        src = pathlib.Path(it["path"]).resolve().as_uri()
        # 进入动画（首镜不做）
        if i > 0:
            tr = REG[seq[i - 1]]
            body = tr["in_"]
            if body:
                css.append(_kf("t%s_in" % i, body))
                css.append(".k%d{animation:t%s_in %.3fs %s calc(-1 * var(--T) + %.3fs) both paused%s}"
                           % (i, i, td, tr.get("ease", D), t_in,
                              ";transform-origin:%s" % tr["oi"] if tr.get("oi") else ""))
        # 退出动画
        if i < n - 1:
            tr = REG[seq[i]]
            if tr.get("out"):
                css.append(_kf("t%s_out" % i, tr["out"]))
                css.append(".s%d{animation:t%s_out %.3fs %s calc(-1 * var(--T) + %.3fs) both paused%s}"
                           % (i, i, td, tr.get("ease", D), starts[i + 1],
                              ";transform-origin:%s" % tr["oo"] if tr.get("oo") else ""))
        # 推近（整镜缓慢放大，和转场叠乘，互不干扰）
        zoom = float(it.get("zoom") or 0)
        if zoom > 0:
            css.append(_kf("t%s_zoom" % i, "from{transform:scale(1)}to{transform:scale(%.4f)}"
                           % (1 + zoom)))
            css.append(".z%d{animation:t%s_zoom %.3fs linear calc(-1 * var(--T) + %.3fs) both paused}"
                       % (i, i, max(0.2, win), t_in))
        # 可见窗口闸门：只靠「from 状态是隐藏的」不够稳 —— 多镜叠在一起时，
        # 只要有一镜的 from 仍可见（翻书/圆形收这类），它就会从片头一直挂在那儿。
        # 所以每镜外面再套一层，opacity 在窗口外硬切 0。
        t_end = t_in + win
        if i == 0:
            gk = "0%%{opacity:1}%.4f%%{opacity:1}" % ((t_end / total * 100) - 0.02)
        elif i == n - 1:
            gk = ("0%%{opacity:0}%.4f%%{opacity:0}%.4f%%{opacity:1}100%%{opacity:1}"
                  % ((t_in / total * 100) - 0.02, (t_in / total * 100) + 0.02))
        else:
            gk = ("0%%{opacity:0}%.4f%%{opacity:0}%.4f%%{opacity:1}"
                  "%.4f%%{opacity:1}%.4f%%{opacity:0}100%%{opacity:0}"
                  % (max(0.0, (t_in / total * 100) - 0.02), (t_in / total * 100) + 0.02,
                     (t_end / total * 100) - 0.02, (t_end / total * 100) + 0.02))
        css.append(_kf("gate%d" % i, gk))
        css.append(".g%d{animation:gate%d %.3fs linear calc(-1 * var(--T)) both paused}"
                   % (i, i, total))
        divs.append('<div class="g g%d" style="z-index:%d"><div class="s s%d"><div class="k k%d">'
                    '<div class="z z%d"><img src="%s"></div></div></div></div>'
                    % (i, zidx[i], i, i, i, src))

    return """<!doctype html><html><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:%(w)dpx;height:%(h)dpx;overflow:hidden;background:%(bg)s}
:root{--T:0s}
#stage{position:relative;width:%(w)dpx;height:%(h)dpx;overflow:hidden;
  perspective:1500px;perspective-origin:50%% 50%%;background:%(bg)s}
.g{position:absolute;inset:0;will-change:opacity}
.s{position:absolute;inset:0;transform-style:preserve-3d;backface-visibility:hidden;
  will-change:transform}
.k,.z{position:absolute;inset:0;transform-style:preserve-3d;will-change:transform}
.k{backface-visibility:hidden}
.z{overflow:hidden}
.z img{width:100%%;height:100%%;object-fit:cover;display:block;
  transform-origin:50%% 50%%;backface-visibility:hidden}
%(css)s
</style></head><body><div id="stage">%(body)s</div></body></html>
""" % {"w": w, "h": h, "bg": bg or CFG["bg"], "css": "\n".join(css), "body": "".join(divs)}


# ---------------------------------------------------------------- 渲染成片
def _ffmpeg():
    return CFG["ffmpeg"]


def _ffprobe():
    f = CFG["ffmpeg"]
    if CFG["ffprobe"]:
        return CFG["ffprobe"]
    if f not in ("ffmpeg", ""):
        cand = str(pathlib.Path(f).with_name("ffprobe.exe"))
        if pathlib.Path(cand).is_file():
            return cand
    return "ffprobe"


def duration(p):
    o = subprocess.run([_ffprobe(), "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    try:
        return float((o.stdout or "0").strip())
    except ValueError:
        return 0.0


def reel(items, out, total, w, h, fps=None, trans="auto", trans_dur=0.5,
         tmpdir=None, workers=4, log=True):
    """把多张图 + 30 种 CSS3 转场串成一条 w×h 的无声 mp4（时长精确等于 total）。

    🔴 引擎是「动画暂停 + 负延迟定格」：每条 animation 都是 paused，靠改写
    `--T` 把整页拨到某一时刻再截图。这条路是确定的 —— 逐帧可复现、不会漏帧；
    实时录制（CDP screencast）在 1080 宽 + 30 层合成时会掉到 13fps 甚至画面冻结。

    代价是慢：1080×608 每帧约 0.9 秒，所以默认开 4 路并发（各自独立 Chrome 进程）。
    """
    import shutil
    from concurrent.futures import ThreadPoolExecutor
    ofps = int(fps or CFG["fps"])
    if not CFG["shot"]:
        raise RuntimeError("ml_trans 未注入截帧端：先 configure(shot=...)")
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = pathlib.Path(tmpdir or (out.parent / "_trans"))
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    base = reel_html(items, w, h, total, trans, trans_dur, ofps)
    if log:
        seq = ([resolve(x) for x in trans] if isinstance(trans, (list, tuple))
               else auto_list(len(items) - 1))
        print("[转场] %d 镜 %d 个转场：%s%s" % (len(items), len(seq),
              " → ".join(REG[x]["cn"] for x in seq[:10]), "…" if len(seq) > 10 else ""),
              flush=True)
    nfr = max(1, int(round(float(total) * ofps)))
    gate = "<style>:root{--T:%.4fs}</style>"

    def one(i):
        html = base.replace("</head>", gate % (i / ofps) + "</head>", 1)
        return CFG["shot"](html, "t%05d" % i, tmp, int(w), int(h))

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
        list(ex.map(one, range(nfr)))
    src = tmp / "t%05d.png"
    if not (tmp / "t00000.png").exists():
        raise RuntimeError("截帧失败：%s" % src)
    r = subprocess.run([_ffmpeg(), "-y", "-v", "error", "-framerate", str(ofps),
                        "-i", str(src), "-t", "%.3f" % float(total),
                        "-vf", "scale=%d:%d,setsar=1" % (int(w), int(h)),
                        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                        "-pix_fmt", "yuv420p", "-an", str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not out.exists():
        raise RuntimeError("转场成片编码失败: %s" % (r.stderr or "")[-400:])
    shutil.rmtree(tmp, ignore_errors=True)
    if log:
        print("[转场] -> %s  %.2fs / %d 帧" % (out.name, duration(out), nfr), flush=True)
    return out


def gallery_html(items, w, h, per=1.5, trans_dur=0.55, bg=None, label=True):
    """把全部转场串成一条「效果一览」片：每镜一种转场，左上角标中文名。

    用绝对定位的标签做说明，跟转场同一条时间轴，不用后期加字。
    """
    n = len(TRANS)
    body = reel_html(items, w, h, per * n, [t["k"] for t in TRANS[:n - 1]],
                     trans_dur, 25, bg)
    if not label:
        return body
    tags = []
    for i in range(n - 1):
        tags.append('<div class="tag t%d"><b>%02d</b> %s<small>%s</small></div>'
                    % (i, i + 1, TRANS[i]["cn"], TRANS[i]["k"]))
    css = "".join(".t%d{animation:tagshow%d %.3fs step-end calc(-1 * var(--T) + %.3fs) both paused}"
                  % (i, i, per, i * per) for i in range(n - 1))
    kf = "".join("@keyframes tagshow%d{0%%{opacity:0}1%%{opacity:1}99%%{opacity:1}100%%{opacity:0}}"
                 % i for i in range(n - 1))
    style = ("<style>%s%s.tag{position:absolute;left:26px;top:22px;z-index:9999;"
             "font:700 30px/1.2 'Microsoft YaHei',sans-serif;color:#fff;"
             "background:rgba(0,0,0,.55);padding:8px 16px;border-radius:10px;"
             "letter-spacing:1px;pointer-events:none}"
             ".tag b{color:#FFD84D;margin-right:8px}.tag small{opacity:.55;font-size:18px;"
             "margin-left:10px;font-weight:400}</style>" % (kf, css))
    return body.replace("</head>", style + "</head>").replace(
        '<div id="stage">', '<div id="stage">' + "".join(tags))
