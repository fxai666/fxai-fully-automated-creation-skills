# -*- coding: utf-8 -*-
"""判定封面文字该贴哪一侧：贴「空余最多」的一边，不与主体抢位置。

为什么需要：竖排标题贴左是写死的默认值。遇到人物偏左的底图，文字和人物挤在同一侧，
标题压住了主体边缘，右边大片空景反而浪费。正确判据是先看底图，再决定文字侧；
主体尽量留在反侧。

判据（只用 PIL，不引 numpy / 不调模型）：
  1. 灰度缩到 120×200，取上 75%（避开底部暗部与字幕区）
  2. 比较「左带 0~24%」与「右带 76~100%」的空度：
     empty = 1 - 亮像素占比 - 边缘能量/255
     bright 高 = 有浅色主体（人脸、米白裙、白字）；edge 高 = 纹理/轮廓多
  3. 空度更高的一侧 = 文字侧；另算亮像素水平重心做交叉印证
  4. 三段（上部 / 中部 / 全段）都判同一侧才敢自动翻边；不一致就保守用左侧

项目 封面.json 可写 "text_side": "auto" | "left" | "right" 覆盖判定。
"""
from __future__ import annotations

from PIL import Image, ImageFilter

_BAND_L = (0, 29)
_BAND_R = (91, 120)
_Y0, _Y1 = 0, 150          # 120×200 图上的上 75%
_BRIGHT = 145              # 灰度阈值：浅色主体


def analyze(bg):
    """返回 (side, info)。side 为 'left' / 'right'。"""
    im = Image.open(bg).convert("L").resize((120, 200))
    g = im.load()
    e = im.filter(ImageFilter.FIND_EDGES).load()

    def score(x0, x1, y0=_Y0, y1=_Y1):
        n = bright = 0
        es = 0.0
        for y in range(y0, y1):
            for x in range(x0, x1):
                v = g[x, y]
                n += 1
                if v > _BRIGHT:
                    bright += 1
                es += e[x, y]
        b = bright / n
        ed = es / n
        return dict(bright=round(b, 3), edge=round(ed, 1),
                    empty=round(1 - b - ed / 255.0, 3))

    left, right = score(*_BAND_L), score(*_BAND_R)
    upper_l = score(*_BAND_L, y0=0, y1=80)
    upper_r = score(*_BAND_R, y0=0, y1=80)
    mid_l = score(*_BAND_L, y0=80, y1=150)
    mid_r = score(*_BAND_R, y0=80, y1=150)

    # 亮像素水平重心（主体大致在哪一侧）
    xs, cnt = 0, 0
    for y in range(_Y0, _Y1):
        for x in range(120):
            if g[x, y] > 160:
                xs += x
                cnt += 1
    centroid = round(xs / cnt / 120 * 100, 1) if cnt else 50.0

    votes = [right["empty"] > left["empty"],
             upper_r["empty"] > upper_l["empty"],
             mid_r["empty"] > mid_l["empty"]]
    unanimous = all(votes)
    margin = round(right["empty"] - left["empty"], 3)
    tie_break = abs(margin) < 0.05
    if tie_break:
        # 两侧都很空、空度差不足以定案 → 用主体重心反侧兜底
        side = "right" if centroid < 50 else "left"
    else:
        side = "right" if (unanimous and margin > 0) else "left"

    info = dict(left=left, right=right,
                upper=(upper_l, upper_r), mid=(mid_l, mid_r),
                centroid=centroid, votes=sum(votes), margin=margin,
                tie_break=tie_break, unanimous=unanimous,
                reason="右侧更空" if side == "right" else "左侧更空")
    return side, info


def resolve_side(bg, configured="auto"):
    """auto 走检测；left/right 为人工覆盖。返回 (side, info)。"""
    c = (configured or "auto").strip().lower()
    if c in ("left", "right"):
        return c, dict(reason="项目配置指定", source="configured")
    side, info = analyze(bg)
    info["source"] = "auto"
    return side, info


def describe(side, info):
    """给 CLI 打印一行人话。"""
    if info.get("source") == "configured":
        return "文字侧：%s（项目配置指定）" % side
    l, r = info["left"], info["right"]
    extra = "；两侧都空，按主体重心反侧兜底" if info.get("tie_break") else ""
    return ("文字侧：%s —— 主体亮部重心 x=%.1f%%，左带空度 %.3f / 右带空度 %.3f（%s%s）"
            % (side, info["centroid"], l["empty"], r["empty"],
               info["reason"], extra))


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        raise SystemExit("用法：python cover_side.py <底图>")
    s, i = analyze(sys.argv[1])
    print(describe(s, i))
