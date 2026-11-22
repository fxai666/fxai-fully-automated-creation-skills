# -*- coding: utf-8 -*-
"""换人链路门禁：按源片镜头数锁定生成链路。

判定：
    有效镜头数 >= 2 -> multi_shot，走逐镜切段链路（references/06 第十一节）
    有效镜头数 == 1 -> single_shot，走默认均分链路（char_swap.py 常规用法）

用法：
    python shot_gate.py SRC [--out-dir DIR]
    python shot_gate.py SRC --assert-route single_shot   # 判定不符时退出码 2
    python shot_gate.py SRC --reuse-seg 旧片段.mp4 --reuse-range 660,915
                                                         # 提交前核复用段时间轴
    python shot_gate.py --self-check                     # 规则与程序一致性自检

切点判据复用 shot_split.py 的 detect_cuts / merge_short，两处共用一份实现。
"""
import argparse
import json
import os
import subprocess
import sys

from shot_split import detect_cuts, merge_short, probe_streams

DEFAULT_THRESHOLD = 0.25
DEFAULT_MIN_FRAMES = 24

NEXT_STEPS = {
    "multi_shot": [
        "python shot_split.py SRC --out-dir DIR",
        "逐镜提交 char_swap.py：每镜独立 --subdir，--start-index 0 --end-index -1",
        "python shot_assemble.py ... 按帧号嵌回，音轨整条取源片",
        "python shot_verify.py ... 逐帧验构图、人数、运动量",
    ],
    "single_shot": [
        "python char_swap.py SRC --start-index 0 --end-index -1 --out-dir DIR",
        "常规三道后处理与帧数验收",
    ],
}


def route(src, threshold=DEFAULT_THRESHOLD, min_frames=DEFAULT_MIN_FRAMES,
          fps_override=0, end_frame=-1):
    """返回链路判定记录；切点口径与 shot_split.py 同源。"""
    fps, total, _ = probe_streams(src)
    fps = fps_override or fps
    end = end_frame if end_frame > 0 else total
    if end <= 0:
        raise ValueError("源片帧数不可读，请显式传 --end-frame")
    cuts = detect_cuts(src, threshold)
    bounds = [0] + [round(s * fps) for s in cuts] + [end]
    bounds = sorted(set(b for b in bounds if 0 <= b <= end))
    bounds[0], bounds[-1] = 0, end
    segs = merge_short([[bounds[i], bounds[i + 1]] for i in range(len(bounds) - 1)],
                       min_frames)
    decision = "multi_shot" if len(segs) >= 2 else "single_shot"
    return {
        "route": decision,
        "shot_count": len(segs),
        "threshold": threshold,
        "min_frames": min_frames,
        "fps": fps,
        "total_frames": end,
        "cuts_sec": cuts,
        "bounds": [[s, e] for s, e in segs],
        "next_steps": NEXT_STEPS[decision],
    }


def self_check(skill_root):
    """核对文档口径与程序常量、脚本落点是否一致。"""
    checks = []
    scripts = os.path.join(skill_root, "scripts")
    for name in ("shot_split.py", "shot_assemble.py", "shot_verify.py",
                 "char_swap.py", "shot_gate.py", "shot_qc.py",
                 "shot_seam_check.py"):
        checks.append(("脚本落点 " + name, os.path.isfile(os.path.join(scripts, name))))

    ref = os.path.join(skill_root, "references", "06-角色替换ComfyUI链.md")
    skill = os.path.join(skill_root, "SKILL.md")
    ref_text = ""
    if os.path.isfile(ref):
        with open(ref, encoding="utf-8") as f:
            ref_text = f.read()
    skill_text = ""
    if os.path.isfile(skill):
        with open(skill, encoding="utf-8") as f:
            skill_text = f.read()

    checks.append(("06 册登记门禁入口", "shot_gate.py" in ref_text))
    checks.append(("SKILL.md 登记门禁铁律", "shot_gate.py" in skill_text))
    checks.append(("切点阈值口径 0.25", "0.25" in ref_text and DEFAULT_THRESHOLD == 0.25))
    checks.append(("短段合并口径 24 帧", "24 帧" in ref_text and DEFAULT_MIN_FRAMES == 24))
    split_path = os.path.join(scripts, "shot_split.py")
    split_src = ""
    if os.path.isfile(split_path):
        with open(split_path, encoding="utf-8") as f:
            split_src = f.read()
    checks.append(("切段与门禁判据同源", "def detect_cuts" in split_src
                   and "def merge_short" in split_src))

    checks.append(("06 册登记全片扫描", "--scan-step" in ref_text))
    checks.append(("06 册登记接缝门禁", "shot_seam_check.py" in ref_text))
    checks.append(("SKILL.md 登记双质检门禁",
                   "shot_seam_check.py" in skill_text and "--scan-step" in skill_text))
    seam_path = os.path.join(scripts, "shot_seam_check.py")
    seam_src = ""
    if os.path.isfile(seam_path):
        with open(seam_path, encoding="utf-8") as f:
            seam_src = f.read()
    checks.append(("接缝阈值口径 15 与程序一致",
                   "阈值 15" in ref_text and "CUT_TH = 15.0" in seam_src))
    return checks


