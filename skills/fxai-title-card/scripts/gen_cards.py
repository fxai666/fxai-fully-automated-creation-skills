#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标题卡文字 → 透明 PNG（一卡一张，尺寸/位置可配）。

字号是**固定档**（`cardkit.TIERS`），不按画布比例算：
按短边分 小(≤800) / 中(≤1200) / 大(≤1800) / 超大，同一档内所有分辨率同字号。
768×1280、720×1280、1280×720 都是小档（主标 64）；1080×1920、1920×1080 都是中档（主标 96）。
改字号只改那张表。

排版规范（写死，别在项目里另调）：
· 主标 得意黑 SmileySans-Oblique，金色 (224,180,95) + 深棕描边 (45,33,14)
· 副标 Noto Sans SC Bold 纯白；引语 Noto Sans SC (235,235,235,220)
· 横排（竖版片）：主标基线 top_y，三行收拢，占屏越小越好
· 竖排（横版片）：贴左/右边缘竖着排，主标贴边、副标引语依次向内并低一截
· 纯文字零蒙版：标题卡不画渐变底

为什么横版要竖排：竖版人物在画面中间偏上，顶部压字不挡人；横版人物居中，
顶部一行横字正压脸上。竖排靠边只占一侧窄条，人物区域全留出来。

用法：
  python scripts/gen_cards.py --timeline timeline.json --size 768x1280 --out image/
  python scripts/gen_cards.py --timeline t.json --size 1920x1080 --position left --out titles/
  （--position 默认 auto：竖版走 top，横版**左右交替**贴边竖排；
   钉死一侧用 --position left / right，人物明显偏某一侧时才用）
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cardkit as K
from PIL import Image, ImageDraw, ImageFont

# 竖排（横版侧边）版式：这些是**相对字号的比例**，不是固定像素
SIDE_MAXW = 0.42      # 竖排块最多占画布宽度的比例，留出中间给人
V_GAP_IN = 0.50       # 同一组内换列：列间距 = 字号 × 该值
V_GAP_OUT = 0.62      # 主标→副标→引语：列间距 = 相邻字号均值 × 该值
V_DROP = 0.42         # 副标/引语列比主标顶低多少（× 主标字号），做出层次
V_CAP_TITLE = 9       # 一列最多几个字（主标）：超过就分列，宁可两列也别顶天立地
V_CAP_OTHER = 8       # 副标/引语一列最多几个字（一句话竖着排会占满屏高）
V_PUNCT = "、，。；：！？,.;:!?…—）】」』"


# ---------------- 横排（竖版片） ----------------
def _draw_horizontal(im, d, title, sub, foot, W, H, T, position):
    ft = ImageFont.truetype(K.F_TITLE, T["title"] if len(title) <= 7 else T["title_long"])
    fs = ImageFont.truetype(K.F_NOTO_B, T["sub"])
    stroke = T["stroke"]
    tb = d.textbbox((0, 0), title, font=ft, stroke_width=stroke)
    sb = d.textbbox((0, 0), sub, font=fs) if sub else (0, 0, 0, 0)

    if position == "center":
        block_h = tb[3] + (sb[3] + T["gap_sub"] if sub else 0)
        ty = int(H * 0.36 - block_h / 2)
    elif position == "bottom":
        ty = H - T["bottom_pad"]
    else:
        ty = T["top_y"]

    if title:
        K.text_center(d, W, ty, title, ft, K.GOLD, stroke=stroke, stroke_fill=K.DARK)
    if sub:
        sy = ty + tb[3] + T["gap_sub"]
        K.text_center(d, W, sy, sub, fs, (255, 255, 255, 255))
    else:
        sy = ty + tb[3]

    if foot:
        ff = ImageFont.truetype(K.F_NOTO, T["foot"])
        fy = sy + sb[3] + T["gap_foot"]
        K.text_center(d, W, fy, foot, ff, (235, 235, 235, 220))


