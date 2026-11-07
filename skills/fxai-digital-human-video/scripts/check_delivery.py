#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S5 · 交付自检门禁（缺一项就非零退出）

把「什么才算做完」变成机器可验证的清单，而不是靠人记。
每一条都对应一次真实翻车，不是形式主义：

  · 只交到 成片_{W}x{H}.mp4（中间成片）→ 没有标题卡、没有封面
  · 场景图只留在服务端/缓存 → 项目里没有，等于没交付
  · _beat_times.json 没算 → 标题卡定位不了，干脆不叠
  · 封面段没垫音 → 正文人声提前冒出来，音画错位

用法：
  python scripts/check_delivery.py --project <项目>
  python scripts/check_delivery.py --project <项目> --size 1280x768
退出码：0 全绿；1 有缺项。
"""
import argparse, glob, json, os, subprocess, sys

OK, BAD, WARN, INFO = "✅", "❌", "⚠️ ", "•"


def _cover_sec():
    """封面段秒数。帧数定案见 fxai-poster/references/05-封面模板库.md「封面段时长」，
    本脚本只按本产线帧率换算，不写死数值。"""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from overlay_titles import COVER_SEC
        return COVER_SEC
    except Exception:
        return 10 / 24.0


def ffprobe_dur(path):
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import overlay_titles
        ff = overlay_titles.ffmpeg_bin()
        probe = os.path.join(os.path.dirname(ff), "ffprobe.exe")
        if not os.path.isfile(probe):
            probe = "ffprobe"
        p = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", path],
                           capture_output=True, text=True)
        return float(p.stdout.strip())
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    ap.add_argument("--size", default="768x1280")
    a = ap.parse_args()
    proj, size = a.project, a.size
    img = os.path.join(proj, "image")

    rows = []   # (状态, 项, 说明)

    def chk(cond, item, detail, warn_only=False):
        rows.append((OK if cond else (WARN if warn_only else BAD), item, detail))
        return cond

    # 1 中间成片
    mid = os.path.join(proj, "成片_%s.mp4" % size)
    has_mid = os.path.isfile(mid) and os.path.getsize(mid) > 1024
    chk(has_mid, "中间成片 成片_%s.mp4" % size,
        ("%.1f MB" % (os.path.getsize(mid) / 1e6)) if has_mid else "缺（S3 没跑完）")

    # 2 交付物
    final = os.path.join(proj, "成片_最终_%s.mp4" % size)
    has_final = os.path.isfile(final) and os.path.getsize(final) > 1024
    chk(has_final, "★交付物 成片_最终_%s.mp4" % size,
        ("%.1f MB" % (os.path.getsize(final) / 1e6)) if has_final
        else "缺 —— 没跑 S4 finalize.py，这只是半成品")

    # 3 节拍时间码
    beats_path = os.path.join(proj, "_beat_times.json")
    beats = []
    if os.path.isfile(beats_path):
        try:
            beats = json.load(open(beats_path, encoding="utf-8")).get("beats", [])
        except Exception:
            beats = []
    chk(bool(beats), "_beat_times.json", ("%d 个节拍" % len(beats)) if beats else "缺（标题卡无法定位）")

    # 4-5 标题卡（可选：没有 cards.json 就是不叠顶部字，不算缺项）
    cards = []
    cp = os.path.join(img, "cards.json")
    if os.path.isfile(cp):
        try:
            cards = json.load(open(cp, encoding="utf-8"))
        except Exception:
            cards = []
        chk(bool(cards), "image/cards.json", ("%d 条" % len(cards)) if cards else "文件存在但读不出")
        if cards and beats:
            chk(len(cards) == len(beats), "cards 与节拍一一对应",
                "%d 条 vs %d 段" % (len(cards), len(beats)))
        if beats:
            miss = [i for i in range(1, len(beats) + 1)
                    if not os.path.isfile(os.path.join(img, "title_%02d.png" % i))]
            chk(not miss, "image/title_NN.png",
                ("%d 张齐" % len(beats)) if not miss else "缺 %d 张：%s" % (len(miss), miss[:6]))
    else:
        rows.append((INFO, "image/cards.json", "无 —— 本片不叠标题卡（可选，不算缺项）"))

    # 6 封面（默认有，可 --no-cover 跳过；有没有拼上由第 10 项按时长判定）
    cover = os.path.join(img, "cover.png")
    has_cover = os.path.isfile(cover)
    rows.append((OK if has_cover else INFO, "image/cover.png",
                 "在" if has_cover else "无（本片不拼封面，可选）"))

    # 7 场景图已取回本地（multiref 至少 2 张）
    scenes = glob.glob(os.path.join(img, "scene_*.png"))
    chk(len(scenes) >= 2, "image/scene_NN.png",
        "%d 张已落本地" % len(scenes) if len(scenes) >= 2
        else "只有 %d 张 —— 服务端生成的场景图没取回本地" % len(scenes))

    # 8 字幕
    subs = [p for p in (os.path.join(proj, "字幕.srt"), os.path.join(proj, "字幕.ass"))
            if os.path.isfile(p)]
    chk(bool(subs), "字幕文件", " / ".join(os.path.basename(p) for p in subs) if subs else "缺")

    # 9 清单
    lists = [p for p in (os.path.join(proj, "交付清单.md"), os.path.join(proj, "视频清单.md"))
             if os.path.isfile(p)]
    chk(bool(lists), "交付清单", " / ".join(os.path.basename(p) for p in lists) if lists else "缺",
        warn_only=True)

    # 10 封面有没有真的拼上（时长差应等于封面段；--no-cover 时差 ≈0，属可选）
    # 封面段帧数的定案在 fxai-poster/references/05-封面模板库.md「封面段时长」，此处只换算。
    if has_mid and has_final:
        d1, d2 = ffprobe_dur(mid), ffprobe_dur(final)
        if d1 and d2:
            gap = d2 - d1
            want = _cover_sec()
            detail = "中间 %.2fs → 最终 %.2fs（差 %.2fs，应为 %.2fs）" % (d1, d2, gap, want)
            if abs(gap - want) <= 0.25:
                rows.append((OK, "封面已拼在片头", detail))
            elif abs(gap) <= 0.15:
                if has_cover:
                    rows.append((BAD, "封面已拼在片头",
                                 detail + " —— cover.png 在，但没拼上（流程 bug）"))
                else:
                    rows.append((INFO, "封面已拼在片头",
                                 detail + " —— 本片不拼封面（可选）"))
            else:
                rows.append((BAD, "封面已拼在片头", detail + " —— 时长差异常"))

    # ★ 分镜 + 提示词门禁（preflight）
    # 交付前必须过：分镜合规、提示词里没有头部动作词、motion/expression 真的进了提示词。
    # 这三类肉眼看不出来 —— 成片「看着还行」但人物呆板，就是提示词层出的问题。
    pf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preflight.py")
    sb = os.path.join(proj, "image", "storyboard.json")
    if os.path.isfile(pf) and os.path.isfile(sb):
        try:
            r = subprocess.run([sys.executable, pf, "--project", proj],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            ok = (r.returncode == 0)
            lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
            # 取「N 项未过 / 全过」那行做摘要（preflight 后面还会打返修清单，最后一行是分隔线）
            summary = next((l for l in reversed(lines)
                            if "项未过" in l or "全过" in l),
                           lines[-1] if lines else "无输出")
            chk(ok, "★分镜/提示词门禁 preflight", summary)
            if not ok:
                # 把「哪几镜要返修 + 现成命令」直接抄进门禁输出。
                # 门禁只报「有未过项」而不给返修入口，人就会去整片重跑 —— 21 号就是这么翻车的。
                pfj = os.path.join(proj, "doc", "_preflight.json")
                tips = []
                try:
                    with open(pfj, encoding="utf-8") as f:
                        pfd = json.load(f)
                    shots = pfd.get("rework_shots") or []
                    if shots:
                        cmd = ("python \"%s\" --project \"%s\" --shots %s"
                               % (os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                               "refix.py"),
                                  os.path.abspath(proj),
                                  ",".join(map(str, shots))))
                        tips.append("需要返修：第 %s 镜（未过项 %s）"
                                    % ("、".join(map(str, shots)),
                                       "、".join(pfd.get("failed") or [])))
                        tips.append("单段返修（串行，全部渲完才继续，别整片重跑）：" + cmd)
                except Exception:                                # noqa: BLE001
                    for l in lines:
                        if "refix.py" in l or "需要返修" in l:
                            tips.append(l)
                for t in tips:
                    rows.append((BAD, "   → 返修入口", t))
                for l in lines:
                    if l.startswith(BAD) and "返修" not in l:
                        rows.append((INFO, "   门禁未过", l[1:].strip()))
        except Exception as e:
            chk(False, "★分镜/提示词门禁 preflight", "跑不起来：%s" % e)

    # 输出
    print("\n交付自检 · %s（%s）\n" % (os.path.abspath(proj), size))
    for st, item, detail in rows:
        print("  %s %-28s %s" % (st, item, detail))
    bad = [r for r in rows if r[0] == BAD]
    info = [r for r in rows if r[0] == INFO]
    print("\n%s  %d 项通过，%d 项未过%s" %
          (OK if not bad else BAD, len(rows) - len(bad) - len(info), len(bad),
           ("，%d 项可选未做" % len(info)) if info else ""))
    if bad:
        print("\n未过项需补齐后重跑本脚本。")
        sys.exit(1)
    print("\n全绿，可交付：%s" % os.path.join(proj, "成片_最终_%s.mp4" % size))


if __name__ == "__main__":
    main()
