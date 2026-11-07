# -*- coding: utf-8 -*-
"""fxai-slide-video · 配图渲染

画面一律 HTML/CSS 精确绘制 → Chrome headless 截图成 PNG（768×1280 竖屏）。

用法：
    from slides import render, steps_slide, list_slide
    render("S01_开场", title_slide(...))
"""
import subprocess, pathlib, shutil, base64, os, io, zipfile, urllib.request, sys

# ---------- 公共件：混排素材槽（fxai-mixed-layout） ----------
# 槽内网页的 HTML 生成、上中下分栏的壳都在公共件里，这里只做转发 ——
# 换展示形式不该复制一份 CSS，规格只该有一处。
_ML = pathlib.Path(__file__).resolve().parent.parent / "fxai-mixed-layout"
if _ML.is_dir() and str(_ML) not in sys.path:
    sys.path.insert(0, str(_ML))
try:
    import ml_page as _ml_page
except Exception:            # 公共件缺位时 media_page 会显式报错，不静默降级
    _ml_page = None
try:
    import ml_frame
except Exception:
    ml_frame = None
try:
    import ml_split
except Exception:
    ml_split = None
try:
    import ml_trio
except Exception:
    ml_trio = None

# ---------- 动画库（页内元素进场方式，可命名选择；见 references/03-动画与录制.md） ----------
# segments.json 每段 "anim": <名字> | true(默认) | false(静态)。
ANIMS = ("分层弹入", "逐条错峰", "整体淡入", "缩放弹入", "上滑入",
         "左滑入", "右滑入", "下落弹入", "模糊聚焦")
DEFAULT_ANIM = "分层弹入"

# ---------- 路径与显卡能力：统一由 gpu.py 提供（全局检测一次并缓存） ----------
from gpu import global_cfg, chrome_exe, node_exe, ffmpeg_bin, chrome_gpu_args
from gpu import detect as _detect_caps
from gpu import brand as _brand, official_site_url as _official_site_url


def detect_gpu():
    """全局检测：浏览器能否真用显卡（结果进程内缓存，只测一次）。"""
    return bool(_detect_caps()["chrome_gpu"])


# ---------- 画布尺寸（宽高可传参） ----------
# 页面设计阶段按「视频实际宽高」出图：**宽度锁死**，高度允许内容溢出；
# 溢出的部分由「录制阶段」自己量高度并模拟上滚 —— 两阶段解耦，页面侧不写任何滚动代码。
CANVAS = {"w": 768, "h": 1280}

def set_canvas(w=None, h=None):
    """设定画布尺寸（页面/渲染/录制共用）。"""
    if w:
        CANVAS["w"] = int(w)
    if h:
        CANVAS["h"] = int(h)
    return CANVAS["w"], CANVAS["h"]

# ---------- 标题字体：得意黑 Smiley Sans（SIL OFL 1.1，可商用） ----------
# 字体【不随技能包分发】；用户机子缺失时由其官方发布源自动下载（见 ensure_font）。
# 注意：字体由原作者 atelier-anchor 在 GitHub 发布，凤希AI 不托管、不提供下载地址。
_FONT_PATH = pathlib.Path(__file__).parent / "assets" / "fonts" / "SmileySans-Oblique.otf"
_FONT_FACE = ""
_FONT_TRIED = False

# 官方发布源（zip 内含 OTF；如需自建可直连的镜像，可设环境变量 FXAI_FONT_URL 覆盖）
_FONT_URLS = [
    "https://github.com/atelier-anchor/smiley-sans/releases/download/v2.0.1/smiley-sans-v2.0.1.zip",
]

def ensure_font():
    """确保标题字体存在：本地缺失时按官方发布源自动下载（SIL OFL 1.1 可商用）。

    字体由原作者 atelier-anchor 在 GitHub 发布，凤希AI 不托管、不提供下载地址；
    下载失败不致命——退化为系统默认中文字体（仅观感受影响，不影响出片）。
    环境变量 FXAI_FONT_URL 可指定一个可直连的镜像 zip（由使用者自行提供）。
    """
    if _FONT_PATH.exists():
        return True
    _FONT_PATH.parent.mkdir(parents=True, exist_ok=True)
    urls = list(_FONT_URLS)
    env = os.environ.get("FXAI_FONT_URL")
    if env:
        urls.insert(0, env)
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=90) as resp:
                if _save_font_from_zip(resp.read()):
                    return True
        except Exception:
            continue
    return False

def _save_font_from_zip(data: bytes) -> bool:
    """从字体发布 zip 中取出 OTF 落到 _FONT_PATH。返回是否成功。"""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        otf = next((n for n in z.namelist() if n.lower().endswith(".otf")), None)
        if not otf:
            return False
        _FONT_PATH.write_bytes(z.read(otf))
        return _FONT_PATH.exists() and _FONT_PATH.stat().st_size > 100_000
    except Exception:
        return False

def _font_face_css():
    """标题字体 @font-face。base64 内嵌，避开 file:// 跨源加载字体失败。"""
    global _FONT_FACE, _FONT_TRIED
    if _FONT_FACE:                        # 已成功内嵌，直接返回
        return _FONT_FACE
    if not _FONT_TRIED and not _FONT_PATH.exists():
        _FONT_TRIED = True
        ensure_font()                     # 缺失则自动下载（仅尝试一次）
    if _FONT_PATH.exists():
        b64 = base64.b64encode(_FONT_PATH.read_bytes()).decode("ascii")
        _FONT_FACE = ('@font-face{font-family:"Smiley Sans";'
                      'src:url(data:font/otf;base64,' + b64 + ') format("opentype");'
                      'font-display:block}')
    return _FONT_FACE

# ---------- 样式集（主题）：一套样式 = 一个 CSS 文件，加载即用 ----------
# 基座 CSS 只留结构与变量占位（var(--ink) 这样），配色/圆角/阴影/描边全交给主题文件。
# 主题文件：技能目录/themes/<样式名>.css，内容 = :root{变量表} + 可选附加样式（能覆盖任意细节）。
# 选样式：slides.set_theme("樱花粉")，或命令行 --theme 樱花粉 / --theme-file 路径.css。
# 变量表、选样式的行业对应、新增样式的规则 → references/07-样式集.md
THEME_DIR = pathlib.Path(__file__).parent / "themes"
DEFAULT_THEME = "深夜蓝金"
_current_theme = {"name": DEFAULT_THEME, "css": ""}

# 兜底：themes/ 缺失时按技能原始配色（深夜蓝金）出图，不因缺文件而空白
_FALLBACK_THEME_CSS = """
:root{--bg1:#0f2740;--bg2:#08172a;--bg3:#040a14;
--ink:#eaf1fb;--ink2:#9fb4d4;--ink3:#7fb0e8;
--ac:#ffb020;--ac2:#ff6b35;--acsoft:#ffd479;--ac3:#2fe6a0;--ac4:#4aa8ff;
--ac5:#ff6b9d;--ac6:#c08cff;--ac3soft:#173a2e;
--onac:#0a1626;--bad:#ff6b6b;
--acrgb:255,176,32;--ac2rgb:255,107,53;--ac3rgb:47,230,160;--badrgb:255,107,107;
--panel:#16233b;--panelrgb:30,70,120;--panel2rgb:12,26,48;--onpanel:#fff;--onpanel2:#c7d6ee;
--card:#ffffff;--cardink:#16233b;
--linergb:120,170,230;--chiprgb:255,255,255;--shadowrgb:0,0,0;
--bubble:#f2f5fa;--bubbleink:#16233b}
"""


def list_themes():
    """可用样式名（themes/ 下所有 .css 的文件名，_ 开头的不算）。"""
    if not THEME_DIR.is_dir():
        return []
    return sorted(p.stem for p in THEME_DIR.glob("*.css") if not p.name.startswith("_"))


def set_theme(name=None, path=None):
    """加载样式：name → themes/<name>.css；path → 任意路径的 .css。返回样式名。"""
    if path:
        p = pathlib.Path(path)
        if not p.is_file():
            raise SystemExit("样式文件不存在：%s" % p)
        _current_theme["name"] = p.stem
        _current_theme["css"] = p.read_text(encoding="utf-8")
        return _current_theme["name"]
    if not name or name == DEFAULT_THEME:
        _current_theme["name"] = DEFAULT_THEME
        _current_theme["css"] = ""
        return DEFAULT_THEME
    p = THEME_DIR / ("%s.css" % name)
    if not p.is_file():
        raise SystemExit("未找到样式 %r（可用：%s）" % (name, "、".join(list_themes()) or "无"))
    _current_theme["name"] = name
    _current_theme["css"] = p.read_text(encoding="utf-8")
    return name


def current_theme():
    """当前样式名。"""
    return _current_theme["name"]


def theme_css():
    """当前样式的 CSS（拼在基座之后：变量与附加样式都在这一层生效，优先级最高）。"""
    return _current_theme["css"] or _FALLBACK_THEME_CSS


def _canvas_css(css, w=None, h=None):
    """把 CSS 里的 __W__ / __H__ 占位替换成实际画布尺寸，并注入动态缩放变量。

    注入 :root{--s,--w,--h}：
      --s = min(w/768, h/1280)  —— 设计基准缩放因子，用于「固定尺寸元素到底/左侧」的动态定位；
      --w/--h = 实际像素，供需要绝对像素的样式取用。
    竖版/横版共用，页面侧用 vh/vw + --s 实现自适应布局（宽度用 %，不写 max-width）。
    末尾拼当前样式 CSS（见 set_theme）。
    """
    w = int(w or CANVAS["w"]); h = int(h or CANVAS["h"])
    s = min(w / 768.0, h / 1280.0)
    inject = f":root{{--s:{s:.4f};--w:{w}px;--h:{h}px}}\n"
    return (inject + _font_face_css()
            + css.replace("__W__", str(w)).replace("__H__", str(h))
            + theme_css())


# ---------- 风格基座（浅色底教程风，暖橙 + 深墨蓝） ----------
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:__W__px;height:__H__px;overflow:hidden}
body{font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;
  background:linear-gradient(160deg,var(--bg1) 0%,var(--bg2) 100%);color:var(--panel);
  padding:70px 56px 130px;display:flex;flex-direction:column}
.brand{font-size:25px;letter-spacing:3px;color:var(--ink3);font-weight:700;margin-bottom:16px;flex:none}
h1{font-family:"Smiley Sans","Microsoft YaHei",sans-serif;font-size:66px;line-height:1.16;font-weight:400;letter-spacing:1px;flex:none}
h1 .hl{color:var(--ac2)}
.sub{font-size:28px;color:var(--ink3);margin-top:18px;line-height:1.5;flex:none}
.card{background:var(--card);border-radius:24px;box-shadow:0 14px 40px rgba(var(--shadowrgb),.10)}
.foot{margin-top:auto;background:var(--panel);color:var(--onpanel);border-radius:24px;
  padding:34px 40px;font-size:34px;font-weight:700;line-height:1.4;flex:none}
