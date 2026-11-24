#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""引导文案 × 循环 → 完整多拍 timeline（标题卡 · T-01 多段循环引导卡）。

片子一长，"引导几步各占 1/3"会把字条从片头挂到尾。正确做法：
引导文案按固定节拍**反复进出**，一拍只显示几秒就撤，隔一段再整组循环。

节拍口径（规范见 references/02 第三节）：
  ON  = 7.0s  一拍显示时长（竖排 8 字副标读完约 2-3s，7s 读完从容不赶）
  OFF = 1.5s  拍间/组间留白（含换字缓冲），换字不在脸上硬闪
  换边：默认按拍序左右交替写 side（跨循环连续）——相邻两拍落不同侧，
        同位硬切闪跳消掉；--no-alternate 恢复整片同侧
  循环周期 = 步数 × (ON + OFF)
  收尾：剩余 < ON 时不起新拍；单拍不足 2s 不放

用法：
  python scripts/make_loop_timeline.py --steps templates/guide-loop-card.json \
         --total 88.92 --out timeline.json
  # 节奏可改：--on 7 --off 1.5；同侧钉死：--no-alternate

⚠ 配套：出卡后 align_titles.py 要带 --timeline 才能按 side 分边对齐；
   gen_cards.py 已认 beat 里的 side 字段，无需额外参数。
"""
import argparse
import json
import sys

MIN_BEAT = 2.0  # 收尾残拍不足这个数就不放


def loop_timeline(steps, total, on=7.0, off=1.5, alternate=True):
    """steps = [{title, sub, foot}, ...]（忽略 start/end，只取文案）。"""
    steps = [s for s in steps if (s.get("title") or s.get("sub"))]
    if not steps:
        sys.exit("文案里没有有效拍（title/sub 全空）")
    beats, t, i = [], 0.0, 0
    stopped = False
    while not stopped:
        for s in steps:
            if t + on > total + 0.01:
                end = total
                if end - t < MIN_BEAT:
                    stopped = True
                    break
            else:
                end = t + on
            i += 1
            b = {"i": i, "start": round(t, 2), "end": round(end, 2),
                 "title": s.get("title", ""), "sub": s.get("sub", ""),
                 "foot": s.get("foot", "")}
            if alternate:
                b["side"] = "left" if i % 2 == 1 else "right"
            beats.append(b)
            t = end + off
        if t >= total - MIN_BEAT:
            break
    return {"total": total, "beats": beats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", required=True, help="引导文案 json（timeline/cards/steps 皆可，只取文案）")
    ap.add_argument("--total", required=True, type=float, help="片长（秒）")
    ap.add_argument("--on", type=float, default=7.0, help="一拍显示时长，默认 7.0")
    ap.add_argument("--off", type=float, default=1.5, help="拍间留白，默认 1.5")
    ap.add_argument("--no-alternate", action="store_true", help="整片同一侧，不左右交替")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    d = json.load(open(a.steps, encoding="utf-8"))
    if isinstance(d, dict):
        steps = d.get("beats") or d.get("cards") or d.get("steps") or []
    else:
        steps = d
    tl = loop_timeline(steps, a.total, a.on, a.off, alternate=not a.no_alternate)
    json.dump(tl, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    n = len(tl["beats"])
    cycles = n / max(1, len(steps))
    print("循环 timeline：%d 拍（约 %.1f 组循环，ON=%gs OFF=%gs 换边=%s）-> %s"
          % (n, cycles, a.on, a.off, "左右交替" if not a.no_alternate else "同侧", a.out))
    if n < 2 * len(steps):
        print("[warn] 只排得下不到两组循环，建议直接走一次性排布版")


if __name__ == "__main__":
    main()
