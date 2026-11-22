# -*- coding: utf-8 -*-
"""逐镜质检：按帧号比对成片与源片，判定每镜「该换却没换」的失败镜。

判据：某镜代表帧的成片与源片画面差异低于阈值，视为该镜未替换（回填 / 没换上），
列入失败清单供重跑。空镜 / 无目标人物镜由 --skip 显式声明，跳过质检。

只依赖 ffmpeg + Pillow，不依赖 cv2 / numpy。

用法：
    python shot_qc.py --manifest MANIFEST.json --out OUT.mp4 --src SRC.mp4 \
        --skip 4 --out-dir DIR
    python shot_qc.py ... --fail-out FAIL.json   # 输出失败镜清单给编排器重跑

manifest 每条含 idx / start_frame / end_frame / frames。代表帧取镜头中点，
避开段首条件帧。对每镜在成片与源片同一绝对帧号抽帧比较。

镜内混场景：一个 manifest 镜内部若有真实 scene 切点（该镜跨两个场景），
取中点帧会误判。用 --split「镜号:切点1,切点2;镜号2:切点」声明内部切点，
这些镜按切点拆成子段、逐子段取中点质检，取各子段最大差异为镜结论。
空镜子段差异低属正常，由同镜其他有目标子段的高差异覆盖，镜结论取最高差异。

最后一道把关 --scan-step：只取中点会漏「镜内某一段没换」（如整镜换了、中段回填）。
给 --scan-step N 即对全片按 N 帧步进逐帧比对，连续低差异帧数 >= --scan-min-run
的区间判 FAIL 并列出帧号。交付前必跑，默认 4，传 0 关闭。
"""
import argparse
import io
import json
import os
import subprocess

from PIL import Image, ImageChops, ImageStat


def read_gray(path, start=None, end=None, w=160, h=90):
    """按帧区间抽灰度小图序列（扫描用，一次解码比逐帧起进程快两个量级）。"""
    vf = "scale=%d:%d:flags=area,format=gray" % (w, h)
    if start is not None:
        vf = "select='between(n\\,%d\\,%d)',%s" % (start, end, vf)
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vf", vf, "-vsync", "0",
         "-f", "rawvideo", "-"], capture_output=True)
    if r.returncode != 0:
        raise SystemExit("扫描抽帧失败 %s" % os.path.basename(path))
    size = w * h
    n = len(r.stdout) // size
    return [Image.frombytes("L", (w, h), r.stdout[i * size:(i + 1) * size])
            for i in range(n)]


def img_mad(a, b):
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


def scan_low_runs(out_path, src_path, step, min_diff, min_run, skip_ranges):
    """全片步进扫描，返回 (低差异区间列表, 比对帧数)。

    区间元素 (起帧, 止帧, 帧数)。与 --skip 镜区间重叠 >=70% 的区间视为空镜正常，丢弃。
    """
    of = read_gray(out_path)
    sf = read_gray(src_path)
    n = min(len(of), len(sf))
    bad = [i for i in range(0, n, step) if img_mad(of[i], sf[i]) < min_diff]
    runs = []
    for i in bad:
        if runs and i - runs[-1][1] <= step:
            runs[-1][1] = i
        else:
            runs.append([i, i])

    def in_skip(a, b):
        tot = b - a + 1
        ov = 0
        for s, e in skip_ranges:
            lo, hi = max(a, s), min(b, e - 1)
            if hi >= lo:
                ov += hi - lo + 1
        return tot and ov / tot >= 0.7

    kept = []
    for a, b in runs:
        length = b - a + step
        if length < min_run or in_skip(a, b):
            continue
        kept.append((a, b, length))
    return kept, n


def grab(path, n, size):
    """按帧号抽一帧并缩放，返回 RGB 的 PIL Image。"""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path,
         "-vf", "select='eq(n\\,%d)'" % n, "-frames:v", "1",
         "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    return Image.open(io.BytesIO(r.stdout)).convert("RGB").resize(size)


def frame_diff(a, b):
    """两图灰度平均绝对差；0 = 完全相同。"""
    d = ImageChops.difference(a.convert("L"), b.convert("L"))
    return ImageStat.Stat(d).mean[0]


def diff_at(out_path, src_path, n, size):
    """指定绝对帧号的成片/源片差异。"""
    b = grab(src_path, n, size)
    o = grab(out_path, n, size)
    if b is None or o is None:
        return None
    return frame_diff(o, b)


