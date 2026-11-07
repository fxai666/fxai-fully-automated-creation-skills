#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""设计质检门禁：出图前查规格，出图后查成图。

用法：
    python qc.py pre  --spec spec.json [--json]
    python qc.py pre  --kind record_cover --w 1680 --h 1054 --title "一个人|顶一支团队" \
                      --sub "写稿·配音·出镜·剪辑 全是 AI" --tag "完整实操教程" --hooks 数字前置,反差反转
    python qc.py post --img cover.png [--kind record_cover] [--json]
    python qc.py types                       # 列出用途类型与规格

退出码：0 = PASS/WARN，2 = FAIL（脚本可直接用于流水线卡口）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import sys

_SKILLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_SKILLS, "fxai-audio-video"))

# 用途类型库：尺寸与字数上限是唯一出处，08 册引本表
TYPES = {
    "record_cover": dict(
        label="录屏 / 教程视频封面", size="1680x1054", mode=("web", "hybrid", "image"),
        title_chars=7, title_lines=2, sub_chars=20, tag_chars=10, keys=3,
        require=("title",), hooks_min=2,
    ),
    "video_cover": dict(
        label="竖版视频封面（抖音 / 视频号 / 快手）", size="768x1280", mode=("web", "hybrid", "image"),
        title_chars=7, title_lines=2, sub_chars=14, tag_chars=10, keys=3,
        require=("title",), hooks_min=2,
    ),
    "video_cover_h": dict(
        label="横版视频封面（B 站 / YouTube）", size="1280x768", mode=("web", "hybrid", "image"),
        title_chars=9, title_lines=2, sub_chars=18, tag_chars=10, keys=3,
        require=("title",), hooks_min=2,
    ),
    "poster": dict(
        label="海报（活动 / 课程 / 品牌 / 招聘）", size="1080x1440", mode=("web", "hybrid"),
        title_chars=8, title_lines=2, sub_chars=22, tag_chars=12, keys=4,
        require=("title",), hooks_min=2,
    ),
    "article_head": dict(
        label="文章头图 / 公众号封面", size="900x383", mode=("web", "hybrid"),
        title_chars=10, title_lines=2, sub_chars=16, tag_chars=10, keys=2,
        require=("title",), hooks_min=1,
    ),
    "article_inline": dict(
        label="文章内插图 / 段落配图", size="1080x810", mode=("image", "hybrid"),
        title_chars=12, title_lines=1, sub_chars=20, tag_chars=10, keys=0,
        require=(), hooks_min=0,
    ),
    "xhs_note": dict(
        label="小红书笔记封面", size="1080x1440", mode=("web", "hybrid", "image"),
        title_chars=8, title_lines=2, sub_chars=18, tag_chars=10, keys=3,
        require=("title",), hooks_min=2,
    ),
    "social_square": dict(
        label="方图（朋友圈 / IG Feed / 微博）", size="1080x1080", mode=("web", "hybrid", "image"),
        title_chars=8, title_lines=2, sub_chars=16, tag_chars=10, keys=3,
        require=("title",), hooks_min=1,
    ),
    "ecom_main": dict(
        label="电商主图（淘宝 / 京东 / 拼多多）", size="800x800", mode=("hybrid", "web"),
        title_chars=6, title_lines=2, sub_chars=12, tag_chars=8, keys=2,
        require=("title",), hooks_min=2,
    ),
    "ecom_detail": dict(
        label="电商详情长图", size="750x3000", mode=("web", "hybrid"),
        title_chars=10, title_lines=2, sub_chars=24, tag_chars=12, keys=4,
        require=("title",), hooks_min=1,
    ),
    "infographic": dict(
        label="信息图 / 一图读懂 / 框架图", size="1080x1440", mode=("web",),
        title_chars=14, title_lines=2, sub_chars=24, tag_chars=12, keys=0,
        require=("title", "blocks"), hooks_min=0,
    ),
    "video_overlay": dict(
        label="视频内嵌图（标题卡 / 信息贴片 / 角标 / 人物卡）", size="1080x1920", mode=("web",),
        title_chars=14, title_lines=2, sub_chars=28, tag_chars=12, keys=0,
        require=("title",), hooks_min=0,
    ),
    "podcast_cover": dict(
        label="播客 / 音频专辑封面", size="3000x3000", mode=("web", "hybrid"),
        title_chars=10, title_lines=2, sub_chars=18, tag_chars=10, keys=0,
        require=("title",), hooks_min=1,
    ),
    "channel_banner": dict(
        label="频道横幅 / 主页头图", size="2560x1440", mode=("web", "hybrid"),
        title_chars=12, title_lines=1, sub_chars=20, tag_chars=10, keys=0,
        require=("title",), hooks_min=1,
    ),
    "live_cover": dict(
        label="直播封面 / 预告图", size="1080x1920", mode=("web", "hybrid", "image"),
        title_chars=8, title_lines=2, sub_chars=18, tag_chars=12, keys=3,
        require=("title", "time"), hooks_min=2,
    ),
    "scene": dict(
        label="纯画面 / 场景图 / 人物图 / 氛围底图（无文字）", size="1680x1054",
        mode=("image",),
        title_chars=0, title_lines=0, sub_chars=0, tag_chars=0, keys=0,
        require=(), hooks_min=0,
    ),
}

