#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""广告片交付清单：扫项目产出，写 delivery.json + 交付清单.md。

用法：
    python ad_delivery.py <项目目录> [--title 项目名]

规范见 fxai-ad-video/references/06。
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.dont_write_bytecode = True

VIDEO_EXT = (".mp4", ".mov", ".mkv")
AUDIO_EXT = (".wav", ".mp3", ".m4a")
IMG_EXT = (".png", ".jpg", ".jpeg")


def probe(path):
    ff = os.environ.get("FXAI_FFPROBE") or "ffprobe"
    try:
        r = subprocess.run([ff, "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height,r_frame_rate",
                            "-show_entries", "format=duration",
                            "-of", "json", path],
                           capture_output=True, text=True, errors="replace")
        data = json.loads(r.stdout or "{}")
    except (OSError, ValueError):
        return {}
    st = (data.get("streams") or [{}])[0]
    try:
        num, _, den = (st.get("r_frame_rate") or "25/1").partition("/")
        fps = round(float(num) / float(den or 1), 2)
    except (ValueError, ZeroDivisionError):
        fps = 0
    return {"duration": round(float((data.get("format") or {}).get("duration") or 0), 2),
            "width": int(st.get("width") or 0), "height": int(st.get("height") or 0),
            "fps": fps}


def audio_duration(path):
    ff = os.environ.get("FXAI_FFPROBE") or "ffprobe"
    try:
        r = subprocess.run([ff, "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", path],
                           capture_output=True, text=True, errors="replace")
        return round(float(r.stdout.strip() or 0), 2)
    except (OSError, ValueError):
        return 0.0


def load_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def scan(root):
    videos, audios, images = [], [], []
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if not os.path.isfile(p):
            continue
        low = name.lower()
        if low.endswith(VIDEO_EXT) and not name.startswith("_"):
            videos.append(p)
        elif low.endswith(AUDIO_EXT):
            audios.append(p)
        elif low.endswith(IMG_EXT):
            images.append(p)
    return videos, audios, images


def mb(path):
    try:
        return round(os.path.getsize(path) / 1048576.0, 1)
    except OSError:
        return 0.0


def placeholders(brand):
    """brand.json 里带尖括号或「占位」的值即待确认项。"""
    out = []
    for k, v in (brand or {}).items():
        if isinstance(v, str) and ("<" in v or "占位" in v):
            out.append((k, v))
    return out


def build(root, title):
    videos, audios, images = scan(root)
    brand = load_json(os.path.join(root, "brand.json"))
    cards = load_json(os.path.join(root, "infocards.json"))
    timeline = load_json(os.path.join(root, "timeline.json"))

    vinfo = [(os.path.basename(p), probe(p), mb(p)) for p in videos]
    vinfo.sort(key=lambda v: (0 if "成片" in v[0] else 1, -v[1].get("duration", 0)))
    ainfo = [(os.path.basename(p), audio_duration(p), mb(p)) for p in audios]
    card_items = (cards or {}).get("infocards") or (cards if isinstance(cards, list) else [])
    beats = (timeline or {}).get("beats") or []

    data = {
        "project": title,
        "project_root": os.path.abspath(root),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "videos": len(vinfo),
            "audios": len(ainfo),
            "audio_total_seconds": round(sum(a[1] for a in ainfo), 2),
            "infocards": len(card_items),
            "beats": len(beats),
        },
        "videos": [{"file": n, "duration_sec": i.get("duration", 0),
                    "resolution": "%dx%d" % (i.get("width", 0), i.get("height", 0)),
                    "fps": i.get("fps", 0), "size_mb": s}
                   for n, i, s in vinfo],
        "audios": [{"file": n, "duration_sec": d, "size_mb": s} for n, d, s in ainfo],
        "images": [{"file": os.path.basename(p), "size_mb": mb(p)} for p in images],
        "brand": brand,
        "pending": [{"key": k, "value": v} for k, v in placeholders(brand)],
    }

    lines = ["# 交付清单 · %s" % title, "",
             "- 生成时间：%s" % data["generated_at"],
             "- 项目目录：`%s`" % os.path.abspath(root), ""]

    lines += ["## 一、成片", ""]
    if vinfo:
        lines += ["| 文件 | 类型 | 时长 | 分辨率 | 帧率 | 大小 |",
                  "|---|---|---|---|---|---|"]
        for n, i, s in vinfo:
            kind = "交付版" if "成片" in n else "过程版"
            lines.append("| %s | %s | %.2fs | %dx%d | %s | %.1f MB |"
                         % (n, kind, i.get("duration", 0), i.get("width", 0),
                            i.get("height", 0), i.get("fps", 0), s))
    else:
        lines.append("（项目根暂无成片）")
    lines.append("")

    lines += ["## 二、音频", ""]
    if ainfo:
        lines += ["| 文件 | 时长 | 大小 |", "|---|---|---|"]
        for n, d, s in ainfo:
            lines.append("| %s | %.2fs | %.1f MB |" % (n, d, s))
        lines.append("")
        lines.append("合计 %.2fs。" % data["summary"]["audio_total_seconds"])
    else:
        lines.append("（暂无音频）")
    lines.append("")

    lines += ["## 三、后期层", "",
              "| 层 | 条数 / 区间 | 来源 |", "|---|---|---|",
              "| 信息贴片 | %d 条 | `infocards.json` |" % len(card_items),
              "| 标题卡节拍 | %d 条 | `timeline.json` |" % len(beats),
              "| 封面 / 片尾卡 | %d 张 | 项目根图片 |" % len(images), ""]

    lines += ["## 四、品牌信息", ""]
    if brand:
        for k, v in brand.items():
            if k.startswith("_") or isinstance(v, dict):
                continue
            flag = "占位待确认" if isinstance(v, str) and ("<" in v or "占位" in v) else "真值"
            lines.append("- %s：%s（%s）" % (k, v, flag))
        pend = data["pending"]
        lines += ["", "待确认 %d 项，改 `brand.json` 后重出片尾与贴片。" % len(pend)]
    else:
        lines.append("（项目根无 brand.json）")
    lines.append("")

    lines += ["## 五、自检", "",
              "- [ ] `check_project_clean.py <项目> --strict` 返回 0",
              "- [ ] 抽帧联络表逐段核过",
              "- [ ] 时长 / 音画 / 文字层三项核对通过",
              "- [ ] 项目清单 `deliver` 段已登记", ""]

    with open(os.path.join(root, "delivery.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(os.path.join(root, "交付清单.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    return data


def main():
    ap = argparse.ArgumentParser(description="广告片交付清单")
    ap.add_argument("project")
    ap.add_argument("--title", default="")
    args = ap.parse_args()
    root = os.path.abspath(args.project)
    if not os.path.isdir(root):
        raise SystemExit("目录不存在：%s" % root)
    title = args.title or os.path.basename(root.rstrip("\\/"))
    data = build(root, title)
    s = data["summary"]
    print("OK  %s" % os.path.join(root, "交付清单.md"))
    print("    成片 %d 条 / 音频 %d 条（%.2fs）/ 贴片 %d 条 / 节拍 %d 条 / 待确认 %d 项"
          % (s["videos"], s["audios"], s["audio_total_seconds"],
             s["infocards"], s["beats"], len(data["pending"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
