# -*- coding: utf-8 -*-
"""分镜表机械自检（语义项由智能体对照 09 册人工过）。

查的是「不用脑子就能判对错」的那些：段数、时间码、动作重复、运镜越界、B-roll 落点、
**主体运动轨迹 / 空镜物体动态 / 空镜景别 / 运动镜头占比**。
语义项（动作是否贴合内容、是否与场景实物对应）机器判不了，留给智能体读。

用法：
  python scripts/check_storyboard.py --storyboard image/storyboard.json \
         --audio-dir music --scenes image
"""
import argparse
import glob
import json
import os
import sys
import wave

OK, BAD, WARN = "✅", "❌", "⚠️ "


def audio_segments(audio_dir):
    fs = sorted(glob.glob(os.path.join(audio_dir, "dh-vo[0-9][0-9].wav")))
    if not fs:
        fs = sorted(glob.glob(os.path.join(audio_dir, "*.wav")))
    out = []
    for f in fs:
        try:
            w = wave.open(f)
            out.append((f, w.getnframes() / w.getframerate()))
        except Exception:
            continue
    return out


def h3_count(total, avg_seg=10.0):
    """分镜对齐的是 **H3 视频段数**，不是文案段数（16 段文案常被压成 12 段视频）。"""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "fxai-audio-video")
        if p not in sys.path:
            sys.path.insert(0, p)
        import audio_video as av
        _s, b = av.h3_segments(total, avg_seg)
        return len(b)
    except Exception:
        return max(1, int(round(total / 9.41667)))


