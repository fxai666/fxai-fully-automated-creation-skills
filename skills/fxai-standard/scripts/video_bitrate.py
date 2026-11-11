# -*- coding: utf-8 -*-
"""交付视频码率验收：ffprobe 实测总比特率，落在容差内才放行。

规格见 references/12-交付视频编码规格.md。
用法：python -B video_bitrate.py <成片.mp4>
退出码：0 通过 / 1 未过 / 2 参数或文件错误。
"""
import os
import sys

sys.dont_write_bytecode = True
_HERE = os.path.dirname(os.path.abspath(__file__))
_COMMON = os.path.normpath(os.path.join(_HERE, "..", "..", "fxai-common"))
if _COMMON not in sys.path:
    sys.path.insert(0, _COMMON)

from video_encode import (DELIVERY_TOTAL_KBPS, assert_delivery_bitrate)  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print("用法：python -B video_bitrate.py <成片.mp4>")
        return 2
    path = sys.argv[1]
    if not os.path.isfile(path):
        print("文件不存在：%s" % path)
        return 2
    try:
        kbps = assert_delivery_bitrate(path)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    print("OK  %s  实测总比特率 %.0f kbps（目标 %d）"
          % (path, kbps, DELIVERY_TOTAL_KBPS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
