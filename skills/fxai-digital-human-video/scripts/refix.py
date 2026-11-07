#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单镜返修：只重渲指定的一镜，其余段走服务端缓存合并。

为什么要这个脚本
----------------
出片后发现某一镜不对（人物侧脸、腰线切在地面、走位看不见…），正确做法是
**只改那一镜的分镜字段 + 只重渲那一镜**，不是整片重来：

    整片 88 s ≈ 70 min    单镜 9.42 s ≈ 8~10 min（四视图命中缓存后约 2~3 min）

2026-10-21 两个事故都栽在这上面：
  1. `--start-index/--end-index` 传了被主流程的「已有产物→跳过」吃掉，参数形同虚设；
  2. 每次跑都重新生成角色四视图，白白多烧 6 分钟。

现在两处都修了：主流程识别返修模式强制重渲；四视图命中本地缓存直接上传复用。
本脚本把「定位镜号 → 复现原始参数 → 单段重渲 → 抽帧复验」串成一条命令。

用法
----
    python scripts/refix.py --project <项目目录> --shot 7
    python scripts/refix.py --project <项目目录> --shots 3,7,9        # 多镜批量
    python scripts/refix.py --project <项目目录> --shots 3,7,9 --dry-run   # 只打印
    python scripts/refix.py --project <项目目录> --shots 3,7,9 --no-frames # 不抽帧

镜号 1 起（跟分镜表、跟人说话一致）；内部自动换算成 0 起的 start/end index。

多镜为什么必须串行
------------------
**服务端每渲完一段，都会拿其它段的缓存拼回完整成片。** 所以：
  · 并行提交会互相覆盖对方刚生成的段 → 成片里总有几镜是旧的；
  · 中途那几次成片都是半成品，**只认最后一段渲完的那一次**；
  · 全部渲完之前，不许烧字幕、不许做封面、不许交付。
本脚本就是那条串行队列：一镜渲完才提交下一镜，全部返回 0 才退出 0。
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, SKILL)

# 单段时长 / 时间码换算 / 返修判定一律走公共件，别在本地再写一份
sys.path.insert(0, os.path.join(os.path.dirname(SKILL), "fxai-common"))
import rework as rw  # noqa: E402

SHOT_SECONDS = rw.SEG_SECONDS


def say(msg=""):
    print(msg, flush=True)


def die(msg):
    say("✗ " + msg)
    sys.exit(1)


# ---------------------------------------------------------------- 读项目
def load_json(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                            # noqa: BLE001
        return default


def find_project(arg):
    """--project 可以给项目目录，也可以给项目名（在 cwd / video 下找）。"""
    cand = [arg, os.path.abspath(arg)]
    if not os.path.isdir(arg):
        for base in (os.getcwd(), os.path.join(os.getcwd(), "video")):
            p = os.path.join(base, arg)
            if os.path.isdir(p):
                cand.append(p)
    for c in cand:
        if os.path.isdir(c) and os.path.isfile(os.path.join(c, "image", "storyboard.json")):
            return os.path.abspath(c)
        if os.path.isdir(c) and os.path.isfile(os.path.join(c, "storyboard.json")):
            return os.path.abspath(c)
    die("找不到项目或分镜表：%s（需要 <项目>/image/storyboard.json）" % arg)


def storyboard_path(proj):
    p = os.path.join(proj, "image", "storyboard.json")
    return p if os.path.isfile(p) else os.path.join(proj, "storyboard.json")


def shots_of(path):
    d = load_json(path, [])
    if isinstance(d, list):
        return d
    for k in ("shots", "storyboard", "list"):
        if isinstance(d.get(k), list):
            return d[k]
    die("分镜表结构不认识：%s" % path)


def audio_path(proj, run):
    """整段音频（返修必须给**整段**：服务端按索引从整段里取第 N 段）。"""
    names = []
    if run.get("audio"):
        names.append(run["audio"])
    names += ["dh-vo.wav", "口播音频.wav", "合并音频.wav"]
    for n in names:
        for base in (proj, os.path.join(proj, "music")):
            p = os.path.join(base, n)
            if os.path.isfile(p):
                return p
    wavs = [f for f in os.listdir(proj) if f.lower().endswith(".wav")]
    die("找不到整段音频（试过 %s；目录里的 wav：%s）"
        % ("/".join(names), ", ".join(wavs[:6]) or "无"))


def read_scenes(proj):
    p = os.path.join(proj, "scenes.txt")
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip()]


