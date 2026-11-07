#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""信息贴片 · 渲染阶段：把 infocards.json 里每条「补充文字 + 形状 + 主题」
画成一张**紧致透明 PNG**（只含形状+文字，背景透明），并产出 render.json 清单。

──── 信息贴片是什么 ────
它是叠在视频上的**第三层**，独立于底部字幕与顶部标题卡：
  · 字幕（底部，跟语音逐句）—— 语音正文
  · 标题卡（顶部，跟节拍）—— 段落路标
  · **信息贴片**（任意位置）—— 辅助性补充（说明 / 名词解释 / 操作提示 / 注意事项…）
🔴 信息贴片的内容**不是**从口播文案里提炼的，是**另外写的补充内容**，
   配合着口播作为提示或补充出现。

──── 十三种形状 ────
  bubble    冒气泡（圆角矩形+尾巴，指向说话人）→ 引用/对话/疑问
  round     圆形泡泡（椭圆气泡+尾巴）          → 干净对话泡/鱼吐的透明泡（低 bg_alpha）
  cloud     云朵泡泡（漫画云朵+一圈小凸包）    → 漫画对话/旁白/内心os
  capsule   胶囊袋（全圆角）                  → 短关键词/标签/数字
  chip      小方块（小圆角）                  → 并列标签/短补充
  card      方块卡片（带顶部强调条）          → 说明/解释/长句【默认】
  note      便签纸（右下角折起）              → 提醒/待办/随手记
  ribbon    绶带（左右燕尾缺口）              → 结论/口号/强调横幅
  tag       尖标签（右端收尖，带孔）          → 名词标注/价格/指向物
  bookmark  书签（顶部色带，底边中间凹 V）    → 挂在画面边缘的短标注
  label     纸签（内外双框，吊牌感）          → 名词卡/价格牌/提示牌
  underline 下划线（只画一条线，不铺面板）    → 关键词强调，完全不挡画面
  bracket   括号（左右竖线+四角短横）         → 引用区间/重点框选

  🔴 本技能与产线无关、可通用：数字人口播 / MV / 短视频 / 标题卡流水线都能直接调用
     `make_infocards.py` + `overlay_infocards.py`（视频+json 进，叠加视频出），
     不关心视频是谁做的。漫画对话里多个人各来一个 cloud/round 泡、用 tail_side/tail_at 指向对应人即可。

──── 半透明 + 质感 ────
多套配色体系（`theme` 字段任选，背景一律半透明不挡画面）：
  dark 深色(默认) / light 浅色 / brand 品牌橙
  tech 科技蓝 / neon 霓虹紫 / fresh 清新绿 / sunset 暖阳 / mono 极简灰
`bg_alpha` 可逐条覆盖（0~255，默认 ~160 ≈ 0.63）。
质感细节：**竖向渐变面板**（上浅下深）+ **柔和投影**（悬浮感）+ **1px 细描边**（玻璃边）。

──── 字体 ────
🔴 默认用**得意黑**（品牌展示字）；得意黑缺失自动回退**微软雅黑**，再回退 Noto / PIL 默认。
（fxai-standard 06 查证：微软雅黑屏幕渲染与输出可用，非商业禁用品。）

──── 气泡尾巴 + 指示箭头（不规则形状） ────
bubble / round / cloud 三种泡都支持多种尾巴，不只是小三角；+ cloud 自带「思考尾泡」：
  tail="triangle" 直小三角（默认）  tail="bent" 弯角斜指  tail="none" 无
  tail_side 挂在哪条边（bottom/top/left/right）  tail_at 沿边位置 0~1  tail_bend 弯向
  thought=true（仅 cloud）尾巴换成「一串变小圆」的思考尾泡，漫画内心os专用
