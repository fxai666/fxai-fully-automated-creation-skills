# -*- coding: utf-8 -*-
"""片头卡尺寸门禁：片头卡 PNG 必须严格等于成片分辨率，1:1 拼接，禁止 scale 缩放。

2026-11-28 立。根因：拿平台横封面 1920×1080 缩放塞进 1280×768 成片，
一道 scale 把文字边缘抹糊。正确做法：ffprobe 读成片宽高 → 向上取 32 倍数原生生图
→ 只裁不缩回到视频尺寸。尺寸解析统一走 video_size.py，不在这里另写一套。

用法：
    python check_head_size.py --head <片头卡.png> --video <成片.mp4>
    python check_head_size.py --head <片头卡.png> --video <成片.mp4> --native-size 1280x768
退出码：0=通过；非 0=硬拦（尺寸不一致 / 读不出尺寸）。
"""
import argparse
import os
import sys

SKILL = os.path.dirname(os.path.abspath(__file__))
if SKILL not in sys.path:
    sys.path.insert(0, SKILL)
import video_size as VS         # noqa: E402


probe_size = VS.probe_size


def image_size(png):
    """读 PNG 宽高，返回 (w, h)。"""
    from PIL import Image
    with Image.open(png) as im:
        return im.width, im.height


def main():
    ap = argparse.ArgumentParser(description="片头卡尺寸门禁（片头卡 == 成片，1:1 不缩放）")
    ap.add_argument("--head", required=True, help="片头卡 PNG")
    ap.add_argument("--video", required=True, help="成片 MP4（或干净正片）")
    ap.add_argument("--native-size", default="",
                    help="生图时的 32 倍数原生尺寸 WxH；给了就校验「只裁不缩」")
    ap.add_argument("--allow-scale", action="store_true",
                    help="逃生阀：尺寸不等也放行（仅调试，交付禁用；打印 WARN）")
    a = ap.parse_args()

    if not os.path.isfile(a.head):
        print("FAIL 片头卡不存在：%s" % a.head, file=sys.stderr)
        return 2
    if not os.path.isfile(a.video):
        print("FAIL 成片不存在：%s" % a.video, file=sys.stderr)
        return 2

    hw, hh = image_size(a.head)
    vw, vh = probe_size(a.video)
    ok = (hw, hh) == (vw, vh)
    print("片头卡 %dx%d / 成片 %dx%d → %s" %
          (hw, hh, vw, vh, "一致 ✅" if ok else "不一致 ❌"))

    # 最终 PNG 可以不是 32 倍数：成片尺寸本身非 32 倍数时，按 ceil32 原生出图后只裁不缩。
    mod32 = (hw % 32 == 0 and hh % 32 == 0)
    if not mod32:
        print("INFO 片头卡非 32 倍数属正常（成片 %dx%d 非 32 倍数，"
              "原生出图后只裁不缩的结果）" % (vw, vh), file=sys.stderr)

    if a.native_size:
        nw, nh = VS.parse_size(a.native_size)
        expect = VS.native_size(vw, vh)
        if (nw, nh) != expect:
            print("FAIL 原生生图尺寸 %dx%d ≠ 按成片推算的 %dx%d（32 倍数向上取整）"
                  % (nw, nh, expect[0], expect[1]), file=sys.stderr)
            return 4
        if hw > nw or hh > nh or (nw - hw) >= 32 or (nh - hh) >= 32:
            print("FAIL 原生 %dx%d → 成品 %dx%d 不是合法裁切（应只裁 ≤31px，禁止缩放）"
                  % (nw, nh, hw, hh), file=sys.stderr)
            return 4
        print("只裁不缩校验：原生 %dx%d → 成品 %dx%d（裁掉 %d×%d px）"
              % (nw, nh, hw, hh, nw - hw, nh - hh))

    if not ok:
        msg = ("FAIL 片头卡尺寸 != 成片尺寸：拼接会走 scale 缩放，文字/人物发虚。"
               "重出命令：cover_batch.py --project <项目> --gen-cover "
               "--gen-size video_head --video <成片.mp4>；"
               "或显式 --video-size WxH。禁止用 scale 补救。")
        if a.allow_scale:
            print("WARN " + msg + "（--allow-scale 已放行，交付禁用）", file=sys.stderr)
            return 0
        print(msg, file=sys.stderr)
        return 3

    print("PASS 片头卡与成片 1:1 匹配，可无缩放拼接。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