# 锐度档位：按「字多字少、画幅、主题」分档取值，一套绝对值量不住所有图
#   high = 字多且结构密（信息图 / 长图 / 横幅）
#   low  = 字少或没有字（插图 / 内嵌图 / 纯画面）
SHARP_TIER = {
    "infographic": "high",
    "ecom_detail": "high",
    "channel_banner": "high",
    "article_inline": "low",
    "video_overlay": "low",
    "scene": "low",
}

HOOKS = ("热点词", "数字前置", "反差反转", "身份标签", "结果承诺", "紧迫感")

# 成图判据（阈值由本机实测样板校准：正常封面缩略梯度 30~49 / 模糊 15 / 低清锐度 11 / 纯色 RMS 0）
TH = dict(
    rms_min=28.0,        # 全图灰度标准差：低于此值画面发平
    rms_max=88.0,        # 过高通常是噪点/过曝细节
    sat_min=0.14,        # HSV 饱和度均值：媒体推广图要有色彩冲击
    bright_min=20.0,     # 灰度均值下限：整图压死（深色科技风实测 25.9）
    bright_max=228.0,    # 灰度均值上限：整图发白
    text_area_min=0.12,  # 高梯度像素占比下限：简洁排版实测 14.3%，纯图 5~10%
    text_area_max=0.45,  # 上限：信息过载
    # 梯度类判据按类型档位取值（实测：竖版深色 64 / 竖版浅色 25 / 方图浅色 28 / 横版 81）
    # 梯度同时受字号、对比度、画幅影响，绝对值只做「明显不合格」的下限
    sharp_tier=dict(
        high=dict(edge=35.0, thumb=18.0, p95=70.0),
        mid=dict(edge=20.0, thumb=14.0, p95=45.0),
        low=dict(edge=10.0, thumb=10.0, p95=20.0),
    ),
    # 重采样一致性：缩 1/2 再放大回原尺寸，与原图比 PSNR。
    # 越高说明原图越缺高频（模糊或小图拉伸）。实测：清晰 27.7~42.6 / 低清 43.6~51.7 / 模糊 54.1~57.3
    psnr_max=44.0,       # 高于此值判「缺高频」：按目标尺寸重出
    edge_ratio_max=1.8,  # 四边安全区梯度密度 / 全图密度，过高=关键内容贴边
    tile_flat_min=6.0,   # 最平网格的对比度：存在死区（只提示不判失败）
)


# 生图提示词要素（千问2.1 口径：文字是它的强项，文案该写就写进引号）
PROMPT_RULES = (
    ("画布与质感", "写画幅比例 + 质感总纲（写实摄影 / 3D 渲染 / 电影感 / 扁平插画）",
     r"摄影|写实|电影感|电影级|3D|渲染|插画|扁平|胶片|纪实|商业摄影|画面比例|画幅|竖版|横版|方形"),
    ("主体", "写清画面里是谁 / 是什么，以及它在做什么",
     r"人物|男人|女人|男性|女性|男孩|女孩|男生|女生|人像|角色|双手|一只手|机器人|界面|产品|物体|主体|站在|坐在|正在|手持|拿着|穿|戴|背影|侧脸"),
    ("场景", "交代环境与所在位置，让主体有落点",
     r"背景|场景|环境|四周|室内|户外|街道|街边|房间|窗外|桌面|地面|空中|城市|森林|海岸|工作室|实验室|台上|墙|庭院|院子|花园|树林|山|湖|河|桥|屋顶|天台|走廊|台阶|石阶|殿|阁|楼|车站|机场|教室|操场|厨房|阳台|门口|棚|办公室|大厅|广场|码头"),
    ("光线", "给方向 + 质感，光比是画面强度的来源",
     r"逆光|侧光|顶光|底光|柔光|硬光|霓虹|暖金|冷蓝|晨光|黄昏|夜色|夜景|光晕|丁达尔|体积光|打光|补光|光斑|辉光|glow|rim light|光源"),
    ("构图落位", "定景别、机位与主体占比，决定信息层级",
     r"特写|近景|中景|全景|远景|俯拍|俯视|仰拍|仰视|平视|三分|居中|对称|浅景深|景深|广角|长焦|微距|低角度|高角度|占画面|画面(中部|上方|下方|左侧|右侧)"),
)

