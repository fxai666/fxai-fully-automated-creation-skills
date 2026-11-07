# -*- coding: utf-8 -*-
"""测千问单次合成的文本长度上限：不同字数各跑一条，看是否完整、时长是否合理。

判断异常的依据：中文口播约 4.5~5 字/秒。
  实际时长 / 预期时长 ≈ 1.0 → 正常
  明显 > 1.2 或突然暴涨 → 重复/拖沓（长文本的典型退化）

用法: python qwen3tts_len_test.py [--url ...] [--route clone|speaker] [--lens 80,160,320]
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.request

REF_AUDIO = os.environ.get("QWEN3TTS_REF_AUDIO", "")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")

PARA = (
    "很多人以为做内容最重要的是灵感，其实不是。灵感来得快去得也快，真正决定你能不得"  # std:allow
    "持续做下去的，是一套稳定可重复的流程。你要先想清楚这件事到底为谁解决什么问题，"
    "然后把它拆成足够小的步骤，小到每天都能完成一点点。这样做的好处是，你不需要每天"
    "都状态很好，只需要每天都能往前走一步。时间一长，复利就出来了。我见过太多人，"
    "开头热情很高，几周之后就停了，原因往往不是能力不够，而是目标定得太大，"
    "每次开始都要消耗巨大的意志力。所以我的建议是，把标准降到你能长期坚持的水平，"
    "先做满一百天，再谈优化。"
)


def post(url, path, payload, timeout=1800):
    req = urllib.request.Request(
        url.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8086")
    ap.add_argument("--route", default="clone", choices=["clone", "speaker"])
    ap.add_argument("--lens", default="80,160,320")
    a = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    ref_b64 = base64.b64encode(open(REF_AUDIO, "rb").read()).decode("ascii") \
        if a.route == "clone" else None

    lens = [int(x) for x in a.lens.split(",") if x.strip()]
    print("=" * 88)
    print("路线：%s | 段落池 %d 字" % (a.route, len(PARA)))
    print("=" * 88)
    print("%6s %8s %9s %9s %8s %7s  %s" %
          ("字数", "音频s", "预期s", "比值", "耗时s", "状态", "备注"))
    print("-" * 88)

    for n in lens:
        text = (PARA * 3)[:n]
        payload = {"text": text, "mode": "cross_lingual", "speed": 1.0}
        if a.route == "clone":
            payload["prompt_audio"] = ref_b64
        else:
            payload["speaker"] = "vivian"
            payload["mode"] = "instruct"

        t0 = time.time()
        try:
            res = post(a.url, "/synthesize", payload)
        except Exception as e:
            print("%6d %8s %9s %9s %8s %7s  %s" % (n, "-", "-", "-", "-", "异常", e))
            continue
        el = time.time() - t0
        if not res.get("success"):
            print("%6d %8s %9s %9s %8.1f %7s  %s" % (n, "-", "-", "-", el, "失败",
                                                     res.get("error")))
            continue
        dur = res["total_duration"]
        exp = n / 4.7
        ratio = dur / exp
        status = "正常" if 0.7 <= ratio <= 1.3 else ("偏长⚠" if ratio > 1.3 else "偏短⚠")
        out = os.path.join(OUT_DIR, "len_%s_%03d.wav" % (a.route, n))
        open(out, "wb").write(base64.b64decode(res["audio_base64"]))
        print("%6d %8.2f %9.1f %9.2f %8.1f %7s  %s" %
              (n, dur, exp, ratio, el, status, os.path.basename(out)))
        for w in res.get("warnings") or []:
            print("        ⚠ %s" % w)
    print("=" * 88)


if __name__ == "__main__":
    sys.exit(main())