def main():
    ap = argparse.ArgumentParser(description="换人链路门禁：按镜头数锁定生成链路")
    ap.add_argument("src", nargs="?", help="源视频路径")
    ap.add_argument("--out-dir", default="", help="判定记录写出目录；留空只打印")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help="自动测切点的场景阈值")
    ap.add_argument("--min-frames", type=int, default=DEFAULT_MIN_FRAMES,
                    help="短于此帧数的段并入相邻")
    ap.add_argument("--fps", type=float, default=0, help="覆盖探测到的帧率")
    ap.add_argument("--end-frame", type=int, default=-1, help="结束帧号")
    ap.add_argument("--assert-route", choices=("multi_shot", "single_shot"),
                    default="", help="声明拟走链路；与判定不符时退出码 2")
    ap.add_argument("--self-check", action="store_true",
                    help="规则与程序一致性自检")
    ap.add_argument("--reuse-seg", default="",
                    help="拟复用的既有成片段；给出即核其时间轴")
    ap.add_argument("--reuse-range", default="",
                    help="既有片段对应的源片帧区间 a,b（配 --reuse-seg）")
    ap.add_argument("--reuse-offset", type=int, default=0,
                    help="既有片段自身起点对应的源片帧号")
    a = ap.parse_args()

    if a.self_check:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        checks = self_check(root)
        for label, ok in checks:
            print("[%s] %s" % ("PASS" if ok else "FAIL", label))
        bad = [label for label, ok in checks if not ok]
        print("自检 %d 项：通过 %d，失败 %d" % (len(checks), len(checks) - len(bad), len(bad)))
        raise SystemExit(1 if bad else 0)

    if not a.src:
        ap.error("缺少源视频路径；或用 --self-check")

    result = route(a.src, a.threshold, a.min_frames, a.fps, a.end_frame)

    if a.assert_route and a.assert_route != result["route"]:
        print("门禁拦截：判定 %s（%d 镜），拟走 %s；链路已锁定，不得跨链路提交。"
              % (result["route"], result["shot_count"], a.assert_route))
        raise SystemExit(2)

    # 复用既有成片段的提交前核对：时间轴错位即拦下，按源片切点为锚重定位
    if a.reuse_seg:
        if not a.reuse_range:
            ap.error("--reuse-seg 需要同时给 --reuse-range a,b")
        seam = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "shot_seam_check.py")
        cmd = [sys.executable, seam, "--mode", "align", "--seg", a.reuse_seg,
               "--src", a.src, "--range", a.reuse_range,
               "--seg-offset", str(a.reuse_offset)]
        if subprocess.run(cmd).returncode != 0:
            print("门禁拦截：拟复用的既有片段与原片时间轴不一致，"
                  "须以源片切点为锚重定位或重跑该段，再提交。")
            raise SystemExit(2)

    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        path = os.path.join(a.out_dir, "gate.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print("判定记录: %s" % path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