# 文字区四要素：文案生在图上时必须逐条写清
TEXT_RULES = (
    ("引号文案", "逐字要呈现的文案用双引号包住，引号外才是描述",
     r'["“][^"”]{1,40}["”]'),
    ("文案位置", "写明每条文案落在画面哪个位置",
     r"画面(中部|上方|下方|左侧|右侧|中央|正中)|左上角|右上角|左下角|右下角|底部居中|顶部居中|居左|居右"),
    ("字号层级", "写明字号关系，否则主副标一样大",
     r"大字|小字|字号|大一号|小一号|占画面.{0,4}(宽|高)|字号占|行距|主标|副标"),
    ("字色字体", "写明颜色与字体风格，否则与底图撞色",
     r"白色|黑色|金色|红色|蓝色|绿色|紫色|粗体|粗黑体|黑体|宋体|楷体|圆体|综艺|描边|发光|渐变字|加粗"),
)

TAGSTACK = re.compile(r"8k|4k|masterpiece|best quality|ultra detailed|highly detailed|超高清超精细", re.I)
NEGATIVE = re.compile(r"不要出现|不要有|避免出现|禁止出现|不能出现|不要写")  # std:allow
OUTFIT = re.compile(r"衬衫|外套|西装|裙|纱|卫衣|风衣|毛衣|上衣|T恤|礼服|斗篷|工装|针织|马甲|衣|服")
EXPOSE_RISK = re.compile(r"裸露|裸肩|赤裸|上身无衣|无上衣|全裸")


def check_prompt(text, has_role=False, need_text=True):
    """提示词要素检查：缺哪类补哪类。need_text=True 时按文案生在图上查文字区。"""
    checks = []
    t = text or ""

    def add(name, ok, expect, fix, warn=False, value=None):
        checks.append(dict(item=f"提示词·{name}", ok=bool(ok),
                           value=value if value is not None else ("已写" if ok else "缺失"),
                           expect=expect, fix=fix, level="warn" if warn else "fail"))

    for name, why, pat in PROMPT_RULES:
        add(name, re.search(pat, t, re.I), why, f"补「{name}」：{why}")

    if need_text:
        for name, why, pat in TEXT_RULES:
            add(name, re.search(pat, t, re.I), why, f"补「{name}」：{why}")
        quoted = re.findall(r'["“]([^"”]{1,40})["”]', t)
        add("文案条数", len(quoted) <= 4, "≤4 条（主标 / 副标 / 徽章 / 底栏）",
            "文案超过四条模型会合并或丢条：保留主标 + 副标 + 徽章 + 底栏，其余挪到叠加层",
            value=f"{len(quoted)} 条")
        long_lines = [q for q in quoted if len(q) > 14]
        add("引号内字数", not long_lines, "单条 ≤14 字；主标另按 ≤7 字（横版 ≤9）",
            f"超长文案：{'、'.join(long_lines[:3])}；拆成两行或换短词，长句模型会漏字折行",
            value=f"最长 {max((len(q) for q in quoted), default=0)} 字")

    if has_role:
        add("人物着装", bool(OUTFIT.search(t)), "挂角色参考图时必须写着装",
            "补服装描写（衬衫 / 西装 / 演出服 / 透明薄纱）；参考图肩部以上无上衣，漏写直接出问题")
        add("着装红线", not EXPOSE_RISK.search(t), "胸部与私密部位不裸露",
            "改成「透而不露」：透明薄纱、贴身礼服、演出服，遮住胸部与私密部位")
        add("参考图绑定", bool(re.search(r"<image\d+>", t)), "每张参考图写一个 <imageN> 标签",
            "补 <image1> 并说明它对应画面里哪个人物或元素；编号按参考图顺序（男主在前），少一个就被生图端拦下")
        add("发饰配饰", bool(re.search(r"耳饰|耳环|项链|发带|发饰|发簪|帽子|眼镜|戒指|手链|配饰", t)),
            "写耳饰 / 项链 / 发带一类配饰",
            "补一件配饰（如细银耳饰、细项链），人物质感明显更精致", warn=True)

    bad = TAGSTACK.findall(t)
    add("标签堆", not bad, "用自然语言句子，不堆 8k / masterpiece 类标签",
        f"删掉 {','.join(sorted(set(bad)))}：千问2.1 不认标签堆，换成具体镜头、光线、材质描述",
        warn=True, value=f"含 {','.join(sorted(set(bad)))}" if bad else "无")

    neg = NEGATIVE.findall(t)
    add("负向描述", not neg, "只做正向描述，靠具体内容挤掉不需要的元素",
        f"改写 {'、'.join(sorted(set(neg)))} 那句：千问2.1 没有负向字段，直接写画面里有什么",
        warn=True, value=f"含 {'、'.join(sorted(set(neg)))}" if neg else "无")

    fails = [c for c in checks if not c["ok"] and c["level"] == "fail"]
    return dict(stage="prompt", verdict="PASS" if not fails else "FAIL",
                score=round(100 * (len(checks) - len(fails)) / len(checks)),
                summary=f"{len(checks) - len(fails)}/{len(checks)} 项通过",
                checks=checks)



# ---------- ffmpeg 像素通道 ----------

