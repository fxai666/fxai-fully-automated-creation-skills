# -*- coding: utf-8 -*-
"""fxai-image-to-jpeg: 把 PNG/源图转成最高质量 JPEG。

典型用途：公众号/文章配图。默认 quality=95 + subsample=0(4:4:4 无 chroma
子采样) + optimize=True，体积压到 <2MB。PNG 母版默认保留不删。
纯 PIL 实现，跨项目复用，不依赖任何项目本地路径。
"""
import argparse
import glob
import os
import sys
from pathlib import Path

from PIL import Image

DEFAULT_QUALITY = 95
DEFAULT_SUBSAMPLE = 0  # 0 = 4:4:4 无 chroma 子采样，最高质量
DEFAULT_OPTIMIZE = True
DEFAULT_MAX_MB = 2.0
SUPPORTED = {".png", ".webp", ".bmp", ".tif", ".tiff"}


def convert_one(src, out_dir, quality, subsample, optimize, max_mb):
    im = Image.open(src)
    im.load()  # 触发完整解码，校验文件完整性（含 IEND / 文件尾）
    rgb = im.convert("RGB") if im.mode != "RGB" else im
    out_dir = out_dir or str(Path(src).parent)
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, Path(src).stem + ".jpg")
    rgb.save(dst, "JPEG", quality=quality, subsample=subsample, optimize=optimize)
    sz = os.path.getsize(dst)
    flag = "" if sz <= max_mb * 1024 * 1024 else " !! >%.1fMB 注意公众号封面限制(2MB)" % max_mb
    print("OK %s -> %s (%dKB)%s" % (Path(src).name, Path(dst).name, sz // 1024, flag), flush=True)
    return dst


def collect(src):
    if any(ch in src for ch in "*?["):
        files = sorted(glob.glob(src, recursive=False))
    else:
        files = [src]
    if len(files) == 1 and os.path.isdir(files[0]):
        d = files[0]
        files = [os.path.join(d, f) for f in os.listdir(d)]
    return [f for f in files if Path(f).suffix.lower() in SUPPORTED]


def main():
    ap = argparse.ArgumentParser(description="PNG/源图 -> 最高质量 JPEG(默认 quality=95, subsample=0)")
    ap.add_argument("--src", required=True, help="源文件 / glob / 目录，如 <你的项目>/测试图/img*_song.png 或 <你的项目>/测试图")
    ap.add_argument("--out", default="", help="输出目录，默认与源同目录")
    ap.add_argument("--quality", type=int, default=DEFAULT_QUALITY)
    ap.add_argument("--subsample", type=int, default=DEFAULT_SUBSAMPLE, help="0=4:4:4 最高质量；2=4:2:0 更小")
    ap.add_argument("--no-optimize", action="store_true", help="关闭 optimize（默认开）")
    ap.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB, help="体积告警阈值 MB")
    args = ap.parse_args()

    files = collect(args.src)
    if not files:
        print("没有匹配的源文件: %s" % args.src, file=sys.stderr)
        sys.exit(1)

    n = 0
    for f in files:
        try:
            convert_one(f, args.out or None, args.quality, args.subsample,
                        not args.no_optimize, args.max_mb)
            n += 1
        except Exception as e:
            print("FAIL %s: %s" % (f, e), file=sys.stderr)
    print("DONE: %d 张转成 JPEG（PNG 母版保留）" % n, flush=True)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
