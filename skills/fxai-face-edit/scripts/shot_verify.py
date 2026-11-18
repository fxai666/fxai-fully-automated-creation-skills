# -*- coding: utf-8 -*-
"""多镜头替换验收 · 按帧号抽多方并排对照 + 逐帧运动量对比。

只依赖 ffmpeg + Pillow，不依赖 cv2 / numpy，普通 python 环境即可运行。

用法：
    python shot_verify.py --vid 原片=ORI.mp4 --vid 新版=NEW.mp4 \
        --frames 0,60,120 --out-dir DIR [--motion]

--vid 可重复，key 为行标签、value 为视频路径；按 --frames 的帧号抽帧，
每行一个视频、每列一个帧号，拼成对照图写到 out-dir。
--motion 打开时另算 0.5s 窗口的相邻帧灰度平均绝对差，按视频逐行打印，
用成片与源片是否同量级判画面是否凝固。判定点取镜头切点后的代表帧，不取段首条件帧。
"""
import argparse
import os
import subprocess

from PIL import Image, ImageChops, ImageDraw, ImageStat


def grab(path, n, size):
    """用 ffmpeg 按帧号抽一帧并缩放，返回 RGB 的 PIL Image。"""
    flt = "select='eq(n\\,%d)'" % n
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vf", flt,
         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    import io
    im = Image.open(io.BytesIO(r.stdout)).convert("RGB")
    return im.resize(size)


def motion(path, window, size, upto):
    """相邻窗口帧的灰度平均绝对差序列，返回 [(帧号, 差值)]。"""
    out = []
    for n in range(0, upto, window):
        a, b = grab(path, n, size), grab(path, n + window, size)
        if a is None or b is None:
            out.append((n, None))
            continue
        d = ImageChops.difference(a.convert("L"), b.convert("L"))
        out.append((n, ImageStat.Stat(d).mean[0]))
    return out


def main():
    ap = argparse.ArgumentParser(description="多镜头替换逐帧验收")
    ap.add_argument("--vid", action="append", required=True, metavar="标签=路径",
                    help="参与对照的视频，可重复")
    ap.add_argument("--frames", required=True, help="逗号分隔的帧号")
    ap.add_argument("--out-dir", required=True, help="对照图输出目录")
    ap.add_argument("--motion", action="store_true", help="另算逐帧运动量")
    ap.add_argument("--window", type=int, default=15, help="运动量窗口帧数")
    ap.add_argument("--size", default="320x180", help="抽帧缩放尺寸")
    a = ap.parse_args()

    vids = []
    for item in a.vid:
        if "=" not in item:
            raise SystemExit("--vid 格式: 标签=路径，收到: %s" % item)
        label, path = item.split("=", 1)
        vids.append((label, path))
    frames = [int(x) for x in a.frames.split(",") if x.strip()]
    w, h = (int(x) for x in a.size.lower().split("x"))
    size = (w, h)
    os.makedirs(a.out_dir, exist_ok=True)

    cw, ch, lh = w, h, 18
    cols = len(frames)
    rows = len(vids)
    canvas = Image.new("RGB", (cw * cols, (ch + lh) * rows + 24), "white")
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 5), "按帧号对齐（列=帧号，行=视频）", fill=(200, 0, 0))
    for j, n in enumerate(frames):
        draw.text((j * cw + 4, 8), "n=%d" % n, fill=(0, 0, 0))
    for i, (label, path) in enumerate(vids):
        y0 = 24 + i * (ch + lh)
        draw.text((4, y0 + 2), label, fill=(0, 0, 0))
        for j, n in enumerate(frames):
            im = grab(path, n, size)
            if im is None:
                continue
            canvas.paste(im, (j * cw, y0 + lh))
    out = os.path.join(a.out_dir, "逐帧对照.jpg")
    canvas.save(out, quality=90)
    print("对照图:", out)

    if a.motion:
        upto = max(frames) + a.window
        data = [(label, motion(path, a.window, (160, 90), upto)) for label, path in vids]
        print("\n帧号  " + "  ".join("%10s" % v[0] for v in data))
        for i in range(len(data[0][1])):
            n = data[0][1][i][0]
            cells = ["%10.1f" % d[1][i][1] if d[1][i][1] is not None else "%10s" % "NA"
                     for d in data]
            print("%5d  " % n + "  ".join(cells))


if __name__ == "__main__":
    main()