.foot b{color:var(--acsoft)}
"""

def page(inner, w=None, h=None):
    return ('<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            f'<style>{_canvas_css(CSS, w, h)}</style></head><body>{inner}</body></html>')

def head(title, sub="", brand="凤希AI 自动化 · 教程"):
    return (f'<div class="brand">{brand}</div><h1>{title}</h1>'
            + (f'<div class="sub">{sub}</div>' if sub else ""))

# ---------- 版式 1：步骤逐步展开（已完成打勾 / 当前高亮 / 后续置灰） ----------
def steps_slide(steps, cur, title=None, sub=None, brand="凤希AI 自动化 · 教程"):
    """steps: [(标题, 说明), ...]；cur: 当前第几步（1 起）"""
    rows = []
    for i, (t, d) in enumerate(steps, 1):
        if i < cur:
            bg, fg, ic, tc, dc = "var(--ac3soft)", "var(--ac3)", "&#10003;", "var(--ink3)", "var(--ink3)"
        elif i == cur:
            bg, fg, ic, tc, dc = "var(--panel)", "var(--acsoft)", str(i), "var(--onpanel)", "var(--onpanel2)"
        else:
            bg, fg, ic, tc, dc = "var(--card)", "var(--ink3)", str(i), "var(--ink3)", "var(--ink3)"
        pad = "34px 36px" if i == cur else "26px 32px"
        sz = 72 if i == cur else 58
        fs = 38 if i == cur else 31
        dfs = 26 if i == cur else 24
        rows.append(
            f'<div style="display:flex;align-items:center;gap:24px;background:{bg};border-radius:22px;'
            f'padding:{pad};margin-bottom:18px">'
            f'<div style="width:{sz}px;height:{sz}px;border-radius:50%;flex:none;background:{fg};'
            f'color:var(--onac);font-size:{sz//2 + 2}px;font-weight:800;display:flex;align-items:center;'
            f'justify-content:center">{ic}</div>'
            f'<div><div style="font-size:{fs}px;font-weight:700;color:{tc};line-height:1.25">{t}</div>'
            f'<div style="font-size:{dfs}px;color:{dc};margin-top:6px">{d}</div></div></div>')
    return page(head(title or f'第 <span class="hl">{cur}</span> 步',
                     sub or f"一共 {len(steps)} 步，跟着走就行", brand)
                + '<div style="margin-top:44px;flex:1">' + "".join(rows) + '</div>')

# ---------- 版式 2：清单逐项点亮 ----------
def list_slide(items, cur, title, sub=None, brand="凤希AI 自动化 · 教程"):
    """items: [(图标, 标题, 说明), ...]；cur: 当前第几项（1 起）"""
    rows = []
    for i, (ic, t, d) in enumerate(items, 1):
        hi = (i == cur)
        bg = "var(--panel)" if hi else ("var(--ac3soft)" if i < cur else "var(--card)")
        tc = "var(--onpanel)" if hi else ("var(--ink3)" if i < cur else "var(--ink3)")
        mark = "&#10003;" if i < cur else str(i)
        fgc = "var(--ac3)" if i < cur else ("var(--acsoft)" if hi else "var(--ink3)")
        rows.append(
            f'<div style="display:flex;align-items:center;gap:24px;background:{bg};border-radius:24px;'
            f'padding:{"38px 36px" if hi else "28px 32px"};margin-bottom:20px">'
            f'<div style="width:{70 if hi else 56}px;height:{70 if hi else 56}px;border-radius:50%;'
            f'flex:none;background:{fgc};color:var(--onac);font-size:{36 if hi else 28}px;font-weight:800;'
            f'display:flex;align-items:center;justify-content:center">{mark}</div>'
            f'<div style="flex:1"><div style="font-size:{38 if hi else 31}px;font-weight:700;color:{tc}">{t}</div>'
            f'<div style="font-size:{26 if hi else 24}px;color:{"var(--onpanel2)" if hi else "var(--ink3)"};margin-top:6px">{d}</div></div>'
            f'<div style="font-size:{44 if hi else 36}px">{ic}</div></div>')
    return page(head(title, sub or f"第 {cur} 项 / 共 {len(items)} 项", brand)
                + '<div style="margin-top:40px;flex:1">' + "".join(rows) + '</div>')

# ---------- 版式 3：并排对比卡 ----------
def compare_slide(cards, title, sub=None, foot=None, brand="凤希AI 自动化 · 教程"):
    """cards: [(图标, 标题, 说明, 是否高亮), ...]（2~3 张）"""
    boxes = []
    for ic, t, d, hi in cards:
        bg = "var(--panel)" if hi else "var(--card)"
        tc = "var(--onpanel)" if hi else "var(--cardink)"
        dc = "var(--onpanel2)" if hi else "var(--ink3)"
        boxes.append(
            f'<div style="flex:1;background:{bg};border-radius:26px;padding:40px 30px;'
            f'box-shadow:0 14px 40px rgba(var(--shadowrgb),.10)">'
            f'<div style="font-size:52px;margin-bottom:14px">{ic}</div>'
            f'<div style="font-size:36px;font-weight:800;color:{tc};line-height:1.3">{t}</div>'
            f'<div style="font-size:26px;color:{dc};margin-top:14px;line-height:1.6">{d}</div></div>')
    inner = head(title, sub or "", brand) + '<div style="margin-top:56px;display:flex;gap:22px">' + "".join(boxes) + '</div>'
    if foot:
        inner += f'<div class="foot">{foot}</div>'
    return page(inner)

# ---------- 版式 4：要点卡（标题 + 若干行要点） ----------
def points_slide(points, title, sub=None, foot=None, brand="凤希AI 自动化 · 教程", color="var(--ac2)"):
    rows = "".join(
        f'<div style="display:flex;align-items:center;gap:22px;font-size:36px;font-weight:600;'
        f'margin-bottom:26px"><span style="width:18px;height:18px;border-radius:50%;'
        f'background:{color};flex:none"></span>{p}</div>' for p in points)
    inner = head(title, sub or "", brand) + f'<div style="margin-top:52px">{rows}</div>'
    if foot:
        inner += f'<div class="foot">{foot}</div>'
    return page(inner)

# ---------- 版式 5：终端 / 命令卡 ----------
def code_slide(code, title, sub=None, foot=None, brand="凤希AI 自动化 · 教程", note=None):
    inner = (head(title, sub or "", brand)
             + f'<div style="margin-top:46px;background:var(--panel);border-radius:22px;padding:38px 40px;'
               f'color:var(--onpanel2);font-family:"Cascadia Mono","Consolas",monospace;font-size:25px;'
               f'line-height:1.7;white-space:pre-wrap;word-break:break-all">{code}</div>')
    if note:
        inner += (f'<div style="margin-top:44px;font-size:32px;font-weight:600;color:var(--ink);'
                  f'line-height:1.6">{note}</div>')
    if foot:
        inner += f'<div class="foot">{foot}</div>'
    return page(inner)

# ---------- 版式 6：深色对话 / 引述卡 ----------
def quote_slide(text, tail, title, sub=None, brand="凤希AI 自动化 · 教程"):
    inner = (head(title, sub or "", brand)
             + '<div style="margin-top:50px;background:var(--panel);border-radius:26px;padding:44px 42px;'
               'box-shadow:0 18px 44px rgba(var(--shadowrgb),.28)">'
               '<div style="font-size:25px;color:var(--ink3);font-weight:700;letter-spacing:2px;'
               'margin-bottom:18px">你可以这样说</div>'
               f'<div style="font-size:42px;font-weight:700;color:var(--onpanel);line-height:1.5">{text}</div>'
               f'<div style="font-size:28px;color:var(--acsoft);margin-top:22px;font-weight:700">{tail}</div></div>')
    return page(inner)

# ---------- 版式 7：生图做主视觉的商品展示（满屏图 + 底部紧凑暗卡） ----------
def showcase_slide(image_path, title, points=None, sub=None, brand="凤希AI · 服饰严选", color="var(--ac2)"):
    """image_path: 生成的服装/商品图（绝对路径，base64 内嵌）。
    服装/商品图本身是主角，画面要清晰不被蒙板吃掉：满屏铺图，不叠全幅渐变；
    标题/要点放到**底部一张紧凑的半透暗卡**（离底 180px，给烧录字幕让位），上半图完全裸露。"""
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        src = "data:image/png;base64," + b64
    except Exception:
        src = ""
    bullets = "".join(
        f'<div style="display:flex;align-items:center;gap:14px;font-size:27px;font-weight:600;'
        f'margin-bottom:12px;color:var(--onpanel2)"><span style="width:12px;height:12px;border-radius:50%;'
        f'background:{color};flex:none"></span>{p}</div>' for p in (points or []))
    bg = (f'<div style="position:fixed;inset:0;background-image:url({src});'
          f'background-size:cover;background-position:center center"></div>')
    card = (
        f'<div style="position:fixed;left:5%;right:5%;bottom:180px;'
        f'background:rgba(var(--panel2rgb),.78);border-radius:26px;padding:26px 30px;'
        f'box-shadow:0 12px 32px rgba(var(--shadowrgb),.35)">'
        f'<div style="font-size:21px;color:var(--onpanel2);letter-spacing:2px;font-weight:700;'
        f'margin-bottom:8px">{brand}</div>'
        f'<div style="font-size:42px;color:var(--onpanel);font-weight:800;line-height:1.2;'
        f'margin-bottom:6px">{title}</div>'
        + (f'<div style="font-size:23px;color:var(--onpanel2);margin-bottom:16px">{sub}</div>' if sub else "")
        + bullets + '</div>')
    return page(bg + card)

# ---------- 版式 8：营销 / 花字风（深色底 + 大标题高亮 + 章节胶囊 + 编号 + 胶囊 + 图标流程 + UI模拟） ----------
# 复刻「深色底 + 大花字 + 图标流程 + 软件UI截图」的爆款图文口播风。动画（fade-through-black 换页）
# 由 sv_media 的 --fade 在拼接阶段实现（换页淡出到近黑再淡入）。
MARKETING_CSS = """
@keyframes mkUp{from{opacity:0;translate:0 46px}to{opacity:1;translate:0 0}}
@keyframes mkUpBig{from{opacity:0;translate:0 130px}to{opacity:1;translate:0 0}}
@keyframes mkPop{0%{opacity:0;scale:.72}62%{opacity:1;scale:1.06}100%{opacity:1;scale:1}}
@keyframes mkZoom{from{opacity:0;scale:.8}to{opacity:1;scale:1}}
@keyframes mkFade{from{opacity:0}to{opacity:1}}
@keyframes mkLeft{from{opacity:0;translate:-96px 0}to{opacity:1;translate:0 0}}
@keyframes mkRight{from{opacity:0;translate:96px 0}to{opacity:1;translate:0 0}}
@keyframes mkDrop{from{opacity:0;translate:0 -76px}to{opacity:1;translate:0 0}}
@keyframes mkBlur{from{opacity:0;filter:blur(16px)}to{opacity:1;filter:blur(0)}}
:root{--T:3s}
.mk-a,.mk-p{animation-duration:.5s;animation-fill-mode:both;animation-play-state:paused;
  animation-delay:calc(var(--d,0s)*var(--step,1) - var(--T))}
.mk-a{animation-name:mkUp}
.mk-p{animation-name:mkPop}
/* —— 动画库：body[data-anim="<动画名>"] 切换；--step 控制错峰强度（0=整页一起动）—— */
body[data-anim="整体淡入"]{--step:0}
body[data-anim="整体淡入"] .mk-a,body[data-anim="整体淡入"] .mk-p{animation-name:mkFade;animation-duration:.72s}
body[data-anim="缩放弹入"] .mk-a,body[data-anim="缩放弹入"] .mk-p{animation-name:mkZoom;animation-duration:.55s}
body[data-anim="上滑入"] .mk-a,body[data-anim="上滑入"] .mk-p{animation-name:mkUpBig;animation-duration:.6s}
body[data-anim="左滑入"] .mk-a,body[data-anim="左滑入"] .mk-p{animation-name:mkLeft;animation-duration:.55s}
body[data-anim="右滑入"] .mk-a,body[data-anim="右滑入"] .mk-p{animation-name:mkRight;animation-duration:.55s}
body[data-anim="下落弹入"] .mk-a,body[data-anim="下落弹入"] .mk-p{animation-name:mkDrop;animation-duration:.55s}
body[data-anim="模糊聚焦"] .mk-a,body[data-anim="模糊聚焦"] .mk-p{animation-name:mkBlur;animation-duration:.72s}
body[data-anim="逐条错峰"]{--step:1.5}
body[data-anim="分层弹入"]{--step:1}
.mk-brand{--d:0s}.mk-chapter{--d:.08s}.mk-badge{--d:.16s}.mk-title{--d:.22s}
.mk-sub{--d:.30s}.mk-chips{--d:.38s}.mk-flow{--d:.46s}.mk-mock{--d:.46s}
.mk-play{--d:.56s}.mk-hint{--d:.64s}
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:__W__px;height:__H__px;overflow:hidden}
body{font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;
  background:radial-gradient(120% 80% at 50% 24%,var(--bg1) 0%,var(--bg2) 46%,var(--bg3) 100%);
  color:var(--ink);position:relative}
.mk-brand{position:fixed;top:44px;left:50%;transform:translateX(-50%);display:flex;align-items:center;
  gap:12px;padding:12px 28px;border-radius:999px;border:1px solid rgba(var(--linergb),.35);
  background:rgba(var(--panelrgb),.28);font-size:26px;font-weight:700;color:var(--ink2)}
.mk-brand .dot{width:14px;height:14px;border-radius:50%;background:var(--ac);box-shadow:0 0 12px var(--ac)}
.mk-chapter{position:fixed;top:150px;left:56px;padding:10px 22px;border-radius:999px;
  border:1px solid rgba(var(--linergb),.30);background:rgba(var(--panelrgb),.55);font-size:24px;
  font-weight:700;color:var(--ink2);letter-spacing:1px}
.mk-wrap{position:absolute;left:56px;right:56px;top:300px;text-align:center}
.mk-frame{position:absolute;left:6%;right:6%;top:50%;transform:translateY(-50%);
  background:rgba(var(--panel2rgb),.55);border:1.5px solid rgba(var(--linergb),.36);border-radius:38px;
  padding:58px 42px;box-shadow:0 24px 64px rgba(var(--shadowrgb),.46)}
.mk-frame .mk-wrap{position:static;left:auto;right:auto;top:auto;text-align:center}
.mk-badge{display:inline-block;padding:12px 32px;border-radius:999px;border:1.5px solid rgba(var(--linergb),.5);
  background:rgba(var(--panelrgb),.5);font-size:30px;font-weight:800;color:var(--ink2);margin-bottom:34px}
.mk-title{font-family:"Smiley Sans","Microsoft YaHei",sans-serif;font-size:58px;line-height:1.26;font-weight:400;letter-spacing:1px;color:var(--ink)}
.mk-title .hl{color:var(--ac)}.mk-title .hl2{color:var(--ac3)}.mk-title .hl3{color:var(--ac4)}
.mk-sub{font-size:32px;color:var(--ink2);margin-top:24px;font-weight:600;line-height:1.5}
.mk-chips{display:flex;gap:16px;justify-content:center;flex-wrap:wrap;margin-top:44px}
.mk-chip{padding:14px 30px;border-radius:999px;border:1.5px solid rgba(var(--linergb),.4);
  background:rgba(var(--chiprgb),.05);font-size:28px;font-weight:700;color:var(--ink2)}
.mk-chip .c1{color:var(--ac)}.mk-chip .c2{color:var(--ac3)}.mk-chip .c3{color:var(--ac4)}
.mk-play{margin:54px auto 0;width:130px;height:130px;border-radius:50%;border:3px solid rgba(var(--acrgb),.85);
  display:flex;align-items:center;justify-content:center;font-size:54px;color:var(--ac);
  background:rgba(var(--acrgb),.12);box-shadow:0 0 44px rgba(var(--acrgb),.35)}
.mk-hint{margin-top:28px;font-size:28px;color:var(--ink2);font-weight:700;letter-spacing:2px}
.mk-tag{position:fixed;top:44px;right:56px;padding:10px 24px;border-radius:999px;
  background:linear-gradient(135deg,var(--ac),var(--ac2));color:var(--onac);font-size:24px;font-weight:800;letter-spacing:1px}
.mk-hook{font-size:36px;color:var(--acsoft);margin-top:18px;font-weight:800;letter-spacing:1px}
.mk-media{margin:40px auto 0;width:74%;border-radius:22px;overflow:hidden;
  border:1.5px solid rgba(var(--linergb),.38);box-shadow:0 18px 44px rgba(var(--shadowrgb),.45)}
.mk-media img{width:100%;display:block}
.mk-flow{display:flex;align-items:center;justify-content:center;gap:12px;margin-top:54px;flex-wrap:wrap}
.mk-node{width:118px;padding:22px 6px;border-radius:20px;border:1.5px solid rgba(var(--linergb),.4);
  background:linear-gradient(180deg,rgba(var(--panelrgb),.5),rgba(var(--panel2rgb),.6));text-align:center}
.mk-node .ic{font-size:44px;margin-bottom:10px}
.mk-node .tx{font-size:26px;font-weight:800;color:var(--ink)}
.mk-arrow{font-size:30px;color:var(--ac);font-weight:900}
.mk-mock{margin:56px auto 0;width:80%;border-radius:24px;border:1.5px solid rgba(var(--linergb),.35);
  background:linear-gradient(180deg,rgba(var(--panelrgb),.92),rgba(var(--panel2rgb),.92));padding:30px 34px;text-align:left}
.mk-mock .h{font-size:26px;color:var(--ink3);font-weight:800;margin-bottom:16px}
.mk-mock .r{font-size:26px;color:var(--ink2);padding:13px 0;border-bottom:1px solid rgba(var(--linergb),.14)}
/* —— 序列步骤条（fields.steps）：每行带 class="seq"，录制时一个个出现（等上一个播完再下一个）—— */
.mk-steps{margin-top:44px;display:flex;flex-direction:column;gap:20px;text-align:left}
.mk-step{display:flex;align-items:center;gap:22px;padding:22px 28px;border-radius:22px;
  border:1.5px solid rgba(var(--linergb),.34);
  background:linear-gradient(180deg,rgba(var(--panelrgb),.62),rgba(var(--panel2rgb),.62))}
.mk-step .no{flex:none;width:74px;height:74px;border-radius:20px;display:flex;align-items:center;
  justify-content:center;font-size:36px;font-weight:900;color:var(--onac)}
.mk-step .tt{font-size:36px;font-weight:900;color:var(--ink);line-height:1.25}
.mk-step .dd{font-size:25px;color:var(--ink2);margin-top:6px}
.mk-steps.tight{margin-top:28px;gap:13px}
.mk-steps.tight .mk-step{padding:15px 26px}
/* —— 穿搭 / 商品展示（outfit 模板）：人物图满屏裸露 + 底部暗卡列单品（遵守铁律 10 不蒙板）—— */
.ot-card{position:fixed;left:5%;right:5%;bottom:150px;background:rgba(var(--panel2rgb),.80);
  border:1.5px solid rgba(var(--linergb),.28);border-radius:28px;padding:28px 32px;
  box-shadow:0 18px 46px rgba(var(--shadowrgb),.5)}
