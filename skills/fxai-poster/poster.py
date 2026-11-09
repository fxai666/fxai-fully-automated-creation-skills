#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fxai-poster · 海报与图片设计（网页模式 / 生图模式 / 混合模式）

三种模式：
  web     HTML/CSS 排版 -> Chrome headless 截图（文字为主、排版精确）
  image   ComfyUI 千问生图（真实质感 / 人物 / 场景）
  hybrid  先生图做底图 -> 网页叠字排版 -> 截图（质感 + 精准文字）

产物统一落 <out>/<name>.png；--attach-video 可直接铺到视频首帧。
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
THEMES = os.path.join(SKILL, "themes")

_SKILLS = os.path.dirname(SKILL)
sys.path.insert(0, os.path.join(_SKILLS, "fxai-scene-gen"))
sys.path.insert(0, os.path.join(_SKILLS, "fxai-audio-video"))
sys.path.insert(0, os.path.join(_SKILLS, "fxai-slide-video"))

# 浏览器寻址、品牌信息、ffmpeg 全部委托对应技能，本技能不自己探测、不写死地址
from gpu import chrome_exe, brand, official_site_url  # noqa: E402

# 截图与 ffmpeg 的临时目录（中文路径会让 ffmpeg 滤镜 / file:// 解析失败）
BURN = os.path.join(tempfile.gettempdir(), "fxai_burn", "poster")


# ---------------------------------------------------------------- 基础

def default_brand():
    b = brand() or {}
    return b.get("name") or "凤希AI"


def default_site():
    """海报底部只显示域名，不带协议与尾斜杠。"""
    try:
        u = official_site_url()
    except Exception:
        u = "https://" + ((brand() or {}).get("official_site") or "www.fxai.site")
    u = u.strip()
    for p in ("https://", "http://"):
        if u.startswith(p):
            u = u[len(p):]
    return u.rstrip("/")


def load_theme(name):
    path = os.path.join(THEMES, name if name.endswith(".css") else name + ".css")
    if not os.path.isfile(path):
        avail = ", ".join(sorted(f[:-4] for f in os.listdir(THEMES) if f.endswith(".css")))
        raise SystemExit("! 没有主题 %s，可用：%s" % (name, avail))
    return open(path, encoding="utf-8").read()


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _lines(s):
    """标题支持 | 或 \\n 分行"""
    return [x.strip() for x in str(s).replace("\\n", "|").split("|") if x.strip()]


# ---------------------------------------------------------------- 网页模板

def build_html(css_vars, tag, title, kicker, sub, keys, brand, site,
               bg_image=None, overlay=0.55):
    """三段结构海报：胶囊标签 → 大标题 → 关键词 + 品牌条。
    bg_image 存在时为混合模式：底图铺满 + 暗色蒙板保证文字可读。"""
    tl = _lines(title) or [""]
    h1 = "<br>".join(
        _esc(t) if i == 0 else '<em>%s</em>' % _esc(t)
        for i, t in enumerate(tl))
    key_html = "".join('<div class="key">%s</div>' % _esc(k) for k in keys)
    bg_css = ('background-image:linear-gradient(165deg,%s 0%%,%s 46%%,%s 100%%);'
              % (css_vars.get("--bg1", "#070c18"), css_vars.get("--bg2", "#0d1830"),
                 css_vars.get("--bg3", "#0a1226")))
    img_css = ""
    if bg_image:
        img_css = ('body::after{content:"";position:absolute;inset:0;'
                   'background:url("%s") center/cover no-repeat;'
                   'opacity:%s;}' % (bg_image, overlay))

    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%%;height:100%%;overflow:hidden}