# ---------------------------------------------------------------- 该镜体检
def audit_shot(sh, idx):
    """只查「这一镜」的硬伤，用的是 preflight 同一张词表（不另立标准）。
    返回 [(镜号, 问题)] 形式的列表。"""
    try:
        import preflight as pf
    except Exception:                                            # noqa: BLE001
        return []
    blob = "".join(str(sh.get(k) or "") for k in
                   ("motion", "action", "expression", "why", "dynamics", "entry"))
    bad = []
    hit = [w for w in pf.HEAD_ACT + pf.FACE_BEAT if w in blob]
    if hit:
        bad.append("头部动作/面部节拍：%s" % "/".join(hit))
    turn = [w for w in pf.BODY_TURN if w in blob]
    if turn:
        bad.append("身体转向（会侧脸）：%s" % "/".join(turn))
    kind = sh.get("kind") or "gesture"
    size = str(sh.get("size") or "")
    if kind == "move" and not any(z in size for z in pf.MOVE_SIZES):
        bad.append("走位镜景别 %s 看不见腿（应 %s）" % (size or "空", "/".join(pf.MOVE_SIZES)))
    if not str(sh.get("expression") or "").strip():
        bad.append("缺 expression（表情交给模型会面瘫）")
    if not str(sh.get("motion") or "").strip():
        bad.append("缺 motion（这一镜人物躯干不会动）")
    return bad


def merged_video(proj):
    """当前完整成片（服务端每渲完一段都会更新它）。"""
    vdir = os.path.join(proj, "video")
    for cand in ([os.path.join(proj, "合并_768x1280.mp4"),
                  os.path.join(proj, "合并_1280x768.mp4")]
                 + [os.path.join(vdir, f)
                    for f in (os.listdir(vdir) if os.path.isdir(vdir) else [])
                    if f.startswith("合并_") and f.endswith(".mp4")]):
        if os.path.isfile(cand):
            return cand
    return None