.ot-head{display:flex;align-items:baseline;gap:16px}
.ot-title{font-family:"Smiley Sans","Microsoft YaHei",sans-serif;font-size:40px;font-weight:400;color:var(--ink);line-height:1.2}
.ot-price{margin-left:auto;font-size:32px;font-weight:900;color:var(--ac);white-space:nowrap}
.ot-sub{font-size:26px;color:var(--ink2);font-weight:700;margin-top:8px}
.ot-items{display:flex;flex-direction:column;gap:12px;margin-top:20px}
.ot-items.two{display:grid;grid-template-columns:1fr 1fr;gap:12px 26px}
.ot-items.two .ot-item .k{min-width:78px;font-size:23px}
.ot-items.two .ot-item .n{font-size:25px}
.ot-item{display:flex;align-items:center;gap:14px;font-size:27px;color:var(--ink)}
.ot-item .k{min-width:92px;font-size:24px;font-weight:800;color:var(--ink3)}
.ot-item .dot{width:11px;height:11px;border-radius:50%;background:var(--ac);box-shadow:0 0 10px var(--ac);flex:0 0 auto}
.ot-item .n{font-weight:700}
.ot-chips{display:flex;gap:12px;flex-wrap:wrap;margin-top:22px}
.ot-chips .mk-chip{font-size:25px;padding:11px 24px}
.ot-tagside{position:fixed;right:44px;top:210px;display:flex;flex-direction:column;gap:16px;align-items:flex-end}
.ot-tagside .t{padding:10px 20px;border-radius:999px;border:1px solid rgba(var(--linergb),.4);
  background:rgba(var(--panel2rgb),.62);font-size:23px;font-weight:800;color:var(--ink2)}
/* —— 商品展示（product 模板）：商品图居中「完整显示不裁切」（contain）+ 底部暗卡列规格 —— */
.pd-shot{position:fixed;left:5%;right:5%;top:212px;bottom:508px;border-radius:30px;
  border:1.5px solid rgba(var(--linergb),.30);background-color:rgba(var(--chiprgb),.05);
  background-size:contain;background-repeat:no-repeat;background-position:center;
  box-shadow:0 22px 54px rgba(var(--shadowrgb),.5)}
.pd-shot::after{content:"";position:absolute;inset:0;border-radius:30px;
  background:radial-gradient(78% 58% at 50% 42%,rgba(var(--linergb),.10),transparent 72%)}
.pd-tagside{position:fixed;right:5%;top:232px;display:flex;flex-direction:column;gap:14px;align-items:flex-end;z-index:3}
.pd-tagside .t{padding:10px 20px;border-radius:999px;border:1px solid rgba(var(--linergb),.42);
  background:rgba(var(--panel2rgb),.70);font-size:23px;font-weight:800;color:var(--ink2)}
/* ================= 动效扩展库（CSS 关键帧 + 工具类） ================= */
@keyframes mkSweep{from{background-size:0% 100%}to{background-size:100% 100%}}       /* 高亮从左扫到右 */
@keyframes mkLine{from{scale:0 1}to{scale:1 1}}                                       /* 下划线/分隔线生长 */
@keyframes mkPulse{0%,100%{scale:1}45%{scale:1.12}}                                   /* 强调脉冲 */
@keyframes mkBreath{0%,100%{opacity:.55}50%{opacity:1}}                               /* 呼吸（弱→强） */
@keyframes mkSpin{from{opacity:0;rotate:-14deg;scale:.86}to{opacity:1;rotate:0;scale:1}} /* 旋转入场 */
@keyframes mkFlash{0%{opacity:0;translate:0 10px}100%{opacity:1;translate:0 0}}        /* 内容刷新（换行） */
@keyframes mkChar{from{opacity:0;translate:0 30px}to{opacity:1;translate:0 0}}         /* 逐字上浮 */
/* 工具类：加到任意元素上即生效（与入场动画叠加，用 animation 复合写法） */
.fx-pulse{animation:mkPulse .8s ease-in-out 2 both}
.fx-breath{animation:mkBreath 1.4s ease-in-out infinite both}
.fx-spin{animation-name:mkSpin!important}
/* 高亮扫过：给行内关键字上底色，宽度从 0 扫到满（配合序列揭示最出彩） */
.fx-sweep{background-image:linear-gradient(90deg,rgba(var(--acrgb),.42),rgba(var(--acrgb),.42));
  background-repeat:no-repeat;background-position:0 62%;background-size:100% 62%}
html.js-seq .fx-sweep{background-size:0% 62%}
.fx-sweep.on,.seq.fx-sweep.on{animation:mkSweep .5s .06s both}
/* 下划线生长 */
.fx-underline{position:relative}
.fx-underline::after{content:"";position:absolute;left:0;right:0;bottom:-8px;height:5px;border-radius:3px;
  background:linear-gradient(90deg,var(--ac),var(--ac2));transform-origin:left center;scale:1 1}
html.js-seq .fx-underline::after{scale:0 1}
.fx-underline.on::after{animation:mkLine .5s .1s both}
/* ================= 序列揭示（seq）：元素一个个出现，「等上一个播完再播下一个」 =================
   用法：给要依次出现的元素加 class="seq" 和 data-seq="1/2/3…"（序号小的先出）。
   仅「录制」引擎有效（--rec）：由 JS 按顺序逐个加 .on，等前一个动画结束再放下一个。
   逐帧定格引擎请改用 --d 步进法（见 references/03-动画与录制.md「页面动效设计规范」）。 */