def _ffmpeg():
    try:
        import audio_video as av
        return av.ffmpeg_libass()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit("ffmpeg 未就绪，先确认 fxai-audio-video 配置：" + str(exc))


def _probe(ffmpeg, img):
    ffprobe = os.path.join(os.path.dirname(ffmpeg),
                           "ffprobe.exe" if ffmpeg.endswith(".exe") else "ffprobe")
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0:s=x", img],
        capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split("x")[:2]
    return int(w), int(h)


def _raw(ffmpeg, img, w, h, pix="rgb24"):
    cmd = [ffmpeg, "-v", "error", "-i", img, "-vf", f"scale={w}:{h}:flags=lanczos",
           "-f", "rawvideo", "-pix_fmt", pix, "-"]
    data = subprocess.run(cmd, capture_output=True, check=True).stdout
    if len(data) < w * h * (3 if pix == "rgb24" else 1):
        raise SystemExit("像素通道读取失败：" + img)
    return data


def _gray(data, w, h):
    """rgb24 → 灰度列表（BT.601 权重）。"""
    g = [0] * (w * h)
    for i in range(w * h):
        j = i * 3
        g[i] = (data[j] * 299 + data[j + 1] * 587 + data[j + 2] * 114) // 1000
    return g


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs, m=None):
    if not xs:
        return 0.0
    m = _mean(xs) if m is None else m
    return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5


def _grad(px, w, h):
    """水平+垂直差分幅值均值，以及逐像素幅值列表。"""
    mags = [0.0] * (w * h)
    total = 0
    for y in range(1, h - 1):
        row = y * w
        for x in range(1, w - 1):
            i = row + x
            gx = px[i + 1] - px[i - 1]
            gy = px[i + w] - px[i - w]
            m = (gx * gx + gy * gy) ** 0.5
            mags[i] = m
            total += m
    return total / max(1, w * h), mags