def check_one(out_path, src_path, start, end, size, subcuts=()):
    """一镜质检：有内部切点时逐子段取中点，取最大差异；否则取整镜中点。

    返回 (代表帧号列表, 差异值)。子段中差异最高者决定镜结论。
    """
    bounds = [start] + list(subcuts) + [end]
    segs = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)
            if bounds[i + 1] > bounds[i]]
    best_n, best_d = None, None
    for a, b in segs:
        n = (a + b) // 2
        d = diff_at(out_path, src_path, n, size)
        if d is not None and (best_d is None or d > best_d):
            best_n, best_d = n, d
    return (best_n, best_d) if len(segs) == 1 else (
        [ (bounds[i] + bounds[i + 1]) // 2 for i in range(len(segs)) ], best_d)


def main():
    ap = argparse.ArgumentParser(description="逐镜质检：该换却没换的镜判失败")
    ap.add_argument("--manifest", required=True, help="镜头清单 JSON")
    ap.add_argument("--out", required=True, help="待检成片")
    ap.add_argument("--src", required=True, help="源片（比对基准）")
    ap.add_argument("--skip", default="", help="逗号分隔的空镜/无目标镜 idx，跳过质检")
    ap.add_argument("--split", default="",
                    help="镜内真实切点，格式「镜号:切点1,切点2;镜号2:切点」，如 10:648")
    ap.add_argument("--min-diff", type=float, default=3.0,
                    help="成片与源片差异低于此值判未替换")
    ap.add_argument("--size", default="320x180", help="抽帧缩放尺寸")
    ap.add_argument("--scan-step", type=int, default=4,
                    help="全片步进扫描间隔帧数，0 关闭；交付前必跑")
    ap.add_argument("--scan-min-run", type=int, default=8,
                    help="连续低差异达到此帧数判该段未替换")
    ap.add_argument("--out-dir", default="", help="质检记录目录")
    ap.add_argument("--fail-out", default="", help="失败镜清单 JSON 写出路径")
    a = ap.parse_args()

    skip = set(int(x) for x in a.skip.split(",") if x.strip())
    split = {}
    for item in a.split.split(";"):
        if not item.strip() or ":" not in item:
            continue
        k, cuts = item.split(":", 1)
        split[int(k)] = [int(c) for c in cuts.split(",") if c.strip()]
    w, h = (int(x) for x in a.size.lower().split("x"))
    manifest = json.load(open(a.manifest, encoding="utf-8"))

    rows, fails = [], []
    for m in manifest:
        k, s, e = m["idx"], m["start_frame"], m["end_frame"]
        if k in skip:
            rows.append({"idx": k, "status": "SKIP", "frame": None, "diff": None})
            continue
        n, diff = check_one(a.out, a.src, s, e, (w, h), split.get(k, ()))
        ok = diff is not None and diff >= a.min_diff
        frame_out = n if isinstance(n, int) else n
        rows.append({"idx": k, "status": "PASS" if ok else "FAIL",
                     "frame": frame_out, "diff": round(diff, 2) if diff is not None else None})
        if not ok:
            fails.append({"idx": k, "start_frame": s, "end_frame": e,
                          "frames": m["frames"], "diff": diff})
        n_show = ",".join(str(x) for x in n) if isinstance(n, list) else str(n)
        print("镜%02d n=%s 差异=%s -> %s" % (
            k, n_show, "%.2f" % diff if diff is not None else "NA",
            "PASS" if ok else "FAIL"))

    print("质检: %d 镜，通过 %d，失败 %d，跳过 %d" % (
        len(rows),
        sum(1 for r in rows if r["status"] == "PASS"), len(fails), len(skip)))

    # 最后一道把关：全片步进扫描，抓「中点帧通过、镜内某段没换」的漏换
    scan_rows = []
    if a.scan_step > 0:
        skip_ranges = [(m["start_frame"], m["end_frame"])
                       for m in manifest if m["idx"] in skip]
        runs, total = scan_low_runs(a.out, a.src, a.scan_step, a.min_diff,
                                    a.scan_min_run, skip_ranges)
        print("全片扫描: %d 帧（间隔 %d 帧），低差异段 %d 处" % (
            total, a.scan_step, len(runs)))
        for s, e, length in runs:
            print("  帧 %d~%d（%.2f~%.2fs，%d 帧）未替换"
                  % (s, e, s / 30.0, e / 30.0, length))
            scan_rows.append({"start": s, "end": e, "frames": length})
            fails.append({"idx": None, "start_frame": s, "end_frame": e,
                          "frames": length, "diff": 0.0, "scan": True})
        if not runs:
            print("  无低差异段，全片已替换")

    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        with open(os.path.join(a.out_dir, "qc_report.json"), "w", encoding="utf-8") as f:
            json.dump({"shots": rows, "scan": scan_rows}, f,
                      ensure_ascii=False, indent=2)
    if a.fail_out:
        with open(a.fail_out, "w", encoding="utf-8") as f:
            json.dump(fails, f, ensure_ascii=False, indent=2)
        print("失败清单:", a.fail_out)
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