html.js-seq .seq{opacity:0}
.seq.on{animation-name:mkUp;animation-duration:.5s;animation-fill-mode:both}
.seq[data-fx="pop"].on{animation-name:mkPop}
.seq[data-fx="zoom"].on{animation-name:mkZoom}
.seq[data-fx="left"].on{animation-name:mkLeft}
.seq[data-fx="right"].on{animation-name:mkRight}
.seq[data-fx="drop"].on{animation-name:mkDrop}
.seq[data-fx="blur"].on{animation-name:mkBlur}
.seq[data-fx="spin"].on{animation-name:mkSpin}
.seq[data-fx="fade"].on{animation-name:mkFade}
.seq[data-fx="char"].on{animation-name:mkChar;animation-duration:.42s}
/* 数字滚动 / 内容刷新：元素文本被 JS 改写时用的刷新动画 */
.fx-flash.on{animation:mkFlash .34s both}
"""

# 序列揭示引擎：按 data-seq 顺序逐个 .on；默认等前一个动画播完再放下一个（waitEnd）。
# 由 record_anim 在「录制」时注入页面（逐帧定格不注入 → 元素保持可见，不影响静态出图）。
SEQ_JS = """
(function(){
  var els=[].slice.call(document.querySelectorAll('.seq'));
  if(!els.length) return;
  document.documentElement.classList.add('js-seq');
  els.sort(function(a,b){return (+a.dataset.seq||0)-(+b.dataset.seq||0);});
  var b=document.body, step=+(b.dataset.seqStep||360), gap=+(b.dataset.seqGap||40),
      waitEnd=(b.dataset.seqWait||'anim')!=='fixed';
  window.__fxSeqBusy = true;      // 序列是否还没播完（供页面自查，不阻塞滚动）
  function next(i){
    if(i>=els.length){ window.__fxSeqBusy = false; return; }
    var el=els[i]; el.classList.add('on');
    var d=0;
    if(waitEnd){ var cs=getComputedStyle(el); d=(parseFloat(cs.animationDuration)||.5)*1000; }
    setTimeout(function(){ next(i+1); }, Math.max(step,d)+gap);
  }
  setTimeout(function(){ next(0); }, +(b.dataset.seqDelay||120));
})();
"""

def marketing_page(inner, w=None, h=None, extra=""):
    """深色营销版式页面壳。

    extra：追加在 MARKETING_CSS 之后的补充样式（新版式专用 CSS 走这里，不污染基座）。
    """
    return ('<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            f'<style>{_canvas_css(MARKETING_CSS + extra, w, h)}</style></head><body>{inner}</body></html>')

def site_bar(text=None, bottom=180, color=None, dot=True, font_size=20):
    """**底部品牌引导条**：在视频页面合适位置（默认 bottom=180，字幕烧录区 + 正文安全区上方）展示
    「官网 + QQ群 + 下载引导」一行。

    ⚠️ **视频是展示/引导用，不指望观众点链接**：链接不可点，只是把官网地址和去哪下载
    **显示**出来。教程类 / 数字人 / 软件下载引导等场景特别合适。

    参数：
      text      自定义文本（默认按全局 brand 自动拼：'官网 www.fxai.site · QQ群 775649071 · 网盘获取安装包'）
      bottom    距底像素（默认 180，给步骤条/正文 + 烧录字幕各留出安全距离；想更靠底可改 140）
      color     文字颜色（默认 var(--ink2)，冷色与 marketing 暗底协调）
      dot       是否显示左侧品牌橙点（默认 True）
      font_size 字号（默认 20，紧凑一行；标题多的页面也可降到 18）

    返回一段独立 HTML，可直接 append 到 marketing_page / page 的 inner 末尾，或
    作为 `fields.site_bar=True` 传进各 slide（marketing/outfit/product/showcase 都识别）。

    从全局 `fxai_global.json` 的 brand 块读取品牌信息；未配则用凤希AI 默认值。
    """
    b = _brand()
    if text is None:
        text = f'官网 {b["official_site"]} · QQ群 {b["qq_group"]} · 网盘获取安装包'
    if color is None:
        color = "var(--ink2)"
    dot_html = ('<span style="display:inline-block;width:9px;height:9px;'
                'border-radius:50%;background:var(--ac);'
                'box-shadow:0 0 6px var(--ac);margin-right:8px;flex:none"></span>') if dot else ""
    return (
        f'<div class="site-bar" style="position:fixed;left:5%;right:5%;bottom:{int(bottom)}px;'
        f'display:flex;justify-content:center;align-items:center;padding:10px 20px;'
        f'border-radius:999px;background:rgba(var(--panel2rgb),.78);'
        f'border:1px solid rgba(var(--linergb),.32);'
        f'color:{color};font-size:{int(font_size)}px;font-weight:600;letter-spacing:.3px;'
        f'box-shadow:0 6px 16px rgba(var(--shadowrgb),.3);z-index:5;'
        f'backdrop-filter:blur(2px);-webkit-backdrop-filter:blur(2px)">'
        + dot_html + text +
        '</div>'
    )

def _resolve_site_bar(fields, bottom=None):
    """把 fields.site_bar 解析成 site_bar HTML 串（False → ''）。

    bottom：可选，强制覆盖引导条距底像素（横版按高度缩放时传入，如 max(80,int(h*0.11))）。
    """
    sb = fields.get("site_bar")
    if not sb:
        return ""
    _b = bottom if bottom is not None else 180
    if isinstance(sb, dict):
        return site_bar(text=sb.get("text"), bottom=sb.get("bottom", _b),
                        color=sb.get("color"), dot=sb.get("dot", True),
                        font_size=sb.get("font_size", 20))
    if isinstance(sb, str):
        return site_bar(text=sb, bottom=_b)
    return site_bar(bottom=_b)  # True → 用全局默认（或传入的 bottom）

# ---------- 顶部胶囊净化：胶囊只放「内容属性」，不放版式名 ----------
# 「封面」是版式概念（第 1 段=封面海报），写进胶囊很突兀。胶囊文字一律用与首句钩子
# 相关的内容属性（观点 / 问题 / 见解 / 痛点 / 结论 / 干货 / 避坑 …）。
# 此处统一兜底：任何胶囊/徽章文字里的「封面」类词一律剥掉，剥完为空则该胶囊整块不渲染。
_PILL_STRIP = ("封面图", "封面")
_PILL_TRIM = "·・-—–|/、,，。:： \t"


def _pill_text(v):
    """净化顶部胶囊/徽章文字；返回空串表示该胶囊不渲染。"""
    t = str(v or "").strip()
    if not t:
        return ""
    for w in _PILL_STRIP:
        t = t.replace(w, "")
    t = t.strip().strip(_PILL_TRIM).strip()
    while "  " in t:
        t = t.replace("  ", " ")
    return t


def _sanitize_pills(fields):
    """就地净化 fields 的胶囊/徽章文字（chapter / tag / badge），返回同一字典。"""
    for k in ("chapter", "tag", "badge"):
        if k in fields:
            fields[k] = _pill_text(fields[k])
    return fields


def marketing_slide(fields):
    """深色营销花字版式（竖版/横版自适应）。

    画布宽 > 高（或 fields.layout=="landscape"）走横版 landscape_slide；
    否则走竖版 _marketing_portrait。
    """
    _sanitize_pills(fields)
    cw, ch = CANVAS["w"], CANVAS["h"]
    layout = fields.get("layout")
    if layout == "landscape" or (layout is None and cw > ch):
        return landscape_slide(fields)
    return _marketing_portrait(fields)


def _marketing_portrait(fields):
    """深色营销花字版式（竖版）。fields:
       brand    顶部品牌胶囊文字（带橙点），默认「凤希AI」
       chapter  左上章节胶囊文字
       badge    可选，编号徽章（如 "① AI 幻灯片"）
       title    大标题（支持 <span class='hl'>橙</span> / hl2 绿 / hl3 蓝）
       sub      可选副标
       chips    可选胶囊标签列表
       steps    可选序列步骤条 [[编号, 标题, 说明], ...]（录制时一行行出现，等上一行播完再下一行）
       flow     可选图标流程 [[图标, 文字], ...]
       mock     可选 UI 模拟 {"title": "...", "rows": ["...", ...]}
       frame    可选，True 则把文字内容套进一张半透明圆角框（居中；纯文字封面用，冲击力强）
      frame_top 可选，透明框的 top 值（如 "20%"），不给则垂直居中
    """
    brand = fields.get("brand", "凤希AI")
    # 模式 B：图片型封面（单独生成的封面图做主视觉）——图满屏裸露 + 底部暗卡放文案（遵守铁律 10，不蒙板）
    ci = fields.get("cover_image")
    if ci:
        try:
            with open(ci, "rb") as f:
                src = "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
        except Exception:
            src = ""
        bg = (f'<div style="position:fixed;inset:0;background-image:url({src});'
              f'background-size:cover;background-position:center top"></div>')
        top = f'<div class="mk-brand"><span class="dot"></span>{brand}</div>'
        if fields.get("tag"):
            top += f'<div class="mk-tag">{fields["tag"]}</div>'
        card = ('<div style="position:fixed;left:5%;right:5%;bottom:150px;'
                'background:rgba(var(--panel2rgb),.82);border-radius:28px;padding:34px 38px;'
                'box-shadow:0 16px 40px rgba(var(--shadowrgb),.42)">')
        if fields.get("chapter"):
            card += (f'<div style="display:inline-block;padding:8px 20px;border-radius:999px;'
                     f'border:1px solid rgba(var(--linergb),.45);font-size:24px;color:var(--ink2);'
                     f'font-weight:700;margin-bottom:18px">{fields["chapter"]}</div>')
        card += (f'<div class="mk-title" style="font-size:{fields.get("title_size", 54)}px;'
                 f'text-align:left">{fields.get("title", "")}</div>')
        if fields.get("hook"):
            card += f'<div class="mk-hook" style="text-align:left">{fields["hook"]}</div>'
        if fields.get("chips"):
            card += ('<div class="mk-chips" style="justify-content:flex-start;margin-top:26px">'
                     + "".join(f'<div class="mk-chip">{c}</div>' for c in fields["chips"]) + '</div>')
        if fields.get("hint"):
            card += f'<div class="mk-hint" style="text-align:left">{fields["hint"]}</div>'
        card += '</div>'
        return marketing_page(bg + top + card + _resolve_site_bar(fields))
    parts = [f'<div class="mk-brand mk-a"><span class="dot"></span>{brand}</div>']
    if fields.get("tag"):
        parts.append(f'<div class="mk-tag">{fields["tag"]}</div>')
    if fields.get("chapter"):
        parts.append(f'<div class="mk-chapter mk-a">{fields["chapter"]}</div>')
    inner = []
    if fields.get("badge"):
        inner.append(f'<div class="mk-badge mk-a">{fields["badge"]}</div>')
    if fields.get("title"):
        _tsz = fields.get("title_size")
        _tst = f' style="font-size:{_tsz}px"' if _tsz else ""
        inner.append(f'<div class="mk-title mk-a"{_tst}>{fields["title"]}</div>')
    if fields.get("hook"):
        inner.append(f'<div class="mk-hook mk-a">{fields["hook"]}</div>')
    if fields.get("sub"):
        inner.append(f'<div class="mk-sub mk-a">{fields["sub"]}</div>')
    chips = fields.get("chips") or []
    if chips:
        inner.append('<div class="mk-chips mk-a">' + "".join(
            f'<div class="mk-chip">{c}</div>' for c in chips) + '</div>')
    if fields.get("hint"):
        inner.append(f'<div class="mk-hint mk-a">{fields["hint"]}</div>')
    flow = fields.get("flow") or []
    if flow:
        nodes = []
        for i, (ic, tx) in enumerate(flow):
            if i:
                nodes.append('<div class="mk-arrow">&#8594;</div>')
            nodes.append(f'<div class="mk-node"><div class="ic">{ic}</div><div class="tx">{tx}</div></div>')
        inner.append('<div class="mk-flow mk-a">' + "".join(nodes) + '</div>')
    mock = fields.get("mock")
    if mock:
        rows = "".join(f'<div class="r">{r}</div>' for r in mock.get("rows", []))
        inner.append(f'<div class="mk-mock mk-a"><div class="h">{mock.get("title", "")}</div>{rows}</div>')
    steps = fields.get("steps") or []
    if steps:
        # 序列步骤条：每行 class="seq" → 录制时按 data-seq 顺序一个个出现（等上一个播完再下一个）
        cols = ("var(--ac)", "var(--ac3)", "var(--ac4)", "var(--ac5)", "var(--ac6)")
        rows = []
        for i, s in enumerate(steps, 1):
            if isinstance(s, (list, tuple)):
                if len(s) >= 3:
                    no, tt, dd = s[0], s[1], s[2]
                else:
                    no, tt, dd = str(i), s[0], (s[1] if len(s) > 1 else "")
            else:
                no, tt, dd = str(i), str(s), ""
            c = cols[(i - 1) % len(cols)]
            rows.append(
                f'<div class="mk-step seq" data-seq="{i}" data-fx="left">'
                f'<div class="no" style="background:{c};'
                f'background:linear-gradient(135deg,{c},'
                f'color-mix(in srgb,{c} 66%,transparent))">{no}</div>'
                f'<div><div class="tt">{tt}</div>'
                + (f'<div class="dd">{dd}</div>' if dd else "") + '</div></div>')
        # site_bar 开启时给 mk-steps 加 padding-bottom，末步完整显示
        _sb = "padding-bottom:130px;" if fields.get("site_bar") else ""
        # 5 行及以上自动收紧行距，末行留在烧录字幕区之上
        _tight = " tight" if len(steps) >= 5 else ""
        inner.append(f'<div class="mk-steps{_tight}" style="{_sb}">' + "".join(rows) + '</div>')
    if fields.get("media"):
        try:
            with open(fields["media"], "rb") as f:
                msrc = "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
        except Exception:
            msrc = ""
        inner.append(f'<div class="mk-media mk-a"><img src="{msrc}" alt=""></div>')
    # 步骤条 ≥5 行时整体上提，给底部烧录字幕区留安全距离
    _wt = int(fields.get("wrap_top", 230 if len(steps) >= 5 else 300))
    parts.append('<div class="mk-wrap" style="top:%dpx">' % _wt + "".join(inner) + '</div>')
    if fields.get("frame"):
        # 透明框居中版式：把整块文字内容套进一张半透明圆角框里（默认垂直居中）
        _ft = fields.get("frame_top")
        _fs = f' style="top:{_ft};transform:none"' if _ft else ""
        parts[-1] = f'<div class="mk-frame"{_fs}>' + parts[-1] + '</div>'
    parts.append(_resolve_site_bar(fields))   # ✅ 引导条：教程/下载场景露官网+QQ群
    return marketing_page("".join(parts))

def outfit_slide(fields):
    """穿搭 / 商品展示版式（竖版/横版自适应）。

    画布宽 > 高（或 fields.layout=="landscape"）走横版 landscape_outfit_slide；
    否则走竖版 _outfit_portrait。
    """
    _sanitize_pills(fields)
    cw, ch = CANVAS["w"], CANVAS["h"]
    layout = fields.get("layout")
    if layout == "landscape" or (layout is None and cw > ch):
        return landscape_outfit_slide(fields)
    return _outfit_portrait(fields)


def _outfit_portrait(fields):
    """穿搭 / 商品展示版式（竖版）：人物图满屏裸露做主角 + 底部暗卡列单品。

    fields:
      brand    顶部品牌胶囊
      chapter  左上章节胶囊
      tag      右上高亮标签
      image    人物/商品图绝对路径（满屏 cover，图裸露）
      title    卡片主标题（如「今日穿搭 · 通勤风」）
      price    可选，右侧价格/亮点（如「￥299」或「3件套」）
      sub      可选副标
      items    单品列表 [[分类, 单品名], ...]（如 [["上衣","米白针织开衫"], ...]）
      chips    可选胶囊标签
      tagside  可选右侧竖排标签（如 ["显瘦","百搭","通勤"]）
      hint     可选底部提示
    """
    brand = fields.get("brand", "凤希AI")
    bg = ""
    img = fields.get("image") or fields.get("cover_image")
    if img:
        try:
            with open(img, "rb") as f:
                src = "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
        except Exception:
            src = ""
        if src:
            bg = (f'<div style="position:fixed;inset:0;background-image:url({src});'
                  f'background-size:cover;background-position:center top"></div>')
    parts = [f'<div class="mk-brand mk-a"><span class="dot"></span>{brand}</div>']
    if fields.get("tag"):
        parts.append(f'<div class="mk-tag">{fields["tag"]}</div>')
    if fields.get("chapter"):
        parts.append(f'<div class="mk-chapter mk-a">{fields["chapter"]}</div>')
    ts = fields.get("tagside") or []
    if ts:
        parts.append('<div class="ot-tagside mk-a">'
                     + "".join(f'<div class="t">{t}</div>' for t in ts) + '</div>')
    card = ['<div class="ot-card mk-a">']
    head = []
    if fields.get("title"):
        head.append(f'<div class="ot-title">{fields["title"]}</div>')
    if fields.get("price"):
        head.append(f'<div class="ot-price">{fields["price"]}</div>')
    if head:
        card.append('<div class="ot-head">' + "".join(head) + '</div>')
    if fields.get("sub"):
        card.append(f'<div class="ot-sub">{fields["sub"]}</div>')
    items = fields.get("items") or []
    if items:
        _cls = "ot-items two" if int(fields.get("cols", 1) or 1) == 2 else "ot-items"
        card.append(f'<div class="{_cls}">' + "".join(
            f'<div class="ot-item"><span class="dot"></span>'
            f'<span class="k">{i[0]}</span><span class="n">{i[1]}</span></div>'
            for i in items) + '</div>')
    chips = fields.get("chips") or []
    if chips:
        card.append('<div class="ot-chips">' + "".join(
            f'<div class="mk-chip">{c}</div>' for c in chips) + '</div>')
    if fields.get("hint"):
        card.append(f'<div class="mk-hint" style="text-align:left">{fields["hint"]}</div>')
    card.append('</div>')
    parts.append("".join(card))
    return marketing_page(bg + "".join(parts) + _resolve_site_bar(fields))


def product_slide(fields):
    """商品展示版式：商品图「完整显示不裁切」（contain）+ 暗卡列规格。

    与 outfit 的区别：outfit 是人物图满屏 cover（会裁切）；product 用 contain 把商品
    完整放进图卡，适合带标签/包装的实物图、竖构图或横构图商品照都行。

    画布宽 > 高（或 fields.layout=="landscape"）走横版 `_product_landscape`
    （左图 contain + 右卡），否则走竖版（居中图卡 + 底部暗卡）。

    fields:
      brand / chapter / tag / badge   顶部信息（同 marketing）
      image      商品图绝对路径（contain 完整显示）
      title      商品名（卡片主标题）
      price      右侧规格/价格（如「500g」「￥29.9」）
      sub        可选副标（一句话卖点）
      items      规格条目 [[标签, 值], ...]（如 [["配料","猪蹄 香辛料"],["保质期","常温9个月"]])
      chips      可选胶囊标签（卖点）
      tagside    可选，浮在图卡右上的竖排标签（如 ["开袋即食","真空锁鲜"]）
      hint       可选底部提示
      shot_top / shot_bottom  可选，微调图卡上下边界（px），默认 212 / 508（仅竖版有效）
    """
    _sanitize_pills(fields)
    _cw, _ch = CANVAS["w"], CANVAS["h"]
    _lay = fields.get("layout")
    if _lay == "landscape" or (_lay is None and _cw > _ch):
        return _product_landscape(fields)
    brand = fields.get("brand", "凤希AI")
    parts = [f'<div class="mk-brand mk-a"><span class="dot"></span>{brand}</div>']
    if fields.get("tag"):
        parts.append(f'<div class="mk-tag">{fields["tag"]}</div>')
    if fields.get("chapter"):
        parts.append(f'<div class="mk-chapter mk-a">{fields["chapter"]}</div>')
    img = fields.get("image") or ""
    src = ""
    if img:
        try:
            with open(img, "rb") as f:
                src = "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
        except Exception:
            src = ""
    _st = int(fields.get("shot_top", 212) or 212)
    _sb = int(fields.get("shot_bottom", 508) or 508)
    parts.append(f'<div class="pd-shot mk-a" style="top:{_st}px;bottom:{_sb}px;'
                 f'background-image:url({src})"></div>')
    ts = fields.get("tagside") or []
    if ts:
        parts.append('<div class="pd-tagside mk-a">'
                     + "".join(f'<div class="t">{t}</div>' for t in ts) + '</div>')
    card = ['<div class="ot-card mk-a">']
    head = []
    if fields.get("title"):
        head.append(f'<div class="ot-title">{fields["title"]}</div>')
    if fields.get("price"):
        head.append(f'<div class="ot-price">{fields["price"]}</div>')
    if head:
        card.append('<div class="ot-head">' + "".join(head) + '</div>')
    if fields.get("sub"):
        card.append(f'<div class="ot-sub">{fields["sub"]}</div>')
    items = fields.get("items") or []
    if items:
        _cls = "ot-items two" if int(fields.get("cols", 1) or 1) == 2 else "ot-items"
        card.append(f'<div class="{_cls}">' + "".join(
            f'<div class="ot-item"><span class="dot"></span>'
            f'<span class="k">{i[0]}</span><span class="n">{i[1]}</span></div>'
            for i in items) + '</div>')
    chips = fields.get("chips") or []
    if chips:
        card.append('<div class="ot-chips">' + "".join(
            f'<div class="mk-chip">{c}</div>' for c in chips) + '</div>')
    if fields.get("hint"):
        card.append(f'<div class="mk-hint" style="text-align:left">{fields["hint"]}</div>')
    card.append('</div>')
    parts.append("".join(card))
    return marketing_page("".join(parts) + _resolve_site_bar(fields))


def _product_landscape(fields):
    """横版商品展示：左图 **contain 完整不裁切**（`.pd-l`）+ 右卡列规格（`.ot-cover-r`）。

    竖版那套 `.pd-shot{top:212px;bottom:508px}` 是给 768×1280 定的绝对坐标，
    搬到 1366×768 上会把图卡压成一条缝、价格还会压住 tagside —— 所以横版单独一条。

    `tagside` 在横版不浮在图上了（图占左半屏，浮上去必压商品），
    改成排在右卡顶部的一行胶囊。
    """
    _sanitize_pills(fields)
    brand = fields.get("brand", "凤希AI")
    img = fields.get("image") or fields.get("cover_image") or ""
    src = ""
    if img:
        try:
            with open(img, "rb") as f:
                src = "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
        except Exception:
            src = ""
    left = f'<div class="pd-l mk-a" style="background-image:url({src})"></div>'
    right = [f'<div class="mk-brand mk-a"><span class="dot"></span>{brand}</div>']
    if fields.get("chapter"):
        right.append(f'<div class="mk-chapter mk-a">{fields["chapter"]}</div>')
    if fields.get("badge"):
        right.append(f'<div class="mk-badge mk-a">{fields["badge"]}</div>')
    ts = fields.get("tagside") or []
    if ts:
        right.append('<div class="ot-chips mk-a">'
                     + "".join(f'<div class="mk-chip">{t}</div>' for t in ts) + '</div>')
    head = []
    if fields.get("title"):
        head.append(f'<div class="ot-title">{fields["title"]}</div>')
    if fields.get("price"):
        head.append(f'<div class="ot-price">{fields["price"]}</div>')
    if head:
        right.append('<div class="ot-head">' + "".join(head) + '</div>')
    if fields.get("sub"):
        right.append(f'<div class="ot-sub">{fields["sub"]}</div>')
    items = fields.get("items") or []
    if items:
        _cls = "ot-items two" if int(fields.get("cols", 1) or 1) == 2 else "ot-items"
        right.append(f'<div class="{_cls} mk-a">' + "".join(
            f'<div class="ot-item"><span class="dot"></span>'
            f'<span class="k">{i[0]}</span><span class="n">{i[1]}</span></div>'
            for i in items) + '</div>')
    chips = fields.get("chips") or []
    if chips:
        right.append('<div class="ot-chips">' + "".join(
            f'<div class="mk-chip">{c}</div>' for c in chips) + '</div>')
    if fields.get("hint"):
        right.append(f'<div class="mk-hint" style="text-align:left">{fields["hint"]}</div>')
    inner = left + '<div class="ot-cover-r">' + "".join(right) + '</div>'
    if fields.get("tag"):
        inner += f'<div class="mk-tag">{fields["tag"]}</div>'
    inner += _resolve_site_bar(fields, bottom=_landscape_sitebar_bottom())
    return landscape_page(inner)


# ---------- 横版（landscape）：左右分栏，尺寸随视口 vh/vw 缩放，宽度用 % 不写 max-width ----------
# 设计原则（与竖版一致）：① 宽度 100%（flex:1 1 0 撑满）；
# ② 尺寸用 vh/vw（随视口比例缩放）；③ 固定位置元素用 --s 缩放因子（注入的 :root 变量）做动态定位。
# 动画时长用固定秒数：mk-a 1.05s / mk-p 0.85s / seq 0.95s。
LANDSCAPE_CSS = """
@keyframes mkUp{from{opacity:0;translate:0 46px}to{opacity:1;translate:0 0}}
@keyframes mkUpBig{from{opacity:0;translate:0 130px}to{opacity:1;translate:0 0}}
@keyframes mkPop{0%{opacity:0;scale:.72}62%{opacity:1;scale:1.06}100%{opacity:1;scale:1}}
@keyframes mkZoom{from{opacity:0;scale:.8}to{opacity:1;scale:1}}
@keyframes mkFade{from{opacity:0}to{opacity:1}}
@keyframes mkLeft{from{opacity:0;translate:-96px 0}to{opacity:1;translate:0 0}}
@keyframes mkRight{from{opacity:0;translate:96px 0}to{opacity:1;translate:0 0}}
@keyframes mkDrop{from{opacity:0;translate:0 -76px}to{opacity:1;translate:0 0}}
@keyframes mkBlur{from{opacity:0;filter:blur(16px)}to{opacity:1;filter:blur(0)}}
@keyframes mkA{from{opacity:0;translate:0 4vh}to{opacity:1;translate:0 0}}
@keyframes mkP{0%{opacity:0;scale:.85}70%{opacity:1;scale:1.06}100%{opacity:1;scale:1}}
@keyframes mkSeq{from{opacity:0;translate:0 3vh}to{opacity:1;translate:0 0}}
:root{--T:3s}
.mk-a,.mk-p{opacity:0;animation-fill-mode:both;animation-play-state:paused;
  animation-delay:calc(var(--d,0s)*var(--step,1) - var(--T))}
