#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量返修编排器（跨技能通用）—— 一次提交多段，串行跑完再返回。

为什么要有这一层
----------------
用户口径（2026-10-21）：
  **「如果三段都有问题，就三段都提交返修；这三段返修完了再去做后面的事。」**
  **「不仅仅是数字人，所有视频生成技能都共同遵守。」**

每个技能都有自己的单段入口（`--start-index N` / `--mv-segment=N` / `--single-shot N`），
但「批量 + 串行 + 全部完成才算完」这层编排是共通的，没必要每个技能重写一遍。

服务端合并规则（这是串行的理由）
--------------------------------
每渲完**一段**，H3 服务端都会拿其它段的缓存拼回一条**完整成片**。所以：
  · 并行提交 → 互相覆盖对方刚生成的段，成片里总有几段是旧的；
  · 中途那几次成片都是半成品，**只认最后一段渲完的那一次**；
  · 全部渲完之前，不许烧字幕、不许做封面、不许交付。

用法
----
    # {seg} = 0 起段索引；{shot} = 1 起镜号；{pos} = 本次第几个（1 起）
    python rework_batch.py --shots 3,7,9 \
        --cmd-template 'python produce_song.py --mv-segment={seg}'

    python rework_batch.py --shots 3,7,9 --dry-run \
        --cmd-template 'python drama_pipeline.py --single-shot {shot}'

    # 也可以只算计划，不跑（给 Agent 看该提交哪几段）
    python rework_batch.py --shots 3,7,9 --plan-only

退出码：0 = 全部成功；1 = 中途某段失败（已停下，未走的段会打印出来）；2 = 参数/模板错。
"""
import argparse
import subprocess
import sys
import time

try:
    import rework as rw
except ImportError:                                              # 允许直接跑
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import rework as rw


def main():
    ap = argparse.ArgumentParser(
        description="批量返修编排：多段一次提交，串行执行，全部完成才返回")
    ap.add_argument("--shots", required=True, help="镜号，1 起：3,7,9 或 3-6")
    ap.add_argument("--cmd-template", default="",
                    help="单段命令模板；{seg}=0 起索引 {shot}=1 起镜号 {pos}=第几个")
    ap.add_argument("--per-shot-min", type=float, default=9.0, help="每段预计分钟（只用于时间账）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的命令")
    ap.add_argument("--plan-only", action="store_true", help="只打印计划，不需要模板")
    ap.add_argument("--stop-on-fail", action="store_true", default=True,
                    help="某段失败立刻停（默认开，别改成 False）")
    a = ap.parse_args()

    seq = rw.plan_batch(a.shots, per_shot_min=a.per_shot_min, quiet=False)
    if not seq:
        print("✗ 镜号解析为空：%s" % a.shots)
        return 2

    if a.plan_only:
        print("\n（--plan-only：只出计划，不执行）")
        return 0

    if not a.cmd_template:
        print("\n✗ 要给 --cmd-template，例如：")
        print("   --cmd-template 'python produce_song.py --mv-segment={seg}'")
        print("  （只想要计划就加 --plan-only）")
        return 2

    cmds = [(m, a.cmd_template.format(seg=m - 1, shot=m, pos=k))
            for k, m in enumerate(seq, 1)]
    print("")
    for m, c in cmds:
        print("  第 %d 镜 → %s" % (m, c))

    if a.dry_run:
        print("\n（--dry-run 到此为止）")
        return 0

    t_all = time.time()
    done = []
    for k, (m, c) in enumerate(cmds, 1):
        print("")
        print("═══ [rework] 第 %d/%d 段：第 %d 镜 ═══" % (k, len(cmds), m))
        rc = subprocess.call(c, shell=True)
        if rc != 0:
            print("")
            print("✗ 第 %d 镜返修失败（返回 %d）" % (m, rc))
            if done:
                print("  已完成：第 %s 镜" % "、".join(map(str, done)))
            print("  未完成：第 %s 镜"
                  % "、".join(map(str, [x for x, _ in cmds if x not in done])))
            print("  修好这个错后重跑本命令；已渲好的段服务端有缓存，不会白渲")
            return 1
        done.append(m)
        print("[rework] ✓ 第 %d 镜渲完（%d/%d）" % (m, k, len(cmds)))

    mins = (time.time() - t_all) / 60.0
    print("")
    print("✅ 全部 %d 段返修完成，用时 %.1f 分钟" % (len(seq), mins))
    print("   成片已由服务端把新段与其它段合并；中间那几次成片都是半成品，别用。")
    print("")
    print("下一步（顺序固定，别跳）：")
    print("  1) 重跑这个技能的验收/预检，必须全绿")
    print("  2) 看返修段的抽帧，确认问题消失")
    print("  3) 才做字幕 / 封面 / 交付自检")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