# ---------------- 竖排（横版片：贴左 / 贴右） ----------------
def _vparts(title, sub, foot):
    """(文字, 类别, 字色, 描边比例, 描边色) —— 顺序即从贴边向内的顺序。

    类别 1 主标 / 2 副标 / 3 引语；字号全部来自固定档，
    描边 = 档位描边宽 × 这个比例（小档 4 / 中档 6 / 大档 8 / 超大 12）。
    """
    p = []
    if title:
        p.append((title, 1, K.GOLD, 1.00, K.DARK))
    if sub:
        # 竖排窄条，副标比横排略大；描一层极淡深色，浅背景（白墙/亮窗）上白字才有轮廓
        p.append((sub, 2, (255, 255, 255, 255), 0.60, (25, 20, 12, 130)))
    if foot:
        p.append((foot, 3, (235, 235, 235, 220), 0.45, (25, 20, 12, 120)))
    return p


def _vfont_size(kind, text, T):
    """主标按字数 7 字分大小两档，副标/引语各一档。全部查表，不乘比例。"""
    if kind == 1:
        return T["title"] if len(text) <= 7 else T["title_long"]
    if kind == 2:
        return T["sub_v"]
    return T["foot_v"]


def _font_path(kind):
    return K.F_TITLE if kind == 1 else (K.F_NOTO_B if kind == 2 else K.F_NOTO)