.mk-a{animation-name:mkA;animation-duration:1.05s;animation-timing-function:cubic-bezier(.2,.7,.3,1);animation-fill-mode:forwards}
.mk-p{animation-name:mkP;animation-duration:.85s;animation-timing-function:cubic-bezier(.2,.7,.3,1);animation-fill-mode:forwards}
/* 无 JS 时的兜底：`.seq` 直接给终态（steps / chat / pricing 这些逐条揭示的行）。
   🔴 千万别写成裸 `.seq{opacity:0;animation-name:mkSeq;…}`：静态出图截在动画第 0 帧，
   整行透明（steps/chat/pricing 全看不见）。竖版 MARKETING_CSS 里 `.seq` 本来就没有
   opacity:0，静态出图正常 —— 横版这条兜底是多余的，改成「有 JS 才隐藏」。
   录制路径由 SEQ_JS 给 <html> 加 .js-seq，再由 .seq.on 逐条播放，跟这条互不影响。 */
html:not(.js-seq) .seq{opacity:1;animation:none}
body[data-anim="整体淡入"]{--step:0}
body[data-anim="整体淡入"] .mk-a,body[data-anim="整体淡入"] .mk-p{animation-name:mkFade;animation-duration:.72s}
body[data-anim="缩放弹入"] .mk-a,body[data-anim="缩放弹入"] .mk-p{animation-name:mkZoom;animation-duration:.55s}
body[data-anim="上滑入"] .mk-a,body[data-anim="上滑入"] .mk-p{animation-name:mkUpBig;animation-duration:.6s}
body[data-anim="左滑入"] .mk-a,body[data-anim="左滑入"] .mk-p{animation-name:mkLeft;animation-duration:.55s}
body[data-anim="右滑入"] .mk-a,body[data-anim="右滑入"] .mk-p{animation-name:mkRight;animation-duration:.55s}
body[data-anim="下落弹入"] .mk-a,body[data-anim="下落弹入"] .mk-p{animation-name:mkDrop;animation-duration:.55s}
body[data-anim="模糊聚焦"] .mk-a,body[data-anim="模糊聚焦"] .mk-p{animation-name:mkBlur;animation-duration:.72s}
body[data-anim="逐条错峰"]{--step:1.5}
body[data-anim="分层弹入"]{--step:1}
.mk-brand{--d:0s}.mk-chapter{--d:.08s}.mk-badge{--d:.16s}.mk-title{--d:.22s}
.mk-sub{--d:.30s}.mk-chips{--d:.38s}.mk-flow{--d:.46s}.mk-mock{--d:.46s}
.mk-play{--d:.56s}.mk-hint{--d:.64s}
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:__W__px;height:__H__px;overflow:hidden}
body{font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;
  background:radial-gradient(120% 80% at 50% 24%,var(--bg1) 0%,var(--bg2) 46%,var(--bg3) 100%);
  color:var(--ink);position:relative}
/* —— 横版营销：左右分栏（左文案 / 右流程·UI·步骤·配图）—— */
.l-row{position:absolute;left:0;top:0;right:0;bottom:0;display:flex;align-items:stretch}
.l-col{position:relative;flex:1 1 0;display:flex;flex-direction:column;justify-content:center;
  padding:11vh 4.5vw 15vh;gap:2.2vh;min-width:0}
.l-col.right{padding-left:3vw;padding-right:5vw}
/* —— 元素样式（横版：宽度用 %/vw，不写 max-width）—— */
/* 🔴 文案字号一律**固定 px**，不跟 vh 缩放（2026-10-27 定）。
   vh 在 1366×768 下标题 5.4vh 只有 41.5px，看着像正文；
   而横版左栏可用宽 560px，和竖版 656px 是同一量级 —— 同一套 px 两边观感才一致。
   档位按「标题 58 / 钩子 34 / 副标 30 / 胶囊 26 / 章节 24」这套比例走。
   ⚠️ 固定值是按 1366×768 定的：1280×720 会略大一点（可接受），
      1920×1080 会偏小，高清档单段用 fields.title_size 覆盖。
   盒子的宽高/内距仍走 vh，版式照样自适应。 */
.mk-brand{position:fixed;top:3vh;left:3vw;display:flex;align-items:center;gap:.8vw;
  padding:1vh 1.6vw;border-radius:999px;border:1px solid rgba(var(--linergb),.35);
  background:rgba(var(--panelrgb),.28);font-size:22px;font-weight:700;color:var(--ink2);z-index:4}
.mk-brand .dot{width:1.1vh;height:1.1vh;border-radius:50%;background:var(--ac);box-shadow:0 0 12px var(--ac)}
.mk-tag{position:fixed;top:3vh;right:3vw;padding:1vh 1.4vw;border-radius:999px;
  background:linear-gradient(135deg,var(--ac),var(--ac2));color:var(--onac);font-size:22px;font-weight:800;letter-spacing:1px;z-index:4}
.mk-chapter{display:inline-block;align-self:flex-start;padding:.8vh 1.4vw;border-radius:999px;
  border:1px solid rgba(var(--linergb),.30);background:rgba(var(--panelrgb),.55);font-size:24px;
  font-weight:700;color:var(--ink2);letter-spacing:1px}
.mk-badge{display:inline-block;align-self:flex-start;padding:1vh 2vw;border-radius:999px;
  border:1.5px solid rgba(var(--linergb),.5);background:rgba(var(--panelrgb),.5);font-size:28px;font-weight:800;color:var(--ink2)}
.mk-title{font-family:"Smiley Sans","Microsoft YaHei",sans-serif;font-size:58px;line-height:1.22;font-weight:400;letter-spacing:1px;color:var(--ink)}
.mk-title .hl{color:var(--ac)}.mk-title .hl2{color:var(--ac3)}.mk-title .hl3{color:var(--ac4)}
.mk-hook{font-size:34px;color:var(--acsoft);font-weight:800;letter-spacing:1px}
.mk-sub{font-size:30px;color:var(--ink2);font-weight:600;line-height:1.5}
.mk-chips{display:flex;gap:1vw;flex-wrap:wrap;margin-top:1vh}
.mk-chip{padding:1vh 1.6vw;border-radius:999px;border:1.5px solid rgba(var(--linergb),.4);
  background:rgba(var(--chiprgb),.05);font-size:26px;font-weight:700;color:var(--ink2)}
.mk-chip .c1{color:var(--ac)}.mk-chip .c2{color:var(--ac3)}.mk-chip .c3{color:var(--ac4)}
.mk-play{margin:1.5vh auto 0;width:9vh;height:9vh;border-radius:50%;border:3px solid rgba(var(--acrgb),.85);
  display:flex;align-items:center;justify-content:center;font-size:38px;color:var(--ac);
  background:rgba(var(--acrgb),.12);box-shadow:0 0 44px rgba(var(--acrgb),.35)}
.mk-hint{font-size:26px;color:var(--ink2);font-weight:700;letter-spacing:2px}
.mk-flow{display:flex;align-items:center;justify-content:center;gap:1vw;margin-top:1vh;flex-wrap:wrap}
.mk-node{width:9vw;padding:1.6vh .6vw;border-radius:1.4vh;border:1.5px solid rgba(var(--linergb),.4);
  background:linear-gradient(180deg,rgba(var(--panelrgb),.5),rgba(var(--panel2rgb),.6));text-align:center}
.mk-node .ic{font-size:30px;margin-bottom:.8vh}
.mk-node .tx{font-size:20px;font-weight:800;color:var(--ink)}
.mk-arrow{font-size:26px;color:var(--ac);font-weight:900}
.mk-mock{margin-top:1vh;width:100%;border-radius:1.6vh;border:1.5px solid rgba(var(--linergb),.35);
  background:linear-gradient(180deg,rgba(var(--panelrgb),.92),rgba(var(--panel2rgb),.92));padding:2vh 2.4vw;text-align:left}
.mk-mock .h{font-size:20px;color:var(--ink3);font-weight:800;margin-bottom:1vh}
.mk-mock .r{font-size:20px;color:var(--ink2);padding:.9vh 0;border-bottom:1px solid rgba(var(--linergb),.14)}
.mk-media{margin:1vh auto 0;width:82%;border-radius:1.4vh;overflow:hidden;
  border:1.5px solid rgba(var(--linergb),.38);box-shadow:0 18px 44px rgba(var(--shadowrgb),.45)}
.mk-media img{width:100%;display:block}
.mk-steps{display:flex;flex-direction:column;gap:1.4vh;margin-top:1vh}
.mk-steps.tight{gap:1vh;margin-top:.5vh}
.mk-steps.tight .mk-step{padding:1.2vh 2vw}
.mk-step{display:flex;align-items:center;gap:1.4vw;padding:1.6vh 2vw;border-radius:1.4vh;
  border:1.5px solid rgba(var(--linergb),.34);
  background:linear-gradient(180deg,rgba(var(--panelrgb),.62),rgba(var(--panel2rgb),.62))}
.mk-step .no{flex:none;width:5vh;height:5vh;border-radius:1.2vh;display:flex;align-items:center;
  justify-content:center;font-size:22px;font-weight:900;color:var(--onac)}
.mk-step .tt{font-size:26px;font-weight:900;color:var(--ink);line-height:1.25}
.mk-step .dd{font-size:18px;color:var(--ink2);margin-top:.4vh}
/* —— 横版穿搭：左图右文（图满屏裸露，右暗卡列单品，不蒙板）—— */
.ot-l{position:absolute;left:0;top:0;bottom:0;width:47%;
  background-color:var(--panel);background-size:cover;background-position:center top;background-repeat:no-repeat}
.ot-r{position:absolute;right:0;top:0;bottom:0;width:53%;
  background:radial-gradient(120% 90% at 70% 30%,var(--bg1) 0%,var(--bg2) 50%,var(--bg3) 100%);
  display:flex;flex-direction:column;justify-content:center;padding:9vh 4vw 14vh;gap:2vh;overflow:hidden}
.ot-tagside{position:absolute;left:1.6vw;bottom:3vh;display:flex;gap:1vw;z-index:4}
.ot-tagside .t{padding:.8vh 1.2vw;border-radius:999px;border:1px solid rgba(var(--linergb),.4);
  background:rgba(var(--panel2rgb),.62);font-size:18px;font-weight:800;color:var(--ink2)}
.ot-head{display:flex;align-items:baseline;gap:1.4vw}
.ot-title{font-family:"Smiley Sans","Microsoft YaHei",sans-serif;font-size:40px;font-weight:400;color:var(--ink);line-height:1.2}
.ot-price{margin-left:auto;font-size:32px;font-weight:900;color:var(--ac);white-space:nowrap}
.ot-sub{font-size:26px;color:var(--ink2);font-weight:700}
.ot-items{display:flex;flex-direction:column;gap:1.2vh;margin-top:1vh}
.ot-items.two{display:grid;grid-template-columns:1fr 1fr;gap:1.2vh 2.4vw}
.ot-items.two .ot-item .k{min-width:5vw;font-size:20px}
.ot-items.two .ot-item .n{font-size:24px}
.ot-item{display:flex;align-items:center;gap:1vw;font-size:26px;color:var(--ink)}
.ot-item .k{min-width:6vw;font-size:22px;font-weight:800;color:var(--ink3)}
.ot-item .dot{width:1vh;height:1vh;border-radius:50%;background:var(--ac);box-shadow:0 0 10px var(--ac);flex:0 0 auto}
.ot-item .n{font-weight:700}
.ot-chips{display:flex;gap:1vw;flex-wrap:wrap;margin-top:1.5vh}
.ot-chips .mk-chip{font-size:24px;padding:.9vh 1.4vw}
/* —— 横版封面（cover_image）：左图右卡 —— */
.ot-cover-l{position:absolute;left:0;top:0;bottom:0;width:55%;
  background-color:var(--panel);background-size:cover;background-position:center;background-repeat:no-repeat}
.ot-cover-r{position:absolute;right:0;top:0;bottom:0;width:45%;
  background:radial-gradient(120% 90% at 30% 30%,var(--bg1) 0%,var(--bg2) 50%,var(--bg3) 100%);
  display:flex;flex-direction:column;justify-content:center;padding:8vh 3vw 12vh;gap:1.6vh}
/* —— 横版商品（product）：左图**完整不裁切** + 右卡列规格 ——
   和 `.ot-l`（cover，会裁）的区别就是 contain；`background-origin:content-box`
   让 contain 算在 padding 之内，商品四周自然留出呼吸位，不贴边。 */
.pd-l{position:absolute;left:0;top:0;bottom:0;width:55%;
  padding:8vh 4vw;
  background-color:var(--panel);
  background-size:contain;background-position:center;background-repeat:no-repeat;
  background-origin:content-box;
  box-shadow:inset calc(-1px*var(--s)) 0 0 rgba(var(--linergb),.22)}
/* —— 透明框居中（纯文字封面）—— */
.mk-frame{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
  width:80%;background:rgba(var(--panel2rgb),.55);border:1.5px solid rgba(var(--linergb),.36);border-radius:2.4vh;
  padding:5vh 4vw;box-shadow:0 24px 64px rgba(var(--shadowrgb),.46)}
.mk-frame .l-row{position:static}
/* ================= 动效扩展库 ================= */
@keyframes mkSweep{from{background-size:0% 100%}to{background-size:100% 100%}}
@keyframes mkLine{from{scale:0 1}to{scale:1 1}}
@keyframes mkPulse{0%,100%{scale:1}45%{scale:1.12}}
@keyframes mkBreath{0%,100%{opacity:.55}50%{opacity:1}}
@keyframes mkSpin{from{opacity:0;rotate:-14deg;scale:.86}to{opacity:1;rotate:0;scale:1}}
@keyframes mkFlash{0%{opacity:0;translate:0 10px}100%{opacity:1;translate:0 0}}
@keyframes mkChar{from{opacity:0;translate:0 30px}to{opacity:1;translate:0 0}}
.fx-pulse{animation:mkPulse .8s ease-in-out 2 both}
.fx-breath{animation:mkBreath 1.4s ease-in-out infinite both}
.fx-spin{animation-name:mkSpin!important}
.fx-sweep{background-image:linear-gradient(90deg,rgba(var(--acrgb),.42),rgba(var(--acrgb),.42));
  background-repeat:no-repeat;background-position:0 62%;background-size:100% 62%}
