# -*- coding: utf-8 -*-
"""qwen3tts_server.py 的端到端测试 / 调用用法。

演示：同一个 /synthesize 协议，两种走法
  1) 带 prompt_audio  → 参考音色克隆（千问 Base）
  2) 不带 prompt_audio → 内置音色 + 情绪指令（千问 CustomVoice）

用法: python qwen3tts_api_test.py [--url http://127.0.0.1:8086] [--only 1,2]
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
TAG = "<|endofprompt|>"

CASES = [
    # (序号, 说明, payload)
    (1, "传了音色 + 带情绪标签 → 走克隆，标签被忽略（只念正文）", {
        "text": "用温柔的语气说%s。今天想跟大家聊一聊，怎么把一件小事坚持做下去。" % TAG,
        "mode": "cross_lingual",
        "ref_audio_path": REF_AUDIO,
        "speed": 1.0,
    }),
    (2, "没传音色 + 愤怒描述 → 内置音色 vivian + 情绪描述", {
        "text": "用特别愤怒的语气说%s。其实我真的有发现，我是一个特别善于观察别人情绪的人。" % TAG,
        "mode": "instruct",
        "speaker": "vivian",
        "speed": 1.0,
    }),
    (3, "没传音色 + 四川话描述 → eric(成都) 音色", {
        "text": "用四川话自然口语的语气说%s。各位老铁，今天这个事儿巴适得很。" % TAG,
        "mode": "instruct",
        "speaker": "eric",
        "speed": 1.0,
    }),
    (4, "传了音色 + 语速 1.25 → 克隆 + 变速", {
        "text": "这个方案我们测试了三轮，效果比预期好很多。",
        "mode": "cross_lingual",
        "ref_audio_path": REF_AUDIO,
        "speed": 1.25,
    }),
    (5, "没传音色 + 没描述 → 纯朗读（默认音色，无 instruct）", {
        "text": "今天天气不错，我们出去走走吧。",
        "mode": "cross_lingual",
    }),
    (6, "音色和内置音色同时给 → 音色优先，走克隆", {
        "text": "用温柔的语气说%s。这句话应该由参考音色念出来。" % TAG,
        "mode": "cross_lingual",
        "ref_audio_path": REF_AUDIO,
        "speaker": "vivian",
    }),
    (7, "标签忘写 <|endofprompt|> 分隔符 → 兜底切掉标签", {
        "text": "用低沉疲惫的语气说。今天真的很累，早点休息吧。",
        "mode": "instruct",
        "speaker": "uncle_fu",
    }),
]


def post(url, path, payload, timeout=600):
    req = urllib.request.Request(
        url.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get(url, path, timeout=10):
    with urllib.request.urlopen(url.rstrip("/") + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8086")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = set(x.strip() for x in a.only.split(",") if x.strip())

    os.makedirs(OUT_DIR, exist_ok=True)

    print("=== GET /health ===")
    h = get(a.url, "/health")
    print(json.dumps({k: h[k] for k in ("status", "engine", "model_loaded", "device",
                                        "sample_rate", "default_speaker") if k in h},
                     ensure_ascii=False, indent=2))
    print()

    for idx, desc, case in CASES:
        if only and str(idx) not in only:
            continue
        payload = dict(case)
        ref_path = payload.pop("ref_audio_path", None)
        if ref_path:
            with open(ref_path, "rb") as f:
                payload["prompt_audio"] = base64.b64encode(f.read()).decode("ascii")

        print("-" * 70)
        print("[%d] %s" % (idx, desc))
        t0 = time.time()
        res = post(a.url, "/synthesize", payload)
        if not res.get("success"):
            print("    ✗ 失败：%s" % res.get("error"))
            continue
        out = os.path.join(OUT_DIR, "api_%02d_%s.wav" % (idx, res.get("route", "x")))
        with open(out, "wb") as f:
            f.write(base64.b64decode(res["audio_base64"]))
        print("    ✓ %s" % os.path.basename(out))
        print("      route=%s | %s" % (res.get("route"), res.get("route_reason")))
        print("      model=%s | speaker=%s" % (res.get("model"), res.get("speaker")))
        print("      instruct=%s | ignored_instruct=%s" %
              (res.get("instruct"), res.get("ignored_instruct")))
        print("      ref_text=%s | x_vector_only=%s" %
              (res.get("ref_text"), res.get("x_vector_only")))
        print("      音频 %.2fs | 采样率 %s | 服务端耗时 %ss | 客户端总耗时 %.1fs" %
              (res.get("total_duration"), res.get("sample_rate"),
               res.get("elapsed"), time.time() - t0))
        print("      text_used=%s" % res.get("text_used"))
        for w in res.get("warnings") or []:
            print("      ⚠ %s" % w)
        for n in res.get("notes") or []:
            print("      · %s" % n)
    print("-" * 70)


if __name__ == "__main__":
    sys.exit(main())
