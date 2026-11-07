# -*- coding: utf-8 -*-
"""正文配图统一导出 —— 宽度与 JPEG 参数写死，别再每个项目重写一遍。

固定规格：
    宽度     1152 px（等比缩放；原图更窄则保留原宽，不放大糊图）
    导出     JPEG quality=95 + subsample=0（4:4:4）+ optimize=True
    体积     单张 >1.5MB 会告警（微信正文图上传上限 1MB，超了先压）
    PNG 母版保留，只新增同名 .jpg

用法：
    python scripts/export_figures.py --dir <项目>/image
    python scripts/export_figures.py --dir image --width 1080 --name 封面
"""
import argparse
import os
import sys

try:
    from PIL import Image
except ImportError:
    sys.exit("缺 Pillow：pip install pillow")

FIG_W = 1152            # 写死：正文配图统一宽度
JPG_QUALITY = 95
JPG_SUBSAMPLE = 0       # 4:4:4
WARN_BYTES = 1_500_000


def export_one(src, dst, width):
    im = Image.open(src)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGB")
    if im.width > width:
        h = round(im.height * width / im.width)
        im = im.resize((width, h), Image.LANCZOS)
    im.save(dst, "JPEG", quality=JPG_QUALITY, subsample=JPG_SUBSAMPLE, optimize=True)
    return im.size, os.path.getsize(dst)


def main():
    ap = argparse.ArgumentParser(description="正文配图统一导出（1152 宽 JPEG q95 4:4:4）")
    ap.add_argument("--dir", required=True, help="图片目录，一般是 <项目>/image")
    ap.add_argument("--width", type=int, default=FIG_W, help=f"目标宽度，默认 {FIG_W}")
    ap.add_argument("--name", default="", help="只处理这一个（不含扩展名）")
    ap.add_argument("--keep-png", action="store_true", default=True, help="保留 PNG 母版（默认就是保留）")
    args = ap.parse_args()

    if not os.path.isdir(args.dir):
        sys.exit(f"目录不存在：{args.dir}")

    targets = []
    for fn in sorted(os.listdir(args.dir)):
        stem, ext = os.path.splitext(fn)
        if ext.lower() != ".png" or fn.startswith("_"):
            continue
        if args.name and stem != args.name:
            continue
        targets.append(fn)
    if not targets:
        sys.exit("没有可导出的 PNG")

    total = 0
    for fn in targets:
        src = os.path.join(args.dir, fn)
        dst = os.path.join(args.dir, os.path.splitext(fn)[0] + ".jpg")
        size, nbytes = export_one(src, dst, args.width)
        total += nbytes
        flag = "  ⚠ 超 1.5MB，发布前先压" if nbytes > WARN_BYTES else ""
        print(f"  {os.path.basename(dst):<20} {size[0]}x{size[1]}  {nbytes / 1024:>6.0f}KB{flag}")

    print(f"\n{len(targets)} 张导出完成，合计 {total / 1024 / 1024:.2f}MB  →  {args.dir}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
