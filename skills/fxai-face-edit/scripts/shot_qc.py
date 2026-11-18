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
"""
import argparse
import io
import json
import os
import subprocess

from PIL import Image, ImageChops, ImageStat


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

    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        with open(os.path.join(a.out_dir, "qc_report.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
    if a.fail_out:
        with open(a.fail_out, "w", encoding="utf-8") as f:
            json.dump(fails, f, ensure_ascii=False, indent=2)
        print("失败清单:", a.fail_out)
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