任意形状可加一道**指示箭头**（callout），指向说话人或物体：
  arrow="curve"/"straight"  arrow_dir 指向(bl/br/tl/tr/left/right/up/down)
  arrow_color 颜色(bar/text/#rrggbb)  arrow_len 长度

──── 配色花样（深色 / 白色 / 透明） ────
  · 深色玻璃感：theme=dark/tech/neon/fresh/sunset/mono（bg_alpha ~160）
  · 白色云朵感：theme=light（白底半透，cloud 即白色云朵）
  · 透明泡泡感：任意 theme + bg_alpha 调低（如 70~90），round/cloud 即鱼吐的透明泡
  bg_alpha 逐条覆盖（0~255）。

──── 排版精度 ────
🔴 文字按**实际墨迹**垂直居中：先画到独立层，量 `alpha >= INK_T` 的实心部分中心，
   再把墨迹中心对齐盒子中心，保证**上下内边距严格相等**。

用法：
  python -B scripts/make_infocards.py --infocards infocards.json --size 768x1280 --out _tmp/infocards [--preview]
"""
import argparse, os, sys, json

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cardkit as K
from PIL import Image, ImageDraw, ImageFont, ImageFilter

SHADOW_A = 95   # 贴片投影透明度（0~255），营造悬浮感
INK_T = 220     # 判定「实心墨迹」的 alpha 阈值（面板+投影底噪约 195，实心字约 250）

# top/bot = 竖向渐变两端（上浅下深，玻璃质感）；border = 细描边 RGBA
# 🔴 多套配色体系，按需选（主题名即 infocards.json 里 "theme" 字段）：
#   dark  深色（默认）  light 浅色   brand 品牌橙
#   tech  科技蓝       neon  霓虹紫  fresh 清新绿
#   sunset 暖阳        mono  极简灰
THEMES = {
    "dark":   {"top": (32, 42, 68),    "bot": (11, 16, 30),   "bg_alpha": 160,
               "border": (255, 255, 255, 42), "text": (255, 255, 255, 235),
               "bar": (255, 176, 32, 235)},
    "light":  {"top": (255, 255, 255), "bot": (236, 240, 246), "bg_alpha": 200,
               "border": (22, 35, 59, 45),   "text": (22, 35, 59, 255),
               "bar": (255, 107, 53, 245)},
    "brand":  {"top": (255, 142, 98),  "bot": (223, 84, 36),  "bg_alpha": 205,
               "border": (255, 255, 255, 120), "text": (255, 255, 255, 255),
               "bar": (255, 255, 255, 235)},
    "tech":   {"top": (38, 66, 116),   "bot": (8, 20, 50),    "bg_alpha": 165,
               "border": (120, 180, 255, 60), "text": (222, 236, 255, 240),
               "bar": (64, 196, 255, 240)},
    "neon":   {"top": (88, 42, 122),   "bot": (28, 12, 56),   "bg_alpha": 165,
               "border": (200, 120, 255, 72), "text": (242, 226, 255, 240),
               "bar": (255, 92, 200, 240)},
    "fresh":  {"top": (28, 96, 80),    "bot": (10, 44, 42),   "bg_alpha": 165,
               "border": (120, 230, 180, 60), "text": (226, 255, 240, 240),
               "bar": (60, 222, 150, 240)},
    "sunset": {"top": (122, 62, 72),   "bot": (54, 20, 36),   "bg_alpha": 165,
               "border": (255, 172, 150, 60), "text": (255, 240, 235, 240),
               "bar": (255, 150, 90, 240)},
    "mono":   {"top": (62, 66, 74),    "bot": (28, 30, 36),   "bg_alpha": 172,
               "border": (255, 255, 255, 40), "text": (245, 245, 245, 235),
               "bar": (200, 205, 215, 235)},
}


# 位置预设 → 贴片中心占画面的比例 (cx, cy)
# 竖屏人像：顶部预设贴近上边缘（不压额头与眼睛），侧边预设落在肩线两侧，
# 底部预设停在字幕区之上。
POS_PRESET = {
    "tl": (0.28, 0.14), "tc": (0.50, 0.12), "tr": (0.72, 0.14),
    "ml": (0.24, 0.46), "mr": (0.76, 0.46),
    "bl": (0.28, 0.70), "bc": (0.50, 0.72), "br": (0.72, 0.70),
    "center": (0.50, 0.50),
}


def wrap(text, font, max_w, d):
    """按像素宽度断行（中文逐字），手写的 \\n 视为强制换行，末行留两字以上。

    先按 \\n 切段再逐段量宽：textlength 遇到多行文本会直接抛
    「can't measure length of multiline text」，手写换行必须在这层处理掉。
    """
    out = []
    for para in str(text).split("\n"):
        lines, cur = [], ""
        for ch in para:
            t = cur + ch
            if d.textlength(t, font=font) <= max_w:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        if not lines:
            lines = [""]
        # 末行只有一个字时从上一行借一个字，使末行保持两字以上
        if len(lines) >= 2 and len(lines[-1]) == 1 and len(lines[-2]) > 1:
            lines[-1] = lines[-2][-1] + lines[-1]
            lines[-2] = lines[-2][:-1]
        out.extend(lines)
    return out or [""]


def vgradient(size, c_top, c_bot, alpha):
    """竖向渐变（上浅下深），保持半透明 alpha 不变。没有 numpy 时退回纯色。"""
    w, h = size
    try:
        import numpy as np
        ys = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
        arr = np.zeros((h, w, 4), dtype=np.uint8)
        for ch_i, (a, b) in enumerate(zip(c_top, c_bot)):
            col = (a * (1 - ys) + b * ys).astype(np.uint8)
            arr[:, :, ch_i] = np.repeat(col, w, axis=1)
        arr[:, :, 3] = alpha
        return Image.fromarray(arr, "RGBA")
    except Exception:
        return Image.new("RGBA", size, tuple(c_top) + (alpha,))


def _resolve_color(c, theme):
    if c == "bar":
        return tuple(theme["bar"])
    if c == "text":
        return tuple(theme["text"])
    if isinstance(c, str) and c.startswith("#"):
        h = c[1:]
        if len(h) in (6, 8):
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return (r, g, b, 255)
    return tuple(theme["bar"])


def _cloud_gradient(W, H, y0, y1, c_top, c_bot, alpha):
    """全画布竖向渐变（云朵/圆形泡泡用：轮廓超出面板矩形，需全尺寸渐变）。"""
    try:
        import numpy as np
        arr = np.zeros((H, W, 4), np.uint8)
        ys = np.clip((np.arange(H) - y0) / max(1.0, (y1 - y0)), 0.0, 1.0)[:, None]
        for ch_i, (a, b) in enumerate(zip(c_top, c_bot)):
            arr[:, :, ch_i] = (a * (1 - ys) + b * ys).astype(np.uint8)
        arr[:, :, 3] = alpha
        return Image.fromarray(arr, "RGBA")
    except Exception:
        return Image.new("RGBA", (W, H), tuple(c_bot) + (alpha,))


def _cloud_mask(size, bx0, by0, bw, bh, bumps, s):
    """云朵/圆形泡泡的轮廓遮罩：round=纯椭圆；cloud=椭圆+一圈小凸包（漫画云）。"""
    W, H = size
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    d.ellipse([bx0, by0, bx0 + bw, by0 + bh], fill=255)
    if bumps > 0:
        import math
        cx, cy, rx, ry = bx0 + bw / 2, by0 + bh / 2, bw / 2, bh / 2
        br = min(rx, ry) * 0.42
        off = 0.86
        for i in range(bumps):
            a = 2 * math.pi * i / bumps + 0.35
            bxp = cx + math.cos(a) * rx * off
            byp = cy + math.sin(a) * ry * off
            d.ellipse([bxp - br, byp - br, bxp + br, byp + br], fill=255)
    return m


def _draw_cloud_bubble(im, theme, bg_a, bx0, by0, bw, bh, s, style,
                       tail_type, tail_side, tail_at, tail_bend, tail_len,
                       thought):
    """画圆形/云朵泡泡：投影→(尾巴/思考尾泡)→渐变填充→细描边。"""
    from PIL import ImageChops
    W, H = im.size
    c_top, c_bot = theme["top"], theme["bot"]
    border = tuple(theme["border"])
    bumps = 0 if style == "round" else 11

    # ① 云朵形柔和投影
    shm = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shm)
    oy = int(5 * s)
    sd.ellipse([bx0, by0 + oy, bx0 + bw, by0 + bh + oy], fill=255)
    if bumps:
        import math
        cx, cy, rx, ry = bx0 + bw / 2, by0 + oy + bh / 2, bw / 2, bh / 2
        br = min(rx, ry) * 0.42
        for i in range(bumps):
            a = 2 * math.pi * i / bumps + 0.35
            bxp = cx + math.cos(a) * rx * 0.86
            byp = cy + math.sin(a) * ry * 0.86
            sd.ellipse([bxp - br, byp - br, bxp + br, byp + br], fill=255)
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sh.paste((0, 0, 0, SHADOW_A), (0, 0), shm)
    im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(int(7 * s))))

    # ② 说话尾巴（在填充之前画，填充会把尾巴根部盖住，只露三角）
    edge_col = (c_bot if tail_side == "bottom" else
                c_top if tail_side == "top" else
                tuple((a + b) // 2 for a, b in zip(c_top, c_bot)))
    if (not thought) and tail_type != "none":
        base_w = int(26 * s)
        pts = _tail_pts(tail_side, tail_at, base_w, tail_len, tail_bend,
                        bx0, by0, bw, bh)
        ImageDraw.Draw(im).polygon(pts, fill=tuple(list(edge_col) + [bg_a]))

    # ③ 渐变填充（全画布，按轮廓遮罩）
    m = _cloud_mask((W, H), bx0, by0, bw, bh, bumps, s)
    grad = _cloud_gradient(W, H, by0, by0 + bh, c_top, c_bot, bg_a)
    fill = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fill.paste(grad, (0, 0), m)
    im.alpha_composite(fill)

    # ④ 细描边（轮廓外扩一圈）
    ringL = ImageChops.subtract(m.filter(ImageFilter.MaxFilter(3)), m)
    ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ring.paste(border, (0, 0), ringL)
    im.alpha_composite(ring)

    # ⑤ 思考尾泡（云朵专属：一串变小圆，不用尖尾巴）
    if thought:
        import math
        cx, cy = bx0 + bw / 2, by0 + bh / 2
        rx, ry = bw / 2, bh / 2
        # 从底部偏左往外飘
        dx, dy = -0.35, 1.0
        dn = math.hypot(dx, dy)
        dx, dy = dx / dn, dy / dn
        n = 3
        r0 = min(rx, ry) * 0.34
        for k in range(1, n + 1):
            t = k / (n + 1)
            bxp = cx + dx * (rx + k * 16 * s)
            byp = cy + dy * (ry + k * 16 * s)
            br = int(r0 * (1 - t * 0.55))
            ImageDraw.Draw(im).ellipse(
                [bxp - br, byp - br, bxp + br, byp + br],
                fill=tuple(list(edge_col) + [bg_a]))


def _ribbon_notch(s, bh):
    """绶带左右燕尾的切口深度（只跟高度与缩放走，便于宽度计算与绘制同源）。"""
    v = max(int(12 * s), int(bh * 0.30))
    return max(4, min(v, bh // 2))


def _tag_tip(s, bh):
    """尖标签右端尖角长度。"""
    v = max(int(12 * s), int(bh * 0.34))
    return max(6, min(v, bh // 2))


def _bookmark_notch(s, bw, bh):
    """书签底部 V 形缺口的深度（跟高度走，窄条也有明显缺口）。"""
    v = max(int(18 * s), int(bh * 0.18))
    return max(6, min(v, bh // 3))


def _flat_mask(shape, bw, bh, s):
    """便签 / 绶带 / 尖标签 / 书签 / 纸签的轮廓遮罩，返回 (mask, meta)。"""
    m = Image.new("L", (bw, bh), 0)
    d = ImageDraw.Draw(m)
    meta = {}
    if shape == "note":
        # 右下角折起：轮廓切掉一个三角
        fold = max(int(14 * s), int(min(bw, bh) * 0.22))
        fold = max(6, min(fold, bh // 3, bw // 3))
        d.polygon([(0, 0), (bw, 0), (bw, bh - fold), (bw - fold, bh), (0, bh)], fill=255)
        meta["fold"] = fold
    elif shape == "ribbon":
        # 左右两端各一个 V 形缺口
        notch = _ribbon_notch(s, bh)
        d.polygon([(notch, 0), (bw - notch, 0), (bw, bh // 2), (bw - notch, bh),
                   (notch, bh), (0, bh // 2)], fill=255)
        meta["notch"] = notch
    elif shape == "tag":
        # 右端收成尖角
        tip = _tag_tip(s, bh)
        d.polygon([(0, 0), (bw - tip, 0), (bw, bh // 2), (bw - tip, bh), (0, bh)], fill=255)
        meta["tip"] = tip
    elif shape == "bookmark":
        # 竖向书签：底边两侧留平边，中间凹一个 V
        notch = _bookmark_notch(s, bw, bh)
        d.polygon([(0, 0), (bw, 0), (bw, bh), (bw * 0.72, bh),
                   (bw / 2, bh - notch), (bw * 0.28, bh), (0, bh)], fill=255)
        meta["notch"] = notch
    elif shape == "label":
        # 纸签：圆角矩形，装饰在 _draw_flat_panel 里画内外双框
        d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=max(3, int(12 * s)), fill=255)
    else:
        d.rectangle([0, 0, bw - 1, bh - 1], fill=255)
    return m, meta


def _draw_flat_panel(im, theme, bg_a, bx0, by0, bw, bh, s, shape):
    """便签 / 绶带 / 尖标签：投影 → 渐变填充 → 细描边 → 形状装饰。"""
    from PIL import ImageChops
    W, H = im.size
    mask, meta = _flat_mask(shape, bw, bh, s)

    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sh.paste((0, 0, 0, SHADOW_A), (bx0, by0 + int(5 * s)), mask)
    im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(int(7 * s))))

    grad = vgradient((bw, bh), theme["top"], theme["bot"], bg_a)
    panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    panel.paste(grad, (bx0, by0), mask)
    im.alpha_composite(panel)

    ringL = ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(3)), mask)
    ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ring.paste(tuple(theme["border"]), (bx0, by0), ringL)
    im.alpha_composite(ring)

    d = ImageDraw.Draw(im)
    bar_col = tuple(theme["bar"])
    if shape == "note":
        fold = meta["fold"]
        # 折起的那一页：略亮的三角，压在右下缺口上
        d.polygon([(bx0 + bw - fold, by0 + bh - fold),
                   (bx0 + bw, by0 + bh - fold),
                   (bx0 + bw - fold, by0 + bh)],
                  fill=tuple(list(theme["top"]) + [min(255, bg_a + 45)]))
        d.line([(bx0 + bw - fold, by0 + bh - fold), (bx0 + bw - fold, by0 + bh)],
               fill=bar_col, width=max(1, int(1.6 * s)))
        d.line([(bx0 + bw - fold, by0 + bh - fold), (bx0 + bw, by0 + bh - fold)],
               fill=bar_col, width=max(1, int(1.6 * s)))
    elif shape == "tag":
        tip = meta["tip"]
        r = max(2, int(3.2 * s))
        cx, cy = bx0 + bw - tip - int(9 * s), by0 + bh // 2
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=bar_col)
    elif shape == "bookmark":
        # 书签头部：一条横贯的色带，像夹在页里的那条
        head = max(3, int(5 * s))
        d.rectangle([bx0, by0, bx0 + bw, by0 + head], fill=bar_col)
    elif shape == "label":
        # 纸签：内缩一圈细框，呈吊牌/贴纸的框线感
        inset = max(3, int(5 * s))
        d.rounded_rectangle([bx0 + inset, by0 + inset,
                             bx0 + bw - inset, by0 + bh - inset],
                            radius=max(2, int(9 * s)), outline=bar_col,
                            width=max(1, int(1 * s)))


def _draw_mark(im, theme, bx0, by0, bw, bh, s, shape, ul_room, text_w):
    """下划线 / 括号：只画标注线，不铺面板。"""
    d = ImageDraw.Draw(im)
    col = tuple(theme["bar"])
    if shape == "underline":
        # 线只覆盖文字宽度，不延伸到内边距
        lh = max(2, int(6 * s))
        x0 = bx0 + (bw - text_w) / 2.0
        y = by0 + bh - ul_room + int(1 * s)
        d.rectangle([x0, y, x0 + text_w, y + lh], fill=col)
    else:  # bracket
        lw = max(2, int(4 * s))
        arm = int(14 * s)
        d.rectangle([bx0, by0, bx0 + lw, by0 + bh], fill=col)
        d.rectangle([bx0, by0, bx0 + arm, by0 + lw], fill=col)
        d.rectangle([bx0, by0 + bh - lw, bx0 + arm, by0 + bh], fill=col)
        rx = bx0 + bw - lw
        d.rectangle([rx, by0, rx + lw, by0 + bh], fill=col)
        d.rectangle([rx + lw - arm, by0, rx + lw, by0 + lw], fill=col)
        d.rectangle([rx + lw - arm, by0 + bh - lw, rx + lw, by0 + bh], fill=col)


def _tail_pts(side, at, base_w, length, bend, bx0, by0, bw, bh):
    """气泡尾巴轮廓多边形（沿 side 边、at 位置、长 length、bent 弯向 -1~1）。"""
    if side in ("bottom", "top"):
        cy = (by0 + bh) if side == "bottom" else by0
        cx = bx0 + at * bw
        if not bend:
            return [(cx - base_w, cy), (cx + base_w, cy), (cx, cy + length * (1 if side == "bottom" else -1))]
        L = length * (1 if side == "bottom" else -1)
        tip = (cx + bend * length, cy + L)
        k1 = (cx - base_w * 0.3, cy + L * 0.55)
        k2 = (cx + base_w * 0.5, cy + L * 0.6)
        return [(cx - base_w, cy), k1, tip, k2, (cx + base_w, cy)]
    else:  # left / right
        cx = bx0 if side == "left" else bx0 + bw
        cy = by0 + at * bh
        if not bend:
            return [(cx, cy - base_w), (cx, cy + base_w), (cx + length * (1 if side == "right" else -1), cy)]
        L = length * (1 if side == "right" else -1)
        tip = (cx + L, cy + bend * length)
        k1 = (cx + L * 0.55, cy - base_w * 0.3)
        k2 = (cx + L * 0.6, cy + base_w * 0.5)
        return [(cx, cy - base_w), k1, tip, k2, (cx, cy + base_w)]


def _arrow_anchor(side, at, bx0, by0, bw, bh):
    if side == "bottom":
        return (bx0 + at * bw, by0 + bh)
    if side == "top":
        return (bx0 + at * bw, by0)
    if side == "left":
        return (bx0, by0 + at * bh)
    return (bx0 + bw, by0 + at * bh)  # right


ARROW_DIRS = {
    "bl": (-0.7071, 0.7071), "br": (0.7071, 0.7071),
    "tl": (-0.7071, -0.7071), "tr": (0.7071, -0.7071),
    "left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1),
}
ARROW_ANCHOR = {
    "bl": ("bottom", 0.22), "br": ("bottom", 0.78),
    "tl": ("top", 0.22), "tr": ("top", 0.78),
    "left": ("left", 0.5), "right": ("right", 0.5),
    "up": ("top", 0.5), "down": ("bottom", 0.5),
}


def _draw_arrow(d, anchor, dir_vec, length, color, width, curve, s):
    import math
    ux, uy = dir_vec
    n = math.hypot(ux, uy) or 1
    ux, uy = ux / n, uy / n
    tip = (anchor[0] + ux * length, anchor[1] + uy * length)
    px, py = -uy, ux
    if curve:
        mx = (anchor[0] + tip[0]) / 2 + px * length * 0.35
        my = (anchor[1] + tip[1]) / 2 + py * length * 0.35
        pts = []
        for i in range(25):
            t = i / 24
            x = (1 - t) ** 2 * anchor[0] + 2 * (1 - t) * t * mx + t * t * tip[0]
            y = (1 - t) ** 2 * anchor[1] + 2 * (1 - t) * t * my + t * t * tip[1]
            pts.append((x, y))
        d.line(pts, fill=color, width=width, joint="curve")
        ax, ay = tip[0] - mx, tip[1] - my
    else:
        d.line([anchor, tip], fill=color, width=width, joint="curve")
        ax, ay = ux, uy
    an = math.hypot(ax, ay) or 1
    ax, ay = ax / an, ay / an
    hx, hy = -ay, ax
    hs = max(10 * s, width * 2.6)
    p1 = (tip[0] - ax * hs + hx * hs * 0.5, tip[1] - ay * hs + hy * hs * 0.5)
    p2 = (tip[0] - ax * hs - hx * hs * 0.5, tip[1] - ay * hs - hy * hs * 0.5)
    d.polygon([tip, p1, p2], fill=color)


def render_info(info, s, out_path):
    shape = (info.get("shape") or "card").lower()
    theme = THEMES.get((info.get("theme") or "dark").lower(), THEMES["dark"])
    bg_a = int(info.get("bg_alpha", theme["bg_alpha"]))
    use_title_font = (info.get("font") == "title") or shape == "capsule"
    fs = int((info.get("fontsize", 30)) * s)
    # 🔴 字体解析：得意黑优先，缺失回退微软雅黑，再回退 Noto
    ft = (K.title_font(fs) if use_title_font else K.body_font(fs))
    text = (info.get("text") or "").strip()
    max_w = int((info.get("max_w", 360)) * s)
    d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lines = wrap(text, ft, max_w, d0) or [""]
    line_h = int(fs * 1.4)
    pad_x = int(20 * s)
    pad_y = int(13 * s)
    text_w = max(d0.textlength(l, font=ft) for l in lines)
    # 下划线在文字下方留一条线的空间；括号在左右各留一条线的空间；书签底部留 V 口
    ul_room = int(12 * s) if shape == "underline" else 0
    br_room = int(7 * s) if shape == "bracket" else 0
    bm_tail = int(26 * s) if shape == "bookmark" else 0
    bh = int(len(lines) * line_h + 2 * pad_y) + ul_room + bm_tail
    # 燕尾与尖角占用侧边宽度，文字不贴边
    if shape == "ribbon":
        bw = int(text_w + 2 * pad_x) + 2 * _ribbon_notch(s, bh)
    elif shape == "tag":
        bw = int(text_w + 2 * pad_x) + _tag_tip(s, bh) + int(6 * s)
    else:
        bw = int(text_w + 2 * pad_x) + 2 * br_room

    # ---- 气泡尾巴参数 ----
    is_puff = shape in ("bubble", "round", "cloud")
    tail_type = (info.get("tail") or "triangle") if is_puff else "none"
    thought = bool(info.get("thought")) if shape == "cloud" else False
    if thought:
        tail_type = "none"
    tail_side = (info.get("tail_side") or "bottom").lower()
    tail_at = float(info.get("tail_at", 0.5))
    tail_bend = float(info.get("tail_bend", 0.0))
    tail_len = int(30 * s) if tail_type != "none" else 0

    # ---- 指示箭头参数（任意形状） ----
    # 顶层 arrow_dir / arrow_color / arrow_len / arrow_width 与 arrow 对象内的
    # dir / color / len / width 等价，对象内优先。
    arrow = info.get("arrow")
    if isinstance(arrow, str):
        arrow = {"kind": arrow}
    if isinstance(arrow, dict):
        a_kind = (arrow.get("kind") or "straight").lower()
        a_dir = (arrow.get("dir") or info.get("arrow_dir") or "bl").lower()
        a_color = arrow.get("color") or info.get("arrow_color") or "bar"
        a_len = int(float(arrow.get("len", info.get("arrow_len", 60))) * s)
        a_width = int(float(arrow.get("width", info.get("arrow_width", 5))) * s)
        a_curve = (a_kind == "curve")
    else:
        arrow = None
        a_len = 0

    bar = int(7 * s) if shape == "card" else 0
    cloud_pad = int(34 * s) if shape in ("round", "cloud") else 0
    margin = int(max(46 * s, tail_len + 16 * s, a_len + 18 * s, cloud_pad + 18 * s))
    W = bw + 2 * margin
    H = bh + tail_len + bar + 2 * margin
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bx0, by0 = margin, margin + bar
    d = ImageDraw.Draw(im)

    if shape in ("round", "cloud"):
        # 圆形/云朵泡泡：投影→尾巴/思考尾泡→渐变填充→细描边（轮廓外扩）
        _draw_cloud_bubble(im, theme, bg_a, bx0, by0, bw, bh, s,
                           "cloud" if shape == "cloud" else "round",
                           tail_type, tail_side, tail_at, tail_bend, tail_len,
                           thought)
    elif shape in ("note", "ribbon", "tag", "bookmark", "label"):
        # 便签 / 绶带 / 尖标签 / 书签 / 纸签：轮廓不是圆角矩形，统一走遮罩
        _draw_flat_panel(im, theme, bg_a, bx0, by0, bw, bh, s, shape)
    elif shape in ("underline", "bracket"):
        # 纯标注：不铺面板，只画线
        _draw_mark(im, theme, bx0, by0, bw, bh, s, shape, ul_room, text_w)
    else:
        radius = {"capsule": bh // 2, "chip": int(12 * s),
                  "bubble": int(22 * s)}.get(shape, int(18 * s))
        c_top, c_bot = theme["top"], theme["bot"]
        border = tuple(theme["border"])
        lw = max(1, int(1 * s))

        # ① 柔和投影
        sl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sl).rounded_rectangle(
            [bx0, by0 + int(5 * s), bx0 + bw, by0 + bh + int(5 * s)],
            radius=radius, fill=(0, 0, 0, SHADOW_A))
        im.alpha_composite(sl.filter(ImageFilter.GaussianBlur(int(7 * s))))

        # ② 气泡尾巴（_panel 之前画，自然融合）
        if tail_type != "none":
            base_w = int(26 * s)
            edge_col = (c_bot if tail_side == "bottom" else
                        c_top if tail_side == "top" else
                        tuple((a + b) // 2 for a, b in zip(c_top, c_bot)))
            pts = _tail_pts(tail_side, tail_at, base_w, tail_len, tail_bend,
                            bx0, by0, bw, bh)
            ImageDraw.Draw(im).polygon(pts, fill=tuple(list(edge_col) + [bg_a]))

        # ③ 渐变面板
        grad = vgradient((bw, bh), c_top, c_bot, bg_a)
        mask = Image.new("L", (bw, bh), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, bw - 1, bh - 1],
                                               radius=radius, fill=255)
        panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        panel.paste(grad, (bx0, by0), mask)
        im.alpha_composite(panel)

        # ④ 细描边
        d.rounded_rectangle([bx0, by0, bx0 + bw - 1, by0 + bh - 1],
                            radius=radius, outline=border, width=lw)

        # ⑤ card 顶部强调条（两端收在顶部平直段内，避开圆角）
        if shape == "card" and bar:
            d.rectangle([bx0 + radius, by0 - bar, bx0 + bw - radius, by0],
                        fill=tuple(theme["bar"]))

    # ⑥ 文字（按实心墨迹垂直居中，上下内边距严格相等）
    tl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td = ImageDraw.Draw(tl)
    txt = tuple(theme["text"])
    ty = by0 + pad_y
    for l in lines:
        td.text((bx0 + bw / 2, ty + line_h / 2), l, font=ft, fill=txt, anchor="mm")
        ty += line_h
    try:
        import numpy as np
        al = np.array(tl)[:, :, 3]
        rows = np.where(al.max(axis=1) >= INK_T)[0]
        top, bot = (int(rows.min()), int(rows.max())) if rows.size else (None, None)
    except Exception:
        bb = tl.getbbox()
        top, bot = (bb[1], bb[3] - 1) if bb else (None, None)
    if top is not None:
        # 下划线的线、书签的 V 口占底部空间，文字中心按「可用文字区」算
        target_cy = by0 + (bh - ul_room - bm_tail) / 2.0
        dy = int(round(target_cy - (top + bot) / 2.0))
        if dy:
            tl = tl.transform(tl.size, Image.AFFINE, (1, 0, 0, 0, 1, -dy),
                              resample=Image.NEAREST)
    im.alpha_composite(tl)

    # ⑦ 指示箭头（callout，指向人或物体）
    if arrow:
        a_side, a_at = ARROW_ANCHOR.get(a_dir, ("bottom", 0.5))
        anchor = _arrow_anchor(a_side, a_at, bx0, by0, bw, bh)
        acol = _resolve_color(a_color, theme)
        _draw_arrow(d, anchor, ARROW_DIRS.get(a_dir, (0, 1)),
                    a_len, acol, a_width, a_curve, s)

    im.save(out_path)
    return {"w": W, "h": H, "bw": bw, "bh": bh}


def resolve_pos(info):
    pos = info.get("pos", "bc")
    if isinstance(pos, dict):
        return float(pos.get("x", 0.5)), float(pos.get("y", 0.5))
    return POS_PRESET.get(pos, POS_PRESET["bc"])


def make_preview(manifest, out_dir, s):
    """把全部贴片拼成一张总览图（深灰底、每条一行、带形状/主题/尺寸标注）。

    用途：只跑渲染阶段就能看字号、配色、断行与留白是否合适，不必等 ffmpeg 叠加。
    """
    rows = []
    for i, m in enumerate(manifest, 1):
        im = Image.open(m["png"]).convert("RGBA")
        rows.append((i, m, im))
    if not rows:
        return None
    pad = int(24 * s)
    label_h = int(26 * s)
    maxw = max(r[2].width for r in rows)
    total_h = sum(r[2].height + label_h + pad for r in rows) + pad
    canvas = Image.new("RGBA", (maxw + 2 * pad, total_h), (26, 28, 34, 255))
    # 棋盘格底：深色与浅色主题的贴片都能看清边界
    ck = 18
    cd = ImageDraw.Draw(canvas)
    for yy in range(0, total_h, ck):
        for xx in range(0, maxw + 2 * pad, ck):
            if ((xx // ck) + (yy // ck)) % 2 == 0:
                cd.rectangle([xx, yy, xx + ck - 1, yy + ck - 1], fill=(52, 54, 60, 255))
    d = ImageDraw.Draw(canvas)
    lf = K.body_font(max(12, int(13 * s)))
    y = pad
    for i, m, im in rows:
        d.rectangle([0, y - int(4 * s), maxw + 2 * pad, y + label_h - int(4 * s)],
                    fill=(22, 24, 30, 255))
        d.text((pad, y),
               "#%02d  %s / %s  %dx%d  t=%.1f  hold=%.1f"
               % (i, m["shape"], m["theme"], m["w"], m["h"], m["t"], m["hold"]),
               font=lf, fill=(160, 172, 188, 255))
        y += label_h
        canvas.alpha_composite(im, (pad, y))
        y += im.height + pad
    out = os.path.join(out_dir, "preview.png")
    canvas.convert("RGB").save(out)
    print("贴片总览 -> %s" % out)
    return out


def make_infocards(spec_path, W, H, out_dir, preview=False):
    """返回 render.json 路径。"""
    os.makedirs(out_dir, exist_ok=True)
    data = json.load(open(spec_path, encoding="utf-8"))
    if isinstance(data, dict):
        items = data.get("infocards", data.get("captions", []))
    else:
        items = data
    s = K.canvas_scale(W, H)
    manifest = []
    for i, info in enumerate(items, 1):
        if not (info.get("text") or "").strip():
            continue
        png = os.path.join(out_dir, "info_%02d.png" % i)
        size = render_info(info, s, png)
        cx, cy = resolve_pos(info)
        manifest.append({
            "png": png,
            "w": size["w"], "h": size["h"],
            "cx": cx, "cy": cy,
            "shape": (info.get("shape") or "card").lower(),
            "anim": (info.get("anim") or "fade").lower(),
            "t": float(info.get("t", 0)),
            "hold": float(info.get("hold", 3.0)),
            "theme": (info.get("theme") or "dark").lower(),
        })
    rj = os.path.join(out_dir, "render.json")
    json.dump({"W": W, "H": H, "items": manifest}, open(rj, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("信息贴片 %d 张 -> %s (render.json 已写)" % (len(manifest), out_dir))
    if preview:
        make_preview(manifest, out_dir, s)
    return rj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--infocards", required=True, help="infocards.json")
    ap.add_argument("--size", default="768x1280", help="视频画布尺寸")
    ap.add_argument("--out", required=True, help="紧致 PNG 输出目录")
    ap.add_argument("--preview", action="store_true",
                    help="额外输出 preview.png 总览图，检查字号/配色/断行")
    a = ap.parse_args()
    W, H = [int(x) for x in a.size.lower().split("x")]
    make_infocards(a.infocards, W, H, a.out, a.preview)


if __name__ == "__main__":
    main()
