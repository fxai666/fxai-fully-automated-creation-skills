# -*- coding: utf-8 -*-
"""跨技能共用的视频帧数实测：规格判据取解码帧数，容器元数据只作参照。"""
import subprocess


def decoded_frames(path):
    """返回 ffprobe -count_frames 实测的可解码帧数；读不到返回 0。"""
    r = subprocess.run(["ffprobe", "-v", "error", "-count_frames",
                        "-select_streams", "v:0", "-show_entries",
                        "stream=nb_read_frames", "-of", "default=nw=1:nk=1",
                        str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    try:
        return int((r.stdout or "").strip().splitlines()[0])
    except (ValueError, IndexError, AttributeError):
        return 0


def assert_frames(path, expected):
    """验收门：解码帧数与期望值相等才放行，差值直接抛错停在交付前。"""
    got = decoded_frames(path)
    if got != expected:
        raise RuntimeError("帧数验收未过：%s 解码实测 %d 帧，期望 %d 帧"
                           % (path, got, expected))
    return got