body{%s background-color:%s;font-family:%s;color:%s;position:relative}
body::before{content:"";position:absolute;inset:0;z-index:0;
  background-image:linear-gradient(%s 1px,transparent 1px),
                   linear-gradient(90deg,%s 1px,transparent 1px);
  background-size:48px 48px;
  -webkit-mask-image:radial-gradient(circle at 50%% 38%%,#000 0%%,transparent 76%%);
          mask-image:radial-gradient(circle at 50%% 38%%,#000 0%%,transparent 76%%)}
%s
.wrap{position:relative;z-index:2;padding:74px 60px;height:100%%;
  display:flex;flex-direction:column}
.chip{display:inline-flex;align-items:center;gap:13px;align-self:flex-start;
  padding:13px 27px;border-radius:999px;border:1.5px solid %s;
  background:%s;font-size:27px;letter-spacing:3px;color:%s;font-weight:700}
.chip i{width:11px;height:11px;border-radius:50%%;background:%s;display:block}
.spacer{flex:1}
.kicker{font-size:31px;letter-spacing:5px;color:%s;font-weight:700;margin-bottom:26px}
.h1{font-size:88px;line-height:1.18;font-weight:800;letter-spacing:1px;margin-bottom:14px}
.h1 em{font-style:normal;color:%s}
.rule{width:132px;height:6px;border-radius:3px;margin:44px 0 34px;
  background:linear-gradient(90deg,%s,%s)}
.sub{font-size:33px;line-height:1.5;color:%s;font-weight:600}
.keys{display:flex;gap:16px;margin-top:44px}
.key{padding:13px 26px;border-radius:14px;font-size:28px;font-weight:700;
  background:%s;border:1.5px solid %s;color:%s}
.brand{position:absolute;left:60px;right:60px;bottom:74px;z-index:2;
  display:flex;align-items:center;justify-content:space-between;
  padding-top:26px;border-top:1.5px solid %s}
.brand b{font-size:31px;letter-spacing:2px;color:%s;font-weight:800}
.brand span{font-size:24px;color:%s;letter-spacing:1px;font-weight:600}
</style></head><body>
<div class="wrap">
  %s
  <div class="spacer"></div>
  %s
  <div class="h1">%s</div>
  <div class="rule"></div>
  %s
  %s
  <div class="spacer"></div>
</div>
%s
</body></html>
""" % (
        bg_css, css_vars.get("--bg1", "#070c18"),
        css_vars.get("--font", '"Microsoft YaHei",sans-serif'),
        css_vars.get("--ink", "#f5fbe8"),
        css_vars.get("--grid", "rgba(255,255,255,.05)"),
        css_vars.get("--grid", "rgba(255,255,255,.05)"),
        img_css,
        css_vars.get("--ac", "#38bdf8"), css_vars.get("--acsoft", "rgba(56,189,248,.1)"),
        css_vars.get("--ac", "#38bdf8"), css_vars.get("--ac", "#38bdf8"),
        css_vars.get("--ink3", "#7f93b5"),
        css_vars.get("--hl", "#f5c451"),
        css_vars.get("--rule1", "#38bdf8"), css_vars.get("--rule2", "#f5c451"),
        css_vars.get("--ink2", "#b9c8de"),
        css_vars.get("--panelrgba", "rgba(255,255,255,.07)"),
        css_vars.get("--panelborder", "rgba(255,255,255,.16)"),
        css_vars.get("--ink", "#f5fbe8"),
        css_vars.get("--panelborder", "rgba(255,255,255,.16)"),
        css_vars.get("--ink", "#f5fbe8"), css_vars.get("--ink3", "#7f93b5"),
        ('<div class="chip"><i></i>%s</div>' % _esc(tag)) if tag else "",
        ('<div class="kicker">%s</div>' % _esc(kicker)) if kicker else "",
        h1,
        ('<div class="sub">%s</div>' % _esc(sub)) if sub else "",
        ('<div class="keys">%s</div>' % key_html) if keys else "",
        ('<div class="brand"><b>%s</b><span>%s</span></div>' % (_esc(brand), _esc(site)))
        if (brand or site) else "",
    )


def screenshot(html_path, png_path, w, h):
    cmd = [chrome_exe(), "--headless=new", "--disable-gpu", "--no-sandbox",
           "--hide-scrollbars", "--force-device-scale-factor=1",
           "--window-size=%d,%d" % (w, h),
           "--screenshot=%s" % png_path, "file:///" + html_path.replace("\\", "/")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(png_path):
        raise SystemExit("! 截图失败：%s" % (r.stderr[-500:] or "无产物"))
    return png_path


def parse_vars(css):
    """从主题 CSS 的 :root{} 里取出变量"""
    out = {}
    body = css.split(":root", 1)[-1]
    body = body.split("}", 1)[0] if "}" in body else body
    for decl in body.split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            k = k.strip()
            if k.startswith("--"):
                out[k] = v.strip()
    return out


# ---------------------------------------------------------------- 三种模式

def gen_web(out_png, w, h, theme, tag, title, kicker, sub, keys, brand, site):
    v = parse_vars(load_theme(theme))
    html = build_html(v, tag, title, kicker, sub, keys, brand, site)
    os.makedirs(BURN, exist_ok=True)
    hp = os.path.join(BURN, "p.html")
    open(hp, "w", encoding="utf-8").write(html)
    screenshot(hp, os.path.join(BURN, "p.png"), w, h)
    shutil.copy(os.path.join(BURN, "p.png"), out_png)
    return out_png


def gen_image(out_png, w, h, prompt, subdir="poster_img", tag=None, role=None,
               force=False):
    """生图：委托 fxai-scene-gen。role=None 纯画面；封面要人物时传作品自己的角色参考图相对路径。

    本地 GPU / 云端容器走同一个 HTTP 接口，服务寻址由 fxai-workflow 负责，本处不写地址。
    """
    from scene_gen import gen_scene_image
    dst, rel = gen_scene_image(prompt, role=role, w=w, h=h,
                               tag=tag or "poster", subdir=subdir, force=force)
    shutil.copy(dst, out_png)
    return out_png


def gen_hybrid(out_png, w, h, theme, prompt, tag, title, kicker, sub, keys,
               brand, site, overlay=0.55, subdir="poster_img", role=None):
    """先生图 → 当底图 → 网页叠字 → 截图"""
    os.makedirs(BURN, exist_ok=True)
    bg_tmp = os.path.join(BURN, "bg.png")
    gen_image(bg_tmp, w, h, prompt, subdir=subdir,
              tag=(tag or "poster") + "_bg", role=role)
    v = parse_vars(load_theme(theme))
    html = build_html(v, tag, title, kicker, sub, keys, brand, site,
                      bg_image="bg.png", overlay=overlay)
    hp = os.path.join(BURN, "p.html")
    open(hp, "w", encoding="utf-8").write(html)
    screenshot(hp, os.path.join(BURN, "p.png"), w, h)
    shutil.copy(os.path.join(BURN, "p.png"), out_png)
    return out_png


# ---------------------------------------------------------------- 接入视频首帧

def attach_to_video(png, video, out=None, frames=10, fps=None,
                    bed_db=-56.0, bed_hz=25, dur=None):
    """把海报做成 **frames 帧**片段拼到视频前（平台安全版）。

    🔴 **顺序规则：BGM 混音必须在铺封面之后**（`bed` 垫音不动，仍是 25Hz 正弦）。
    封面段自带的是垫音，不是 BGM；要让整条音轨从第 0 帧（封面第一帧）就响着配乐，
    做法是把铺完封面的成片再混一次 BGM —— `mix_bgm` 本来就铺满整片，
    封面这一截自然被覆盖。反过来「先混 BGM 再铺封面」会把封面段替换成垫音，
    BGM 在第 0 帧缺一截，平台可能把开头当空白。规范见 04 册「铺封面与 BGM 的先后」。

    🔴 封面段长度**按帧定义，不按秒**：默认 **10 帧**（25fps 下 0.4s）。
    抖音实测：10 帧版在主页「动态封面」（观众端开关，默认开，会播视频开头一小段并循环）
    里不跳变；1 秒版会把正片第一个镜头卷进循环窗口，看着「闪」。
    帧数越少越安全，但也越看不清——10 帧是「不闪」与「还看得见一眼」的平衡点。
    ⚠️ 出片段必须用 `trim=end_frame` 卡死帧数，不得只给 `-t 秒数`：浮点取整会出 9 或 11 帧。

    🔴 另外两个致命点，都踩过：
    1. **不得用 anullsrc**：全零采样会被平台转码器判成「空音频块」，从第一个
       有声采样点开始切，画面还在原地 → 音画错位（视频号表现是「咔嚓跳一下、
       后面口型全跑」）。改成垫次声正弦（有真实采样、听不见），末尾淡出。
    2. **必须 faststart**：moov 在 mdat 后面时，网页端上传/转码解析容易异常。
       一次 filter_complex 编码（不做 concat copy），时间戳天然连续，顺带锁成 CFR。

    参数 bed_db / bed_hz：垫底用的是**次声正弦**（默认 25Hz，输入 -56dB），不是粉噪。
    ⚠️ 封面段这 0.4s 里只有垫音、没有 BGM —— 所以 **BGM 必须排在铺封面之后混**（见上）。
    🔴 历史教训：40Hz @ 输出 -50dB 被用户否了两次 ——「呼呼呼」「我音箱放出来很大」。
    原因是**带低音单元的音箱推得出 40Hz**，-50dBFS 约合 55dB SPL，高于室内底噪，
    在低音炮上就是一片隆隆声。「手机外放听不见」不等于音箱听不见，别按手机估。
    现在压到 25Hz、输出约 -72dB：低于人耳在本底噪声下的可闻阈，也低于多数音箱下潜。
    ⚠️ AAC 对 25Hz 有约 18dB 编码损失（实测 -40→-58 / -50→-68 / -60→-78 / -70→-90），
    改电平必须按映射重测，别拿输入值当输出值。
    bed_db=None 可关掉，但平台该裁还是裁 —— 那说明问题不在电平，见 04 册 Plan B。

    兼容参数 dur：老调用传秒数时按 `round(dur * fps)` 折算成帧数，仅作过渡。
    """
    import audio_video as av
    ffmpeg = av.ffmpeg_libass()
    ffprobe = os.path.join(os.path.dirname(ffmpeg),
                           "ffprobe.exe" if ffmpeg.endswith(".exe") else "ffprobe")
    os.makedirs(BURN, exist_ok=True)
    b_png, b_in, b_out = "poster.png", "in.mp4", "with_cover.mp4"
    shutil.copy(png, os.path.join(BURN, b_png))
    shutil.copy(video, os.path.join(BURN, b_in))

    def probe(sel, entries):
        return subprocess.run([ffprobe, "-v", "error", "-select_streams", sel,
                               "-show_entries", entries, "-of", "csv=s=x:p=0", b_in],
                              cwd=BURN, capture_output=True, text=True).stdout.strip()

    a = probe("a:0", "stream=sample_rate,channels")
    rate, ch = ("48000", "1")
    if "x" in a:
        r0, c0 = a.split("x")[:2]
        rate = "48000" if r0 not in ("44100", "48000") else r0
        ch = c0 or "1"
    ch = "2" if ch == "2" else "1"
    layout = "stereo" if ch == "2" else "mono"

    v = probe("v:0", "stream=width,height")
    pw, ph = (v.split("x")[:2] if "x" in v else ("1080", "1920"))
    pw, ph = int(pw) // 2 * 2, int(ph) // 2 * 2

    # 帧率：显式给了就用，没给就探源片（封面段按帧定义，fps 必须准）
    if not fps:
        fr = probe("v:0", "stream=r_frame_rate")
        try:
            n, d = (fr.split("/") + ["1"])[:2]
            fps = int(round(float(n) / float(d or 1)))
        except Exception:
            fps = 25
    fps = int(fps) or 25

    # 兼容老的 dur 调用：按秒折算成帧
    if dur is not None:
        frames = max(1, int(round(float(dur) * fps)))
    frames = int(frames) or 10
    cover_sec = frames / float(fps)

    fit = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
           "pad=%d:%d:(ow-iw)/2:(oh-ih)/2" % (pw, ph, pw, ph))
    fc = (
        # 封面段：trim=end_frame 卡死帧数（只给 -t 会出 9/11 帧）
        "[0:v]%s,setsar=1,fps=%d,trim=end_frame=%d,setpts=PTS-STARTPTS,"
        "format=yuv420p[v0];"
        "[1:v]%s,setsar=1,fps=%d,format=yuv420p[v1];"
        "[v0][v1]concat=n=2:v=1:a=0[v];"
        "[2:a]volume=%.1fdB,aformat=sample_fmts=fltp:sample_rates=%s:channel_layouts=%s,"
        "atrim=end=%.4f,asetpts=PTS-STARTPTS,afade=t=out:st=%.3f:d=0.06[a0];"
        "[1:a]aformat=sample_fmts=fltp:sample_rates=%s:channel_layouts=%s,"
        "asetpts=PTS-STARTPTS[a1];"
        "[a0][a1]concat=n=2:v=0:a=1[a]"
        % (fit, fps, frames, fit, fps, bed_db, rate, layout, cover_sec,
           max(0.0, cover_sec - 0.06), rate, layout))

    r = subprocess.run(
        [ffmpeg, "-y",
         "-loop", "1", "-framerate", str(fps), "-t", "%.4f" % (cover_sec + 0.2), "-i", b_png,
         "-i", b_in,
         "-f", "lavfi", "-t", "%.4f" % (cover_sec + 0.2),
         "-i", "sine=frequency=%d:sample_rate=%s" % (int(bed_hz), rate),
         "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-maxrate", "8M", "-bufsize", "16M",
         "-pix_fmt", "yuv420p", "-r", str(fps), "-g", str(fps * 2),
         "-profile:v", "high", "-level", "4.1",
         "-c:a", "aac", "-b:a", "192k", "-ar", rate, "-ac", ch,
         "-movflags", "+faststart", b_out],
        cwd=BURN, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("! 首帧拼接失败：%s" % r.stderr[-1200:])

    out = out or video.replace(".mp4", "_带封面.mp4")
    shutil.copy(os.path.join(BURN, b_out), out)
    return out



# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(
        description="海报与图片设计（web 网页 / image 生图 / hybrid 混合）")
    ap.add_argument("--mode", default="web", choices=["web", "image", "hybrid"])
    ap.add_argument("--out-dir", default=".", help="产物目录")
    ap.add_argument("--name", default="poster", help="产物文件名（不含扩展名）")
    ap.add_argument("--size", default="1080x1920", help="宽x高，默认 vert_9x16 (1080x1920)；尺寸唯一真源 cover_sizes.json")
    ap.add_argument("--theme", default="深色科技", help="网页/混合模式主题")
    ap.add_argument("--tag", default="", help="胶囊标签（内容属性词）")
    ap.add_argument("--title", default="", help="主标题，| 或 \\n 分行；第二行起为强调色")
    ap.add_argument("--kicker", default="", help="标题上方小字")
    ap.add_argument("--sub", default="", help="副标题")
    ap.add_argument("--keys", default="", help="关键词，逗号分隔")
    ap.add_argument("--brand", default=None,
                    help="署名文字；默认不显示（媒体平台不允许）。给 auto = 用全局配置，给文字 = 自定义")
    ap.add_argument("--site", default=None,
                    help="网址；默认不显示（媒体平台不允许）。给 auto = 用全局配置，给文字 = 自定义")
    ap.add_argument("--prompt", default="", help="image/hybrid 模式的生图提示词")
    ap.add_argument("--role", default="",
                    help="作品自有角色参考图相对路径；留空=纯场景不含人物（封面须人物时必填）")
    ap.add_argument("--overlay", type=float, default=0.55, help="混合模式底图不透明度")
    ap.add_argument("--attach-video", default="", help="把海报铺到该视频首帧")
    ap.add_argument("--cover-frames", type=int, default=10,
                    help="首帧海报帧数，默认 10（按帧定义，不按秒；25fps 下 0.4s）")
    ap.add_argument("--cover-dur", type=float, default=None,
                    help="兼容旧参：首帧海报时长（秒），给了会按源片帧率折算成帧数")
    a = ap.parse_args()

    w, h = (int(x) for x in a.size.lower().split("x"))
    os.makedirs(a.out_dir, exist_ok=True)
    out_png = os.path.join(a.out_dir, a.name + ".png")
    keys = [k.strip() for k in a.keys.split(",") if k.strip()]
    brand_name = None
    if a.brand is not None:
        brand_name = default_brand() if a.brand == "auto" else a.brand
    site_url = None
    if a.site is not None:
        site_url = default_site() if a.site == "auto" else a.site

    if a.mode == "web":
        if not a.title:
            raise SystemExit("! web 模式必须给 --title")
        gen_web(out_png, w, h, a.theme, a.tag, a.title, a.kicker, a.sub,
                keys, brand_name, site_url)
    elif a.mode == "image":
        if not a.prompt:
            raise SystemExit("! image 模式必须给 --prompt")
        gen_image(out_png, w, h, a.prompt, role=a.role or None)
    else:
        if not a.prompt:
            raise SystemExit("! hybrid 模式必须给 --prompt（底图）")
        gen_hybrid(out_png, w, h, a.theme, a.prompt, a.tag, a.title, a.kicker,
                   a.sub, keys, brand_name, site_url, a.overlay, role=a.role or None)

    print("[poster] ->", out_png)
    if a.attach_video:
        v = attach_to_video(out_png, a.attach_video,
                            frames=a.cover_frames, dur=a.cover_dur)
        print("[poster] 已铺到首帧 ->", v)


def cover_head_swap(png, video, out=None, frames=10, fps=None):
    """Plan B：封面顶掉源片开头 frames 帧的**画面**，音频一律沿用源轨，不拼、不补任何声音。

    ⚠️ **用户已否掉这条路**（「盖上去不就把我们完整视频给破坏了吗」）。
    只有源片开头确实是黑屏/空镜、盖掉不损失内容时才用；源片开头就有人声口播时不得用。

    适用：源片第 0 帧就有人声，前几帧的静音完全是拼接引入的——此时最稳的做法不是把
    填充音压到多低，而是**根本不制造静音段**。

    和 attach_to_video 的区别：
      - attach_to_video：视频 + 音频都往后推，前面补一段（必须补音频，否则被切）
      - cover_head_swap：视频只换画面不延长，音频从 0 原样播，时长不变、零接缝

    代价：源片开头烧进去的字幕会随画面一起被盖掉（「有声无字幕」）。
    封面段帧数的定案见 references/05-封面模板库.md「封面段时长」。
    """
    import audio_video as av
    ffmpeg = av.ffmpeg_libass()
    ffprobe = os.path.join(os.path.dirname(ffmpeg),
                           "ffprobe.exe" if ffmpeg.endswith(".exe") else "ffprobe")

    def q(args):
        return subprocess.run([ffprobe, "-v", "error"] + args + ["-of", "default=nw=1:nk=1",
                                video], capture_output=True, text=True).stdout.strip()

    w = int(q(["-select_streams", "v:0", "-show_entries", "stream=width"]))
    h = int(q(["-select_streams", "v:0", "-show_entries", "stream=height"]))
    ch = int(q(["-select_streams", "a:0", "-show_entries", "stream=channels"]) or 1)
    if fps is None:
        r = q(["-select_streams", "v:0", "-show_entries", "stream=r_frame_rate"])
        try:
            n, d = r.split("/")
            fps = round(float(n) / float(d or 1))
        except Exception:
            fps = 25
    layout = "stereo" if ch >= 2 else "mono"

    frames = int(frames) or 10
    cover_sec = frames / float(fps)
    fc = ("[0:v]scale=%d:%d:force_original_aspect_ratio=decrease,"
          "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=%d,"
          "format=yuv420p,trim=end_frame=%d,setpts=PTS-STARTPTS[v0];"
          "[1:v]trim=start_frame=%d,setpts=PTS-STARTPTS[v1];"
          "[v0][v1]concat=n=2:v=1:a=0[v];"
          "[1:a]asetpts=PTS-STARTPTS,"
          "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=%s[a]"
          % (w, h, w, h, fps, frames, frames, layout))
    cmd = [ffmpeg, "-y", "-v", "error",
           "-loop", "1", "-framerate", str(fps), "-t", "%.4f" % (cover_sec + 0.2), "-i", png,
           "-i", video,
           "-filter_complex", fc,
           "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-maxrate", "8M", "-bufsize", "16M",
           "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
           "-r", str(fps), "-g", str(fps * 2),
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", str(ch),
           "-movflags", "+faststart", "-shortest", out or video.replace(".mp4", "_换头.mp4")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("! 换头失败：%s" % r.stderr[-900:])
    return out or video.replace(".mp4", "_换头.mp4")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