html.js-seq .fx-sweep{background-size:0% 62%}
.fx-sweep.on,.seq.fx-sweep.on{animation:mkSweep .5s .06s both}
.fx-underline{position:relative}
.fx-underline::after{content:"";position:absolute;left:0;right:0;bottom:-8px;height:5px;border-radius:3px;
  background:linear-gradient(90deg,var(--ac),var(--ac2));transform-origin:left center;scale:1 1}
html.js-seq .fx-underline::after{scale:0 1}
.fx-underline.on::after{animation:mkLine .5s .1s both}
html.js-seq .seq{opacity:0}
.seq.on{animation-name:mkUp;animation-duration:.5s;animation-fill-mode:both}
.seq[data-fx="pop"].on{animation-name:mkPop}
.seq[data-fx="zoom"].on{animation-name:mkZoom}
.seq[data-fx="left"].on{animation-name:mkLeft}
.seq[data-fx="right"].on{animation-name:mkRight}
.seq[data-fx="drop"].on{animation-name:mkDrop}
.seq[data-fx="blur"].on{animation-name:mkBlur}
.seq[data-fx="spin"].on{animation-name:mkSpin}
.seq[data-fx="fade"].on{animation-name:mkFade}
.seq[data-fx="char"].on{animation-name:mkChar;animation-duration:.42s}
.fx-flash.on{animation:mkFlash .34s both}
"""

# ---------- 新版式样式：数据战绩 / 聊天对话 / 三档套餐（叠在 MARKETING_CSS 之上） ----------
NEW_LAYOUT_CSS = """
.nl-foot{position:fixed;left:60px;right:60px;bottom:200px;font-size:29px;color:var(--ink2);
  font-weight:700;text-align:center;line-height:1.45}
.nl-hint{position:fixed;left:60px;right:60px;bottom:150px;font-size:24px;color:var(--ink3);
  font-weight:600;text-align:center;letter-spacing:2px}
.st-wrap{position:absolute;left:56px;right:56px;top:250px;text-align:center}
.st-title{font-size:34px;color:var(--ink2);font-weight:700;letter-spacing:2px;margin-bottom:22px}
.st-hero{display:inline-flex;align-items:flex-end;justify-content:center;gap:12px}
.st-val{font-size:148px;line-height:1;font-weight:900;color:var(--ink);letter-spacing:-3px}
.st-unit{font-size:50px;font-weight:800;color:var(--ac);margin-bottom:16px}
.st-delta{font-size:27px;font-weight:800;color:var(--ac3);background:rgba(var(--ac3rgb),.13);
  border:1.5px solid rgba(var(--ac3rgb),.45);border-radius:999px;padding:9px 20px;
  margin-bottom:22px;margin-left:6px}
.st-delta.down{color:var(--bad);background:rgba(var(--badrgb),.13);border-color:rgba(var(--badrgb),.45)}
.st-label{font-size:30px;color:var(--ink2);font-weight:600;margin-top:16px}
.st-bars{display:flex;align-items:flex-end;justify-content:center;gap:20px;margin-top:54px;height:236px}
.st-bar{flex:1;max-width:112px;height:100%;display:flex;flex-direction:column;
  align-items:center;justify-content:flex-end}
.st-bar .v{font-size:26px;font-weight:800;color:var(--ink2);margin-bottom:10px}
.st-bar .b{width:100%;min-height:18px;border-radius:14px 14px 6px 6px;
  background:rgba(var(--chiprgb),.12);border:1.5px solid rgba(var(--linergb),.26)}
.st-bar .l{font-size:23px;color:var(--ink3);font-weight:600;margin-top:14px}
.st-bar.hot .b{background:linear-gradient(180deg,var(--ac),var(--ac2));border-color:rgba(var(--acrgb),.62)}
.st-bar.hot .v{color:var(--ac)}
.st-row{display:flex;justify-content:space-between;align-items:center;padding:19px 28px;
  border-radius:16px;background:rgba(var(--chiprgb),.05);border:1.5px solid rgba(var(--linergb),.2);
  margin-bottom:14px}
.st-row .k{font-size:28px;color:var(--ink2);font-weight:600}
.st-row .v{font-size:33px;font-weight:800;color:var(--ink)}
.st-row.hot .v{color:var(--ac3)}
.ch-wrap{position:absolute;left:44px;right:44px;top:222px;bottom:290px;background:var(--bubble);
  border-radius:30px;padding:26px 24px;display:flex;flex-direction:column;overflow:hidden;
  border:1.5px solid rgba(var(--linergb),.34)}
.ch-head{text-align:center;font-size:27px;color:var(--bubbleink);font-weight:700;padding-bottom:16px;
  border-bottom:1px solid rgba(var(--linergb),.35);margin-bottom:20px;flex:none}
.ch-msg{display:flex;align-items:flex-end;gap:12px;margin-bottom:18px}
.ch-msg.me{flex-direction:row-reverse}
.ch-av{width:52px;height:52px;border-radius:15px;flex:none;display:flex;align-items:center;
  justify-content:center;font-size:23px;font-weight:800;color:var(--bubble);background:var(--ink3)}
.ch-msg.me .ch-av{background:linear-gradient(135deg,var(--ac),var(--ac2))}
.ch-bub{max-width:76%;padding:19px 25px;border-radius:20px;font-size:29px;font-weight:600;line-height:1.45}
.ch-msg.you .ch-bub{background:var(--bubble);color:var(--bubbleink);border-bottom-left-radius:8px}
.ch-msg.me .ch-bub{background:linear-gradient(135deg,var(--ac),var(--ac2));color:var(--onac);border-bottom-right-radius:8px}
.pr-wrap{position:absolute;left:44px;right:44px;top:252px}
.pr-head{text-align:center;margin-bottom:26px}
.pr-htitle{font-family:"Smiley Sans","Microsoft YaHei",sans-serif;font-size:44px;font-weight:400;color:var(--ink);line-height:1.24}
.pr-hhook{font-size:29px;color:var(--acsoft);font-weight:800;margin-top:12px}
.pr-cards{display:flex;gap:18px;align-items:flex-end}
.pr-card{flex:1;min-width:0;background:rgba(var(--chiprgb),.045);border:1.5px solid rgba(var(--linergb),.26);
  border-radius:26px;padding:24px 16px 22px;text-align:center}
.pr-card.hot{background:linear-gradient(180deg,rgba(var(--acrgb),.15),rgba(var(--ac2rgb),.04));
  border:2px solid var(--ac);padding-bottom:30px}
.pr-badge{display:inline-block;padding:7px 16px;border-radius:999px;font-size:20px;font-weight:800;
  background:linear-gradient(135deg,var(--ac),var(--ac2));color:var(--onac);margin-bottom:12px}
.pr-name{font-size:30px;font-weight:800;color:var(--ink);margin-bottom:6px}
.pr-price{font-size:50px;font-weight:900;color:var(--ink2);line-height:1.1}
.pr-card.hot .pr-price{color:var(--ac)}
.pr-unit{font-size:22px;color:var(--ink2);font-weight:600}
.pr-desc{font-size:21px;color:var(--ink2);margin-top:8px;line-height:1.35}
.pr-line{height:1px;background:rgba(var(--linergb),.24);margin:16px 0}
.pr-item{font-size:23px;color:var(--ink2);font-weight:600;line-height:1.45;margin-bottom:9px}
.pr-item.no{color:var(--ink3);text-decoration:line-through}
.pr-btn{margin-top:14px;padding:12px 0;border-radius:14px;font-size:23px;font-weight:800;
  background:rgba(var(--chiprgb),.06);color:var(--ink2);border:1.5px solid rgba(var(--linergb),.3)}