def grab_frames(merged, proj, shot_no, offsets=(0.3, 2.5, 5.5, 8.5)):
    """抽这一镜的 4 帧（不用整片重看）。"""
    t0, _ = rw.shot_timecode(shot_no)
    outdir = os.path.join(proj, "_tmp", "refix_shot%d" % shot_no)
    os.makedirs(outdir, exist_ok=True)
    for k, off in enumerate(offsets, 1):
        dst = os.path.join(outdir, "s%d_%02d_%04.1fs.jpg" % (shot_no, k, off))
        subprocess.call(["ffmpeg", "-v", "error", "-ss", "%.3f" % (t0 + off),
                         "-i", merged, "-frames:v", "1", "-y", dst])
    return outdir


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(
        description="单镜/多镜返修：只重渲指定的那几镜，其余段走服务端缓存")
    ap.add_argument("--project", required=True, help="项目目录或项目名")
    ap.add_argument("--shot", type=int, help="单镜镜号，1 起（与分镜表一致）")
    ap.add_argument("--shots", help="多镜：3,7,9 或 3-6；一次提交、串行渲完")
    ap.add_argument("--char-desc", help="覆盖人物穿搭（默认取 _run_args.json）")
    ap.add_argument("--dry-run", action="store_true", help="只打印命令不执行")
    ap.add_argument("--no-frames", action="store_true", help="渲完不抽帧")
    ap.add_argument("--force", action="store_true", help="传给 dh_video：连四视图/场景图也重跑")
    ap.add_argument("--allow-bad", action="store_true",
                    help="分镜仍有硬伤也强渲（默认拦截：没改分镜就重渲等于白烧 GPU）")
    a = ap.parse_args()

    if not a.shot and not a.shots:
        die("要给 --shot 7 或 --shots 3,7,9")

    proj = find_project(a.project)
    sb = storyboard_path(proj)
    shots = shots_of(sb)
    n = len(shots)

    seq = rw.parse_shots(a.shots) if a.shots else []
    if a.shot:
        seq = sorted(set(seq + [int(a.shot)]))
    if not seq:
        die("镜号解析为空：%s" % (a.shots or a.shot))
    over = [x for x in seq if x > n]
    if over:
        die("镜号 %s 超出范围（共 %d 镜）" % ("、".join(map(str, over)), n))

    say("项目：%s" % proj)
    say("")

    # ---------- 1. 先体检：分镜本身还有硬伤，渲了也是错的 ----------
    broken = {}
    for m in seq:
        sh = shots[m - 1]
        t0, t1 = rw.shot_timecode(m)
        say("第 %d/%d 镜  →  %.2f~%.2f s   kind=%s  size=%s"
            % (m, n, t0, t1, sh.get("kind") or "gesture", sh.get("size") or "-"))
        say("  motion=%s" % (str(sh.get("motion") or "")[:64] or "（空）"))
        bad = audit_shot(sh, m - 1)
        if bad:
            broken[m] = bad
            for b in bad:
                say("  ✗ " + b)
        else:
            say("  ✓ 体检通过（无头部动作 / 无转向 / 景别匹配）")
    if broken:
        say("")
        say("⚠️ 有 %d 镜分镜仍带硬伤（第 %s 镜）：先改分镜再渲，否则重渲出来还是错的"
            % (len(broken), "、".join(map(str, sorted(broken)))))
        if not a.allow_bad:
            say("   改完重跑本命令即可；确认要硬渲加 --allow-bad")
            return 2
        say("   （--allow-bad：照渲）")

    # ---------- 2. 串行返修计划 ----------
    say("")
    seq = rw.plan_batch(seq)

    run = load_json(os.path.join(proj, "doc", "_run_args.json"), {}) or {}
    char_desc = a.char_desc or run.get("char_desc")
    if not char_desc:
        die("拿不到人物穿搭：doc/_run_args.json 缺失，请补 --char-desc（原样照抄上次的，别改字）")
    audio = audio_path(proj, run)
    scenes = read_scenes(proj)
    py = sys.executable

    def build(m):
        i0 = m - 1
        cmd = [py, os.path.join(SKILL, "dh_video.py"),
               "--project", os.path.basename(proj),
               "--audio", audio,
               "--mode", run.get("mode") or "multiref",
               "--role", run.get("role") or "woman",
               "--char-desc", char_desc,
               "--storyboard", sb,
               "--start-index", str(i0), "--end-index", str(i0)]
        if scenes:
            cmd += ["--scene-desc", "|".join(scenes), "--scene-count", str(len(scenes))]
        if run.get("multiref_landscape"):
            cmd.append("--multiref-landscape")
        if a.force:
            cmd.append("--force")
        return cmd

    if a.dry_run:
        say("")
        for k, m in enumerate(seq, 1):
            say("── %d/%d  第 %d 镜" % (k, len(seq), m))
            say("   " + " ".join('"%s"' % c if " " in c else c for c in build(m)))
        say("")
        say("（--dry-run 到此为止）")
        return 0

    # ---------- 3. 串行执行：一镜渲完才提交下一镜 ----------
    merged = merged_video(proj)
    start_ts = time.time()
    done = []
    for k, m in enumerate(seq, 1):
        t0, t1 = rw.shot_timecode(m)
        say("")
        say("═══ [rework] 第 %d/%d 段：第 %d 镜（%.2f~%.2f s）═══" % (k, len(seq), m, t0, t1))
        rc = subprocess.call(build(m), cwd=os.path.dirname(proj) or ".")
        if rc != 0:
            say("")
            say("✗ 第 %d 镜渲染失败（返回 %d）" % (m, rc))
            if done:
                say("  已完成：第 %s 镜（成片已合并进去）" % "、".join(map(str, done)))
            say("  未完成：第 %s 镜"
                % "、".join(map(str, [x for x in seq if x not in done])))
            say("  修好这个错后重跑本命令；已渲好的段服务端有缓存，不会白渲")
            return 1
        done.append(m)
        say("[rework] ✓ 第 %d 镜渲完（%d/%d），成片已由服务端合并" % (m, k, len(seq)))

    # ---------- 4. 确认合并真的发生了（看到才算数）----------
    say("")
    merged = merged_video(proj)
    if merged and os.path.getmtime(merged) >= start_ts - 5:
        say("✅ 全部 %d 镜已渲完，最终成片：%s（%.1f MB）"
            % (len(seq), merged, os.path.getsize(merged) / 1048576.0))
    else:
        say("⚠️ 渲完了但成片时间没更新（%s）——服务端可能没合并，检查日志再继续" % merged)

    # ---------- 5. 抽帧复验（只抽返修过的那几镜）----------
    if not a.no_frames and merged:
        say("")
        for m in seq:
            outdir = grab_frames(merged, proj, m)
            say("复验抽帧 → %s" % outdir)
        say("  共 %d 镜 × 4 帧（0.3 / 2.5 / 5.5 / 8.5 s 偏移）" % len(seq))

    # ---------- 6. 下一步：质检还没过，别急着往下走 ----------
    say("")
    say("下一步（顺序固定，别跳）：")
    say("  1) 重跑分镜预检，确认全绿：")
    say('     python "%s" --project "%s"' % (os.path.join(HERE, "preflight.py"), proj))
    say("  2) 逐张看上面抽的帧，确认问题真的消失了")
    say("  3) 都过了再做字幕 / 封面 / 交付自检")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