def _psnr_self(ffmpeg, img, factor=2, size=480):
    """缩 1/factor 再放大回原尺寸，与原图比 PSNR：越高说明原图越缺高频。

    与画幅、主题、字号无关，只测「高频细节是否真实存在」——
    模糊图和小图拉伸图在这个口径下都会露馅。
    实测：清晰 27.7~42.6 / 低清拉伸 43.6~51.7 / 高斯模糊 54.1~57.3。
    """
    W, H = _probe(ffmpeg, img)
    sw, sh = max(2, W // factor), max(2, H // factor)
    tmp = os.path.join(tempfile.gettempdir(), "_qc_self_%d.png" % os.getpid())
    vf = "scale=%d:%d:flags=bilinear,scale=%d:%d:flags=bilinear" % (sw, sh, W, H)
    try:
        subprocess.run([ffmpeg, "-y", "-v", "error", "-i", img, "-vf", vf, tmp], check=True)
        w = min(size, W)
        h = max(2, round(H * w / W / 2) * 2)
        a = _raw(ffmpeg, img, w, h)
        b = _raw(ffmpeg, tmp, w, h)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    se = 0
    n = w * h
    for i in range(0, n * 3, 3):
        d = a[i] - b[i]
        se += d * d
    mse = se / max(1, n)
    return 99.0 if mse <= 0 else 10 * __import__("math").log10(255 * 255 / mse)


def _sat(data, w, h):
    vals = []
    for i in range(w * h):
        j = i * 3
        r, g, b = data[j], data[j + 1], data[j + 2]
        mx = max(r, g, b)
        if mx:
            vals.append((mx - min(r, g, b)) / mx)
    return _mean(vals)


# ---------- 成图检查 ----------

def postflight(img, kind=None, target=None, no_text=False):
    if not os.path.exists(img):
        raise SystemExit("找不到成图：" + img)
    ffmpeg = _ffmpeg()
    W, H = _probe(ffmpeg, img)
    spec = TYPES.get(kind) if kind else None

    if target is None and spec:
        tw, thh = (int(v) for v in spec["size"].split("x"))
        target = (tw, thh)

    # 采样：统计用 320 宽，缩略图判据 200 宽，锐度判据 640 宽
    sw = 320
    sh = max(2, round(H * sw / W / 2) * 2)
    sdata = _raw(ffmpeg, img, sw, sh)
    gray = _gray(sdata, sw, sh)

    tw200 = 200
    th200 = max(2, round(H * tw200 / W / 2) * 2)
    g200 = _gray(_raw(ffmpeg, img, tw200, th200), tw200, th200)

    tw640 = 640
    th640 = max(2, round(H * tw640 / W / 2) * 2)
    g640 = _gray(_raw(ffmpeg, img, tw640, th640), tw640, th640)
    # 锐度只看强边缘：全图均值会把「构图简洁」误判成「发虚」
    sharp, smags = _grad(g640, tw640, th640)
    svals = sorted(v for v in smags if v > 0)
    if svals:
        cut = int(len(svals) * 0.8)
        sharp_edge = sum(svals[cut:]) / max(1, len(svals) - cut)
        sharp_p95 = svals[min(len(svals) - 1, int(len(svals) * 0.95))]
    else:
        sharp_edge = sharp_p95 = 0.0

    m = _mean(gray)
    rms = _stdev(gray, m)
    sat = _sat(sdata, sw, sh)
    grad_all, mags = _grad(gray, sw, sh)
    text_area = sum(1 for v in mags if v > 26) / (sw * sh)
    thumb_edge, _ = _grad(g200, tw200, th200)

    def density(x0, y0, x1, y1):
        vals = []
        for y in range(y0, y1):
            base = y * sw
            vals.extend(mags[base + x0: base + x1])
        return _mean(vals) if vals else 0.0

    bx, by = int(sw * 0.10), int(sh * 0.10)
    edge = max(
        density(0, 0, sw, by), density(0, sh - by, sw, sh),
        density(0, 0, bx, sh), density(sw - bx, 0, sw, sh),
    )
    edge_ratio = edge / grad_all if grad_all else 9.9

    tiles = []
    for ty in range(4):
        for tx in range(4):
            x0, x1 = tx * sw // 4, (tx + 1) * sw // 4
            y0, y1 = ty * sh // 4, (ty + 1) * sh // 4
            vals = []
            for y in range(y0, y1):
                vals.extend(gray[y * sw + x0: y * sw + x1])
            tiles.append(_stdev(vals))
    tile_flat = min(tiles)

    checks = []

    def add(name, ok, val, expect, fix, warn=False):
        checks.append(dict(item=name, ok=bool(ok), value=val, expect=expect,
                           fix=fix, level="warn" if warn else "fail"))

    if target:
        ratio_tgt = target[0] / target[1]
        dev = abs((W / H) - ratio_tgt) / ratio_tgt * 100
        add("画幅比例", dev <= 3.0, f"{W}x{H}（偏差 {dev:.1f}%）",
            f"{target[0]}x{target[1]} ±3%",
            f"重出为 {target[0]}x{target[1]}；换画幅要重新构图，不裁切母版")
        short_img, short_tgt = min(W, H), min(target)
        add("有效分辨率", short_img >= short_tgt * 0.85,
            f"短边 {short_img}px", f"≥{int(short_tgt * 0.85)}px",
            "成图小于目标尺寸是拉伸出来的，放大即糊：按目标尺寸重新出图")

    add("整图亮度", TH["bright_min"] <= m <= TH["bright_max"], f"{m:.1f}",
        f"{TH['bright_min']:.0f}~{TH['bright_max']:.0f}",
        "压死则提亮底图或降蒙版；发白则加暗层让文字立起来")

    add("画面层次(RMS)", rms >= TH["rms_min"], f"{rms:.1f}", f"≥{TH['rms_min']:.0f}",
        "画面发平：拉开光比与景深，加前后景层次，别靠加装饰")

    add("色彩强度", sat >= TH["sat_min"], f"{sat:.3f}", f"≥{TH['sat_min']:.2f}",
        "饱和度偏低：需要冲击力就提主色浓度；极简白 / 高级灰是刻意设计，人眼确认即可",
        warn=True)

    if not no_text:
        add("文字/细节量", TH["text_area_min"] <= text_area <= TH["text_area_max"],
            f"{text_area:.1%}", f"{TH['text_area_min']:.0%}~{TH['text_area_max']:.0%}",
            "过低：确认文案是否真的渲染出来了（极简封面属正常）；过高：信息过载，减字或拆图",
            warn=True)
    else:
        add("文字/细节量", text_area <= TH["text_area_max"], f"{text_area:.1%}",
            f"纯画面图 ≤{TH['text_area_max']:.0%}", "细节过密：减元素，纯画面图靠光影而非堆信息",
            warn=True)

    # 锐度按类型档位取值：字多且密用 high，少字 / 无字用 low（见 09 册）
    tier = "low" if no_text else SHARP_TIER.get(kind or "", "mid")
    t = TH["sharp_tier"][tier]

    add("缩略图可读", thumb_edge >= t["thumb"], f"{thumb_edge:.2f}",
        f"≥{t['thumb']:.0f}（{tier} 档）",
        "缩到信息流尺寸可读性偏低：加大主标字号、拉高字底对比；浅色留白多的设计天然偏低，手机上扫一眼确认",
        warn=True)

    add("画面锐度", sharp_edge >= t["edge"], f"{sharp_edge:.1f}", f"≥{t['edge']:.0f}（{tier} 档）",
        "边缘发虚：小图拉伸或生图分辨率不足；按目标尺寸重新出图")

    add("细节峰值", sharp_p95 >= t["p95"], f"{sharp_p95:.1f}", f"≥{t['p95']:.0f}（{tier} 档）",
        "边缘峰值偏低：实心字、硬边描边能提上来；浅色低对比主题天然偏低，人眼确认即可",
        warn=True)

    psnr = _psnr_self(ffmpeg, img)
    add("高频真实性", psnr <= TH["psnr_max"], f"PSNR {psnr:.1f}", f"≤{TH['psnr_max']:.0f}",
        "整图缺高频＝模糊或小图放大：按目标尺寸重新出图；大面积纯色 / 极简设计属正常，人眼确认")

    add("边缘安全区", edge_ratio <= TH["edge_ratio_max"], f"{edge_ratio:.2f}×",
        f"≤{TH['edge_ratio_max']:.1f}× 全图密度",
        "四边偏满：把主体与文字往中间 60% 收，竖版上下各留 10~14%；满版构图属正常，确认平台 UI 不压字",
        warn=True)

    add("无死区", tile_flat >= TH["tile_flat_min"], f"最平格 {tile_flat:.1f}",
        f"≥{TH['tile_flat_min']:.0f}",
        "有大片空白或糊区：补元素或收紧构图；深色底角落属正常，人工确认即可", warn=True)

    fails = [c for c in checks if not c["ok"] and c["level"] == "fail"]
    score = round(100 * (len(checks) - len(fails)) / len(checks))
    return dict(stage="postflight", image=os.path.abspath(img), size=f"{W}x{H}",
                kind=kind, score=score,
                verdict="PASS" if not fails else "FAIL",
                metrics=dict(brightness=round(m, 1), rms=round(rms, 1), psnr=round(psnr, 1), tier=tier,
                             saturation=round(sat, 3), text_area=round(text_area, 3),
                             thumb_edge=round(thumb_edge, 2), sharpness=round(sharp_edge, 1), sharp_p95=round(sharp_p95, 1),
                             edge_ratio=round(edge_ratio, 2),
                             flattest_tile=round(tile_flat, 1)),
                checks=checks)


def preflight(spec):
    kind = spec.get("kind")
    if kind not in TYPES:
        raise SystemExit(f"未知用途类型 {kind}；可用：{', '.join(TYPES)}")
    t = TYPES[kind]
    checks = []

    def add(name, ok, value, expect, fix, warn=False):
        checks.append(dict(item=name, ok=bool(ok), value=str(value), expect=expect,
                           fix=fix, level="warn" if warn else "fail"))

    # 画幅
    w, h = spec.get("w"), spec.get("h")
    tw, thh = (int(v) for v in t["size"].split("x"))
    if w and h:
        dev = abs((w / h) - (tw / thh)) / (tw / thh) * 100
        add("画幅", dev <= 2.0, f"{w}×{h}（{w / h:.2f}:1）", f"{t['size']}（{tw / thh:.2f}:1）",
            f"画布改为 {t['size']}；用途决定画幅，不按习惯定")
    else:
        add("画幅", False, "未声明", t["size"], f"补画布尺寸 {t['size']}")

    # 模式
    mode = spec.get("mode")
    add("出图模式", mode in t["mode"], mode or "未声明", "/".join(t["mode"]),
        f"该用途走 {'/'.join(t['mode'])}：要中文精确排版必须有网页层，要真实质感必须有生图层")

    # 必填字段
    for field in t["require"]:
        if field == "blocks":
            n = len(spec.get("blocks") or [])
            add("信息块", n >= 3, f"{n} 块", "≥3 块（否则改走大字封面）",
                "信息块不足 3 个就不是信息图，改用封面样式库出图")
            continue
        add(f"字段 {field}", bool(spec.get(field)), spec.get(field) or "缺失",
            "必填", f"补 {field}：用途 {t['label']} 缺此项不成图")

    # 主张
    add("单一主张", spec.get("single_claim") is not False,
        "1 个" if spec.get("single_claim") is not False else "多个",
        "1 个", "一屏一个主张：其余内容拆第二张图或降为条目")

    # 标题
    lines = [s for s in str(spec.get("title", "")).split("|") if s.strip()]
    if lines:
        over = [s for s in lines if len(s.strip()) > t["title_chars"]]
        add("主标行数", len(lines) <= t["title_lines"], f"{len(lines)} 行",
            f"≤{t['title_lines']} 行", "标题超过两行拆成主标+副标，第三行信息价值低于折行代价")
        add("主标字宽", not over, f"最长 {max(len(s.strip()) for s in lines)} 字",
            f"每行 ≤{t['title_chars']} 字",
            "超宽行删字或换短词（保留数字与反转词），靠缩字号补救会破坏视觉统一")

    # 副标 / 角标 / 关键词
    sub = str(spec.get("sub") or "")
    if sub:
        add("副标字数", len(sub) <= t["sub_chars"], f"{len(sub)} 字",
            f"≤{t['sub_chars']} 字", "副标收一句就够，超长拆进正文")
    tag = str(spec.get("tag") or "")
    if tag:
        add("角标字数", len(tag) <= t["tag_chars"], f"{len(tag)} 字",
            f"≤{t['tag_chars']} 字", "角标是徽章，超过两行就不成立")
    keys = spec.get("keys") or []
    if t["keys"]:
        add("关键词数", len(keys) <= t["keys"], f"{len(keys)} 个", f"≤{t['keys']} 个",
            "关键词超过四枚就变正文，减到最该被记住的那几个")

    # 吸睛元素
    hooks = [h for h in (spec.get("hooks") or []) if h in HOOKS]
    if t["hooks_min"]:
        add("吸睛元素", len(hooks) >= t["hooks_min"], f"{len(hooks)} 项",
            f"≥{t['hooks_min']} 项：{'/'.join(HOOKS)}",
            "补足吸睛元素：数字前置、反差反转、身份标签、结果承诺优先")
    bad = [h for h in (spec.get("hooks") or []) if h not in HOOKS]
    if bad:
        add("吸睛元素命名", False, ",".join(bad), "/".join(HOOKS),
            "按标准命名登记，质检才认")

    # 隐喻与文字安全
    meta = spec.get("metaphors") or ([spec["metaphor"]] if spec.get("metaphor") else [])
    add("视觉隐喻", len(meta) <= 1, f"{len(meta)} 个", "≤1 个",
        "一张图只用一个隐喻，混用直接毁理解")
    add("文字对比方案", bool(spec.get("contrast_plan")), spec.get("contrast_plan") or "未声明",
        "声明蒙版/压暗/描边之一",
        "字压底图必须给对比方案：底部压暗渐变、半透明蒙版或字描边，出图后按 4.5:1 实测")
    add("主体位置", bool(spec.get("subject_pos")), spec.get("subject_pos") or "未声明",
        "center / 指定方位",
        "主体落在中间 60% 区域，四边留给平台 UI 压盖")

    # 生图提示词一并过门（AI 出图路径必检）
    if spec.get("prompt"):
        checks.extend(check_prompt(spec["prompt"], bool(spec.get("role")),
                                  bool(spec.get("title")))["checks"])

    fails = [c for c in checks if not c["ok"] and c["level"] == "fail"]
    score = round(100 * (len(checks) - len(fails)) / len(checks))
    return dict(stage="preflight", kind=kind, label=t["label"], score=score,
                verdict="PASS" if not fails else "FAIL",
                summary=f"{len(checks) - len(fails)}/{len(checks)} 项通过",
                checks=checks)


# ---------- CLI ----------

def _show(rep):
    print(f"[{rep['verdict']}] {rep['stage']} · {rep.get('label') or rep.get('kind') or ''}"
          f" · 得分 {rep['score']}")
    if rep.get("metrics"):
        print("  指标 " + "  ".join(f"{k}={v}" for k, v in rep["metrics"].items()))
    for c in rep["checks"]:
        if c["ok"]:
            continue
        mark = "WARN" if c["level"] == "warn" else "FAIL"
        print(f"  [{mark}] {c['item']}：{c['value']}（要求 {c['expect']}）")
        print(f"         → {c['fix']}")
    if rep["verdict"] == "PASS":
        print("  全部通过")


# ---------- 长图裁白 ----------

def _bg_color(data, w, h):
    """取四条边采样最多的颜色当背景色。"""
    tally = {}
    sx = max(1, w // 160)
    sy = max(1, h // 160)
    for x in range(0, w, sx):
        for y in (0, h - 1):
            j = (y * w + x) * 3
            k = (data[j], data[j + 1], data[j + 2])
            tally[k] = tally.get(k, 0) + 1
    for y in range(0, h, sy):
        for x in (0, w - 1):
            j = (y * w + x) * 3
            k = (data[j], data[j + 1], data[j + 2])
            tally[k] = tally.get(k, 0) + 1
    return max(tally.items(), key=lambda kv: kv[1])[0]


def fit_crop(img, out=None, pad=0, tol=12):
    """按背景色裁掉四周空白，返回精确内容尺寸。长图 / 网页模式出图后必用。"""
    if not os.path.exists(img):
        raise SystemExit("找不到成图：" + img)
    ffmpeg = _ffmpeg()
    W, H = _probe(ffmpeg, img)

    # 缩到 400 宽采样求边界，再换算回原图坐标，省掉全分辨率遍历
    sw = 400
    sh = max(2, round(H * sw / W / 2) * 2)
    data = _raw(ffmpeg, img, sw, sh)
    bg = _bg_color(data, sw, sh)

    def is_bg(x, y):
        j = (y * sw + x) * 3
        return (abs(data[j] - bg[0]) <= tol
                and abs(data[j + 1] - bg[1]) <= tol
                and abs(data[j + 2] - bg[2]) <= tol)

    def row_blank(y):
        return all(is_bg(x, y) for x in range(sw))

    def col_blank(x):
        return all(is_bg(x, y) for y in range(sh))

    top = next((y for y in range(sh) if not row_blank(y)), 0)
    bottom = next((y for y in range(sh - 1, -1, -1) if not row_blank(y)), sh - 1)
    left = next((x for x in range(sw) if not col_blank(x)), 0)
    right = next((x for x in range(sw - 1, -1, -1) if not col_blank(x)), sw - 1)

    kx, ky = W / sw, H / sh
    x0 = max(0, int(left * kx) - pad)
    y0 = max(0, int(top * ky) - pad)
    x1 = min(W, int((right + 1) * kx) + pad)
    y1 = min(H, int((bottom + 1) * ky) + pad)
    cw, ch = x1 - x0, y1 - y0

    rep = dict(stage="fit", image=os.path.abspath(img), origin=f"{W}x{H}",
               box=[x0, y0, cw, ch], size=f"{cw}x{ch}", bg=list(bg),
               trimmed=f"{W - cw}x{H - ch}")
    if out:
        subprocess.run([ffmpeg, "-y", "-v", "error", "-i", img,
                        "-vf", f"crop={cw}:{ch}:{x0}:{y0}", out], check=True)
        rep["output"] = os.path.abspath(out)
    return rep


def main():
    ap = argparse.ArgumentParser(description="设计质检门禁")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("pre", help="出图前查设计规格（可带 prompt 一起查）")
    p1.add_argument("--spec", help="规格 JSON 文件")
    p1.add_argument("--kind")
    p1.add_argument("--w", type=int)
    p1.add_argument("--h", type=int)
    p1.add_argument("--mode")
    p1.add_argument("--title")
    p1.add_argument("--sub")
    p1.add_argument("--tag")
    p1.add_argument("--keys")
    p1.add_argument("--hooks")
    p1.add_argument("--metaphor")
    p1.add_argument("--prompt", help="生图提示词全文，给了就一并查要素")
    p1.add_argument("--role", action="store_true", help="是否挂了角色参考图（查着装）")
    p1.add_argument("--contrast-plan")
    p1.add_argument("--subject-pos")
    p1.add_argument("--json", action="store_true")

    p3 = sub.add_parser("prompt", help="只查生图提示词要素")
    p3.add_argument("--text", required=True)
    p3.add_argument("--role", action="store_true")
    p3.add_argument("--no-text", action="store_true", help="纯画面提示词，不查文字区")
    p3.add_argument("--json", action="store_true")

    p2 = sub.add_parser("post", help="出图后查成图")
    p2.add_argument("--img", required=True)
    p2.add_argument("--kind")
    p2.add_argument("--target", help="自定义目标尺寸，如 1080x1920")
    p2.add_argument("--no-text", action="store_true", help="纯画面图（不该有文字），跳过文字量下限")
    p2.add_argument("--json", action="store_true")

    p4 = sub.add_parser("types", help="列出用途类型（--json 供其他技能读取）")
    p4.add_argument("--json", action="store_true")

    p5 = sub.add_parser("fit", help="裁掉长图四周空白，报精确内容尺寸")
    p5.add_argument("--img", required=True)
    p5.add_argument("--out", help="输出路径；省略则只报尺寸")
    p5.add_argument("--pad", type=int, default=0, help="保留边距像素")
    p5.add_argument("--tol", type=int, default=12, help="背景色容差")
    p5.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.cmd == "fit":
        rep = fit_crop(a.img, a.out, a.pad, a.tol)
        if a.json:
            print(json.dumps(rep, ensure_ascii=False, indent=2))
        else:
            print(f"原图 {rep['origin']} → 内容 {rep['size']}（裁掉 {rep['trimmed']}）")
            if rep.get("output"):
                print("已保存：" + rep["output"])
        return 0

    if a.cmd == "types":
        if a.json:
            print(json.dumps(TYPES, ensure_ascii=False, indent=2))
            return 0
        for k, v in TYPES.items():
            print(f"{k:16s} {v['size']:11s} {v['label']}  模式={'/'.join(v['mode'])}")
        return 0

    if a.cmd == "pre":
        spec = {}
        if a.spec:
            with open(a.spec, encoding="utf-8") as fh:
                spec = json.load(fh)
        for k in ("kind", "w", "h", "mode", "title", "sub", "tag", "metaphor", "prompt"):
            v = getattr(a, k, None)
            if v:
                spec[k] = v
        if getattr(a, "contrast_plan", None):
            spec["contrast_plan"] = a.contrast_plan
        if getattr(a, "subject_pos", None):
            spec["subject_pos"] = a.subject_pos
        if a.role:
            spec["role"] = True
        if a.keys:
            spec["keys"] = [s for s in re.split(r"[,，]", a.keys) if s.strip()]
        if a.hooks:
            spec["hooks"] = [s for s in re.split(r"[,，]", a.hooks) if s.strip()]
        rep = preflight(spec)
    elif a.cmd == "prompt":
        rep = check_prompt(a.text, a.role, not a.no_text)
    else:
        target = None
        if a.target:
            target = tuple(int(v) for v in a.target.lower().split("x"))
        rep = postflight(a.img, a.kind, target, a.no_text)

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        _show(rep)
    return 0 if rep["verdict"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