def main():
    ap = argparse.ArgumentParser(description="分镜表机械自检")
    ap.add_argument("--storyboard", required=True)
    ap.add_argument("--audio-dir", help="段落音频目录（算段数与时长的基准）")
    ap.add_argument("--scenes", help="场景图目录（查 scene 路径是否存在）")
    ap.add_argument("--tol", type=float, default=0.25, help="时间码容差秒")
    ap.add_argument("--avg-seg", type=float, default=10.0, help="H3 平均分段时长（与 dh_video 一致）")
    a = ap.parse_args()

    sb = json.load(open(a.storyboard, encoding="utf-8"))
    shots = sb.get("shots") or []
    rows = []

    if not shots:
        sys.exit("分镜表没有 shots")

    # 1 段数（对齐 H3 视频段数）
    segs = audio_segments(a.audio_dir) if a.audio_dir else []
    if segs:
        total_a = sum(d for _f, d in segs)
        n_h3 = h3_count(total_a, a.avg_seg)
        if len(shots) == n_h3:
            rows.append((OK, "段数与 H3 一致", "%d 镜 / 音频 %.2fs 切 %d 段" % (len(shots), total_a, n_h3)))
        else:
            rows.append((BAD, "段数与 H3 不一致", "分镜 %d vs H3 %d（音频 %.2fs）"
                         % (len(shots), n_h3, total_a)))
    else:
        rows.append((WARN, "未给音频目录，段数未校验", ""))

    # 2 时间码
    shots_sorted = sorted(shots, key=lambda x: float(x.get("start", 0)))
    gaps = []
    prev_end = None
    for s in shots_sorted:
        st, en = float(s.get("start", 0)), float(s.get("end", 0))
        if en <= st:
            gaps.append("第 %s 段 end<=start" % s.get("i"))
        if prev_end is not None and abs(st - prev_end) > a.tol:
            gaps.append("第 %s 段起点 %.2f 与上一段终点 %.2f 不接" % (s.get("i"), st, prev_end))
        prev_end = en
    if segs:
        total_audio = sum(d for _f, d in segs)
        if abs(prev_end - total_audio) > 0.6:
            gaps.append("末尾 %.2f 与音频总长 %.2f 差得多" % (prev_end, total_audio))
    if gaps:
        rows.append((BAD, "时间码有问题", "；".join(gaps[:3])))
    else:
        rows.append((OK, "时间码首尾相接", "0 → %.2f" % (prev_end or 0)))

    # 3 动作重复
    acts = [str(s.get("action", "")).strip() for s in shots if s.get("action")]
    dup = {}
    for x in acts:
        dup[x] = dup.get(x, 0) + 1
    worst = max(dup.values()) if dup else 0
    if not acts:
        rows.append((BAD, "没有动作描述", "每段都要写 action"))
    elif worst > 2:
        rows.append((BAD, "动作重复过多", "同一动作出现 %d 次：%s" % (
            worst, [k[:18] for k, v in dup.items() if v == worst][0])))
    else:
        rows.append((OK, "动作不重复", "%d 条动作，最多重复 %d 次" % (len(acts), worst)))

    # 4 运镜越界
    BAN = ("平移", "摇", "跟拍", "环绕", "升降", "拉远", "推远", "后拉")
    bad_cam = []
    for s in shots:
        cam = str(s.get("camera", ""))
        for b in BAN:
            if b in cam:
                bad_cam.append("第 %s 段运镜含「%s」" % (s.get("i"), b))
                break
    if bad_cam:
        rows.append((BAD, "运镜越界", "；".join(bad_cam[:3])))
    else:
        rows.append((OK, "运镜在边界内", "固定 / 极缓推近"))

    # 5 B-roll
    bro = [s for s in shots if s.get("broll")]
    bro_bad = []
    for s in bro:
        b = s["broll"]
        if not b.get("what"):
            bro_bad.append("第 %s 段 broll 没写 what" % s.get("i"))
        d = float(b.get("dur", 0) or 0)
        if d < 2.0 or d > 8.0:
            bro_bad.append("第 %s 段 broll 时长 %.1fs 不在 3~6s 附近" % (s.get("i"), d))
        st = float(b.get("start", 0) or 0)
        if st < float(s.get("start", 0)) - 0.01 or st + d > float(s.get("end", 0)) + 0.05:
            bro_bad.append("第 %s 段 broll 落点越出本段" % s.get("i"))
    if bro_bad:
        rows.append((BAD, "B-roll 标记有问题", "；".join(bro_bad[:3])))
    elif bro:
        rows.append((OK, "B-roll 标记完整", "%d 段" % len(bro)))
    else:
        rows.append((OK, "本片不插 B-roll", "全 A-roll"))

    # 6 场景文件
    if a.scenes:
        miss = [s.get("scene") for s in shots
                if s.get("scene") and not os.path.isfile(s["scene"])
                and not os.path.isfile(os.path.join(a.scenes, os.path.basename(str(s["scene"]))))]
        if miss:
            rows.append((WARN, "场景图找不到", "、".join(str(x) for x in miss[:3])))
        else:
            rows.append((OK, "场景图都在", "%d 张" % len(shots)))

    # 8 主体运动轨迹（09 册第 10 条）
    no_motion = [s.get("i") for s in shots if not str(s.get("motion", "")).strip()]
    # 「站在原地」「站着讲解」这类是状态不是轨迹
    STATIC_WORDS = ("站在原地", "原地站立", "静止不动", "不动")
    state_like = [s.get("i") for s in shots
                  if any(w in str(s.get("motion", "")) for w in STATIC_WORDS)]
    if no_motion:
        rows.append((BAD, "缺主体运动轨迹", "第 %s 段没写 motion" % "、".join(str(x) for x in no_motion[:5])))
    elif state_like:
        rows.append((BAD, "motion 写成了状态", "第 %s 段" % "、".join(str(x) for x in state_like[:5])))
    else:
        move_words = sum(1 for s in shots
                         if any(w in str(s.get("motion", "")) for w in
                                ("走", "移动", "靠近", "横移", "俯身", "坐下", "站起", "退", "重心")))
        rows.append((OK, "每镜都有运动轨迹", "%d 镜，其中 %d 镜有位移或朝向变化"
                     % (len(shots), move_words)))

    # 9 空镜 / B-roll 的物体动态（09 册第 11 条）
    bro_no_dyn = [s.get("i") for s in bro
                  if not str((s["broll"] or {}).get("dynamics", "")).strip()
                  and not any(w in str((s["broll"] or {}).get("what", ""))
                              for w in ("滚", "按", "翻", "闪", "移动", "亮", "画", "延伸", "抖", "推进"))]
    if bro_no_dyn:
        rows.append((BAD, "空镜没有物体动态", "第 %s 段 broll 没写 dynamics，出来是静图"
                     % "、".join(str(x) for x in bro_no_dyn[:5])))
    elif bro:
        rows.append((OK, "空镜都有物体动态", "%d 段" % len(bro)))

    # 10 空镜景别（09 册第 12 条）
    CLOSE_UP = ("特写", "近景", "大特写")
    bro_close = []
    for s in bro:
        sz = str((s["broll"] or {}).get("size", "") or "")
        if any(w in sz for w in CLOSE_UP):
            bro_close.append("第 %s 段 %s" % (s.get("i"), sz))
    if bro_close:
        rows.append((BAD, "空镜景别过近", "；".join(bro_close[:3]) + "（空镜用中景或中远景）"))
    elif bro:
        rows.append((OK, "空镜景别合适", "中景 / 中远景"))

    # 11 有运动的镜头占多数（09 册第 13 条）
    moving = sum(1 for s in shots
                 if any(w in str(s.get("motion", "")) for w in
                        ("走", "移动", "靠近", "横移", "俯身", "坐下", "站起", "重心"))
                 or any(w in str(s.get("camera", "")) for w in ("推近", "推"))
                 or str(s.get("dynamics", "")).strip())
    if moving * 2 >= len(shots):
        rows.append((OK, "运动镜头占多数", "%d / %d 镜有运动" % (moving, len(shots))))
    else:
        rows.append((BAD, "静止镜头过多", "只有 %d / %d 镜有运动，多数镜头会僵"
                     % (moving, len(shots))))

    # 7 why
    no_why = [s.get("i") for s in shots if not str(s.get("why", "")).strip()]
    if no_why:
        rows.append((WARN, "有镜头没写 why", "第 %s 段" % "、".join(str(x) for x in no_why[:5])))
    else:
        rows.append((OK, "每镜都有设计理由", "why 齐全"))

    print("\n分镜自检 · %s\n" % a.storyboard)
    for mark, name, detail in rows:
        print("  %s %-18s %s" % (mark, name, detail))
    bad = sum(1 for m, _n, _d in rows if m == BAD)
    warn = sum(1 for m, _n, _d in rows if m == WARN)
    print("\n%s  %d 项通过，%d 项未过，%d 项待看\n"
          % (OK if bad == 0 else BAD, len(rows) - bad - warn, bad, warn))
    print("语义项（动作是否贴合内容 / 是否与场景实物对应 / 是否在技术边界内）"
          "对照 references/09-分镜设计与验收.md 再读一遍。\n")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