.pr-card.hot .pr-btn{background:linear-gradient(135deg,var(--ac),var(--ac2));color:var(--onac);border:none}
"""


# ---------- 版式 14：上中下分栏（固定节目壳 + 中区换素材） ----------
# 壳的画法（上带标题 / 下带品牌 / 中区一条槽）已归公共件 fxai-mixed-layout/ml_frame.py：
#   壳是资产 —— 一段写一次、全片复用；中间那一格按段换素材（图片轮播 / 视频 / 网页同一套）。
# 这里保留同名常量与入口，老调用方（slide_spec、fxai-poster）不用改。
if ml_frame is None:
    raise RuntimeError("缺公共件 fxai-mixed-layout（应在 %s）" % _ML)
ml_frame.configure(canvas_css=_canvas_css, sanitize=_sanitize_pills)

# 比例由 `frame_ratios(w, h)` 按画布算（竖屏中区恒全宽 16:9）。
# 这里两个常量按规范画布 9:16 取值，只给老调用方留个默认数；要坐标一律调 frame_box。
FRAME_TOP, FRAME_MEDIA = ml_frame.frame_ratios(*ml_frame.FRAME_PORTRAIT)
FRAME_BOX = ml_frame.frame_box
FRAME_CSS = ml_frame.FRAME_CSS
FRAME_BRAND = ml_frame.FRAME_BRAND
frame_page = ml_frame.frame_page
_media_src = ml_frame._media_src


def frame_slide(fields, w=None, h=None):
    """版式 14 · 上中下分栏（转发公共件）。

    字段说明见 `fxai-mixed-layout/ml_frame.py`；规格与坑见
    `fxai-mixed-layout/references/01-素材槽规格.md`。
    """
    return ml_frame.frame_slide(fields, w or CANVAS["w"], h or CANVAS["h"])


def frame_mask(fields, w=None, h=None):
    """上中下分栏的壳遮罩（转发公共件）：上下带 + 花字照画，媒体区留透明。"""
    return ml_frame.frame_mask(fields, w or CANVAS["w"], h or CANVAS["h"])


# ---------- 版式 15：分栏（半屏文字 + 半屏媒体） ----------
# 「旁白 + 素材混排」那一路的画法已归公共件 fxai-mixed-layout/ml_split.py：
#   文字随旁白逐段换，所以遮罩也是每段一张（不像上中下分栏的壳全片复用）。
#   槽位坐标由 split_box() 统一给出，合成端与 CSS 不各算一遍。
if ml_split is None:
    raise RuntimeError("缺公共件 fxai-mixed-layout（应在 %s）" % _ML)
ml_split.configure(canvas_css=_canvas_css, sanitize=_sanitize_pills)

SPLIT_CSS = ml_split.SPLIT_CSS
SPLIT_BRAND = ml_split.SPLIT_BRAND


def split_slide(fields, w=None, h=None):
    """版式 15 · 分栏（转发公共件）。横屏左文右图，竖屏自动改上下分栏。

    字段与坑见 `fxai-mixed-layout/references/03-分栏版式.md`。
    """
    return ml_split.split_slide(fields, w or CANVAS["w"], h or CANVAS["h"])


def split_mask(fields, w=None, h=None):
    """分栏的半屏遮罩（转发公共件）：文字照画，媒体区留透明。"""
    return ml_split.split_mask(fields, w or CANVAS["w"], h or CANVAS["h"])


def split_box(w=None, h=None, side="right", full=False):
    """分栏媒体槽在画布上的 (x, y, w, h)，交给合成端垫素材。"""
    return ml_split.split_box(w or CANVAS["w"], h or CANVAS["h"], side=side, full=full)


# ---------- 版式 16：左中右分栏（中槽 + 两翼竖排文字） ----------
# 与上中下分栏同族：壳元素（两行标题 / 品牌落款 / 中区花字）一样，只换槽的形状 ——
#   上中下 = 全宽 16:9 一条带；
#   左中右 = 槽形看**槽里素材的方向**：竖素材 → 满高竖条（上下贴边零边距、宽度可调）；
#            横素材 → 16:9 横带居中。
# 实现归公共件 fxai-mixed-layout/ml_trio.py（槽位坐标只从 trio_box 取）。
if ml_trio is None:
    raise RuntimeError("缺公共件 fxai-mixed-layout/ml_trio.py（应在 %s）" % _ML)
ml_trio.configure(canvas_css=_canvas_css, sanitize=_sanitize_pills)

TRIO_CSS = ml_trio.TRIO_CSS
TRIO_BRAND = ml_trio.FRAME_BRAND


def trio_slide(fields, w=None, h=None, media_w=None, portrait_media=None):
    """版式 16 · 左中右分栏（转发公共件）。

    槽形看素材方向：竖素材 = 满高竖条（上下百分之百显示、宽度可调），
    横素材 = 16:9 横带居中。左栏竖排两行标题，右栏竖排品牌落款。
    字段见 `ml_trio.trio_slide`。
    """
    return ml_trio.trio_slide(fields, w or CANVAS["w"], h or CANVAS["h"],
                              media_w=media_w, portrait_media=portrait_media)


def trio_mask(fields, w=None, h=None, media_w=None, portrait_media=None):
    """左中右分栏的壳遮罩（转发公共件）：两翼 + 花字照画，中槽留透明。"""
    return ml_trio.trio_mask(fields, w or CANVAS["w"], h or CANVAS["h"],
                             media_w=media_w, portrait_media=portrait_media)


def trio_box(w=None, h=None, media_w=None, portrait_media=None):
    """左中右分栏中槽在画布上的框，交给合成端垫素材。

    竖素材 → `(x, 0, mw, h)`（满高）；横素材 → `(x, y, mw, mh)`（16:9 居中）。
    """
    return ml_trio.trio_box(w or CANVAS["w"], h or CANVAS["h"],
                            media_w=media_w, portrait_media=portrait_media)


# ---------- 版式 14b：槽内网页（媒体区里跑一页 HTML，录下来当素材）----------
# 中区不是只有「图片」一条路：文字卡、长图滚动、真网页（H5 混合模式）都走这一格。
# 实现已归到公共件 fxai-mixed-layout/ml_page.py（幻灯 / 广告 / 以后的展示形式共用一套）：
#   造页面 → rec_anim.js 真实录制 → 得到一条 w×媒体区高 的无声 mp4 → 垫到壳下面。
# 这里保留同名入口，老调用方（含 fxai-poster）不用改。
MEDIA_PAGE_CSS = _ml_page.MEDIA_PAGE_CSS if _ml_page else ""


def media_page(spec, w=768, h=440):
    """槽内网页的 HTML（转发公共件）。字段见 fxai-mixed-layout/SKILL.md。"""
    if _ml_page is None:
        raise RuntimeError("缺公共件 fxai-mixed-layout（应在 %s）" % _ML)
    return _ml_page.page_html(spec, w=w, h=h)


def _media_page_text(spec):
    if _ml_page is None:
        raise RuntimeError("缺公共件 fxai-mixed-layout（应在 %s）" % _ML)
    return _ml_page.text_html(spec)


def _nl_head(fields):
    """新版式共用的顶部区：品牌胶囊 / 右上标签 / 章节胶囊。"""
    _sanitize_pills(fields)
    out = ['<div class="mk-brand mk-a"><span class="dot"></span>%s</div>'
           % fields.get("brand", "凤希AI")]
    if fields.get("tag"):
        out.append('<div class="mk-tag">%s</div>' % fields["tag"])
    if fields.get("chapter"):
        out.append('<div class="mk-chapter mk-a">%s</div>' % fields["chapter"])
    return "".join(out)


def _nl_tail(fields):
    """新版式共用的底部区：总结句 / 提示 / 引导条。"""
    out = ""
    if fields.get("foot"):
        out += '<div class="nl-foot mk-a">%s</div>' % fields["foot"]
    if fields.get("hint"):
        out += '<div class="nl-hint mk-a">%s</div>' % fields["hint"]
    return out + _resolve_site_bar(fields)


def stats_slide(fields):
    """版式 11 · 数据战绩：大数字 + 涨跌幅 + 上升柱状图 / 数据行。

    用于讲「结果」的一屏：播放涨了多少、省了多少时间、涨了多少粉。

    fields:
      brand/chapter/tag/foot/hint/site_bar   同 marketing
      value    主数字（如 "12.8"）        unit   单位（如 "万"）
      delta    涨跌幅（如 "+236%"；以 "-" 开头自动转红 = 下降）
      label    数字下方说明               title  数字上方小标题（可选）
      bars     柱状图 [[底部标签, 柱顶数值, 高度值(0~100)], ...]，最高的那根自动橙高亮；
               传 hot_bar:<索引> 可手动指定高亮哪根
      rows     数据行 [[名称, 值], ...]（bars 未给时改用这种两列排布）
    """
    inner = []
    if fields.get("title"):
        inner.append('<div class="st-title mk-a">%s</div>' % fields["title"])
    hero = []
    if fields.get("value"):
        hero.append('<div class="st-val">%s</div>' % fields["value"])
    if fields.get("unit"):
        hero.append('<div class="st-unit">%s</div>' % fields["unit"])
    if fields.get("delta"):
        d = str(fields["delta"]).strip()
        hero.append('<div class="st-delta%s">%s</div>' % (" down" if d.startswith("-") else "", d))
    if hero:
        inner.append('<div class="st-hero mk-a">' + "".join(hero) + '</div>')
    if fields.get("label"):
        inner.append('<div class="st-label mk-a">%s</div>' % fields["label"])
    bars = fields.get("bars") or []
    if bars:
        mx = max(float(b[2]) for b in bars) or 1.0
        hi = fields.get("hot_bar")
        items = []
        for i, b in enumerate(bars):
            h = float(b[2])
            hot = (i == int(hi)) if hi is not None else (h >= mx)
            items.append(
                '<div class="st-bar%s seq" data-seq="%d">'
                '<div class="v">%s</div>'
                '<div class="b" style="height:%.0fpx"></div>'
                '<div class="l">%s</div></div>'
                % (" hot" if hot else "", i + 1, b[1], max(18.0, h / mx * 176), b[0]))
        inner.append('<div class="st-bars mk-a">' + "".join(items) + '</div>')
    rows = fields.get("rows") or []
    if rows:
        items = []
        for i, r in enumerate(rows):
            hot = bool(r[2]) if len(r) > 2 else False
            items.append('<div class="st-row%s seq" data-seq="%d">'
                         '<div class="k">%s</div><div class="v">%s</div></div>'
                         % (" hot" if hot else "", i + 1, r[0], r[1]))
        inner.append('<div class="st-rows mk-a">' + "".join(items) + '</div>')
    body = '<div class="st-wrap">' + "".join(inner) + '</div>'
    return marketing_page(_nl_head(fields) + body + _nl_tail(fields), extra=NEW_LAYOUT_CSS)


def chat_slide(fields):
    """版式 12 · 聊天对话：仿 IM 气泡，逐条弹出。

    用于「有人问我…」「客户说…」「评论区问得最多的是…」这类句式。

    fields:
      brand/chapter/tag/foot/hint/site_bar   同 marketing
      title    对话对象（如 "客户 · 王姐"），显示在浅色卡顶部
      msgs     消息列表 [["you"|"me", 文本], ...]；you=左侧白泡，me=右侧橙泡。建议 4~6 条
      avatars  [对方头像字, 我方头像字]，默认 ["客","我"]
    """
    avs = fields.get("avatars") or ["客", "我"]
    rows = []
    for i, m in enumerate(fields.get("msgs") or [], 1):
        side = str(m[0] or "you").lower()
        side = "me" if side in ("me", "我", "right", "1", "true") else "you"
        rows.append('<div class="ch-msg %s seq" data-seq="%d">'
                    '<div class="ch-av">%s</div>'
                    '<div class="ch-bub">%s</div></div>'
                    % (side, i, avs[1] if side == "me" else avs[0], m[1]))
    head = '<div class="ch-head">%s</div>' % fields["title"] if fields.get("title") else ""
    body = '<div class="ch-wrap mk-a">' + head + "".join(rows) + '</div>'
    return marketing_page(_nl_head(fields) + body + _nl_tail(fields), extra=NEW_LAYOUT_CSS)


def pricing_slide(fields):
    """版式 13 · 三档套餐：并排价格卡，高亮档自动放大。

    讲报价、讲版本、讲方案三选一时用。中间档默认高亮（引导到想卖的那一档）。

    fields:
      brand/chapter/tag/foot/hint/site_bar   同 marketing
      title/hook  卡片上方的小标题 / 副标（可选）
      plans       套餐列表，每档一个 dict：
                    {"name":"专业版","price":"199","unit":"/月","desc":"小团队主力",
                     "items":["5 个账号","不限次数生成","全部版式"],
                     "btn":"选它"}
                  条目文字前加 "!" 表示「不含」→ 划线灰显
      hot         高亮第几档（0 起，默认 1 = 中间档）
      badge       高亮档顶部徽章文字（默认 "最推荐"）
    """
    parts = []
    if fields.get("title") or fields.get("hook"):
        h = ['<div class="pr-head mk-a">']
        if fields.get("title"):
            h.append('<div class="pr-htitle">%s</div>' % fields["title"])
        if fields.get("hook"):
            h.append('<div class="pr-hhook">%s</div>' % fields["hook"])
        h.append('</div>')
        parts.append("".join(h))
    hot_i = int(fields.get("hot", 1))
    badge = fields.get("badge", "最推荐")
    cards = []
    for i, p in enumerate(fields.get("plans") or [], 0):
        is_hot = (i == hot_i)
        c = ['<div class="pr-card%s seq" data-seq="%d">' % (" hot" if is_hot else "", i + 1)]
        if is_hot:
            c.append('<div class="pr-badge">%s</div>' % badge)
        c.append('<div class="pr-name">%s</div>' % p.get("name", ""))
        c.append('<div class="pr-price">%s<span class="pr-unit">%s</span></div>'
                 % (p.get("price", ""), p.get("unit", "")))
        if p.get("desc"):
            c.append('<div class="pr-desc">%s</div>' % p["desc"])
        items = p.get("items") or []
        if items:
            c.append('<div class="pr-line"></div>')
            for it in items:
                s = str(it)
                if s.startswith("!"):
                    c.append('<div class="pr-item no">%s</div>' % s[1:])
                else:
                    c.append('<div class="pr-item">%s</div>' % s)
        if p.get("btn"):
            c.append('<div class="pr-btn">%s</div>' % p["btn"])
        c.append('</div>')
        cards.append("".join(c))
    if cards:
        parts.append('<div class="pr-cards mk-a">' + "".join(cards) + '</div>')
    body = '<div class="pr-wrap">' + "".join(parts) + '</div>'
    return marketing_page(_nl_head(fields) + body + _nl_tail(fields), extra=NEW_LAYOUT_CSS)


def landscape_page(inner, w=None, h=None):
    return ('<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            f'<style>{_canvas_css(LANDSCAPE_CSS, w, h)}</style></head><body>{inner}</body></html>')


def _landscape_sitebar_bottom():
    """横版引导条距底像素：按实际高度缩放。"""
    return max(80, int(CANVAS["h"] * 0.11))


def landscape_slide(fields):
    """横版营销花字版式：左右分栏（左=品牌/章节/标题/副标/胶囊/播放/提示，右=流程/UI/步骤/配图）。"""
    _sanitize_pills(fields)
    brand = fields.get("brand", "凤希AI")
    ci = fields.get("cover_image")
    if ci:
        # 封面模式：左图满屏裸露 + 右暗卡文案（横版）
        try:
            with open(ci, "rb") as f:
                src = "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
        except Exception:
            src = ""
        left = (f'<div class="ot-cover-l" style="background-image:url({src})"></div>')
        right = [f'<div class="mk-brand mk-a"><span class="dot"></span>{brand}</div>']
        if fields.get("tag"):
            right.append(f'<div class="mk-tag">{fields["tag"]}</div>')
        if fields.get("chapter"):
            right.append(f'<div class="mk-chapter mk-a">{fields["chapter"]}</div>')
        if fields.get("title"):
            right.append(f'<div class="mk-title mk-a" style="text-align:left">{fields.get("title", "")}</div>')
        if fields.get("hook"):
            right.append(f'<div class="mk-hook mk-a">{fields["hook"]}</div>')
        if fields.get("chips"):
            right.append('<div class="mk-chips mk-a" style="justify-content:flex-start">'
                         + "".join(f'<div class="mk-chip">{c}</div>' for c in fields["chips"]) + '</div>')
        if fields.get("hint"):
            right.append(f'<div class="mk-hint mk-a" style="text-align:left">{fields["hint"]}</div>')
        inner = left + '<div class="ot-cover-r">' + "".join(right) + '</div>'
        inner += _resolve_site_bar(fields, bottom=_landscape_sitebar_bottom())
        return landscape_page(inner)
    left, right = [], []
    left.append(f'<div class="mk-brand mk-a"><span class="dot"></span>{brand}</div>')
    if fields.get("tag"):
        left.append(f'<div class="mk-tag">{fields["tag"]}</div>')
    if fields.get("chapter"):
        left.append(f'<div class="mk-chapter mk-a">{fields["chapter"]}</div>')
    if fields.get("badge"):
        left.append(f'<div class="mk-badge mk-a">{fields["badge"]}</div>')
    if fields.get("title"):
        _tsz = fields.get("title_size")
        _tst = f' style="font-size:{_tsz}px"' if _tsz else ""
        left.append(f'<div class="mk-title mk-a"{_tst}>{fields["title"]}</div>')
    if fields.get("hook"):
        left.append(f'<div class="mk-hook mk-a">{fields["hook"]}</div>')
    if fields.get("sub"):
        left.append(f'<div class="mk-sub mk-a">{fields["sub"]}</div>')
    chips = fields.get("chips") or []
    if chips:
        left.append('<div class="mk-chips mk-a">'
                    + "".join(f'<div class="mk-chip">{c}</div>' for c in chips) + '</div>')
    if fields.get("hint"):
        left.append(f'<div class="mk-hint mk-a">{fields["hint"]}</div>')
    flow = fields.get("flow") or []
    if flow:
        nodes = []
        for i, (ic, tx) in enumerate(flow):
            if i:
                nodes.append('<div class="mk-arrow">&#8594;</div>')
            nodes.append(f'<div class="mk-node"><div class="ic">{ic}</div><div class="tx">{tx}</div></div>')
        right.append('<div class="mk-flow mk-a">' + "".join(nodes) + '</div>')
    mock = fields.get("mock")
    if mock:
        rows = "".join(f'<div class="r">{r}</div>' for r in mock.get("rows", []))
        right.append(f'<div class="mk-mock mk-a"><div class="h">{mock.get("title", "")}</div>{rows}</div>')
    steps = fields.get("steps") or []
    if steps:
        cols = ("var(--ac)", "var(--ac3)", "var(--ac4)", "var(--ac5)", "var(--ac6)")
        rows = []
        for i, s in enumerate(steps, 1):
            if isinstance(s, (list, tuple)):
                if len(s) >= 3:
                    no, tt, dd = s[0], s[1], s[2]
                else:
                    no, tt, dd = str(i), s[0], (s[1] if len(s) > 1 else "")
            else:
                no, tt, dd = str(i), str(s), ""
            c = cols[(i - 1) % len(cols)]
            rows.append(
                f'<div class="mk-step seq" data-seq="{i}" data-fx="left">'
                f'<div class="no" style="background:{c};'
                f'background:linear-gradient(135deg,{c},'
                f'color-mix(in srgb,{c} 66%,transparent))">{no}</div>'
                f'<div><div class="tt">{tt}</div>'
                + (f'<div class="dd">{dd}</div>' if dd else "") + '</div></div>')
        _tight = " tight" if len(steps) >= 5 else ""
        right.append(f'<div class="mk-steps{_tight}">' + "".join(rows) + '</div>')
    if fields.get("media"):
        try:
            with open(fields["media"], "rb") as f:
                msrc = "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
        except Exception:
            msrc = ""
        right.append(f'<div class="mk-media mk-a"><img src="{msrc}" alt=""></div>')
    inner = ('<div class="l-row"><div class="l-col left">'
             + "".join(left) + '</div><div class="l-col right">'
             + "".join(right) + '</div></div>')
    if fields.get("frame"):
        _ft = fields.get("frame_top")
        _fs = f' style="top:{_ft};transform:none"' if _ft else ""
        inner = f'<div class="mk-frame"{_fs}>' + inner + '</div>'
    inner += _resolve_site_bar(fields, bottom=_landscape_sitebar_bottom())
    return landscape_page(inner)


def landscape_outfit_slide(fields):
    """横版穿搭 / 商品展示：左图满屏裸露 + 右暗卡列单品（遵守铁律 10：不蒙板）。"""
    _sanitize_pills(fields)
    brand = fields.get("brand", "凤希AI")
    img = fields.get("image") or fields.get("cover_image")
    src = ""
    if img:
        try:
            with open(img, "rb") as f:
                src = "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
        except Exception:
            src = ""
    left = f'<div class="ot-l" style="background-image:url({src})"></div>'
    right = [f'<div class="mk-brand mk-a"><span class="dot"></span>{brand}</div>']
    if fields.get("chapter"):
        right.append(f'<div class="mk-chapter mk-a">{fields["chapter"]}</div>')
    head = []
    if fields.get("title"):
        head.append(f'<div class="ot-title">{fields["title"]}</div>')
    if fields.get("price"):
        head.append(f'<div class="ot-price">{fields["price"]}</div>')
    if head:
        right.append('<div class="ot-head">' + "".join(head) + '</div>')
    if fields.get("sub"):
        right.append(f'<div class="ot-sub">{fields["sub"]}</div>')
    items = fields.get("items") or []
    if items:
        _cls = "ot-items two" if int(fields.get("cols", 1) or 1) == 2 else "ot-items"
        right.append(f'<div class="{_cls} mk-a">' + "".join(
            f'<div class="ot-item"><span class="dot"></span>'
            f'<span class="k">{i[0]}</span><span class="n">{i[1]}</span></div>'
            for i in items) + '</div>')
    chips = fields.get("chips") or []
    if chips:
        right.append('<div class="ot-chips">' + "".join(
            f'<div class="mk-chip">{c}</div>' for c in chips) + '</div>')
    if fields.get("hint"):
        right.append(f'<div class="mk-hint" style="text-align:left">{fields["hint"]}</div>')
    right_html = '<div class="ot-r">' + "".join(right) + '</div>'
    extras = ""
    ts = fields.get("tagside") or []
    if ts:
        extras += '<div class="ot-tagside mk-a">' + "".join(
            f'<div class="t">{t}</div>' for t in ts) + '</div>'
    if fields.get("tag"):
        extras += f'<div class="mk-tag">{fields["tag"]}</div>'
    inner = left + right_html + extras
    inner += _resolve_site_bar(fields, bottom=_landscape_sitebar_bottom())
    return landscape_page(inner)


# ---------- 渲染 ----------
def render(name, html, out_dir, tmp_dir=None, w=768, h=1280, transparent=False):
    """HTML → PNG（Chrome headless）。返回 PNG 路径。

    transparent=True：出带 alpha 的 PNG（媒体区留空），供「上中下分栏」视频段当遮罩层用。
    """
    out_dir = pathlib.Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = pathlib.Path(tmp_dir) if tmp_dir else out_dir / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    hp = tmp_dir / f"{name}.html"
    hp.write_text(html, encoding="utf-8")
    import tempfile
    shot = pathlib.Path(tempfile.gettempdir()) / "fxai_slide_shot"
    shot.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{name}.png"
    raw = shot / f"{name}.png"
    if raw.exists():
        raw.unlink()
    extra = ["--default-background-color=00000000"] if transparent else []
    r = subprocess.run([chrome_exe(), "--headless=new", "--no-sandbox"] + chrome_gpu_args() +
                       ["--hide-scrollbars", "--force-device-scale-factor=1"] + extra +
                       [f"--window-size={w},{h}", f"--screenshot={raw}", hp.as_uri()],
                       capture_output=True, text=True)
    if not raw.exists():
        raise RuntimeError(f"配图渲染失败 {name}: {(r.stderr or '')[-300:]}")
    shutil.move(str(raw), str(dst))
    print("OK", name, dst.stat().st_size, flush=True)
    return dst

def render_many(mapping, out_dir, tmp_dir=None):
    """mapping: {name: html} 一次渲染多张。"""
    return {n: render(n, h, out_dir, tmp_dir) for n, h in mapping.items()}

def render_anim(name, html, out_dir, entrance=1.2, fps=20, tmp_dir=None, w=768, h=1280,
                anim_name="分层弹入"):
    """把带 CSS 动画的 HTML 逐帧定格渲染成帧序列（用 --T 变量控制动画时刻）。

    anim_name 从 ANIMS 里选一种进场方式（写进 <body data-anim="...">）。
    依赖 HTML 里存在 `:root{--T:3s}`（marketing / outfit 模板默认），逐帧替换成当前时刻。
    产物：<out_dir>/anim/<name>/f_0000.png …（entrance 秒 × fps 帧）。返回帧目录。
    """
    out_dir = pathlib.Path(out_dir)
    # 注入动画名到 body（注意：CSS 里也有 "data-anim" 字样，必须精确匹配 body 标签）
    if anim_name and '<body data-anim' not in html:
        html = html.replace("<body>", '<body data-anim="%s">' % anim_name, 1)
    fr = out_dir / "anim" / name
    if fr.exists():
        shutil.rmtree(fr)
    fr.mkdir(parents=True, exist_ok=True)
    tmp = pathlib.Path(tmp_dir) if tmp_dir else out_dir / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    n = max(1, int(round(entrance * fps)))
    for i in range(n):
        hh = html.replace(":root{--T:3s}", ":root{--T:%.3fs}" % (i / fps))
        hp = tmp / f"{name}_a{i:04d}.html"
        hp.write_text(hh, encoding="utf-8")
        raw = tmp / f"{name}_a{i:04d}.png"
        if raw.exists():
            raw.unlink()
        r = subprocess.run([chrome_exe(), "--headless=new", "--no-sandbox"] + chrome_gpu_args() +
                           ["--hide-scrollbars", "--force-device-scale-factor=1",
                            f"--window-size={w},{h}", f"--screenshot={raw}", hp.as_uri()],
                           capture_output=True, text=True)
        if not raw.exists():
            raise RuntimeError(f"动画帧渲染失败 {name}#{i}: {(r.stderr or '')[-200:]}")
        shutil.move(str(raw), str(fr / f"f_{i:04d}.png"))
    print("ANIM", name, n, flush=True)
    return fr

def record_anim(name, html, out_dir, entrance=1.2, fps=24, tmp_dir=None, w=768, h=1280,
                anim_name=DEFAULT_ANIM, chrome=None, wait_ms=150, js_file=None, gpu=None,
                scroll="auto", audio_dur=None, scroll_min=750, scroll_max=2200, idle=450):
    """真实录制网页动画（CDP screencast）：只启动一次 Chrome，让动画按真实时间播放并抓屏。

    与 render_anim（逐帧定格截图）的差别：
      · 帧率=浏览器真实帧率（约 30~60fps），比定格 20fps 更顺滑；
      · 只启动 1 次 Chrome（定格法要 24+ 次），速度快一个量级；
      · 动画播完页面静止，screencast 自然静默 → 自动收工。
    可选：wait_ms 开录前等待（避白屏）；js_file 交互脚本（点击/输入…，全程录）；
          gpu=False 关闭显卡渲染（默认开）。
          scroll：内容超出视口时是否自动上滚 —— "auto"(默认，逐步 scrollTo) / "wheel"(真实滚轮事件) / "none"。
          audio_dur：本段音频时长(秒)。给了就按「音频剩余时间」定滚动节奏（跟着说话进度滚），
                     不给则按超出量自动（clamp(超出px*2.6, scroll_min, scroll_max)）。
    产物：<out_dir>/anim/<name>/clip.mp4（已归一化成 fps 的 CFR 片段，可直接混剪）。
    """
    out_dir = pathlib.Path(out_dir)
    if anim_name and '<body data-anim' not in html:
        html = html.replace("<body>", '<body data-anim="%s">' % anim_name, 1)
    # 录制时注入「序列揭示」引擎（这是页面动效，属设计阶段）；滚动不注入 —— 由录制端自己做。
    if ("data-seq" in html or 'class="seq' in html or "class='seq" in html
            or ' seq"' in html or " seq'" in html):
        inject = "<script>%s</script>" % SEQ_JS
        if "</body>" in html:
            html = html.replace("</body>", inject + "</body>", 1)
        else:
            html += inject
    fr = out_dir / "anim" / name
    if fr.exists():
        shutil.rmtree(fr)
    fr.mkdir(parents=True, exist_ok=True)
    tmp = pathlib.Path(tmp_dir) if tmp_dir else out_dir / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    hp = tmp / f"{name}.html"
    hp.write_text(html, encoding="utf-8")
    js = pathlib.Path(__file__).with_name("rec_anim.js")
    # ★ 以「音频时长」为准：算出「留给滚动的时间」，交给录制端自己决定滚多远
    scroll_ms = 0
    if audio_dur and scroll != "none":
        rest = float(audio_dur) - entrance - 0.35          # 入场播完 + 留点收尾
        scroll_ms = int(rest * 1000) if rest >= 0.5 else 0
    # 有交互脚本时给足时长上限（真正结束靠「画面静止自动收工」，这里只是保险）
    cap = entrance + (10.0 if js_file else 0.5) + (2.6 if scroll != "none" else 0.0)
    if scroll_ms > 0:
        cap = max(cap, float(audio_dur) + 0.6)
    cmd = [node_exe(), str(js), "--html", str(hp), "--out", str(fr),
           "--duration", "%.2f" % cap, "--fps", str(fps), "--idle", str(int(idle)),
           "--wait", str(wait_ms), "--w", str(w), "--h", str(h),
           "--scroll", str(scroll), "--scroll-min", str(scroll_min),
           "--scroll-max", str(scroll_max), "--scroll-speed", "500",
           "--chrome", chrome or chrome_exe()]
    if scroll_ms > 0:
        cmd += ["--audio-dur", str(scroll_ms)]
    if js_file:
        cmd += ["--js", str(js_file)]
    # 默认走全局检测：真能用显卡就用显卡，否则软件渲染（探测结果进程内缓存）
    use_gpu = gpu if gpu is not None else detect_gpu()
    if not use_gpu:
        cmd += ["--no-gpu"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not (fr / "frames.txt").exists():
        raise RuntimeError("录制失败 %s: %s" % (name, (r.stderr or r.stdout or "")[-300:]))
    clip = fr / "clip.mp4"
    try:
        from gpu import video_encoder
        enc = video_encoder()
    except Exception:
        enc = "libx264"
    if enc == "h264_nvenc":
        venc = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "20", "-b:v", "0"]
    else:
        venc = ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
    rr = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
                         "-i", str(fr / "frames.txt"),
                         "-vf", "fps=%d,scale=%d:%d,setsar=1" % (fps, w, h)]
                        + venc + ["-pix_fmt", "yuv420p", str(clip)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if rr.returncode != 0 and enc != "libx264":
        rr = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
                             "-i", str(fr / "frames.txt"),
                             "-vf", "fps=%d,scale=%d:%d,setsar=1" % (fps, w, h),
                             "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                             "-pix_fmt", "yuv420p", str(clip)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if rr.returncode != 0 or not clip.exists():
        raise RuntimeError("录制片段归一化失败 %s: %s" % (name, (rr.stderr or "")[-300:]))
    for p in fr.glob("f_*.*"):     # 只留 clip.mp4 + frames.txt
        p.unlink()
    print("REC", name, (r.stdout or "").strip(), flush=True)
    return clip

def screenshot_url(url, out_path, w=768, h=1280, wait_ms=3000, chrome=None, gpu=None):
    """打开一个真实网页**截图**（不是录视频）。

    只想「进页面截个图」时用这个；录视频用 record_url。

    wait_ms：用 Chrome 的 --virtual-time-budget 等待资源/网络就位再截（联网页面建议 3000~6000）。

    产物：<out_path>.png
    """
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw = pathlib.Path(__import__("tempfile").gettempdir()) / "fxai_shot_url" / out_path.name
    raw.parent.mkdir(parents=True, exist_ok=True)
    if raw.exists():
        raw.unlink()
    # chrome_gpu_args() 内部已含「能显卡就显卡、否则软件渲染」的回落，这里直接用
    r = subprocess.run([chrome or chrome_exe(), "--headless=new", "--no-sandbox"]
                       + chrome_gpu_args()
                       + ["--hide-scrollbars", "--force-device-scale-factor=1",
                          f"--window-size={w},{h}",
                          f"--virtual-time-budget={int(wait_ms)}",
                          f"--screenshot={raw}", url],
                       capture_output=True, text=True)
    if not raw.exists():
        raise RuntimeError("网页截图失败 %s: %s" % (url, (r.stderr or "")[-300:]))
    shutil.move(str(raw), str(out_path))
    print("SHOT", url, "->", out_path.name, out_path.stat().st_size, flush=True)
    return out_path

def record_url(url, name, out_dir, duration=6.0, fps=24, w=768, h=1280, wait_ms=1200,
               scroll="none", chrome=None, gpu=None, js_file=None, audio_dur=None,
               entrance=0.0, scroll_min=800, scroll_max=12000, scroll_speed=500,
               hold=None):
    """录制一个真实网页（别人的页面、我们自己写的线上页面都行）。

    与 record_anim 的差别：不需要先有 HTML，直接给 URL；联网加载，等待更久。

    ⚠️ **默认不滚动（scroll="none"）**：别人的网站高度千差万别，不好控制；
       只有在明确要做「滚动演示」时才显式打开滚动，并**用音频时长决定滚多远**。

    scroll： "none"(默认，只录首屏) / "auto"(程序精准 scrollTo) / "wheel"(真实滚动手势)。
    audio_dur：本段音频时长(秒)。**给了就以时间为准**——
               先按 scroll_speed 算出「这段时间最多能滚多远」，再和页面实际超出量取小：
               · 页面更长 → 只滚到时间允许的位置（滚不完就滚不完）
               · 页面更短 → 用满时长慢慢滚完（速度自动变慢）

    典型场景：
      record_url("https://www.fxai.site/", "首屏", out)                          # 默认不滚，只录首屏
      record_url("https://www.fxai.site/", "滚动演示", out, scroll="auto", audio_dur=5.0)
                                                                                # 5 秒音频 → 最多滚 5×500=2500px

    产物：<out_dir>/anim/<name>/clip.mp4
    """
    out_dir = pathlib.Path(out_dir)
    fr = out_dir / "anim" / name
    if fr.exists():
        shutil.rmtree(fr)
    fr.mkdir(parents=True, exist_ok=True)
    js = pathlib.Path(__file__).with_name("rec_anim.js")
    # 以音频时长为准：算出留给滚动的时间（毫秒）
    scroll_ms = 0
    if audio_dur and scroll != "none":
        rest = float(audio_dur) - entrance - 0.3
        scroll_ms = int(rest * 1000) if rest >= 0.5 else 0
    if scroll_ms > 0:
        duration = max(duration, float(audio_dur) + 0.6)
    hold_ms = 0
    if hold is None:
        hold_ms = int(duration * 1000) if scroll == "none" else 0   # 默认不滚时按「首屏停留」
    elif hold:
        hold_ms = int(hold * 1000)
    cmd = [node_exe(), str(js), "--url", url, "--out", str(fr),
           "--duration", "%.2f" % duration, "--fps", str(fps),
           "--wait", str(wait_ms), "--w", str(w), "--h", str(h),
           "--scroll", str(scroll), "--scroll-min", str(scroll_min),
           "--scroll-max", str(scroll_max), "--scroll-speed", str(scroll_speed),
           "--chrome", chrome or chrome_exe()]
    if scroll_ms > 0:
        cmd += ["--audio-dur", str(scroll_ms)]
    if hold_ms > 0:
        cmd += ["--hold", str(hold_ms)]
    if js_file:
        cmd += ["--js", str(js_file)]
    use_gpu = gpu if gpu is not None else detect_gpu()
    if not use_gpu:
        cmd += ["--no-gpu"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not (fr / "frames.txt").exists():
        raise RuntimeError("录网页失败 %s: %s" % (url, (r.stderr or r.stdout or "")[-300:]))
    clip = fr / "clip.mp4"
    try:
        from gpu import video_encoder
        enc = video_encoder()
    except Exception:
        enc = "libx264"
    venc = (["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "20", "-b:v", "0"]
            if enc == "h264_nvenc" else ["-c:v", "libx264", "-preset", "medium", "-crf", "18"])
    rr = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
                         "-i", str(fr / "frames.txt"),
                         "-vf", "fps=%d,scale=%d:%d,setsar=1" % (fps, w, h)]
                        + venc + ["-pix_fmt", "yuv420p", str(clip)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if rr.returncode != 0 and enc != "libx264":
        rr = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
                             "-i", str(fr / "frames.txt"),
                             "-vf", "fps=%d,scale=%d:%d,setsar=1" % (fps, w, h),
                             "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                             "-pix_fmt", "yuv420p", str(clip)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if rr.returncode != 0 or not clip.exists():
        raise RuntimeError("录网页归一化失败 %s: %s" % (url, (rr.stderr or "")[-300:]))
    for p in fr.glob("f_*.*"):
        p.unlink()
    print("URL", name, (r.stdout or "").strip(), flush=True)
    return clip