def _split_even(cl, cap, fit):
    """切列：优先在标点后断开，其次按列数均分。

    11 个字宁可 6+5，不要 8+3（末列剩两三个字很难看）；
    但如果均分点落在词中间（"找配" / "音、找剪辑"），就往回退到最近的顿号/逗号后断，
    宁可两列高矮不齐，也不能把词劈开。
    """
    n = len(cl)
    limit = min(cap, fit)
    if n <= limit:
        return [cl]
    out, i = [], 0
    while i < n:
        left = n - i
        if left <= limit:
            out.append(cl[i:]); break
        k = -(-left // limit)              # 还够几列
        per = -(-left // k)                # 均分
        cut = i + per
        for j in range(max(i + 1, cut - 2), min(i + limit, n - 1) + 1):
            if cl[j - 1][-1] in V_PUNCT:   # 上一个单元是标点 → 在它后面断
                cut = j; break
        out.append(cl[i:cut]); i = cut
    return out


def _layout_vertical(parts, title, W, H, T, f):
    """排一遍：返回 (列清单, 块宽, 块高, 可用高度)。f = 整组字号缩放系数（放不下才小于 1）。"""
    margin_y = T["side_y"]
    avail_h = max(40, H - 2 * margin_y)
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    base_title = _vfont_size(1, title, T)
    drop = int(base_title * V_DROP * f)     # 副标/引语列下移，主标永远最高
    cols = []
    for gi, (text, kind, fill, swr, sfill) in enumerate(parts):
        fsz = _vfont_size(kind, text, T)
        font = ImageFont.truetype(_font_path(kind), max(12, int(fsz * f)))
        stroke = max(2, int(T["stroke"] * swr * f)) if swr else 0
        cell = K.vcell(font)
        fit = max(1, int((avail_h - (drop if gi else 0)) // cell))
        cap = V_CAP_TITLE if gi == 0 else V_CAP_OTHER
        for chunk in _split_even(K.vclusters(text), cap, fit):
            cw = font.size
            for c in chunk:
                if not K.is_cjk(c[0]) and len(c) > 3:
                    cw = max(cw, int(font.size * 1.35))     # 旋转串宽 ≈ 字高
                else:
                    bb = probe.textbbox((0, 0), c, font=font, stroke_width=stroke)
                    cw = max(cw, bb[2] - bb[0])
            cols.append(dict(cl=chunk, font=font, fill=fill, stroke=stroke,
                             sfill=sfill, gi=gi, w=int(cw),
                             y_off=(drop if gi else 0),
                             h=min(len(chunk) * cell, avail_h)))
    # 列间距：同组内窄、跨组稍宽（按字号比例）
    for i in range(len(cols) - 1):
        a, b = cols[i], cols[i + 1]
        if a["gi"] == b["gi"]:
            cols[i]["gap"] = int(a["font"].size * V_GAP_IN)
        else:
            cols[i]["gap"] = int((a["font"].size + b["font"].size) / 2 * V_GAP_OUT)
    if cols:
        cols[-1]["gap"] = 0
    total_w = sum(c["w"] for c in cols) + sum(c["gap"] for c in cols)
    total_h = max((c["y_off"] + c["h"] for c in cols), default=0)
    return cols, total_w, total_h, avail_h


def _draw_vertical(im, d, title, sub, foot, W, H, T, side):
    parts = _vparts(title, sub, foot)
    if not parts:
        return
    margin_x = T["side_x"]
    max_w = W * SIDE_MAXW

    f = 1.0
    for _ in range(10):
        cols, tw, th, avail_h = _layout_vertical(parts, title, W, H, T, f)
        if tw <= max_w and th <= avail_h:
            break
        f *= 0.92                      # 放不下就整组缩字号，先缩字不裁字

    y0 = T["side_y"] + max(0, (avail_h - th) // 2)     # 安全区里垂直居中

    if side == "right":
        x = W - margin_x
        for c in cols:
            x -= c["w"] / 2
            K.draw_vcol(im, d, x, y0 + c["y_off"], c["cl"], c["font"], c["fill"],
                        c["stroke"], c["sfill"])
            x -= c["w"] / 2 + c["gap"]
    else:
        x = margin_x
        for c in cols:
            x += c["w"] / 2
            K.draw_vcol(im, d, x, y0 + c["y_off"], c["cl"], c["font"], c["fill"],
                        c["stroke"], c["sfill"])
            x += c["w"] / 2 + c["gap"]


def resolve_side(position, W, H, seq=1):
    """按第几张卡（seq 从 1 数）定这一张落哪。

    · auto / alternate（默认）：竖版（含方形）压顶部；**横版左右交替**（奇数右、偶数左）——
      一直贴同一侧看久了呆板，交替出现视觉更活；人物居中也不怕，两侧轮流空出来。
    · left / right：整条片钉死一侧（人物明显偏某一侧时用，把人让开）。
    · top / center / bottom：横排。
    """
    if position in ("left", "right", "top", "center", "bottom"):
        return position
    if W > H:
        return "right" if seq % 2 == 1 else "left"
    return "top"


def make_cards(beats, W, H, out_dir, position="auto", prefix="title_"):
    os.makedirs(out_dir, exist_ok=True)
    T = K.tier_for(W, H)             # 固定字号档，不按画布比例算
    n = 0
    for idx, b in enumerate(beats, 1):
        title = (b.get("title") or "").strip()
        sub = (b.get("sub") or "").strip()
        foot = (b.get("foot") or "").strip()
        if not title and not sub:     # 空卡不压，也不占交替位
            continue

        # 单拍可以钉死一侧：beat 里写 "side": "left"|"right"（人物正好站那侧时手动让开）
        want = (b.get("side") or b.get("pos") or "").strip().lower()
        if want in ("left", "right", "top", "center", "bottom"):
            pos = want
        else:
            pos = resolve_side(position, W, H, n + 1)   # 按「第几张可见卡」左右交替
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        if pos in ("left", "right"):
            _draw_vertical(im, d, title, sub, foot, W, H, T, pos)
        else:
            _draw_horizontal(im, d, title, sub, foot, W, H, T, pos)

        im.save(os.path.join(out_dir, "%s%02d.png" % (prefix, idx)))
        n += 1
    if position in ("auto", "alternate", None, "") and W > H:
        pos_desc = "左右交替"
    else:
        pos_desc = resolve_side(position, W, H, 1)
    print("标题卡 %d 张 -> %s/%s01..%02d.png（位置 %s，字号档 %s／短边 %d）"
          % (n, out_dir, prefix, len(beats), pos_desc, T["name"], min(W, H)))
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", required=True, help="timeline.json")
    ap.add_argument("--cards", default=None, help="兼容：分离式文字清单")
    ap.add_argument("--size", default="768x1280", help="画布尺寸，默认 768x1280")
    ap.add_argument("--position", default="auto",
                    choices=["auto", "alternate", "top", "center", "bottom", "left", "right"],
                    help="auto=竖版压顶/横版左右交替竖排；left/right 钉死一侧；alternate 同 auto")
    ap.add_argument("--out", required=True, help="PNG 输出目录")
    a = ap.parse_args()

    W, H = [int(x) for x in a.size.lower().split("x")]
    beats = K.load_timeline(a.timeline, a.cards)
    make_cards(beats, W, H, a.out, a.position)


if __name__ == "__main__":
    main()
