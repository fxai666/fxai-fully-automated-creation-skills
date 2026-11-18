# -*- coding: utf-8 -*-
"""多镜头逐镜替换 · 闭环编排：探测切点 → 每镜替换 → 按帧质检 → 失败重跑。

漏换防护：取消人工预设「空镜回填集合」，默认每镜都跑替换；只有 --skip 显式
声明的无目标人物镜回填。质检按绝对帧号比对成片与源片，成片≈源片的镜判漏换，
换 seed 重跑后再检，直到全过或到 --max-retry。

只编排，不重实现：切点用 shot_split.py，替换用 char_swap.py，拼装用
shot_assemble.py，质检用 shot_qc.py。用能跑 char_swap.py 的解释器启动本脚本。

用法：
    python shot_pipeline.py SRC --ref REF --prompt PROMPT --work-dir DIR \
        --out OUT.mp4 [--skip 4] [--seed 42] [--max-retry 2] \
        [--threshold 0.25] [--min-frames 24] [--min-diff 3.0] [--max-side 960]
    python shot_pipeline.py ... --dry     # 只打印计划，不执行
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def tool(name):
    return os.path.join(HERE, name)


def run(cmd, label, log=None):
    print(">>> %s" % label, flush=True)
    r = subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    text = r.stdout or ""
    if log:
        with open(log, "w", encoding="utf-8") as f:
            f.write(text)
    print(text[-1500:], flush=True)
    return r.returncode, text


def build_plan(a):
    """拼出本轮要执行的命令计划（含探测/切段，若清单不存在）。"""
    plan = []
    manifest = os.path.join(a.work_dir, "manifest.json")
    if not os.path.isfile(manifest):
        plan.append(("探点切段", [sys.executable, tool("shot_split.py"), a.src,
                    "--out-dir", a.work_dir, "--threshold", str(a.threshold),
                    "--min-frames", str(a.min_frames)]))
    return plan


def swap_cmd(a, idx, seed, attempt):
    """单镜替换命令；重跑用新 subdir 隔离旧缓存。"""
    suffix = "" if attempt == 0 else "_r%d" % (attempt + 1)
    return [sys.executable, tool("char_swap.py"), a.seg_files[idx],
            "--ref", a.ref, "--prompt", a.prompt, "--seed", str(seed),
            "--max-side", str(a.max_side), "--duration", "4",
            "--start-index", "0", "--end-index", "-1",
            "--subdir", "%s%02d%s" % (a.subdir, idx, suffix),
            "--out-dir", a.seg_dir, "--name", "镜%02d.mp4" % idx,
            "--timeout", "3600"]


def qc_cmd(a, skip):
    skip_arg = ",".join(str(x) for x in sorted(skip))
    cmd = [sys.executable, tool("shot_qc.py"), "--manifest",
           os.path.join(a.work_dir, "manifest.json"),
           "--out", a.out, "--src", a.src, "--min-diff", str(a.min_diff),
           "--fail-out", os.path.join(a.work_dir, "qc_fail.json")]
    if skip_arg:
        cmd += ["--skip", skip_arg]
    return cmd


def assemble_cmd(a, swap):
    return [sys.executable, tool("shot_assemble.py"), a.src,
            "--manifest", os.path.join(a.work_dir, "manifest.json"),
            "--seg-dir", a.seg_dir,
            "--swap", ",".join(str(x) for x in sorted(swap)),
            "--out", a.out]


def main():
    ap = argparse.ArgumentParser(description="逐镜替换闭环编排")
    ap.add_argument("src", help="源视频路径")
    ap.add_argument("--ref", required=True, help="参考图，多张逗号分隔")
    ap.add_argument("--prompt", required=True, help="提示词文件")
    ap.add_argument("--work-dir", required=True, help="工作目录（清单/切段/质检记录）")
    ap.add_argument("--out", required=True, help="成片输出路径")
    ap.add_argument("--skip", default="", help="逗号分隔的无目标人物镜 idx，回填不替换")
    ap.add_argument("--seed", type=int, default=42, help="首轮 seed")
    ap.add_argument("--max-retry", type=int, default=2, help="失败镜最多重跑轮数")
    ap.add_argument("--threshold", type=float, default=0.25, help="测切点阈值")
    ap.add_argument("--min-frames", type=int, default=24, help="短段合并帧数")
    ap.add_argument("--min-diff", type=float, default=3.0, help="质检未替换差异阈值")
    ap.add_argument("--max-side", type=int, default=960, help="换人长边档位")
    ap.add_argument("--subdir", default="h3_shot", help="ComfyUI 任务子目录前缀")
    ap.add_argument("--dry", action="store_true", help="只打印计划，不执行")
    a = ap.parse_args()

    os.makedirs(a.work_dir, exist_ok=True)
    a.seg_dir = os.path.join(a.work_dir, "seg")
    os.makedirs(a.seg_dir, exist_ok=True)
    skip = set(int(x) for x in a.skip.split(",") if x.strip())

    # 探点切段（清单不存在时）
    for label, cmd in build_plan(a):
        print("[计划] %s: %s" % (label, " ".join(cmd)))
        if not a.dry and run(cmd, label)[0] != 0:
            raise SystemExit("探点切段失败")

    manifest_path = os.path.join(a.work_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        if a.dry:
            print("[DRY] 清单尚未生成，后续按已存在清单规划")
            return
        raise SystemExit("清单缺失")
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    a.seg_files = {m["idx"]: os.path.join(a.work_dir, "clips",
                                          "clip_%02d.mp4" % m["idx"])
                   for m in manifest}
    all_idx = [m["idx"] for m in manifest]
    swap = set(all_idx) - skip

    # 首轮：每镜都替换（skip 除外）
    for idx in sorted(swap):
        cmd = swap_cmd(a, idx, a.seed, 0)
        print("[计划] 替换镜%02d: %s" % (idx, " ".join(cmd)))
        if not a.dry and run(cmd, "替换镜%02d" % idx,
                             os.path.join(a.work_dir, "log_shot%02d.txt" % idx))[0] != 0:
            raise SystemExit("镜%02d 替换失败" % idx)

    # 拼装 + 质检 + 失败重跑闭环
    for attempt in range(a.max_retry + 1):
        print("=== 第 %d 轮质检 ===" % (attempt + 1))
        if a.dry:
            print("[计划] 拼装:", " ".join(assemble_cmd(a, swap)))
            print("[计划] 质检:", " ".join(qc_cmd(a, skip)))
            break
        if run(assemble_cmd(a, swap), "拼装")[0] != 0:
            raise SystemExit("拼装失败")
        code, _ = run(qc_cmd(a, skip), "质检")
        if code == 0:
            print("质检全过，成片:", a.out)
            return
        fails = json.load(open(os.path.join(a.work_dir, "qc_fail.json"),
                               encoding="utf-8"))
        if attempt == a.max_retry:
            print("达到最大重跑轮数，仍有失败镜:", [f["idx"] for f in fails])
            raise SystemExit(2)
        for f in fails:
            idx = f["idx"]
            seed = a.seed + attempt + 1
            cmd = swap_cmd(a, idx, seed, attempt + 1)
            print("[重跑] 镜%02d seed=%d" % (idx, seed))
            if run(cmd, "重跑镜%02d" % idx,
                   os.path.join(a.work_dir, "log_shot%02d_r%d.txt" % (idx, attempt + 1)))[0] != 0:
                raise SystemExit("镜%02d 重跑失败" % idx)


if __name__ == "__main__":
    main()
